import os
import pathlib
import shutil
import subprocess
import tempfile
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

    # We need a local temp dir as Docker cannot mount into /tmp on some systems (e.g. OS X)
    base_tmp = pathlib.Path(".tmp")
    base_tmp.mkdir(parents=True, exist_ok=True)
    host_dir = pathlib.Path(tempfile.mkdtemp(dir=".tmp/"))

    upstream_bare = host_dir / "upstream.git"
    mirror_bare = host_dir / "mirror.git"
    workdir = host_dir / "work"

    try:
        call_check(["git", "init", "--bare", str(upstream_bare)], what="git init --bare upstream")
        call_check(["git", "init", "--bare", str(mirror_bare)], what="git init --bare mirror")

        workdir.mkdir(parents=True, exist_ok=True)
        call_check(["git", "init", "-b", "main"], cwd=workdir, what="git init workdir")
        call_check(["git", "config", "user.email", "test@example.com"], cwd=workdir, what="git config user.email")
        call_check(["git", "config", "user.name", "Test"], cwd=workdir, what="git config user.name")

        (workdir / "README.md").write_text("hello\n", encoding="utf-8")
        call_check(["git", "add", "README.md"], cwd=workdir, what="git add")
        call_check(["git", "commit", "-m", "initial"], cwd=workdir, what="git commit")

        call_check(["git", "remote", "add", "origin", str(upstream_bare)], cwd=workdir, what="git remote add")
        call_check(["git", "push", "origin", "main"], cwd=workdir, what="git push upstream")
        call_check(["git", "tag", "v0"], cwd=workdir, what="git tag")
        call_check(["git", "push", "origin", "v0"], cwd=workdir, what="git push tag")

        call_check(["docker", "build", "-t", tag, "."], what="docker build")

        upstream_url = "file:///repos/upstream.git"
        mirror_url = "file:///repos/mirror.git"

        # fix permissions in host_dir to allow non-root user in container to read/write
        host_dir.chmod(0o777)
        for root, dirs, files in host_dir.walk():
            for entry in dirs:
                (root / entry).chmod(0o777)
            for entry in files:
                (root / entry).chmod(0o666)

        call_check(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "-v",
                f"{host_dir}:/repos",
                tag,
                "--source",
                upstream_url,
                "--target",
                mirror_url,
            ],
            what="docker run kalandra",
        )

        upstream_head = call_check(
            ["git", "--git-dir", str(upstream_bare), "rev-parse", "refs/heads/main"],
            what="git rev-parse upstream",
        )
        mirror_head = call_check(
            ["git", "--git-dir", str(mirror_bare), "rev-parse", "refs/heads/main"],
            what="git rev-parse mirror",
        )

        assert upstream_head.stdout.strip() == mirror_head.stdout.strip()

        # Tag should be mirrored as well.
        call_check(["git", "--git-dir", str(mirror_bare), "rev-parse", "refs/tags/v0"], what="git rev-parse mirror tag")
    finally:
        # Best-effort cleanup.
        subprocess.run(["docker", "rmi", "-f", tag], check=False, capture_output=True)
        shutil.rmtree(host_dir, ignore_errors=True)
