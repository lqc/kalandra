import logging
import pathlib
from urllib.parse import urlparse

import github

from kalandra.auth.basic import CredentialProvider

logger = logging.getLogger(__name__)


class GitHubAppCredentialProvider(CredentialProvider):
    _installation_id: int | None = None

    def __init__(self, integration: github.GithubIntegration, installation_id: int | None) -> None:
        self._integration = integration
        self._installation_id = installation_id

    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        if origin != "github.com":
            return None

        if self._installation_id is None:
            return None

        return ("x-access-token", self._integration.get_access_token(self._installation_id).token)


class GithubAPI:
    def __init__(self, app_id: str, private_key: pathlib.Path) -> None:
        self._auth = github.Auth.AppAuth(app_id=app_id, private_key=lambda: private_key.read_bytes())
        self._integration = github.GithubIntegration(auth=self._auth)
        self._org_installations: dict[str, int | None] = {}

    def get_installation_id(self, org: str) -> None | int:
        if not org:
            raise ValueError("No org provided")

        if org not in self._org_installations:
            installation = self._integration.get_org_installation(org)
            self._org_installations[org] = installation.id if installation else None

        return self._org_installations[org]

    def get_org_api(self, org: str) -> github.Github:
        installation_id = self.get_installation_id(org)
        if installation_id is None:
            raise ValueError(f"No installation found for {org}")

        return github.Github(auth=self._auth.get_installation_auth(installation_id))

    async def get_repo_property(
        self,
        repo_url: str,
        property_name: str,
    ) -> None | str:
        url = urlparse(repo_url)

        path_parts = url.path.strip("/").split("/")
        org = path_parts[0]
        repo_name = path_parts[1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        logging.info("Looking up property %s for org '%s' and repo '%s'", property_name, org, repo_name)
        api = self.get_org_api(org)
        repo = api.get_repo(f"{org}/{repo_name}")
        value = repo.get_custom_properties().get(property_name, None)  # type: ignore
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Property {property_name} is not a string: {value}")
        return value

    def crendentials_provider_for_org(self, org: str) -> GitHubAppCredentialProvider:
        installation_id = self.get_installation_id(org)
        return GitHubAppCredentialProvider(self._integration, installation_id)
