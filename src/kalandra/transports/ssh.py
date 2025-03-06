import asyncio
import logging
from typing import Literal

import asyncssh

from kalandra.auth.basic import CredentialProvider
from kalandra.gitprotocol import PacketLine

from .base import (
    BaseConnection,
    ConnectionException,
    FetchConnection,
    PushConnection,
    Transport,
)

logger = logging.getLogger(__name__)


class SSHConnection(BaseConnection["SSHTransport"]):
    _ssh: asyncssh.SSHClientConnection

    async def _open_service_connection(
        self, service_name: Literal["git-upload-pack", "git-receive-pack"]
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        logger.debug("Connecting to %s", self.transport.url)

        options = asyncssh.SSHClientConnectionOptions(
            # ignore_encrypted=False,
            # passphrase=(lambda x: input("Enter passphrase for %s: " % x)), # type: ignore
        )
        # open the SSH connection
        self._ssh = await asyncssh.connect(
            host=self.transport.host,
            username=self.transport.user,
            port=self.transport.port,
            options=options,
        )

        try:
            # run the git-upload-pack command
            self._process = await self._ssh.create_process(
                f"{service_name} {self.transport.path}",
                env={"GIT_PROTOCOL": f"version={self.git_protocol}"},
                encoding=None,
            )

            return self._process.stdout, self._process.stdin  # type: ignore
        except Exception:
            self._ssh.close()
            raise

    async def _close_service_connection(self) -> None:
        try:
            self._process.stdin.write_eof()
            await self._process.wait()
        finally:
            self._ssh.close()

    async def _read_packet(self) -> PacketLine:
        try:
            if self._process.exit_status is not None:
                raise EOFError("Process has exited")
            return await super()._read_packet()
        except asyncio.IncompleteReadError:
            logger.debug("EOF during packet read", exc_info=True)
            errors = (await self._process.stderr.read()).decode("utf-8")
            raise ConnectionException("Server closed connection: " + errors) from None


class SSHFetchConnection(SSHConnection, FetchConnection["SSHTransport"]):
    async def _open_fetch_service_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-upload-pack")


class SSHPushConnection(SSHConnection, PushConnection["SSHTransport"]):
    async def _open_push_service_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-receive-pack")


class SSHTransport(Transport):
    def __init__(self, *, url: str, credentials_provider: CredentialProvider):
        assert url.startswith("ssh://")
        super().__init__(url=url, credentials_provider=credentials_provider)

        address, self.path = url[6:].split("/", 1)
        self.user, host = address.split("@", 1)

        logger.debug("Resolved SSH host: %s", host)

        if ":" in host:
            self.host, port = host.split(":", 1)
            self.port = int(port)
        else:
            self.host = host
            self.port = 22

    @classmethod
    def can_handle_url(cls, url: str) -> bool:
        return url.startswith("ssh://")

    def fetch(self) -> SSHFetchConnection:
        return SSHFetchConnection(transport=self)

    def push(self) -> SSHPushConnection:
        return SSHPushConnection(transport=self)

    @property
    def url(self) -> str:
        return f"ssh://{self.user}@{self.host}:{self.port}/{self.path}"
