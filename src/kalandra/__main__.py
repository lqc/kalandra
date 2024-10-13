import argparse
import asyncio
import logging

from .commands.update_mirror import update_mirror
from .transports import Transport

logger = logging.getLogger(__name__)


def create_parser():
    parser = argparse.ArgumentParser(description="Update a mirror of a git repository")

    parser.add_argument("upstream", help="URL of the repository to take changes from")
    parser.add_argument("mirror", help="URL of the mirror to push changes to")

    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Don't download or push any changes, just print the ref differences",
    )

    return parser


async def main():
    parser = create_parser()
    args = parser.parse_args()

    logger.debug("Args: %s", args)

    upstream = Transport.from_url(args.upstream)
    mirror = Transport.from_url(args.mirror)

    await update_mirror(upstream, mirror, dry_run=args.dry_run)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main())
