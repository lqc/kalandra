import argparse
import logging
import pathlib

from .auth import ChainedCredentialProvider, NetrcCredentialProvider
from .commands.update_mirror import update_mirror
from .transports import Transport

logger = logging.getLogger(__name__)


def create_parser():
    parser = argparse.ArgumentParser(
        description="Update a mirror of a git repository",
        exit_on_error=False,
    )

    parser.add_argument("upstream", help="URL of the repository to take changes from")
    parser.add_argument("mirror", help="URL of the mirror to push changes to")

    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Don't download or push any changes, just print the ref differences",
    )

    parser.add_argument(
        "--netrc",
        help="Path to the .netrc file to read credentials from. If not provided, the default location is used.",
        type=pathlib.Path,
        nargs="?",
        const=True,
    )

    parser.add_argument(
        "--github-app-id",
        help="GitHub Application ID to be used for HTTP authentication.",
    )

    parser.add_argument(
        "--github-app-key",
        help="GitHub Application Private Key to be used for HTTP authentication.",
        type=pathlib.Path,
    )

    parser.add_argument(
        "--github-org",
        help="GitHub Application Private Key to be used for HTTP authentication.",
    )

    return parser


async def main(cmdline_args: list[str]) -> int:
    parser = create_parser()
    try:
        args = parser.parse_args(cmdline_args)
    except argparse.ArgumentError as e:
        logger.error("Error parsing arguments: %s", e)
        parser.print_help()
        return 2

    credentials_provider = ChainedCredentialProvider()

    if args.netrc is not None:
        netrc_file = args.netrc if isinstance(args.netrc, pathlib.Path) else None
        credentials_provider.add_provider(NetrcCredentialProvider(netrc_file))

    if args.github_app_id or args.github_app_key:
        assert args.github_app_id and args.github_app_key, "GitHub App requires both ID and private key"
        from kalandra.auth.github import GitHubAppCredentialProvider

        provider = GitHubAppCredentialProvider(args.github_app_id, args.github_app_key, args.github_org)
        credentials_provider.add_provider(provider)

    upstream = Transport.from_url(args.upstream, credentials_provider=credentials_provider)
    mirror = Transport.from_url(args.mirror, credentials_provider=credentials_provider)

    try:
        await update_mirror(upstream, mirror, dry_run=args.dry_run)
        return 0
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return 1
