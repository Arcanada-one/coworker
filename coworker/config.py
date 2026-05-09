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


def load_providers() -> dict:
    """Load providers.yaml; returns dict keyed by provider name."""
    if not PROVIDERS_YAML.exists():
        raise FileNotFoundError(
            f"providers.yaml not found at {PROVIDERS_YAML}. "
            f"Create one based on examples/providers.yaml.example."
        )
    return yaml.safe_load(PROVIDERS_YAML.read_text()) or {}
