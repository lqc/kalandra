import asyncio
import logging
import shutil
from pathlib import Path
from typing import Literal

from kalandra.auth.basic import CredentialProvider

from .base import BaseConnection, FetchConnection, PushConnection, Transport

logger = logging.getLogger(__name__)


class FileConnection(BaseConnection["FileTransport"]):
    process: asyncio.subprocess.Process

    def _service_argv(self, service_name: Literal["git-upload-pack", "git-receive-pack"]) -> list[str]:
        # Prefer the dedicated plumbing binaries when available, but fall back to
        # invoking via `git` for portability (some distros keep git-* outside PATH).
        if shutil.which(service_name):
            return [service_name, str(self.transport.path)]

        # Map to `git <subcommand>`.
        if service_name == "git-upload-pack":
            subcommand = "upload-pack"
        elif service_name == "git-receive-pack":
            subcommand = "receive-pack"
        else:
            raise ValueError(f"Unsupported service: {service_name}")

        return ["git", subcommand, str(self.transport.path)]

    async def _open_service_connection(
        self, service_name: Literal["git-upload-pack", "git-receive-pack"]
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        logger.debug("Connecting to %s", self.transport.path)
        argv = self._service_argv(service_name)
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"GIT_PROTOCOL": f"version={self.git_protocol}"},
        )

        if self.process.returncode is not None:
            logger.error(f"Failed to start process {service_name}: {self.process} / {self.process.returncode}")
            raise RuntimeError(f"Failed to start process {service_name}")

        assert self.process.stdout is not None
        assert self.process.stdin is not None
        assert self.process.stderr is not None

        # We don't need stderr, so we can just read it in the background
        self._err_task = asyncio.create_task(self.log_error_messages(self.process.stderr))

        return (self.process.stdout, self.process.stdin)

    async def log_error_messages(self, stream: asyncio.StreamReader) -> None:
        async for line in stream:
            logger.error(f"Error: {line.decode()}")

    async def _close_service_connection(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
        await self.process.wait()

        self._err_task.cancel()
        await self._err_task


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
    def __init__(self, *, url: str, credentials_provider: CredentialProvider):
        assert url.startswith("file://")
        path = Path(url[7:])

        self.path = path.resolve()
        print(self.path.stat())
        if not self.path.is_dir():
            raise FileNotFoundError(f"Path {self.path} must point to a directory")

        objects = self.path / "objects"
        if not objects.is_dir():
            raise FileNotFoundError(f"Dir {self.path} doesn't look like a git repository")

        super().__init__(url=path.as_uri(), credentials_provider=credentials_provider)

    @classmethod
    def can_handle_url(cls, url: str) -> bool:
        return url.startswith("file://")

    def fetch(self) -> FileFetchConnection:
        return FileFetchConnection(transport=self)

    def push(self) -> FilePushConnection:
        return FilePushConnection(transport=self)
