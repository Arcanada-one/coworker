"""Global retry policy tests — exponential backoff + max-retries + fail-loud.

Pure unit, no network, no real sleeping: every test injects a recording
`sleep_fn` (and pins `retry_base_delay` where the CLI path is exercised).
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from coworker.providers import (
    _attempt_with_retry,
    call_with_fallback,
    resolve_retry_policy,
)

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "deepseek/deepseek-chat-v3.5",
    },
}


class _StatusError(Exception):
    def __init__(self, status_code, message=""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        self.message = message


class _SeqClient:
    """Fake OpenAI client: pops one outcome per create() call.

    An outcome that is an Exception instance is raised; anything else is
    returned. Running out of outcomes fails the test loudly.
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if not outer._outcomes:
                    raise AssertionError("unexpected extra create() call")
                out = outer._outcomes.pop(0)
                if isinstance(out, Exception):
                    raise out
                return out

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _factory_map(mapping):
    def factory(prov_cfg):
        return mapping[prov_cfg["env_key"]]
    return factory


def _recorded_sleep():
    delays: list[float] = []
    return delays, delays.append


# --- resolve_retry_policy ----------------------------------------------------

def test_policy_defaults(monkeypatch):
    monkeypatch.delenv("COWORKER_MAX_RETRIES", raising=False)
    monkeypatch.delenv("COWORKER_RETRY_BASE_DELAY", raising=False)
    assert resolve_retry_policy(None) == (2, 1.0)
    assert resolve_retry_policy({"system_prompt": ""}) == (2, 1.0)


def test_policy_profile_keys_win_over_env(monkeypatch):
    monkeypatch.setenv("COWORKER_MAX_RETRIES", "9")
    monkeypatch.setenv("COWORKER_RETRY_BASE_DELAY", "9")
    profile = {"max_retries": 1, "retry_base_delay": 0.5}
    assert resolve_retry_policy(profile) == (1, 0.5)


def test_policy_env_used_when_profile_silent(monkeypatch):
    monkeypatch.setenv("COWORKER_MAX_RETRIES", "4")
    monkeypatch.setenv("COWORKER_RETRY_BASE_DELAY", "0.25")
    assert resolve_retry_policy({"system_prompt": ""}) == (4, 0.25)


def test_policy_invalid_values_fall_through(monkeypatch):
    monkeypatch.setenv("COWORKER_MAX_RETRIES", "not-a-number")
    monkeypatch.setenv("COWORKER_RETRY_BASE_DELAY", "-3")
    profile = {"max_retries": -1, "retry_base_delay": "bogus"}
    assert resolve_retry_policy(profile) == (2, 1.0)


def test_policy_zero_disables_retries_and_wait(monkeypatch):
    monkeypatch.delenv("COWORKER_MAX_RETRIES", raising=False)
    monkeypatch.delenv("COWORKER_RETRY_BASE_DELAY", raising=False)
    assert resolve_retry_policy({"max_retries": 0, "retry_base_delay": 0}) == (0, 0.0)


# --- _attempt_with_retry -----------------------------------------------------

def test_retry_then_success_exponential_delays(capsys):
    outcomes = [_StatusError(429), _StatusError(429), "OK"]
    seq = iter(outcomes)

    def create():
        out = next(seq)
        if isinstance(out, Exception):
            raise out
        return out

    delays, sleep = _recorded_sleep()
    assert _attempt_with_retry(create, "deepseek", 2, 1.0, sleep) == "OK"
    assert delays == [1.0, 2.0]
    err = capsys.readouterr().err
    assert "attempt 1/3" in err and "attempt 2/3" in err


def test_custom_base_delay_doubles(capsys):
    seq = iter([_StatusError(429), _StatusError(429), _StatusError(429), "OK"])

    def create():
        out = next(seq)
        if isinstance(out, Exception):
            raise out
        return out

    delays, sleep = _recorded_sleep()
    assert _attempt_with_retry(create, "deepseek", 3, 0.5, sleep) == "OK"
    assert delays == [0.5, 1.0, 2.0]


def test_non_retryable_raises_immediately():
    calls = []

    def create():
        calls.append(1)
        raise _StatusError(401, "unauthorized")

    delays, sleep = _recorded_sleep()
    with pytest.raises(_StatusError):
        _attempt_with_retry(create, "deepseek", 2, 1.0, sleep)
    assert len(calls) == 1
    assert delays == []


def test_balance_error_never_retried():
    calls = []

    def create():
        calls.append(1)
        raise _StatusError(402, "insufficient balance")

    delays, sleep = _recorded_sleep()
    with pytest.raises(_StatusError):
        _attempt_with_retry(create, "deepseek", 2, 1.0, sleep)
    assert len(calls) == 1
    assert delays == []


