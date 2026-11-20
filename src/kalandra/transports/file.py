import asyncio
import logging
import shutil
from pathlib import Path
from typing import Literal

from kalandra.auth.basic import CredentialProvider

from .base import BaseConnection, FetchConnection, PushConnection, Transport

logger = logging.getLogger(__name__)
STDERR_DRAIN_TIMEOUT = 1.0


class FileServiceProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(limit=2**16, loop=loop)
        self.exited = asyncio.Event()

    def process_exited(self) -> None:
        super().process_exited()
        self.exited.set()


class FileConnection(BaseConnection["FileTransport"]):
    process: asyncio.SubprocessTransport

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
        executable, *args = self._service_argv(service_name)
        loop = asyncio.get_running_loop()
        self.process, protocol = await loop.subprocess_exec(
            lambda: FileServiceProtocol(loop),
            executable,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"GIT_PROTOCOL": f"version={self.git_protocol}"},
        )

        returncode = self.process.get_returncode()
        if returncode is not None:
            self.process.close()
            logger.error(f"Failed to start process {service_name}: {self.process} / {returncode}")
            raise RuntimeError(f"Failed to start process {service_name}")

        assert protocol.stdout is not None
        assert protocol.stdin is not None
        assert protocol.stderr is not None

        self._process_exited = protocol.exited
        self._err_task = asyncio.create_task(self.log_error_messages(protocol.stderr))

        return protocol.stdout, protocol.stdin

    async def log_error_messages(self, stream: asyncio.StreamReader) -> None:
        async for line in stream:
            logger.error(f"Error: {line.decode()}")

    async def _close_service_connection(self) -> None:
        try:
            if self.process.get_returncode() is None:
                self.process.terminate()
            # Process.wait() can wait for pipe closure. A descendant may still hold stderr open.
            await self._process_exited.wait()
            await asyncio.wait_for(self._err_task, timeout=STDERR_DRAIN_TIMEOUT)
        except TimeoutError:
            logger.debug("Timed out draining stderr from %s", self.transport.path)
        finally:
            self._err_task.cancel()
            self.process.close()


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
