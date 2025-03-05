import netrc
from abc import abstractmethod
from pathlib import Path


class CredentialProvider:
    @abstractmethod
    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        """
        Get the username and password for the given URL.

        :param origin: The Origin to get the credentials for.
        :return: A tuple of username and password or None if no credentials are available.
        """
        return None  # pragma: no cover


class NoopCredentialProvider(CredentialProvider):
    """
    A credential provider that does nothing.
    """


class NetrcCredentialProvider(CredentialProvider):
    """
    Resolve credentials using the .netrc file.
    """

    def __init__(self, netrc_path: Path | None):
        self._netrc = netrc.netrc(str(netrc_path) if netrc_path else None)

    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        match = self._netrc.authenticators(origin)
        return (match[0], match[2]) if match else None


class ChainedCredentialProvider(CredentialProvider):
    def __init__(self, *providers: CredentialProvider):
        self._providers = list(providers)

    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        for provider in self._providers:
            credentials = await provider.get_credentials(origin)
            if credentials is not None:
                return credentials
        return None

    def add_provider(self, provider: CredentialProvider):
        self._providers.append(provider)
