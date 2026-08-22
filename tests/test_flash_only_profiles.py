from pathlib import Path

import yaml

ENABLED_PROFILES = ("doc-read", "classifier", "write", "datarim-write")
DISABLED_PROFILES = ("code", "codex", "datarim", "social")


def test_shipped_automatic_profiles_are_deepseek_flash_only():
    example = Path(__file__).parents[1] / "examples" / "profiles.yaml.example"
    profiles = yaml.safe_load(example.read_text())

    for name in ENABLED_PROFILES:
        profile = profiles[name]
        assert profile["recommended_provider"] == "deepseek"
        assert profile["recommended_model"] == "deepseek-v4-flash"
        assert "fallback_provider" not in profile
        assert "fallback_model" not in profile


def test_shipped_legacy_profiles_remain_fail_closed():
    example = Path(__file__).parents[1] / "examples" / "profiles.yaml.example"
    profiles = yaml.safe_load(example.read_text())

    for name in DISABLED_PROFILES:
        assert profiles[name]["recommended_provider"] == "none"
