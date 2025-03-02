import logging
import pathlib
from urllib.parse import urlparse

import github


async def get_repo_property(
    repo_url: str,
    property_name: str,
    app_id: str,
    private_key: pathlib.Path,
) -> None | str:
    api = github.Github(auth=github.Auth.AppAuth(app_id, private_key=lambda: private_key.read_bytes()))
    url = urlparse(repo_url)

    path_parts = url.path.strip("/").split("/")
    org = path_parts[0]
    repo_name = path_parts[1]

    logging.info("Looking up property %s for org '%s' and repo '%s'", property_name, org, repo_name)
    repo = api.get_repo(f"{org}/{repo_name}")
    value = repo.get_custom_properties().get(property_name, None)  # type: ignore
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Property {property_name} is not a string: {value}")
    return value
