import asyncio
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from kalandra.auth import CredentialProvider
from kalandra.transports import HTTPTransport


class StaticCredentialProvider(CredentialProvider):
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    async def get_credentials(self, origin: str) -> tuple[str, str]:
        return self.username, self.password


class MockResponse(NamedTuple):
    status: int
    headers: dict[str, str]
    content: asyncio.StreamReader

    @classmethod
    def create(cls, status: int, headers: dict[str, str], content: bytes):
        reader = asyncio.StreamReader()
        reader.feed_data(content)
        reader.feed_eof()
        return cls(status, dict(headers), reader)


def mock_session(*args: MockResponse) -> type[aiohttp.ClientSession]:
    responses = list(args)

    def take_next_request(*args: Any, **kwargs: Any) -> MockResponse:
        return responses.pop(0)

    session = MagicMock(aiohttp.ClientSession)
    session.get = AsyncMock()
    session.post = AsyncMock()
    session.get.side_effect = take_next_request
    session.post.side_effect = take_next_request

    session_factory = MagicMock()
    session_factory.return_value = session
    return session_factory  # type: ignore


@pytest.fixture(scope="function")
def mocked_http_transport(request: pytest.FixtureRequest) -> HTTPTransport:
    """
    Create an HTTPTransport object with a mocked session factory.
    """
    assert isinstance(request.node, pytest.Function)  # type: ignore
    marker = request.node.get_closest_marker("http_interactions")
    if marker is None:
        raise ValueError("Test must be decorated with @pytest.mark.http_interactions")

    session_factory = mock_session(*marker.args)
    transport = HTTPTransport(
        url="http://example.test/repo.git",
        credentials_provider=StaticCredentialProvider("john", "pass"),
        session_factory=session_factory,
    )
    return transport


# GitHub correctly responds using V1 of the protocol for fetch
GITHUB_RECEIVE_HELLO = b"""001f# service=git-receive-pack
0000000eversion 1
0128cbdb5e1688df40685e1711f84f3b893966ff41bb refs/heads/fix-cve\x00report-status report-status-v2 delete-refs side-band-64k ofs-delta atomic object-format=sha1 quiet agent=github/spokes-receive-pack-b977044edd25c391d0b076eb24f83b37fc71b4e8 session-id=EE47:6777:1754F34:1E8F407:67BC6C3F push-options
003d0c3358886db1586913aba030e59e4b53c80659c9 refs/heads/main
0042f0b710df22916ef01713e96b0d77abac50e45a6b refs/heads/publisher
0000"""

# Some versions of Gerrit are mission the "version 1" packet
GERRIT_RECEIVE_HELLO = b"""001f# service=git-receive-pack
000000baf8355e1c8022fb6825c0d901c5d8617297ff626e refs/heads/main\x00 side-band-64k delete-refs report-status quiet atomic ofs-delta push-options agent=JGit/v6.10.0.202406032230-r-73-gd5cc102e7
003e28d140655d50e594417908cf4193e4387d05f6ff refs/meta/config
0000"""


@pytest.mark.asyncio
@pytest.mark.http_interactions(
    MockResponse.create(200, {"Content-Type": "application/x-git-receive-pack-advertisement"}, GITHUB_RECEIVE_HELLO),
)
async def test_http_push_hello_v1(mocked_http_transport: HTTPTransport):
    async with mocked_http_transport.push() as connection:
        assert connection.refs == {
            "refs/heads/fix-cve": "cbdb5e1688df40685e1711f84f3b893966ff41bb",
            "refs/heads/main": "0c3358886db1586913aba030e59e4b53c80659c9",
            "refs/heads/publisher": "f0b710df22916ef01713e96b0d77abac50e45a6b",
        }


@pytest.mark.asyncio
@pytest.mark.http_interactions(
    MockResponse.create(200, {"Content-Type": "application/x-git-receive-pack-advertisement"}, GERRIT_RECEIVE_HELLO),
)
async def test_http_push_hello_v0(mocked_http_transport: HTTPTransport):
    async with mocked_http_transport.push() as connection:
        assert connection.refs == {
            "refs/heads/main": "f8355e1c8022fb6825c0d901c5d8617297ff626e",
            "refs/meta/config": "28d140655d50e594417908cf4193e4387d05f6ff",
        }


GITHUB_UPLOAD_HELLO = b"""001e# service=git-upload-pack
0000000eversion 2
0022agent=git/github-d6c9584635a2
0013ls-refs=unborn
0027fetch=shallow wait-for-done filter
0012server-option
0017object-format=sha1
0000"""


# JGit/Gerrit incorrectly omits the '# service=git-upload-pack' line
# https://github.com/eclipse-jgit/jgit/blob/68f454af418224b1ba654337c073bfb06cfb16c6/org.eclipse.jgit/src/org/eclipse/jgit/transport/TransportHttp.java#L1337
GERRIT_UPLOAD_HELLO = (
    b"000dversion 2000bls-refs0011fetch=shallow0011server-option0033agent=JGit/v6.10.0.202406032230-r-73-gd5cc102e70000"
)


@pytest.mark.asyncio
@pytest.mark.http_interactions(
    MockResponse.create(200, {"Content-Type": "application/x-git-upload-pack-advertisement"}, GITHUB_UPLOAD_HELLO),
    MockResponse.create(
        200,
        {"Content-Type": "application/x-git-upload-pack-response"},
        b"""003d0c3358886db1586913aba030e59e4b53c80659c9 refs/heads/main
0042f0b710df22916ef01713e96b0d77abac50e45a6b refs/heads/publisher
0000
""",
    ),
)
async def test_http_fetch_hello_v2(mocked_http_transport: HTTPTransport):
    async with mocked_http_transport.fetch() as connection:
        assert connection.capabilities == {
            "ls-refs=unborn",
            "fetch=shallow wait-for-done filter",
            "object-format=sha1",
            "server-option",
            "agent=git/github-d6c9584635a2",
        }

        known_refs = {ref.name: ref.object_id async for ref in connection.ls_refs()}
        assert known_refs == {
            "refs/heads/main": "0c3358886db1586913aba030e59e4b53c80659c9",
            "refs/heads/publisher": "f0b710df22916ef01713e96b0d77abac50e45a6b",
        }


@pytest.mark.asyncio
@pytest.mark.http_interactions(
    MockResponse.create(200, {"Content-Type": "application/x-git-upload-pack-advertisement"}, GERRIT_UPLOAD_HELLO),
)
async def test_http_fetch_hello_v2_gerrit(mocked_http_transport: HTTPTransport):
    async with mocked_http_transport.fetch() as connection:
        assert connection.capabilities == {
            "ls-refs",
            "fetch=shallow",
            "server-option",
            "agent=JGit/v6.10.0.202406032230-r-73-gd5cc102e7",
        }
