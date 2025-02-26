import asyncio
import logging
from typing import AsyncIterator, Iterable, Literal
from urllib.parse import urlparse, urlunsplit

import aiohttp
from aiofiles.threadpool.binary import AsyncBufferedIOBase

from kalandra.auth.basic import CredentialProvider
from kalandra.gitprotocol import PacketLine, PacketLineType

from .base import (
    BaseConnection,
    ConnectionException,
    FetchConnection,
    PushConnection,
    Transport,
)

logger = logging.getLogger(__name__)

type SessionFactory = type[aiohttp.ClientSession]


def _auth_headers(credentials: tuple[str, str] | str | None) -> dict[str, str]:
    if credentials is None:
        return {}
    if isinstance(credentials, str):
        return {"Authorization": credentials}
    return {"Authorization": aiohttp.BasicAuth(*credentials).encode()}


class HTTPSmartConnection(BaseConnection["HTTPTransport"]):
    _session: aiohttp.ClientSession | None = None
    _service: str | None = None

    async def _open_service_connection(
        self, service_name: Literal["git-upload-pack", "git-receive-pack"]
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        origin = urlparse(self.transport.url).hostname
        assert origin is not None, "No hostname in service URL"
        logger.debug("Getting credentials for %s", origin)
        credentials = await self.transport.get_credentials(origin)

        self._session = self.transport.session_factory(
            headers=dict(
                {
                    "Git-Protocol": self.git_protocol,
                    "User-Agent": "git/2.46.0",
                },
                **_auth_headers(credentials),
            ),
        )

        # As per https://git-scm.com/docs/gitprotocol-http#_url_format
        service_url = self.transport.url + f"/info/refs?service={service_name}"

        logger.debug("Connecting to %s, protocol %s", service_url, self.git_protocol)
        hello_resp = await self._session.get(service_url, headers={"Git-Protocol": self.git_protocol})

        if hello_resp.status != 200:
            import pdb

            pdb.set_trace()
            raise ConnectionException(f"Failed to connect to server {hello_resp.reason} ({hello_resp.status})")

        content_type = hello_resp.headers.get("Content-Type")
        if content_type != f"application/x-{service_name}-advertisement":
            raise ConnectionException(f"Expected 'smart' HTTP response, got {content_type}")

        # Read the first packet and verify it is the service we requested
        self._service = service_name
        self.reader = hello_resp.content  # type: ignore

        header = await self._read_packet()
        if header.data != f"# service={service_name}\n".encode("ascii"):
            if service_name == "git-upload-pack" and header.data == b"version 2":
                # JGit servers do not send the service name, but they do send the version immediately
                logger.warning("JGit server did not send service name, assuming git-upload-pack")
                self._shift_packet(header)
            else:
                raise ConnectionException(f"Smart protocol requires service header, instead got: {header}")
        else:
            pkt = await self._read_packet()
            assert pkt.type == PacketLineType.FLUSH, "Expected flush after service header, got %r" % pkt

        return hello_resp.content, None  # type: ignore

    async def _close_service_connection(self) -> None:
        if self._session:
            await self._session.close()


class HTTPSmartFetchConnection(HTTPSmartConnection, FetchConnection["HTTPTransport"]):
    async def _open_fetch_service_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-upload-pack")

    async def _send_command_v2(
        self,
        command: str,
        args: Iterable[str],
        **capabilities: dict[str, str],
    ) -> None:
        assert self.writer is None
        assert self._session is not None

        async def generate_command_data() -> AsyncIterator[bytes]:
            async for pkt in self._generate_command_v2(command, args, **capabilities):
                yield pkt.marker_bytes
                yield pkt.data

        # Send a new HTTP POST request with the command
        url = self.transport.url + f"/{self._service}"
        logger.debug("Sending command to %s", url)
        resp = await self._session.post(
            url,
            headers={
                "Content-Type": f"application/x-{self._service}-request",
                "Cache-Control": "no-cache",
                "Accept": f"application/x-{self._service}-result",
            },
            data=generate_command_data(),
        )

        if resp.status != 200:
            raise ConnectionException(f"Failed to send '{command}' command: {resp.reason} ({resp.status})")

        self.reader = resp.content  # type: ignore


class HTTPSmartPushConnection(HTTPSmartConnection, PushConnection["HTTPTransport"]):
    async def _open_push_service_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-receive-pack")

    async def _send_commands(self, packets: AsyncIterator[PacketLine], packfile: AsyncBufferedIOBase | None) -> None:
        """
        As per <https://git-scm.com/docs/http-protocol#_smart_service_git_receive_pack>, the client must
        make a new POST request directly to the service URL.
        """
        assert self._session is not None, "HTTP session not initialized"
        assert self.writer is None

        async def generate_command_data():
            async for pkt in packets:
                yield pkt.marker_bytes
                yield pkt.data

            if packfile is not None:
                # logger.debug("Sending packfile")
                await packfile.seek(0)
                bytes_sent = 0
                async for chunk in packfile:
                    yield chunk
                    bytes_sent += len(chunk)
                logger.debug("Packfile sent: %d bytes", bytes_sent)
            else:
                logger.debug("No packfile to send")

        # Send a new HTTP POST request with the command
        url = self.transport.url + f"/{self._service}"
        logger.debug("Sending command to %s", url)
        response = await self._session.post(
            url,
            read_until_eof=False,
            read_bufsize=5000,
            headers={
                "Content-Type": f"application/x-{self._service}-request",
                "Cache-Control": "no-cache",
                "Accept": f"application/x-{self._service}-result",
            },
            data=generate_command_data(),
        )
        logger.debug("Response: %s", response.headers)
        if response.status != 200:
            raise ConnectionException(f"Request failed: {response.status}: {response.reason}")

        self.reader = response.content  # type: ignore


class HTTPTransport(Transport):
    def __init__(
        self,
        *,
        url: str,
        credentials_provider: CredentialProvider,
        session_factory: SessionFactory = aiohttp.ClientSession,
    ):
        super().__init__(url=url, credentials_provider=credentials_provider)

        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https"), f"Unsupported protocol: {parsed.scheme}"
        self.protocol = parsed.scheme

        self.user = parsed.username
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path
        self._session_factory = session_factory

    @classmethod
    def can_handle_url(cls, url: str) -> bool:
        return url.startswith(("https://", "http://"))

    def fetch(self) -> HTTPSmartFetchConnection:
        return HTTPSmartFetchConnection(transport=self)

    def push(self) -> HTTPSmartPushConnection:
        return HTTPSmartPushConnection(transport=self)

    @property
    def url(self) -> str:
        netloc = f"{self.host}:{self.port}" if self.port else self.host
        return urlunsplit((self.protocol, netloc, self.path, "", ""))

    @property
    def session_factory(self) -> SessionFactory:
        return self._session_factory
