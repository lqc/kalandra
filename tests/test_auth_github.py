import pathlib
from unittest.mock import Mock

import pytest
from github import GithubIntegration

from kalandra.github_config_utils import GitHubAppCredentialProvider


@pytest.mark.asyncio
async def test_auth_github(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    integration = Mock(spec=GithubIntegration)
    provider = GitHubAppCredentialProvider(integration, 123)
    assert provider._installation_id == 123  # type: ignore

    assert await provider.get_credentials("github.com") is not None, "Should return credentials"
    integration.get_access_token.assert_called_once_with(123)

    integration.reset_mock()

    assert await provider.get_credentials("gitlab.com") is None, "Should return None for other origins"
    integration.get_access_token.assert_not_called()
