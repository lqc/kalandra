from typing import AsyncIterator, Callable, Iterable

import pytest

from kalandra.commands.update_mirror import calculate_mirror_updates, create_glob_filter
from kalandra.gitprotocol import NULL_OBJECT_ID, Ref, RefChange


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
