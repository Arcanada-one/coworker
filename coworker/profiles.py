"""Profile loader — system_prompt + recommended_provider per profile."""

import yaml

from .config import PROFILES_YAML


def load_profiles() -> dict:
    """Load all profiles."""
    if not PROFILES_YAML.exists():
        raise FileNotFoundError(
            f"profiles.yaml not found at {PROFILES_YAML}. "
            f"Create one based on examples/profiles.yaml.example."
        )
    return yaml.safe_load(PROFILES_YAML.read_text()) or {}


def load_profile(name: str) -> dict:
    """Load a single profile by name."""
    profiles = load_profiles()
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'. Available: {list(profiles)}")
    return profiles[name]
