from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from kalandra.stream_utils import BytesStreamReader
from kalandra.transports import CredentialProvider, HTTPTransport


class StaticCredentialProvider(CredentialProvider):
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    async def get_credentials(self, origin: str) -> tuple[str, str]:
        return self.username, self.password


class MockResponse(NamedTuple):
    status: int
    headers: dict[str, str]
    content: BytesStreamReader

    @classmethod
    def create(cls, status: int, headers: dict[str, str], content: bytes):
        return cls(status, dict(headers), BytesStreamReader(content))


def mock_session(*responses: MockResponse) -> type[aiohttp.ClientSession]:
    session = MagicMock(aiohttp.ClientSession)
    session.get = AsyncMock()
    session.post = AsyncMock()
    session.get.side_effect = responses
    session.post.side_effect = responses

    session_factory = MagicMock()
    session_factory.return_value = session
    return session_factory  # type: ignore


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
async def test_http_push_connection_hello_v1():
    session_factory = mock_session(
        MockResponse.create(
            200, {"Content-Type": "application/x-git-receive-pack-advertisement"}, GITHUB_RECEIVE_HELLO
        ),
    )

    transport = HTTPTransport(
        url="http://example.test/repo.git",
        credentials_provider=StaticCredentialProvider("john", "pass"),
        session_factory=session_factory,
    )

    async with transport.push() as connection:
        assert connection.refs == {
            "refs/heads/fix-cve": "cbdb5e1688df40685e1711f84f3b893966ff41bb",
            "refs/heads/main": "0c3358886db1586913aba030e59e4b53c80659c9",
            "refs/heads/publisher": "f0b710df22916ef01713e96b0d77abac50e45a6b",
        }


@pytest.mark.asyncio
async def test_http_push_connection_hello_v0():
    session_factory = mock_session(
        MockResponse.create(
            200, {"Content-Type": "application/x-git-receive-pack-advertisement"}, GERRIT_RECEIVE_HELLO
        ),
    )

    transport = HTTPTransport(
        url="http://example.test/repo.git",
        credentials_provider=StaticCredentialProvider("john", "pass"),
        session_factory=session_factory,
    )

    async with transport.push() as connection:
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
async def test_http_fetch_connection_hello():
    session_factory = mock_session(
        MockResponse.create(200, {"Content-Type": "application/x-git-upload-pack-advertisement"}, GITHUB_UPLOAD_HELLO),
    )

    transport = HTTPTransport(
        url="http://example.test/repo.git",
        credentials_provider=StaticCredentialProvider("john", "pass"),
        session_factory=session_factory,
    )

    async with transport.fetch() as connection:
        assert connection.capabilities == {
            "ls-refs=unborn",
            "fetch=shallow wait-for-done filter",
            "object-format=sha1",
            "server-option",
            "agent=git/github-d6c9584635a2",
        }


@pytest.mark.asyncio
async def test_http_fetch_connection_hello_gerrit():
    session_factory = mock_session(
        MockResponse.create(200, {"Content-Type": "application/x-git-upload-pack-advertisement"}, GERRIT_UPLOAD_HELLO),
    )

    transport = HTTPTransport(
        url="http://example.test/repo.git",
        credentials_provider=StaticCredentialProvider("john", "pass"),
        session_factory=session_factory,
    )

    async with transport.fetch() as connection:
        assert connection.capabilities == {
            "ls-refs",
            "fetch=shallow",
            "server-option",
            "agent=JGit/v6.10.0.202406032230-r-73-gd5cc102e7",
        }
