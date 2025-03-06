import asyncio
import logging
import os
import time
from typing import AsyncIterator, Literal
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


def http_timeout():
    total_timeout = int(os.environ.get("KALANDRA_HTTP_TIMEOUT", 1200))  # default 20 minutes
    logger.debug("HTTP timeout set to %d seconds", total_timeout)
    return aiohttp.ClientTimeout(total=total_timeout, connect=60, sock_connect=60)


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
            headers={
                "User-Agent": "git/2.46.0",
            },
            auth=aiohttp.BasicAuth(*credentials) if credentials else None,
        )

        # As per https://git-scm.com/docs/gitprotocol-http#_url_format
        service_url = self.transport.url + f"/info/refs?service={service_name}"

        logger.debug("Connecting to %s, protocol %s", service_url, self.git_protocol)
        hello_resp = await self._session.get(
            service_url,
            headers={
                "Accept": f"application/x-{service_name}-advertisement",
                "Git-Protocol": f"version={self.git_protocol}",
            },
        )

        if hello_resp.status != 200:
            raise ConnectionException(f"Failed to connect to server {hello_resp.reason} ({hello_resp.status})")

        content_type = hello_resp.headers.get("Content-Type")
        if content_type != f"application/x-{service_name}-advertisement":
            raise ConnectionException(f"Expected 'smart' HTTP response, got {content_type}")

        # Read the first packet and verify it is the service we requested
        self._service = service_name
        self.reader = hello_resp.content  # type: ignore

        header = await self._read_packet()

        if header.type != PacketLineType.DATA:
            raise ConnectionException(f"Expected data packet, got {header}")

        if header.data == f"# service={service_name}\n".encode("ascii"):
            # The server sent the service name. GitHub does this event for v2, so we need to read the next packet
            pkt = await self._read_packet()
            assert pkt.type == PacketLineType.FLUSH, "Expected flush after service header, got %r" % pkt

            pkt = await self._read_packet()
            if pkt.type == PacketLineType.DATA and pkt.data.rstrip() == b"version 2":
                logger.debug("Server supports protocol version 2 (with quirks)")
                self._negotiated_protocol = 2
            else:
                self._negotiated_protocol = 1
                logger.debug("Server supports protocol version 1")

            # put the packet back in the buffer
            self._shift_packet(pkt)

        elif header.data.rstrip() == b"version 2":
            logger.debug("Server supports protocol version 2")
            self._negotiated_protocol = 2
            self._shift_packet(header)
        else:
            raise ConnectionException(f"Expected service header or version packet, instead got: {header}")

        return hello_resp.content, None  # type: ignore

    async def _close_service_connection(self) -> None:
        if self._session:
            await self._session.close()


class HTTPSmartFetchConnection(HTTPSmartConnection, FetchConnection["HTTPTransport"]):
    async def _open_fetch_service_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-upload-pack")

    async def _send_packet_transaction(self, packets: AsyncIterator[PacketLine]) -> None:
        assert self.writer is None
        assert self._session is not None

        async def generate_command_data() -> AsyncIterator[bytes]:
            async for pkt in packets:
                yield pkt.marker_bytes
                yield pkt.data

        # Send a new HTTP POST request with the command
        url = self.transport.url + f"/{self._service}"
        logger.debug("Sending 'fetch' request %s", url)
        response = await self._session.post(
            url,
            headers={
                "Content-Type": f"application/x-{self._service}-request",
                "Cache-Control": "no-cache",
                "Accept": f"application/x-{self._service}-result, */*",
                "Git-Protocol": f"version={self.negotiated_protocol}",
            },
            data=generate_command_data(),
            timeout=http_timeout(),
        )

        if response.status != 200:
            logger.error("Error response: %s", await response.text())
            raise ConnectionException(f"Failed to send packets: {response.reason} ({response.status})")

        logger.debug("Request complete, got: %s", response.headers)

        self.reader = response.content  # type: ignore


MEGABTE = 1024 * 1024


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
                logger.debug("Sending packfile")
                await packfile.seek(0)
                bytes_sent = 0
                total_sent = 0
                start_time = time.perf_counter()
                async for chunk in packfile:
                    yield chunk
                    bytes_sent += len(chunk)
                    if bytes_sent > 32 * MEGABTE:
                        end_time = time.perf_counter()
                        total_sent += bytes_sent
                        elapsed_seconds = end_time - start_time
                        logger.debug(
                            "Sent %.2f MB so far, current speed: %.2f MB/s",
                            total_sent / MEGABTE,
                            bytes_sent / (elapsed_seconds) / MEGABTE,
                        )
                        bytes_sent = 0
                        start_time = time.perf_counter()

                logger.debug("Packfile sent.")
            else:
                logger.debug("No packfile to send")

        # Send a new HTTP POST request with the command
        url = self.transport.url + f"/{self._service}"
        logger.debug("Sending PUSH command to %s", url)
        response = await self._session.post(
            url,
            headers={
                "Content-Type": f"application/x-{self._service}-request",
                "Cache-Control": "no-cache",
                "Accept": f"application/x-{self._service}-result, */*",
                "Git-Protocol": f"version={self.negotiated_protocol}",
            },
            timeout=http_timeout(),
            data=generate_command_data(),
        )
        if response.status != 200:
            logger.error("Error response: %s", await response.text())
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