def test_exhaustion_fails_loud_naming_attempts(capsys):
    def create():
        raise _StatusError(429, "rate limit")

    delays, sleep = _recorded_sleep()
    with pytest.raises(_StatusError):
        _attempt_with_retry(create, "deepseek", 2, 1.0, sleep)
    assert delays == [1.0, 2.0]
    err = capsys.readouterr().err
    assert "failed after 3 attempt(s)" in err
    assert "retry budget exhausted" in err


# --- call_with_fallback with retry policy ------------------------------------

def test_primary_retries_before_fallback_hop(monkeypatch):
    monkeypatch.delenv("COWORKER_MAX_RETRIES", raising=False)
    primary = _SeqClient([_StatusError(429), _StatusError(429), _StatusError(429)])
    fallback = _SeqClient(["OK"])
    factory = _factory_map({
        "DEEPSEEK_API_KEY": primary,
        "OPENROUTER_API_KEY": fallback,
    })
    profile = {"fallback_provider": "openrouter", "max_retries": 2, "retry_base_delay": 1.0}
    delays, sleep = _recorded_sleep()
    resp, name, _, _, _ = call_with_fallback(
        "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
        profile, PROVIDERS, {"messages": [], "max_tokens": 8},
        client_factory=factory, sleep_fn=sleep,
    )
    assert resp == "OK"
    assert name == "openrouter"
    assert len(primary.calls) == 3
    assert len(fallback.calls) == 1
    assert delays == [1.0, 2.0]


def test_fallback_gets_its_own_retry_budget():
    primary = _SeqClient([_StatusError(429), _StatusError(429)])
    fallback = _SeqClient([_StatusError(429), "OK"])
    factory = _factory_map({
        "DEEPSEEK_API_KEY": primary,
        "OPENROUTER_API_KEY": fallback,
    })
    profile = {"fallback_provider": "openrouter", "max_retries": 1, "retry_base_delay": 0}
    delays, sleep = _recorded_sleep()
    resp, name, _, _, _ = call_with_fallback(
        "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
        profile, PROVIDERS, {"messages": [], "max_tokens": 8},
        client_factory=factory, sleep_fn=sleep,
    )
    assert resp == "OK"
    assert name == "openrouter"
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 2


def test_no_fallback_exhaustion_propagates(capsys):
    primary = _SeqClient([_StatusError(429), _StatusError(429)])
    factory = _factory_map({"DEEPSEEK_API_KEY": primary})
    profile = {"system_prompt": "", "max_retries": 1, "retry_base_delay": 0}
    delays, sleep = _recorded_sleep()
    with pytest.raises(_StatusError):
        call_with_fallback(
            "deepseek", PROVIDERS["deepseek"], "deepseek-chat",
            profile, PROVIDERS, {"messages": [], "max_tokens": 8},
            client_factory=factory, sleep_fn=sleep,
        )
    assert len(primary.calls) == 2
    assert "failed after 2 attempt(s)" in capsys.readouterr().err


# --- CLI integration: `coworker write` fails loud, non-zero -------------------

def _api_429():
    return _StatusError(429, "rate limit")


@patch("coworker.cli.make_client")
@patch("coworker.cli.load_profile")
@patch("coworker.cli.load_providers")
def test_cmd_write_retry_exhaustion_exits_nonzero_naming_attempts(
    mock_load_providers, mock_load_profile, mock_make_client, capsys, tmp_path
):
    from coworker.cli import API_ERROR_EXIT, cmd_write

    mock_load_providers.return_value = {"deepseek": PROVIDERS["deepseek"]}
    mock_load_profile.return_value = {
        "system_prompt": "sys",
        "default_max_tokens_write": 100,
        "max_retries": 1,
        "retry_base_delay": 0,  # no real sleeping in tests
    }
    client = MagicMock()
    client.chat.completions.create.side_effect = _api_429()
    mock_make_client.return_value = client

    args = Namespace(
        provider="deepseek", model=None, profile="write", context=[], spec="do x",
        target=str(tmp_path / "out.md"), max_tokens=None, task_id=None, no_log=True,
        allow_code=False, stdout=False, append=False,
    )
    rc = cmd_write(args)
    assert rc == API_ERROR_EXIT
    err = capsys.readouterr().err
    assert "failed after 2 attempt(s)" in err
    assert "retry budget exhausted" in err
    assert "API error" in err
    assert client.chat.completions.create.call_count == 2
    assert not (tmp_path / "out.md").exists()
