import subprocess
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path

import pytest

from kalandra.auth import NoopCredentialProvider
from kalandra.commands.update_mirror import calculate_mirror_updates, create_glob_filter, update_mirror
from kalandra.gitprotocol import NULL_OBJECT_ID, Ref, RefChange
from kalandra.transports.file import FileTransport


async def async_iter[T](iterable: Iterable[T]) -> AsyncIterator[T]:
    for item in iterable:
        yield item


async def calculate_changes(
    mirror_refs: dict[str, str],
    upstream_refs: list[Ref],
    *,
    include_filter: Callable[[str], bool] | None = None,
    exclude_filter: Callable[[str], bool] | None = None,
) -> list[RefChange]:
    return [
        change
        async for change in calculate_mirror_updates(
            mirror_refs, async_iter(upstream_refs), include_filter=include_filter, exclude_filter=exclude_filter
        )
    ]


@pytest.mark.asyncio
async def test_calculate_mirror_updates_empty_refs():
    assert await calculate_changes({}, []) == []


@pytest.mark.asyncio
async def test_calculate_mirror_updates_update():
    assert await calculate_changes(
        {"refs/heads/master": "abc123"},
        [Ref("refs/heads/master", "def456")],
    ) == [
        RefChange("refs/heads/master", "abc123", "def456"),
    ]


@pytest.mark.asyncio
async def test_calculate_mirror_updates_delete():
    assert await calculate_changes(
        {"refs/heads/master": "abc123"},
        [],
    ) == [
        RefChange("refs/heads/master", "abc123", NULL_OBJECT_ID),
    ]


@pytest.mark.asyncio
async def test_calculate_mirror_updates_create():
    assert await calculate_changes(
        {},
        [Ref("refs/heads/master", "def456")],
    ) == [
        RefChange("refs/heads/master", NULL_OBJECT_ID, "def456"),
    ]


@pytest.mark.asyncio
async def test_calculate_mirror_updates_do_not_delete_filtered_refs():
    assert (
        await calculate_changes(
            {"refs/heads/master": "abc123", "refs/meta/config": "xyz789"},
            [Ref("refs/heads/master", "abc123")],
            include_filter=lambda ref: ref.startswith("refs/heads/"),
        )
        == []
    )


@pytest.mark.asyncio
async def test_calculate_mirror_updates_exclude_some_branches():
    assert await calculate_changes(
        {},
        [
            Ref("refs/heads/master", "abc123"),
            Ref("refs/tags/v1.0", "abc123"),
            Ref("refs/heads/private/excluded", "abc123"),
            Ref("refs/change/123", "xyz789"),
        ],
        include_filter=lambda ref: ref.startswith(("refs/heads/", "refs/tags/")),
        exclude_filter=lambda ref: ref.startswith("refs/heads/private/"),
    ) == [
        RefChange("refs/heads/master", NULL_OBJECT_ID, "abc123"),
        RefChange("refs/tags/v1.0", NULL_OBJECT_ID, "abc123"),
    ]


@pytest.mark.asyncio
async def test_calculate_mirror_updates_with_glob_filter():
    assert await calculate_changes(
        {},
        [
            Ref("refs/heads/master", "abc123"),
            Ref("refs/tags/v1.0", "abc123"),
            Ref("refs/heads/private/excluded", "abc123"),
            Ref("refs/change/123", "xyz789"),
        ],
        include_filter=create_glob_filter("refs/heads/*", "refs/tags/*"),
        exclude_filter=create_glob_filter("refs/heads/private/*"),
    ) == [
        RefChange("refs/heads/master", NULL_OBJECT_ID, "abc123"),
        RefChange("refs/tags/v1.0", NULL_OBJECT_ID, "abc123"),
    ]


@pytest.mark.parametrize(
    ("old_branches", "new_branches"),
    [
        (["foo/bar"], ["foo/bar/buzz", "foo/bar/deep/leaf"]),
        (["foo/bar/buzz", "foo/bar/deep/leaf"], ["foo/bar"]),
    ],
)
async def test_sync_replaces_conflicting_branches(tmp_path: Path, old_branches: list[str], new_branches: list[str]):
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "main", "source")
    git(
        "-C",
        "source",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.org",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    for branch in old_branches:
        git("-C", "source", "branch", branch)
    git("clone", "--bare", "source", "mirror")
    git("-C", "source", "branch", "-D", *old_branches)
    for branch in new_branches:
        git("-C", "source", "branch", branch)

    creds = NoopCredentialProvider()
    upstream = FileTransport(url=(source / ".git").as_uri(), credentials_provider=creds)
    destination = FileTransport(url=mirror.as_uri(), credentials_provider=creds)
    include = create_glob_filter("refs/heads/*", "refs/tags/*")
    before = git("-C", "mirror", "show-ref")
    plan = await update_mirror(upstream, destination, include_filter=include, dry_run=True)
    assert git("-C", "mirror", "show-ref") == before
    assert await update_mirror(upstream, destination, include_filter=include) == plan
    assert git("-C", "mirror", "show-ref") == git("-C", "source", "show-ref")
    assert await update_mirror(upstream, destination, include_filter=include) == []
