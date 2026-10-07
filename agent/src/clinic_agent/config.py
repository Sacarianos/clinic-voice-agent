"""Reading settings from the environment."""

from collections.abc import Mapping


class ConfigError(ValueError):
    pass


def require(env: Mapping[str, str], name: str, needed_for: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(f"{needed_for} needs {name} to be set")
    return value
