from pathlib import Path
from unittest.mock import Mock

import pytest

from kalandra.auth.basic import ChainedCredentialProvider, CredentialProvider, NetrcCredentialProvider


@pytest.fixture
def mock_netrc_provider(tmp_path: Path) -> NetrcCredentialProvider:
    netrc_path = tmp_path / "mock_netrc"
    netrc_path.write_text("""
        machine example.com login john password pass
        machine example.org login jane password secret
    """)

    return NetrcCredentialProvider(netrc_path=netrc_path)


@pytest.mark.parametrize(
    "origin, expected",
    [
        ("example.com", ("john", "pass")),
        ("example.org", ("jane", "secret")),
        ("example.net", None),
    ],
)
@pytest.mark.asyncio
async def test_netrc_provider(
    mock_netrc_provider: NetrcCredentialProvider, origin: str, expected: tuple[str, str] | None
):
    assert await mock_netrc_provider.get_credentials(origin) == expected


@pytest.mark.asyncio
async def test_chained_provider_empty():
    provider = ChainedCredentialProvider()
    assert await provider.get_credentials("example.com") is None


@pytest.mark.asyncio
async def test_chained_provider_first_resolved():
    provider = ChainedCredentialProvider()
    first = Mock(CredentialProvider)
    first.get_credentials.return_value = ("john", "pass")
    provider.add_provider(first)
    second = Mock(CredentialProvider)
    second.get_credentials.return_value = ("jane", "foo")
    provider.add_provider(second)

    result = await provider.get_credentials("example.com")
    assert result == ("john", "pass")

    first.get_credentials.assert_called_once_with("example.com")
    second.get_credentials.assert_not_called()


@pytest.mark.asyncio
async def test_chained_provider_second_resolved():
    provider = ChainedCredentialProvider()
    first = Mock(CredentialProvider)
    first.get_credentials.return_value = None
    provider.add_provider(first)
    second = Mock(CredentialProvider)
    second.get_credentials.return_value = ("jane", "foo")
    provider.add_provider(second)

    result = await provider.get_credentials("example.com")
    assert result == ("jane", "foo")

    first.get_credentials.assert_called_once_with("example.com")
    second.get_credentials.assert_called_once_with("example.com")
