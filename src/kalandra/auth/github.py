import logging
import pathlib

from .basic import CredentialProvider

logger = logging.getLogger(__name__)


class GitHubAppCredentialProvider(CredentialProvider):
    _installation_id: int | None = None

    def __init__(self, app_id: str, private_key: pathlib.Path, org: str):
        from github import Auth, GithubIntegration

        self._integration = GithubIntegration(auth=Auth.AppAuth(app_id, private_key=lambda: private_key.read_bytes()))
        installation = self._integration.get_org_installation(org)
        self._installation_id = installation.id if installation else None

        if self._installation_id is None:
            logger.warning("No installation found for %s, GitHub authentication disabled", org)

    async def get_credentials(self, origin: str) -> tuple[str, str] | str | None:
        if origin != "github.com":
            return None

        if self._installation_id is None:
            return None

        return ("x-access-token", self._integration.get_access_token(self._installation_id).token)
