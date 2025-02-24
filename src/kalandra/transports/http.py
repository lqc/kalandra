import asyncio
import logging
from typing import Iterable, Literal
from urllib.parse import urlparse, urlunsplit

import aiohttp

from kalandra.stream_utils import BytesStreamWriter

from .base import (
    BaseConnection,
    ConnectionException,
    FetchConnection,
    PushConnection,
    Transport,
)
from .credentials import CredentialProvider

logger = logging.getLogger(__name__)

type SessionFactory = type[aiohttp.ClientSession]


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
            auth=aiohttp.BasicAuth(*credentials) if credentials else None,
            headers={"Git-Protocol": self.git_protocol},
        )

        # As per https://git-scm.com/docs/gitprotocol-http#_url_format
        service_url = self.transport.url + f"/info/refs?service={service_name}"

        logger.debug("Connecting to %s, protocol %s", service_url, self.git_protocol)

        hello_resp = await self._session.get(service_url, headers={"Git-Protocol": self.git_protocol})

        if hello_resp.status != 200:
            raise ConnectionException(f"Unexpected status code: {hello_resp.status}")

        content_type = hello_resp.headers.get("Content-Type")
        if content_type != f"application/x-{service_name}-advertisement":
            raise ConnectionException(f"Expected 'smart' HTTP response, got {content_type}")

        # Read the first packet and verify it is the service we requested
        self._service = service_name
        self.reader = hello_resp.content  # type: ignore

        header = None
        async for pkt in self._read_packets_until_flush():
            if header is not None:
                raise ConnectionException("Unexpected packet after header: %r" % pkt)
            header = pkt
        assert header is not None, "No header received"
        if header.data != f"# service={service_name}\n".encode("ascii"):
            raise ConnectionException(f"Unexpected service: {header}")

        return hello_resp.content, None  # type: ignore

    async def _close_service_connection(self) -> None:
        if self._session:
            await self._session.close()

    async def _send_command_v2(
        self,
        command: str,
        args: Iterable[str],
        **capabilities: dict[str, str],
    ) -> None:
        assert self.writer is None
        assert self._session is not None

        buffer = self.writer = BytesStreamWriter()
        await super()._send_command_v2(command, args, **capabilities)
        self.writer = None

        # Send a new HTTP POST request with the command
        url = self.transport.url + f"/{self._service}"
        logger.debug("Sending command to %s", url)
        resp = await self._session.post(
            url,
            data=buffer.getvalue(),
        )

        if resp.status != 200:
            raise ConnectionException(f"Unexpected status code: {resp.status}")

        self.reader = resp.content  # type: ignore


class HTTPSmartFetchConnection(HTTPSmartConnection, FetchConnection["HTTPTransport"]):
    async def _open_fetch_service_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-upload-pack")


class HTTPSmartPushConnection(HTTPSmartConnection, PushConnection["HTTPTransport"]):
    async def _open_push_service_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-receive-pack")


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
        self.user = parsed.username
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.path = parsed.path
        self._session_factory = session_factory

    @classmethod
    def can_handle_url(cls, url: str) -> bool:
        return url.startswith("https://")

    def fetch(self) -> HTTPSmartFetchConnection:
        return HTTPSmartFetchConnection(transport=self)

    def push(self) -> HTTPSmartPushConnection:
        return HTTPSmartPushConnection(transport=self)

    @property
    def url(self) -> str:
        return urlunsplit(("https", f"{self.host}", self.path, "", ""))

    @property
    def session_factory(self) -> SessionFactory:
        return self._session_factory
