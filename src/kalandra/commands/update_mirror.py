import fnmatch
import logging
import re
from typing import AsyncIterator, Callable

import aiofiles

from kalandra.gitprotocol import NULL_OBJECT_ID, Ref, RefChange
from kalandra.transports import Transport

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

        logger.info("Following changes detected:")
        for change in changes:
            logger.info(change)

        if dry_run:
            return changes

        # Fetch objects from upstream
        async with aiofiles.tempfile.NamedTemporaryFile(suffix=".pack", encoding=None) as packfile:
            new_objects = {change.new for change in changes}
            new_objects.discard(NULL_OBJECT_ID)
            have_objects = set(mirror_conn.refs.values())

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
            await mirror_conn.push_changes(changes, packfile)

        return changes
