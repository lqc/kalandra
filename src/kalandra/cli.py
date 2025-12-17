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
        "--github-auth-config",
        help="Path to a TOML file containing GitHub authentication configuration. Use this to specify multiple GitHub Apps for different organizations.",
        type=pathlib.Path,
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

    parser.add_argument(
        "--log-level",
        help="Set the logging level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
    )

    return parser


async def main(cmdline_args: list[str]) -> int:
    parser = create_parser()
    try:
        args = parser.parse_args(cmdline_args)
    except argparse.ArgumentError as e:
        logger.error("Error parsing arguments: %s", e)
        parser.print_help()
        return 1

    logging.root.setLevel(args.log_level)

    include_filter = create_glob_filter(*args.include_ref)
    exclude_filter = create_glob_filter(*args.exclude_ref)

    # Credentials provider
    source_credential_chain = ChainedCredentialProvider()
    target_credential_chain = ChainedCredentialProvider()
    if args.netrc is not None:
        netrc_file = args.netrc if isinstance(args.netrc, pathlib.Path) else None
        source_credential_chain.add_provider(NetrcCredentialProvider(netrc_file))
        target_credential_chain.add_provider(NetrcCredentialProvider(netrc_file))

    # Check if GitHub support is needed
    if args.github_auth_config or args.github_app_id or args.github_app_key:
        try:
            from kalandra.github_config_utils import GithubAPI, setup_github_auth
        except ImportError:
            logger.error("GitHub integration not available, install kalandra[github] to enable it.")
            return 1

        github_api: GithubAPI | None = setup_github_auth(
            config_path=args.github_auth_config,
            app_id=args.github_app_id,
            app_key=args.github_app_key,
            org=args.github_org,
        )
    else:
        github_api = None

    source_url = args.source

    if source_url.startswith("target-prop:"):
        if github_api is None:
            logger.error("GitHub App credentials are required to use target-prop")
            return 1

        logger.info("Looking up source URL from target repository")

        props = github_api.get_repo_properties(args.target)
        source_prop_name = args.source[len("target-prop:") :]

        if source_prop_name not in props:
            logger.error("Property %s not found in target repository", args.source)
            return 1

        source_prop_value = props[source_prop_name]
        if source_prop_value.startswith("/"):
            # Assume it's a relative path and lookup host from another prop
            if f"{source_prop_name}-host" not in props:
                logger.error(
                    "Source property is relative, but no %s-host not found in target repository: %s",
                    args.source,
                    source_prop_value,
                )
                return 1
            source_url = props[f"{source_prop_name}-host"] + source_prop_value
        else:
            source_url = source_prop_value

    if github_api is not None:
        github_api.add_github_credential_provider_if_applicable(
            source_url,
            source_credential_chain,
        )
        github_api.add_github_credential_provider_if_applicable(
            args.target,
            target_credential_chain,
        )

    source = Transport.from_url(source_url, credentials_provider=source_credential_chain)
    target = Transport.from_url(args.target, credentials_provider=target_credential_chain)

    logger.info("Update start [%s -> %s]", source.url, target.url)
    try:
        await update_mirror(
            source,
            target,
            dry_run=args.dry_run,
            include_filter=include_filter,
            exclude_filter=exclude_filter,
        )
        logger.info("Update success [%s -> %s]", source.url, target.url)
        return 0
    except Exception as e:
        logger.error("Update failed [%s -> %s]", source.url, target.url, exc_info=e)
        return 1
