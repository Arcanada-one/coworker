"""Balance-exhausted error classification + call-site exit-code tests.

Pure unit — no network. Constructs SDK-shaped exceptions directly
(verified against openai==2.34.0's APIStatusError.status_code contract).
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import httpx
import openai

from coworker.providers import classify_api_error


def _api_status_error(status: int, message: str) -> openai.APIStatusError:
    resp = httpx.Response(
        status, request=httpx.Request("POST", "https://x"), json={"error": {"message": message}}
    )
    return openai.APIStatusError(message, response=resp, body={"error": {"message": message}})


def test_classify_402_status_is_balance():
    exc = _api_status_error(402, "Insufficient Balance")
    assert classify_api_error(exc) == "balance"


def test_classify_insufficient_balance_message():
    # Non-402 status but a message that matches the substring fallback.
    exc = _api_status_error(429, "insufficient_quota: please check your plan")
    assert classify_api_error(exc) == "balance"


def test_classify_auth_error_is_none():
    exc = _api_status_error(401, "Invalid API key")
    assert classify_api_error(exc) is None


def test_classify_generic_error_is_none():
    assert classify_api_error(ValueError("boom")) is None


# --- call-site tests: 402 exits 7, not a stack-trace ---

def _fake_profile():
    return {"system_prompt": "sys", "default_max_tokens_ask": 100, "default_max_tokens_write": 100}


def _fake_providers():
    return {
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "env_key": "DEEPSEEK_API_KEY",
            "default_model": "deepseek-chat",
        }
    }


@patch("coworker.cli.make_client")
@patch("coworker.cli.load_profile")
@patch("coworker.cli.load_providers")
def test_cmd_ask_balance_exhausted_exits_7(mock_load_providers, mock_load_profile, mock_make_client, capsys, monkeypatch):
    from coworker.cli import cmd_ask

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)  # avoid the stdin-corpus branch
    mock_load_providers.return_value = _fake_providers()
    mock_load_profile.return_value = _fake_profile()
    client = MagicMock()
    client.chat.completions.create.side_effect = _api_status_error(402, "Insufficient Balance")
    mock_make_client.return_value = client

    args = Namespace(
        provider="deepseek", model=None, profile="code", paths=[], question="hi",
        max_tokens=None, task_id=None, no_log=True, allow_code=False,
    )
    rc = cmd_ask(args)
    assert rc == 7
    captured = capsys.readouterr()
    assert "balance exhausted" in captured.err
    assert "Traceback" not in captured.err


@patch("coworker.cli.make_client")
@patch("coworker.cli.load_profile")
@patch("coworker.cli.load_providers")
def test_cmd_ask_generic_api_error_exits_8(mock_load_providers, mock_load_profile, mock_make_client, capsys, monkeypatch):
    from coworker.cli import cmd_ask

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)  # avoid the stdin-corpus branch
    mock_load_providers.return_value = _fake_providers()
    mock_load_profile.return_value = _fake_profile()
    client = MagicMock()
    client.chat.completions.create.side_effect = _api_status_error(401, "Invalid API key")
    mock_make_client.return_value = client

    args = Namespace(
        provider="deepseek", model=None, profile="code", paths=[], question="hi",
        max_tokens=None, task_id=None, no_log=True, allow_code=False,
    )
    rc = cmd_ask(args)
    assert rc == 8
    captured = capsys.readouterr()
    assert "API error" in captured.err
    assert "Traceback" not in captured.err


@patch("coworker.cli.make_client")
@patch("coworker.cli.load_profile")
@patch("coworker.cli.load_providers")
def test_cmd_write_balance_exhausted_exits_7(mock_load_providers, mock_load_profile, mock_make_client, capsys, tmp_path):
    from coworker.cli import cmd_write

    mock_load_providers.return_value = _fake_providers()
    mock_load_profile.return_value = _fake_profile()
    client = MagicMock()
    client.chat.completions.create.side_effect = _api_status_error(402, "Insufficient Balance")
    mock_make_client.return_value = client

    args = Namespace(
        provider="deepseek", model=None, profile="write", context=[], spec="do x",
        target=str(tmp_path / "out.md"), max_tokens=None, task_id=None, no_log=True,
        allow_code=False, stdout=False, append=False,
    )
    rc = cmd_write(args)
    assert rc == 7
    captured = capsys.readouterr()
    assert "balance exhausted" in captured.err
    assert "Traceback" not in captured.err
