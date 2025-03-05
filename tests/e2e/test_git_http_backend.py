#
# Test that we can connect to a Git repository over HTTP served using <https://git-scm.com/docs/git-http-backend>.
#

import asyncio
import logging
import shutil
from pathlib import Path

import pytest
from aiohttp import web

from kalandra.auth import NoopCredentialProvider
from kalandra.transports.http import HTTPTransport

pytestmark = pytest.mark.asyncio(loop_scope="module")


logger = logging.getLogger(__name__)


async def async_exec(*args, **kwargs):  # type: ignore
    process = await asyncio.create_subprocess_exec(*args, **kwargs)  # type: ignore
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise Exception(f"Command failed: {args}")
    return stdout, stderr


class GitServer:
    """
    Mock Git server implementation.
    """

    def __init__(self, repo_path: Path, git_executable: str):
        self.repo_path = repo_path
        self._bare_repo_path = repo_path / ".git"
        self.git_executable = git_executable
        self.override_git_protocol = None

    async def init_repo(self):
        self.repo_path.mkdir(parents=True)
        await async_exec(self.git_executable, "init", cwd=self.repo_path)
        (self.repo_path / "README.md").write_text("Hello, world!")
        await async_exec(self.git_executable, "add", ":/", cwd=self.repo_path)
        await async_exec(self.git_executable, "commit", "-m", "Initial Commit", cwd=self.repo_path)

    async def serve_git_request(self, request: web.Request):
        path = request.match_info["path"].lstrip("/")

        git_cmd_env: dict[str, str] = {
            "PATH_INFO": f"/{path}",
            "GIT_PROJECT_ROOT": str(self._bare_repo_path),
            "CONTENT_TYPE": request.headers.get("Content-Type", ""),
            "REQUEST_METHOD": request.method,
            "QUERY_STRING": request.query_string,
            "REMOTE_USER": "",
            "REMOTE_ADDR": request.remote or "",
            "HTTP_GIT_PROTOCOL": request.headers.get("Git-Protocol", ""),
            "GIT_HTTP_EXPORT_ALL": "1",
        }

        if self.override_git_protocol is not None:
            git_cmd_env["GIT_PROTOCOL"] = self.override_git_protocol

        logging.debug("Request Environment: %s", git_cmd_env)

        backend = await asyncio.create_subprocess_exec(
            self.git_executable,
            "http-backend",
            cwd=self._bare_repo_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env=git_cmd_env,
        )

        body = await request.read()
        stdout, stderr = await backend.communicate(body)
        if backend.returncode != 0:
            return web.Response(body=stderr, status=500)

        logger.debug("git process response: %r", stdout)
        response_headers: dict[str, str] = {}
        index = 0

        while True:
            next_nl = stdout[index:].find(b"\r\n")
            if next_nl == -1:
                break

            line = stdout[index : index + next_nl]
            if line == b"":
                # end of headers
                break

            key, value = line.split(b":", 1)
            response_headers[key.decode()] = value.decode()
            index += next_nl + 2

        response_body = stdout[index + next_nl + 2 :]
        return web.Response(body=response_body, headers=response_headers, status=200)

    def cleanup(self):
        shutil.rmtree(self.repo_path, ignore_errors=True)


@pytest.fixture(scope="function")
async def git_server(tmp_path: Path):
    git_cmd = shutil.which("git")
    if git_cmd is None:
        pytest.skip("git is not installed")

    server = GitServer(tmp_path / "repo.git", git_cmd)
    await server.init_repo()

    yield server

    server.cleanup()


@pytest.fixture(scope="function")
async def git_http_endpoint(git_server: GitServer):
    """
    Start a Git server using `git_http_backend` and return the URL and the path to the repository.
    """
    app = web.Application()
    app.add_routes([web.route("*", "/mock-repo.git{path:.+}", git_server.serve_git_request)])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()

    # Get the port number
    socket = site._server.sockets[0]  # type: ignore
    laddr = socket.getsockname()  # type: ignore

    yield f"http://localhost:{laddr[1]}/mock-repo.git"

    await runner.cleanup()


@pytest.mark.asyncio
async def test_git_http_backend_mock_isclonable(git_http_endpoint: str, git_server: GitServer, tmp_path: Path):
    """
    Test that our Git server mock works with "git clone".
    """
    print("ADDR", git_http_endpoint)
    target_dir = tmp_path / "cloned_repo"

    # Clone the repository
    await async_exec(
        git_server.git_executable,
        "clone",
        git_http_endpoint,
        str(target_dir),
        env={"GIT_CURL_TRACE": "1"},
    )

    assert target_dir.is_dir(), "The repository was not cloned"
    assert (target_dir / "README.md").read_text() == "Hello, world!", "The README file content is incorrect"


@pytest.mark.asyncio
async def test_git_http_backend_kalandra_can_fetch_with_v2(git_http_endpoint: str, git_server: GitServer):
    """
    Test that our Git server mock works with "git clone".
    """
    transport = HTTPTransport(
        url=git_http_endpoint,
        credentials_provider=NoopCredentialProvider(),
    )

    async with transport.fetch() as conn:
        refs = sorted([x.name async for x in conn.ls_refs()])

    assert refs == ["HEAD", "refs/heads/main"], "The repository was not cloned properly"


@pytest.mark.asyncio
async def test_git_http_backend_kalandra_can_fetch_with_v1(
    git_http_endpoint: str, git_server: GitServer, monkeypatch: pytest.MonkeyPatch
):
    """
    Test that our Git server mock works with "git clone".
    """
    monkeypatch.setattr(git_server, "override_git_protocol", "version=1")

    transport = HTTPTransport(
        url=git_http_endpoint,
        credentials_provider=NoopCredentialProvider(),
    )

    async with transport.fetch() as conn:
        refs = sorted([x.name async for x in conn.ls_refs()])

    assert refs == ["HEAD", "refs/heads/main"], "The repository was not cloned properly"
