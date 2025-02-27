import argparse
import asyncio
import logging
import pathlib

from .commands.update_mirror import update_mirror
from .transports import NetrcCredentialProvider, Transport

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

    parser.add_argument(
        "--netrc-file",
        help="Path to the .netrc file to read credentials from. If not provided, the default location is used.",
        type=pathlib.Path,
    )

    return parser


async def main():
    parser = create_parser()
    args = parser.parse_args()

    logger.debug("Args: %s", args)

    credentials_provider = NetrcCredentialProvider(args.netrc_file)

    upstream = Transport.from_url(args.upstream, credentials_provider=credentials_provider)
    mirror = Transport.from_url(args.mirror, credentials_provider=credentials_provider)

    await update_mirror(upstream, mirror, dry_run=args.dry_run)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main())
