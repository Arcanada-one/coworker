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


def test_profile_recommended_model_used_when_no_flag():
    args = Namespace(provider="deepseek", model=None)
    profile = {"recommended_model": "deepseek-v4-pro", "system_prompt": ""}
    _, _, model = resolve_provider_and_model(args, PROVIDERS, profile=profile)
    assert model == "deepseek-v4-pro"


def test_flag_model_overrides_profile_recommended_model():
    args = Namespace(provider="deepseek", model="explicit-model")
    profile = {"recommended_model": "deepseek-v4-pro", "system_prompt": ""}
    _, _, model = resolve_provider_and_model(args, PROVIDERS, profile=profile)
    assert model == "explicit-model"


def test_provider_default_used_when_no_flag_no_profile_model():
    args = Namespace(provider="deepseek", model=None)
    profile = {"system_prompt": ""}
    _, _, model = resolve_provider_and_model(args, PROVIDERS, profile=profile)
    assert model == PROVIDERS["deepseek"]["default_model"]


# ---------------------------------------------------------------------------
# TUNE-0132: provider fallback chain (429/timeout -> declared fallback)
# ---------------------------------------------------------------------------

from coworker.providers import (  # noqa: E402
    call_with_fallback,
    classify_retryable_error,
    resolve_fallback_provider,
)


class _StatusError(Exception):
    def __init__(self, status_code, message=""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        self.message = message


class _APITimeoutError(Exception):
    """Mimics openai.APITimeoutError — no status_code, name matched by classifier."""


# --- resolve_fallback_provider ---------------------------------------------

def test_resolve_fallback_returns_declared_provider():
    profile = {"fallback_provider": "openrouter"}
    fb = resolve_fallback_provider(profile, PROVIDERS, "deepseek")
    assert fb is not None
    name, cfg, model = fb
    assert name == "openrouter"
    assert cfg["base_url"] == PROVIDERS["openrouter"]["base_url"]
    assert model == PROVIDERS["openrouter"]["default_model"]


def test_resolve_fallback_uses_fallback_model_when_declared():
    profile = {"fallback_provider": "openrouter", "fallback_model": "custom/model"}
    _, _, model = resolve_fallback_provider(profile, PROVIDERS, "deepseek")
    assert model == "custom/model"


def test_resolve_fallback_none_when_absent():
    assert resolve_fallback_provider({"system_prompt": ""}, PROVIDERS, "deepseek") is None
    assert resolve_fallback_provider(None, PROVIDERS, "deepseek") is None


def test_resolve_fallback_none_when_same_as_primary():
    profile = {"fallback_provider": "deepseek"}
    assert resolve_fallback_provider(profile, PROVIDERS, "deepseek") is None


def test_resolve_fallback_none_when_unknown_provider(capsys):
    profile = {"fallback_provider": "bogus"}
    assert resolve_fallback_provider(profile, PROVIDERS, "deepseek") is None
    assert "unknown fallback_provider" in capsys.readouterr().err


# --- classify_retryable_error ----------------------------------------------

def test_classify_retryable_on_429():
    assert classify_retryable_error(_StatusError(429)) == "retryable"


def test_classify_retryable_on_timeout_class_name():
    assert classify_retryable_error(_APITimeoutError("request timed out")) == "retryable"


def test_classify_retryable_on_timeout_text():
    assert classify_retryable_error(Exception("upstream timeout")) == "retryable"


def test_classify_retryable_excludes_balance_402():
    assert classify_retryable_error(_StatusError(402, "insufficient balance")) is None


def test_classify_retryable_none_for_generic():
    assert classify_retryable_error(_StatusError(401, "unauthorized")) is None
    assert classify_retryable_error(Exception("bad request")) is None


# --- call_with_fallback (fake client factory, no network) ------------------

class _FakeClient:
    """Fake OpenAI client: raise `raises` on create, else return `resp`."""

    def __init__(self, resp=None, raises=None):
        self._resp = resp
        self._raises = raises
        self.calls = []

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                self._outer.calls.append(kwargs)
                if self._outer._raises is not None:
                    raise self._outer._raises
                return self._outer._resp

        class _Chat:
            def __init__(self, outer):
                self.completions = _Completions(outer)

        self.chat = _Chat(self)


def _factory_map(mapping):
    """Return a client_factory that keys off prov_cfg[env_key]."""
    def factory(prov_cfg):
        return mapping[prov_cfg["env_key"]]
    return factory


def test_call_with_fallback_hops_on_429():
    primary = _FakeClient(raises=_StatusError(429, "rate limit"))
    fallback = _FakeClient(resp="OK")
    factory = _factory_map({
        PROVIDERS["deepseek"]["env_key"]: primary,
        PROVIDERS["openrouter"]["env_key"]: fallback,
    })
    profile = {"fallback_provider": "openrouter"}
    resp, name, cfg, model, latency = call_with_fallback(
        "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
        profile, PROVIDERS, {"messages": [], "max_tokens": 8},
        client_factory=factory,
    )
    assert resp == "OK"
    assert name == "openrouter"
    assert model == PROVIDERS["openrouter"]["default_model"]
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert latency >= 0


def test_call_with_fallback_success_no_hop():
    primary = _FakeClient(resp="PRIMARY")
    fallback = _FakeClient(resp="SHOULD-NOT-BE-CALLED")
    factory = _factory_map({
        PROVIDERS["deepseek"]["env_key"]: primary,
        PROVIDERS["openrouter"]["env_key"]: fallback,
    })
    profile = {"fallback_provider": "openrouter"}
    resp, name, _, _, _ = call_with_fallback(
        "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
        profile, PROVIDERS, {"messages": [], "max_tokens": 8},
        client_factory=factory,
    )
    assert resp == "PRIMARY"
    assert name == "deepseek"
    assert len(fallback.calls) == 0


def test_call_with_fallback_reraises_balance_no_hop():
    primary = _FakeClient(raises=_StatusError(402, "insufficient balance"))
    fallback = _FakeClient(resp="OK")
    factory = _factory_map({
        PROVIDERS["deepseek"]["env_key"]: primary,
        PROVIDERS["openrouter"]["env_key"]: fallback,
    })
    profile = {"fallback_provider": "openrouter"}
    with pytest.raises(_StatusError) as exc:
        call_with_fallback(
            "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
            profile, PROVIDERS, {"messages": [], "max_tokens": 8},
            client_factory=factory,
        )
    assert exc.value.status_code == 402
    assert len(fallback.calls) == 0


def test_call_with_fallback_reraises_when_no_fallback_declared():
    primary = _FakeClient(raises=_StatusError(429, "rate limit"))
    factory = _factory_map({PROVIDERS["deepseek"]["env_key"]: primary})
    with pytest.raises(_StatusError) as exc:
        call_with_fallback(
            "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
            {"system_prompt": ""}, PROVIDERS, {"messages": [], "max_tokens": 8},
            client_factory=factory,
        )
    assert exc.value.status_code == 429


def test_call_with_fallback_single_hop_fallback_error_propagates():
    """Fallback also 429s: at most ONE hop, second error must propagate."""
    primary = _FakeClient(raises=_StatusError(429, "rate limit"))
    fallback = _FakeClient(raises=_StatusError(429, "rate limit again"))
    factory = _factory_map({
        PROVIDERS["deepseek"]["env_key"]: primary,
        PROVIDERS["openrouter"]["env_key"]: fallback,
    })
    profile = {"fallback_provider": "openrouter"}
    with pytest.raises(_StatusError):
        call_with_fallback(
            "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
            profile, PROVIDERS, {"messages": [], "max_tokens": 8},
            client_factory=factory,
        )
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
