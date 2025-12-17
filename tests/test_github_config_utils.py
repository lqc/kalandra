import pathlib
from unittest.mock import MagicMock

import github
import pytest

from kalandra.github_config_utils import setup_github_auth


def test_setup_single_org_via_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
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


def test_setup_multiple_orgs_via_config(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    config_path = tmp_path / "github_config.toml"
    config_path.write_text(
        """\
        [github_apps]

        [github_apps.app1]
        app_id = "11111"
        app_key_path = "{0}/key1.pem"
        orgs = ["org1", "org2"]

        [github_apps.app2]
        app_id = "22222"
        app_key_path = "{0}/key2.pem"
        orgs = ["org3"]
        """.format(tmp_path)
    )

    (tmp_path / "key1.pem").write_text("FAKE KEY 1")
    (tmp_path / "key2.pem").write_text("FAKE KEY 2")

    integration_mock = MagicMock()
    monkeypatch.setattr(github, "GithubIntegration", integration_mock)

    api = setup_github_auth(
        config_path=config_path,
        app_id=None,
        app_key=None,
        org=None,
    )
    assert api is not None
    assert api.get_installation_id("org1") is not None, "Installation ID for org1 should not be None"
    assert api.get_installation_id("org2") is not None, "Installation ID for org2 should not be None"
    assert api.get_installation_id("org3") is not None, "Installation ID for org3 should not be None"
    assert api.get_installation_id("unconfigured") is None, "Unconfigured org should return None"
