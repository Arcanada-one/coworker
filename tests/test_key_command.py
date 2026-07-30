"""Scoped-credential resolution tests for `key_command` — no network, no OpenAI import.

`resolve_api_key` is exercised with real short-lived shell commands (printf / false /
sleep) so the subprocess boundary itself is covered rather than mocked away.
"""

import pytest

from coworker.providers import resolve_api_key


def test_env_key_used_when_no_key_command(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "env-secret")
    cfg = {"base_url": "https://x/v1", "env_key": "FAKE_KEY", "default_model": "m"}
    assert resolve_api_key(cfg) == "env-secret"


def test_missing_env_key_exits(monkeypatch):
    monkeypatch.delenv("FAKE_KEY", raising=False)
    cfg = {"base_url": "https://x/v1", "env_key": "FAKE_KEY", "default_model": "m"}
    with pytest.raises(SystemExit):
        resolve_api_key(cfg)


def test_key_command_stdout_is_key(monkeypatch):
    # env var absent on purpose — key must come from the command
    monkeypatch.delenv("FAKE_KEY", raising=False)
    cfg = {
        "base_url": "https://x/v1",
        "env_key": "FAKE_KEY",
        "key_command": "printf cmd-secret",
        "default_model": "m",
    }
    assert resolve_api_key(cfg) == "cmd-secret"


def test_key_command_output_is_stripped(monkeypatch):
    # trailing newline from the command must be stripped off the key
    monkeypatch.setenv("FAKE_KEY", "should-be-ignored")
    out = resolve_api_key({"env_key": "FAKE_KEY", "key_command": "printf 'padded\\n'"})
    assert out == "padded"


def test_key_command_precedence_over_env(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "env-secret")
    cfg = {
        "env_key": "FAKE_KEY",
        "key_command": "printf cmd-secret",
    }
    assert resolve_api_key(cfg) == "cmd-secret"


def test_key_command_nonzero_exit_fails_loud():
    cfg = {"env_key": "FAKE_KEY", "key_command": "false"}
    with pytest.raises(SystemExit):
        resolve_api_key(cfg)


def test_key_command_empty_output_fails_loud():
    cfg = {"env_key": "FAKE_KEY", "key_command": "true"}  # exit 0, no stdout
    with pytest.raises(SystemExit):
        resolve_api_key(cfg)


def test_key_command_timeout_fails_loud(monkeypatch):
    monkeypatch.setenv("COWORKER_KEY_COMMAND_TIMEOUT", "0.2")
    cfg = {"env_key": "FAKE_KEY", "key_command": "sleep 5"}
    with pytest.raises(SystemExit):
        resolve_api_key(cfg)


def test_invalid_timeout_falls_back_to_default(monkeypatch):
    # a garbage timeout must not crash resolution; a fast command still resolves
    monkeypatch.setenv("COWORKER_KEY_COMMAND_TIMEOUT", "not-a-number")
    cfg = {"env_key": "FAKE_KEY", "key_command": "printf ok"}
    assert resolve_api_key(cfg) == "ok"
