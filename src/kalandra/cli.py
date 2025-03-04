import argparse
import logging
import pathlib

from .auth import ChainedCredentialProvider, NetrcCredentialProvider
from .commands.update_mirror import create_glob_filter, update_mirror
from .transports import Transport

logger = logging.getLogger(__name__)


def create_parser():
    parser = argparse.ArgumentParser(
        description="Update a mirror of a git repository",
        exit_on_error=False,
    )

    parser.add_argument(
        "--source",
        help="URL of the repository to take changes from",
        required=True,
    )
    parser.add_argument(
        "--target",
        help="URL of the repository to push changes to",
        required=True,
    )

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
        help="GitHub Org to use for GitHub App authentication",
    )

    parser.add_argument(
        "--include-ref",
        help="Include only refs that match the given glob patter. Default: refs/heads/*, refs/tags/*",
        action="append",
        default=["refs/heads/*", "refs/tags/*"],
    )

    parser.add_argument(
        "--exclude-ref",
        help="Exclude refs that match the given glob pattern. This takes precedence over --include-ref",
        action="append",
        default=[],
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

    include_filter = create_glob_filter(*args.include_ref)
    exclude_filter = create_glob_filter(*args.exclude_ref)

    # Credentials provider
    credentials_provider = ChainedCredentialProvider()
    if args.netrc is not None:
        netrc_file = args.netrc if isinstance(args.netrc, pathlib.Path) else None
        credentials_provider.add_provider(NetrcCredentialProvider(netrc_file))

    if args.github_app_id or args.github_app_key:
        assert args.github_app_id and args.github_app_key, "GitHub App requires both ID and private key"
        try:
            from kalandra.github_utils import GithubAPI
        except ImportError:
            logger.error("GitHub integration not available, install kalandra[github] to enable it.")
            return 1

        github_api = GithubAPI(args.github_app_id, args.github_app_key)

        credentials_provider.add_provider(github_api.crendentials_provider_for_org(args.github_org))
    else:
        github_api = None

    source_url = args.source

    if source_url.startswith("target-prop:"):
        if github_api is None:
            logger.error("GitHub App credentials are required to use target-prop")
            return 1

        logger.info("Looking up source URL from target repository")

        source_url = await github_api.get_repo_property(
            repo_url=args.target,
            property_name=args.source[len("target-prop:") :],
        )
        if source_url is None:
            logger.error("Property %s not found in target repository", args.source)
            return 1

    source = Transport.from_url(source_url, credentials_provider=credentials_provider)
    target = Transport.from_url(args.target, credentials_provider=credentials_provider)

    try:
        await update_mirror(
            source,
            target,
            dry_run=args.dry_run,
            include_filter=include_filter,
            exclude_filter=exclude_filter,
        )
        return 0
    except Exception as e:
        logger.error("Unexpected error while mirroring", exc_info=e)
        return 1
