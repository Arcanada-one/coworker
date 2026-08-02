"""XDG-compliant paths and config loading."""

import os
import pathlib

import yaml


def _xdg_config_home() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("XDG_CONFIG_HOME", str(pathlib.Path.home() / ".config"))
    )


def _xdg_state_home() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("XDG_STATE_HOME", str(pathlib.Path.home() / ".local" / "state"))
    )


CONFIG_DIR = _xdg_config_home() / "coworker"
STATE_DIR = _xdg_state_home() / "coworker"
LOG_DIR = STATE_DIR / "log"
BLOBS_ROOT = STATE_DIR / "blobs" / "sha256"

PROVIDERS_YAML = CONFIG_DIR / "providers.yaml"
PROFILES_YAML = CONFIG_DIR / "profiles.yaml"
REDACTION_YAML = CONFIG_DIR / "redaction.yaml"
KEYS_ENV = CONFIG_DIR / "keys.env"


def load_keys_env(path: pathlib.Path | None = None) -> list[str]:
    """Load provider API keys from `keys.env` into os.environ.

    Keys on disk are useless if only interactive shells source them: an agent
    invoking `coworker` non-interactively gets "env var 'X_API_KEY' not set"
    while the key sits in the config dir. Read the file here so every caller
    sees the same credentials regardless of shell setup.

    An already-set variable always wins, so `X=... coworker ask` and CI
    secrets still override the file. Returns the names (never the values) of
    the variables this call set, for diagnostics.
    """
    path = path or KEYS_ENV
    try:
        text = path.read_text()
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return []

    loaded = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value or os.environ.get(name):
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def load_providers() -> dict:
    """Load providers.yaml; returns dict keyed by provider name."""
    if not PROVIDERS_YAML.exists():
        raise FileNotFoundError(
            f"providers.yaml not found at {PROVIDERS_YAML}. "
            f"Create one based on examples/providers.yaml.example."
        )
    return yaml.safe_load(PROVIDERS_YAML.read_text()) or {}
