import logging
import pathlib

from .basic import CredentialProvider

logger = logging.getLogger(__name__)

try:
    from github import Auth, GithubIntegration
except ImportError:  # pragma: no cover
    Auth = None
    GithubIntegration = None


def create_integration(app_id: str, private_key: pathlib.Path):
    if Auth is None or GithubIntegration is None:
        raise Exception("GitHub integration not available, install kaladra[github] to enable it.")

    auth = Auth.AppAuth(app_id=app_id, private_key=lambda: private_key.read_bytes())
    return GithubIntegration(auth=auth)


class GitHubAppCredentialProvider(CredentialProvider):
    _installation_id: int | None = None

    def __init__(self, app_id: str, private_key: pathlib.Path, org: str):
        self._integration = create_integration(app_id, private_key)
        installation = self._integration.get_org_installation(org)
        self._installation_id = installation.id if installation else None

        if self._installation_id is None:
            logger.warning("No installation found for %s, GitHub authentication disabled", org)

    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        if origin != "github.com":
            return None

        if self._installation_id is None:
            return None

        return ("x-access-token", self._integration.get_access_token(self._installation_id).token)
