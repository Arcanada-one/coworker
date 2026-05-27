"""Unit tests for `coworker write --append` semantics (no LLM calls)."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "coworker.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _args(**overrides):
    base = dict(
        provider=None,
        model=None,
        profile="write",
        spec="x",
        context=[],
        target="/tmp/_never_used",
        max_tokens=None,
        task_id=None,
        no_log=True,
        stdout=False,
        append=False,
        allow_code=True,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _install_fake_client(monkeypatch, body: str):
    from coworker import cli

    fake_resp = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=body))],
        usage=None,
    )
    fake_completions = types.SimpleNamespace(create=lambda **_: fake_resp)
    fake_chat = types.SimpleNamespace(completions=fake_completions)
    fake_client = types.SimpleNamespace(chat=fake_chat)

    monkeypatch.setattr(cli, "make_client", lambda _cfg: fake_client)
    monkeypatch.setattr(cli, "log_call", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_resolve_allow_code", lambda _a: True)
    monkeypatch.setattr(
        cli,
        "load_providers",
        lambda: {"deepseek": {"api_key_env": "X", "model": "stub"}},
    )
    monkeypatch.setattr(
        cli,
        "load_profile",
        lambda _p: {"system_prompt": "stub", "default_max_tokens_write": 100},
    )
    monkeypatch.setattr(
        cli,
        "resolve_provider_and_model",
        lambda *a, **k: ("deepseek", {"api_key_env": "X"}, "stub"),
    )
    return cli


def test_write_help_lists_append_flag():
    r = _run("write", "--help")
    assert r.returncode == 0
    assert "--append" in r.stdout
    assert "truncate" in r.stdout.lower()


def test_append_and_stdout_are_mutually_exclusive():
    from coworker import cli

    rc = cli.cmd_write(_args(append=True, stdout=True))
    assert rc == 2


def test_append_appends_to_existing_with_newline_separator(tmp_path: Path, monkeypatch):
    cli = _install_fake_client(monkeypatch, body="NEW_BLOCK")
    target = tmp_path / "out.md"
    target.write_text("EXISTING_TAIL")  # no trailing newline

    rc = cli.cmd_write(_args(target=str(target), append=True))
    assert rc in (0, None)
    assert target.read_text() == "EXISTING_TAIL\nNEW_BLOCK"


def test_append_preserves_existing_trailing_newline(tmp_path: Path, monkeypatch):
    cli = _install_fake_client(monkeypatch, body="NEW")
    target = tmp_path / "out.md"
    target.write_text("HEAD\n")

    cli.cmd_write(_args(target=str(target), append=True))
    assert target.read_text() == "HEAD\nNEW"


def test_append_on_missing_file_falls_back_to_write(tmp_path: Path, monkeypatch):
    cli = _install_fake_client(monkeypatch, body="FRESH_BODY")
    target = tmp_path / "fresh.md"
    assert not target.exists()

    cli.cmd_write(_args(target=str(target), append=True))
    assert target.read_text() == "FRESH_BODY"


def test_write_without_append_truncates(tmp_path: Path, monkeypatch):
    cli = _install_fake_client(monkeypatch, body="REPLACEMENT")
    target = tmp_path / "out.md"
    target.write_text("OLD_CONTENT_THAT_MUST_BE_LOST")

    cli.cmd_write(_args(target=str(target), append=False))
    assert target.read_text() == "REPLACEMENT"
