from typing import Literal
from .base import Transport, BaseConnection, FetchConnection, PushConnection
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class FileConnection(BaseConnection["FileTransport"]):
    process: asyncio.subprocess.Process

    async def _open_service_connection(
        self, service_name: Literal["git-upload-pack", "git-receive-pack"]
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        logger.debug("Connecting to %s", self.transport.path)
        self.process = await asyncio.create_subprocess_exec(
            service_name,
            self.transport.path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            env={"GIT_PROTOCOL": self.git_protocol},
        )

        if self.process.returncode is not None:
            logger.error(
                f"Failed to start process {service_name}: {self.process} / {self.process.returncode}"
            )
            raise RuntimeError(f"Failed to start process {service_name}")

        assert self.process.stdout is not None
        assert self.process.stdin is not None

        return (self.process.stdout, self.process.stdin)

    async def _close_service_connection(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
        await self.process.wait()


class FileFetchConnection(FileConnection, FetchConnection["FileTransport"]):
    async def _open_fetch_service_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-upload-pack")


class FilePushConnection(FileConnection, PushConnection["FileTransport"]):
    async def _open_push_service_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await self._open_service_connection("git-receive-pack")


class FileTransport(Transport):
    def __init__(self, path: str | Path):
        if isinstance(path, str):
            assert path.startswith("file://")
            path = Path(path[7:])

        self.path = path.resolve()
        if not self.path.is_dir():
            raise FileNotFoundError(f"Path {self.path} must point to a git repository")

        objects = self.path / "objects"
        if not objects.is_dir():
            raise FileNotFoundError(
                f"Dir {self.path} doesn't look like a git repository"
            )

    @classmethod
    def can_handle_url(cls, url: str) -> bool:
        return url.startswith("file://")

    def fetch(self) -> FileFetchConnection:
        return FileFetchConnection(transport=self)

    def push(self) -> FilePushConnection:
        return FilePushConnection(transport=self)
