import pathlib
from unittest.mock import Mock

import pytest
from github import GithubIntegration

from kalandra.auth import github


@pytest.mark.asyncio
async def test_auth_github(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    integration = Mock(spec=GithubIntegration)
    integration.get_org_installation.return_value = Mock(id=123)

    monkeypatch.setattr(github, "create_integration", Mock(return_value=integration))
    provider = github.GitHubAppCredentialProvider("app_id", tmp_path / "fake.key", "lqc")

    integration.get_org_installation.assert_called_once_with("lqc")
    assert provider._installation_id == 123  # type: ignore

    assert await provider.get_credentials("github.com") is not None, "Should return credentials"
    integration.get_access_token.assert_called_once_with(123)

    integration.reset_mock()

    assert await provider.get_credentials("gitlab.com") is None, "Should return None for other origins"
    integration.get_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_auth_github_missing_installation(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    integration = Mock(spec=GithubIntegration)
    integration.get_org_installation.return_value = None

    monkeypatch.setattr(github, "create_integration", Mock(return_value=integration))
    provider = github.GitHubAppCredentialProvider("app_id", tmp_path / "fake.key", "lqc")

    integration.get_org_installation.assert_called_once_with("lqc")
    assert provider._installation_id is None  # type: ignore

    assert await provider.get_credentials("github.com") is None, "Should NOT return credentials"
    integration.get_access_token.assert_not_called()


def test_auth_github_create_integration():
    assert github.create_integration("12345678", pathlib.Path("fake.key"))


def test_auth_github_create_integration_missing_dependencies(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github, "Auth", None)
    monkeypatch.setattr(github, "GithubIntegration", None)

    with pytest.raises(Exception, match="GitHub integration not available"):
        github.create_integration("12345678", pathlib.Path("fake.key"))
