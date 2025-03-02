import os
from pathlib import Path

import pytest

from kalandra.cli import main


@pytest.fixture
def github_app_from_env(tmp_path: Path):
    app_id = os.getenv("TEST_GITHUB_APP_ID")
    app_key = os.getenv("TEST_GITHUB_APP_KEY")

    if app_id is None or app_key is None:
        pytest.skip("No GitHub App credentials provided")

    tmp_key_path = tmp_path / "app-key.pem"

    with open(tmp_path / "app-key.pem", "w") as f:
        f.write(app_key)

    yield app_id, tmp_key_path


@pytest.mark.asyncio
async def test_github_sync(github_app_from_env: tuple[str, Path]):
    result = await main(
        [
            "--github-app-id",
            github_app_from_env[0],
            "--github-app-key",
            str(github_app_from_env[1]),
            "--github-org",
            "kalandra-test",
            "https://github.com/kalandra-test/mirror-source.git",
            "https://github.com/kalandra-test/mirror-target.git",
        ]
    )
    assert result == 0
