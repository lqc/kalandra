import pathlib
from unittest.mock import MagicMock

import github
import pytest

from kalandra.github_config_utils import setup_github_auth


@pytest.mark.asyncio
async def test_setup_single_org_via_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    integration_mock = MagicMock()
    monkeypatch.setattr(github, "GithubIntegration", integration_mock)

    api = setup_github_auth(
        config_path=None,
        app_id="12345",
        app_key=tmp_path / "fake_key.pem",
        org="my-org",
    )
    integration_mock.assert_called_once()

    assert api is not None
    assert api.get_installation_id("my-org") is not None, "Installation ID should not be None"
    assert api.get_installation_id("unconfigured") is None, "Unconfigured org should return None"
