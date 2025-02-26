import logging
from typing import AsyncIterator

import aiofiles

from kalandra.gitprotocol import NULL_OBJECT_ID, Ref, RefChange
from kalandra.transports import Transport

logger = logging.getLogger(__name__)


async def calculate_mirror_updates(mirror_refs: dict[str, str], upstream_refs: AsyncIterator[Ref]):
    """
    Calculate the updates that need to be pushed to the mirror.
    """
    refs_to_update = {k: v for k, v in mirror_refs.items() if k.startswith("refs/heads/") or k.startswith("refs/tags/")}

    async for ref in upstream_refs:
        if not ref.name.startswith("refs/heads/") and not ref.name.startswith("refs/tags/"):
            logger.debug("Skipping %s, not a branch or tag", ref.name)
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


async def update_mirror(
    upstream: Transport,
    mirror: Transport,
    *,
    dry_run: bool = False,
) -> None:
    async with (
        upstream.fetch() as upstream_conn,
        mirror.push() as mirror_conn,
    ):
        changes = [c async for c in calculate_mirror_updates(mirror_conn.refs, upstream_conn.ls_refs())]

        if not changes:
            logger.info("No changes detected")
            return

        logger.info("Following changes detected:")
        for change in changes:
            logger.info(change)

        if dry_run:
            return

        # Fetch objects from upstream
        async with aiofiles.tempfile.NamedTemporaryFile(suffix=".pack", encoding=None) as packfile:
            new_objects = {change.new for change in changes}
            new_objects.discard(NULL_OBJECT_ID)
            have_objects = set(mirror_conn.refs.values())

            if new_objects:
                logger.info("Fetching objects from upstream")
                await upstream_conn.send_fetch_request(new_objects, have=have_objects, output=packfile)
            else:
                logger.info("No new objects to fetch, only deletes or updates")

            # Push objects to mirror
            await packfile.seek(0)
            logger.info("Sending changes to mirror")
            await mirror_conn.send_change_request(changes, packfile)
