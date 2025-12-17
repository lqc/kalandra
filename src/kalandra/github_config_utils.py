import logging
import pathlib
import tomllib
from typing import Iterable, NamedTuple
from urllib.parse import urlparse

import github

from kalandra.auth.basic import ChainedCredentialProvider, CredentialProvider

logger = logging.getLogger(__name__)


class GitHubAppCredentialProvider(CredentialProvider):
    _installation_id: int

    def __init__(self, integration: github.GithubIntegration, installation_id: int) -> None:
        self._integration = integration
        self._installation_id = installation_id

    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        if origin != "github.com":
            return None

        return ("x-access-token", self._integration.get_access_token(self._installation_id).token)


class AppConfig(NamedTuple):
    app_id: str
    app_key: pathlib.Path
    orgs: tuple[str, ...]


class GithubAPI:
    def __init__(self, configs: Iterable[AppConfig]) -> None:
        self._auth_by_org: dict[str, github.Auth.AppAuth] = {}
        self._integration_by_org: dict[str, github.GithubIntegration] = {}

        for config in configs:
            auth = github.Auth.AppAuth(
                app_id=config.app_id,
                private_key=lambda key_path=config.app_key: key_path.read_bytes(),
            )
            integration = github.GithubIntegration(auth=auth)

            for org in config.orgs:
                if org in self._auth_by_org:
                    raise ValueError(
                        f"Duplicate GitHub App configuration for org: {org}. Processing App {config.app_id}, previously configured App {self._auth_by_org[org].app_id}"
                    )
                self._auth_by_org[org] = auth
                self._integration_by_org[org] = integration

        self._org_installations: dict[str, int | None] = {}
        self._org_providers: dict[str, GitHubAppCredentialProvider] = {}

    def get_installation_id(self, org: str) -> None | int:
        if not org:
            raise ValueError("No org provided")

        if org not in self._org_installations:
            if org not in self._integration_by_org:
                # No auth for this org, mark as None
                self._org_installations[org] = None
                return None

            installation = self._integration_by_org[org].get_org_installation(org)
            self._org_installations[org] = installation.id if installation else None

        return self._org_installations[org]

    def get_org_api(self, org: str) -> github.Github:
        installation_id = self.get_installation_id(org)
        if installation_id is None:
            raise ValueError(f"No installation found for {org}")

        return github.Github(auth=self._auth_by_org[org].get_installation_auth(installation_id))

    def _split_repo_url(self, repo_url: str, expected_host: str = "github.com") -> tuple[str | None, str]:
        url = urlparse(repo_url)

        if url.netloc.lower() != expected_host:
            return (None, "")

        path_parts = url.path.strip("/").split("/")
        org = path_parts[0]
        repo_name = path_parts[1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        return org, repo_name

    def get_repo_properties(
        self,
        repo_url: str,
    ) -> dict[str, str]:
        org, repo_name = self._split_repo_url(repo_url)
        if org is None:
            raise ValueError(f"Not a GitHub repository URL: {repo_url}")

        api = self.get_org_api(org)
        repo = api.get_repo(f"{org}/{repo_name}")

        values: dict[str, str] = {}
        for key, value in repo.custom_properties.items():  # type: ignore
            if isinstance(value, str):
                values[key] = value

        return values

    def credentials_provider_for_org(self, org: str) -> GitHubAppCredentialProvider | None:
        if org not in self._org_providers:
            installation_id = self.get_installation_id(org)
            if installation_id is None:
                logger.error(
                    "App %s has no installation ID for org %s, cannot create credential provider",
                    self._integration_by_org[org].auth.app_id,
                    org,
                )
                return None

            provider = GitHubAppCredentialProvider(self._integration_by_org[org], installation_id)
            self._org_providers[org] = provider

        return self._org_providers[org]

    def add_github_credential_provider_if_applicable(
        self, repo_url: str, credential_chain: ChainedCredentialProvider
    ) -> None:
        org, _ = self._split_repo_url(repo_url)
        # logger.info("ORG: %s, Repo URL: %s", org, repo_url)
        if org is None:
            return

        github_cred_provider = self.credentials_provider_for_org(org)
        if github_cred_provider is not None:
            credential_chain.add_provider(github_cred_provider)


def parse_github_auth_config(config_path: pathlib.Path) -> list[AppConfig]:
    with config_path.open("rb") as config_file:
        config_data = tomllib.load(config_file)
    app_configs: list[AppConfig] = []

    for _key, app_entry in config_data.get("github_apps", {}).items():
        app_id = app_entry.get("app_id")
        app_key_path_str = app_entry.get("app_key_path")
        orgs = tuple(app_entry.get("orgs", []))

        if not app_id or not app_key_path_str or not orgs:
            raise ValueError(f"Invalid GitHub App configuration in {config_path}: {app_entry}")

        app_key_path = pathlib.Path(app_key_path_str)
        if not app_key_path.is_file():
            raise ValueError(f"GitHub App key file does not exist: {app_key_path}")

        app_configs.append(AppConfig(app_id=app_id, app_key=app_key_path, orgs=orgs))

    return app_configs


def setup_github_auth(
    config_path: pathlib.Path | None,
    app_id: str | None,
    app_key: pathlib.Path | None,
    org: str | None,
) -> GithubAPI:
    configs: list[AppConfig] = []
    if config_path is not None:
        configs.extend(parse_github_auth_config(config_path))

    if app_id is not None or app_key is not None or org is not None:
        if app_id is None or app_key is None or org is None:
            raise ValueError("If not using a config file, all of app_id, app_key, and org must be provided.")

        cli_config = AppConfig(app_id=app_id, app_key=app_key, orgs=(org,))
        configs.append(cli_config)

    github_api = GithubAPI(configs)
    return github_api
