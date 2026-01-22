import os
import pathlib
import shutil
import subprocess
import uuid

import pytest


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _has_git() -> bool:
    return shutil.which("git") is not None


def call_check(
    cmd: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    what: str,
) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    assert p.returncode == 0, f"{what} failed\nCMD: {' '.join(p.args)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
    return p


@pytest.mark.e2e
def test_docker_image_can_sync_two_local_file_repos():
    """Build the Docker image and verify it can sync two local file:// repos.

    This exercises the primary code path through `update_mirror()` including
    creation of the temporary packfile.
    """

    if not _has_docker():
        pytest.skip("docker not available")

    if not _has_git():
        pytest.skip("git not available")

    # Allow opting out locally if desired.
    if os.environ.get("KALANDRA_SKIP_DOCKER_TEST"):
        pytest.skip("KALANDRA_SKIP_DOCKER_TEST is set")

    tag = f"kalandra:test-{uuid.uuid4().hex}"

    call_check(["docker", "build", "-t", tag, "."], what="docker build")

    entrypoint_script = pathlib.Path(__file__).parent / "test_image_entrypoint.sh"

    try:
        result = call_check(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "-v",
                f"{entrypoint_script}:/opt/test_image_entrypoint.sh",
                "--entrypoint",
                "/opt/test_image_entrypoint.sh",
                tag,
            ],
            what="docker run kalandra",
        )
        assert result.stdout == "SUCCESS\n", f"Unexpected stderr from docker run:\n{result.stderr}"
    finally:
        # Best-effort cleanup.
        subprocess.run(["docker", "rmi", "-f", tag], check=False, capture_output=True)
