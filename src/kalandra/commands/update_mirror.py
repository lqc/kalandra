import fnmatch
import logging
import re
from collections.abc import AsyncIterator, Callable, Iterator

import aiofiles

from kalandra.gitprotocol import NULL_OBJECT_ID, Ref, RefChange
from kalandra.transports import Transport
from kalandra.transports.base import ServerError

logger = logging.getLogger(__name__)


async def calculate_mirror_updates(
    mirror_refs: dict[str, str],
    upstream_refs: AsyncIterator[Ref],
    *,
    include_filter: Callable[[str], bool] | None = None,
    exclude_filter: Callable[[str], bool] | None = None,
) -> AsyncIterator[RefChange]:
    """
    Calculate the updates that need to be pushed to the mirror.
    """
    include_filter = include_filter or (lambda _: True)
    exclude_filter = exclude_filter or (lambda _: False)

    refs_to_update = {k: v for k, v in mirror_refs.items() if include_filter(k) and not exclude_filter(k)}

    async for ref in upstream_refs:
        if not include_filter(ref.name):
            refs_to_update.pop(ref.name, None)
            continue

        if exclude_filter(ref.name):
            refs_to_update.pop(ref.name, None)
            continue

        old_id = refs_to_update.pop(ref.name, NULL_OBJECT_ID)
        if old_id != ref.object_id:
            yield RefChange(ref.name, old_id, ref.object_id)
        else:
            logger.debug("Skipping %s, already up-to-date", ref.name)

    # Refs to delete
    for ref, old_id in refs_to_update.items():
        yield RefChange(ref, old_id, NULL_OBJECT_ID)


def create_glob_filter(*globs: str) -> Callable[[str], bool] | None:
    """
    Create a filter that matches the given globs.
    """
    if not globs:
        return None

    regex = re.compile("|".join(fnmatch.translate(glob) for glob in globs))

    def filter_func(name: str) -> bool:
        return regex.match(name) is not None

    return filter_func


def _ref_parents(ref: str) -> Iterator[str]:
    while "/" in ref:
        ref = ref.rsplit("/", 1)[0]
        yield ref


def _blocking_deletions(changes: list[RefChange]) -> list[RefChange]:
    creates = {change.ref for change in changes if change.is_create}
    create_parents = {parent for ref in creates for parent in _ref_parents(ref)}
    return [
        change
        for change in changes
        if change.is_delete and (change.ref in create_parents or any(p in creates for p in _ref_parents(change.ref)))
    ]


async def update_mirror(
    upstream: Transport,
    mirror: Transport,
    *,
    dry_run: bool = False,
    include_filter: Callable[[str], bool] | None = None,
    exclude_filter: Callable[[str], bool] | None = None,
) -> list[RefChange]:
    async with (
        upstream.fetch() as upstream_conn,
        mirror.push() as mirror_conn,
    ):
        changes = [
            c
            async for c in calculate_mirror_updates(
                mirror_conn.refs,
                upstream_conn.ls_refs(),
                include_filter=include_filter,
                exclude_filter=exclude_filter,
            )
        ]

        if not changes:
            logger.info("No changes detected")
            return changes

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Following changes detected:")
            for change in changes:
                logger.debug(change)

        if logger.isEnabledFor(logging.INFO):
            logger.info(
                "Following changes detected: %s creates, %d updates, %d deletes",
                sum(c.is_create for c in changes),
                sum(c.is_update for c in changes),
                sum(c.is_delete for c in changes),
            )

        if dry_run:
            return changes

        blockers = _blocking_deletions(changes)
        blocking_refs = {change.ref for change in blockers}

        # Fetch objects from upstream
        async with aiofiles.tempfile.NamedTemporaryFile(suffix=".pack", encoding=None) as packfile:
            new_objects = {change.new for change in changes}
            new_objects.discard(NULL_OBJECT_ID)
            have_objects = {oid for ref, oid in mirror_conn.refs.items() if ref not in blocking_refs}

            if new_objects:
                logger.info("Fetching objects from upstream")
                await upstream_conn.fetch_objects(new_objects, have=have_objects, output=packfile)
            else:
                logger.info("No new objects to fetch, only deletes or updates")

            # Make sure all objects are written to disk
            await packfile.flush()

            await packfile.seek(0, 2)
            packfile_size = await packfile.tell()
            logger.info("Downloaded packfile size: %.2f MB", packfile_size / 1024 / 1024)

            # Push objects to mirror
            logger.info("Sending changes to mirror")
            if blockers:
                logger.info("Deleting %d conflicting refs before creating their replacements", len(blockers))
                await mirror_conn.push_changes(blockers, None)
                async with mirror.push() as next_conn:
                    remaining = blocking_refs.intersection(next_conn.refs)
                    if remaining:
                        raise ServerError(f"Conflicting refs still present on mirror: {', '.join(sorted(remaining))}")
                    await next_conn.push_changes([c for c in changes if c.ref not in blocking_refs], packfile)
            else:
                await mirror_conn.push_changes(changes, packfile)

        return changes
