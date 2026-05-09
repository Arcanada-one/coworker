"""Provider resolution tests — uses a fake providers dict + Namespace, no network."""

from argparse import Namespace

import pytest

from coworker.providers import resolve_provider_and_model

PROVIDERS = {
    "moonshot":   {"base_url": "https://api.moonshot.ai/v1",      "env_key": "MOONSHOT_API_KEY",   "default_model": "kimi-k2.6"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1",  "env_key": "GROQ_API_KEY",       "default_model": "llama-3.3-70b-versatile"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",    "env_key": "OPENROUTER_API_KEY", "default_model": "deepseek/deepseek-chat-v3.5"},
    "deepseek":   {"base_url": "https://api.deepseek.com/v1",     "env_key": "DEEPSEEK_API_KEY",   "default_model": "deepseek-chat"},
    "openai":     {"base_url": "https://api.openai.com/v1",       "env_key": "OPENAI_API_KEY",     "default_model": "gpt-5-mini"},
}


@pytest.mark.parametrize(
    "name,expected_base_url,expected_env_key",
    [
        ("moonshot",   "https://api.moonshot.ai/v1",     "MOONSHOT_API_KEY"),
        ("groq",       "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        ("openrouter", "https://openrouter.ai/api/v1",   "OPENROUTER_API_KEY"),
        ("deepseek",   "https://api.deepseek.com/v1",    "DEEPSEEK_API_KEY"),
        ("openai",     "https://api.openai.com/v1",      "OPENAI_API_KEY"),
    ],
)
def test_provider_flag_selects_correct_config(name, expected_base_url, expected_env_key):
    args = Namespace(provider=name, model=None)
    prov_name, prov_cfg, model = resolve_provider_and_model(args, PROVIDERS, profile=None)
    assert prov_name == name
    assert prov_cfg["base_url"] == expected_base_url
    assert prov_cfg["env_key"] == expected_env_key
    assert model == PROVIDERS[name]["default_model"]


def test_profile_recommended_provider_used_when_no_flag():
    args = Namespace(provider=None, model=None)
    profile = {"recommended_provider": "deepseek", "system_prompt": ""}
    prov_name, _, _ = resolve_provider_and_model(args, PROVIDERS, profile=profile)
    assert prov_name == "deepseek"


def test_explicit_provider_flag_overrides_profile():
    args = Namespace(provider="openai", model=None)
    profile = {"recommended_provider": "deepseek", "system_prompt": ""}
    prov_name, _, _ = resolve_provider_and_model(args, PROVIDERS, profile=profile)
    assert prov_name == "openai"


def test_explicit_model_flag_overrides_default():
    args = Namespace(provider="deepseek", model="deepseek-reasoner")
    _, _, model = resolve_provider_and_model(args, PROVIDERS, profile=None)
    assert model == "deepseek-reasoner"


def test_unknown_provider_exits_nonzero(capsys):
    args = Namespace(provider="bogus", model=None)
    with pytest.raises(SystemExit) as exc:
        resolve_provider_and_model(args, PROVIDERS, profile=None)
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "unknown provider 'bogus'" in captured.err
