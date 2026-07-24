"""Configuration handling for spaxi.

Follows the freedesktop XDG convention.  Configuration values are
layered in last-one-wins order:

    environment variables -> config file -> command line options

Environment variables are named ``SPAXI_<SECTION>_<KEY>`` (upper case,
dashes become underscores), e.g. ``SPAXI_SPACK_EXE``.
"""

import os
import tomllib
from pathlib import Path

# Known configuration values as (section, key) pairs.  Used to map
# environment variables into the same namespace as the config file.
KNOWN_KEYS = [
    ("spack", "exe"),
    ("conda", "channel"),
]


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def default_config_path() -> Path:
    return xdg_config_home() / "spaxi" / "config.toml"


def _from_environ(environ) -> dict:
    """Collect configuration from SPAXI_* environment variables."""
    cfg: dict = {}
    for section, key in KNOWN_KEYS:
        envvar = f"SPAXI_{section}_{key}".replace("-", "_").upper()
        if envvar in environ:
            cfg.setdefault(section, {})[key] = environ[envvar]
    return cfg


def _merge(base: dict, over: dict) -> dict:
    """Recursively merge ``over`` on top of ``base`` (over wins)."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Layered spaxi configuration."""

    def __init__(self, config_file: Path | None = None, environ=None):
        environ = os.environ if environ is None else environ
        layers = [_from_environ(environ)]
        path = config_file or default_config_path()
        if Path(path).is_file():
            with open(path, "rb") as fp:
                layers.append(tomllib.load(fp))
        self.data: dict = {}
        for layer in layers:
            self.data = _merge(self.data, layer)
        self.config_file = Path(path)

    def get(self, section: str, key: str, default=None):
        return self.data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value) -> None:
        """Apply a value at the highest layer (command line options)."""
        if value is not None:
            self.data.setdefault(section, {})[key] = value
