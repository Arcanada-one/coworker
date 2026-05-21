"""File-type gate tests (TUNE-0258).

Default-deny content-type gate on `coworker ask --paths` and `coworker write --context`.
Only `.md`, `.markdown`, `.txt` extensions + extensionless names from
`_EXTENSIONLESS_NAME_ALLOW` pass. Everything else returns exit 6 unless
`--allow-code` (or `COWORKER_ALLOW_CODE=1`) overrides, in which case a
WARN is emitted to stderr.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from coworker.cli import (
    _ALLOWED_EXTENSIONS,
    _EXTENSIONLESS_NAME_ALLOW,
    _apply_gate,
    _build_gate_log_extra,
    _check_file_type,
    _emit_gate_decision,
    _resolve_allow_code,
)

GATE_EXIT_CODE = 6


# ------------------------- unit: _check_file_type -------------------------

@pytest.mark.parametrize("name", [
    "doc.md", "DOC.MD", "doc.markdown", "doc.txt", "readme.MD",
])
def test_allowed_extensions_pass(tmp_path, name):
    f = tmp_path / name
    f.write_text("x")
    assert _check_file_type(f) is None


@pytest.mark.parametrize("name", [
    "src.py", "src.ts", "config.json", "data.yaml", "build.sh", "lib.rs",
    "Main.java", "go.mod", "Cargo.lock",
])
def test_blocked_extensions_fail(tmp_path, name):
    f = tmp_path / name
    f.write_text("x")
    msg = _check_file_type(f)
    assert msg is not None
    assert "not in the allowed list" in msg
    assert "--allow-code" in msg


@pytest.mark.parametrize("name", [
    "README", "LICENSE", "license", "ChangeLog", "AUTHORS",
])
def test_extensionless_name_allowlist_passes(tmp_path, name):
    f = tmp_path / name
    f.write_text("x")
    assert _check_file_type(f) is None


@pytest.mark.parametrize("name", [
    "Makefile", "Dockerfile", "Procfile", "arbitrary",
])
def test_extensionless_unknown_name_blocked(tmp_path, name):
    f = tmp_path / name
    f.write_text("x")
    assert _check_file_type(f) is not None


def test_allow_list_contains_canonical_set():
    assert _ALLOWED_EXTENSIONS == frozenset({".md", ".markdown", ".txt"})


def test_extensionless_name_allowlist_canonical():
    assert _EXTENSIONLESS_NAME_ALLOW == frozenset({
        "readme", "license", "changelog", "authors",
    })


# ------------------------- unit: _resolve_allow_code -------------------------

class _Args:
    def __init__(self, allow_code=False):
        self.allow_code = allow_code


def test_resolve_allow_code_cli_flag(monkeypatch):
    monkeypatch.delenv("COWORKER_ALLOW_CODE", raising=False)
    assert _resolve_allow_code(_Args(allow_code=True)) is True
    assert _resolve_allow_code(_Args(allow_code=False)) is False


def test_resolve_allow_code_env_var(monkeypatch):
    monkeypatch.setenv("COWORKER_ALLOW_CODE", "1")
    assert _resolve_allow_code(_Args(allow_code=False)) is True


def test_resolve_allow_code_env_var_not_one(monkeypatch):
    monkeypatch.setenv("COWORKER_ALLOW_CODE", "true")  # only "1" counts
    assert _resolve_allow_code(_Args(allow_code=False)) is False


def test_resolve_allow_code_missing_attr(monkeypatch):
    monkeypatch.delenv("COWORKER_ALLOW_CODE", raising=False)

    class Bare:
        pass

    assert _resolve_allow_code(Bare()) is False


# ------------------------- unit: _apply_gate -------------------------

def test_apply_gate_all_allowed(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("x")
    txt = tmp_path / "b.txt"
    txt.write_text("x")
    allowed, errors = _apply_gate([str(md), str(txt)], allow_code=False)
    assert allowed == [str(md), str(txt)]
    assert errors == []


def test_apply_gate_mixed_returns_both(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("x")
    py = tmp_path / "b.py"
    py.write_text("x")
    allowed, errors = _apply_gate([str(md), str(py)], allow_code=False)
    assert allowed == [str(md)]
    assert len(errors) == 1
    assert "b.py" in errors[0]


# ------------------------- unit: _emit_gate_decision -------------------------

def test_emit_gate_decision_no_errors(capsys):
    abort = _emit_gate_decision([], allow_code=False)
    assert abort is False
    assert capsys.readouterr().err == ""


def test_emit_gate_decision_default_aborts_and_logs_error(capsys):
    abort = _emit_gate_decision(["err1", "err2"], allow_code=False)
    assert abort is True
    err = capsys.readouterr().err
    assert err.count("[coworker] ERROR:") == 2
    assert "err1" in err and "err2" in err


def test_emit_gate_decision_override_warns_and_continues(capsys):
    abort = _emit_gate_decision(["x.py blocked"], allow_code=True)
    assert abort is False
    err = capsys.readouterr().err
    assert "WARNING (override)" in err
    assert "x.py blocked" in err


# ------------------------- unit: _build_gate_log_extra -------------------------

def test_build_gate_log_extra_no_errors_returns_none():
    assert _build_gate_log_extra([], allow_code=True, paths=["a.md"]) is None


def test_build_gate_log_extra_no_override_returns_none():
    assert _build_gate_log_extra(["err"], allow_code=False, paths=["x.py"]) is None


def test_build_gate_log_extra_override_active_returns_metadata(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("x")
    py = tmp_path / "b.py"
    py.write_text("x")
    extra = _build_gate_log_extra(
        ["err"], allow_code=True, paths=[str(md), str(py)],
    )
    assert extra is not None
    assert extra["coworker.gate_override"] is True
    assert extra["coworker.gate_overridden_files"] == [str(py)]


# ------------------------- integration: subprocess invocations -------------------------

def _run(*args: str, env: dict | None = None, input_text: str | None = None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "coworker.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
        input=input_text,
    )


def test_ask_py_blocked_exit_6(tmp_path):
    py = tmp_path / "x.py"
    py.write_text("print('hi')")
    r = _run("ask", "--paths", str(py), "--question", "x", "--no-log")
    assert r.returncode == GATE_EXIT_CODE, r.stderr
    assert "not in the allowed list" in r.stderr
    assert "--allow-code" in r.stderr


def test_ask_mixed_paths_blocked_all_or_nothing(tmp_path):
    md = tmp_path / "ok.md"
    md.write_text("# hi")
    py = tmp_path / "bad.py"
    py.write_text("x")
    r = _run("ask", "--paths", str(md), str(py), "--question", "x", "--no-log")
    assert r.returncode == GATE_EXIT_CODE
    assert "bad.py" in r.stderr


def test_write_ts_context_blocked_exit_6(tmp_path):
    ts = tmp_path / "x.ts"
    ts.write_text("export {};")
    target = tmp_path / "out.md"
    r = _run(
        "write", "--spec", "gen doc", "--context", str(ts),
        "--target", str(target), "--no-log",
    )
    assert r.returncode == GATE_EXIT_CODE, r.stderr
    assert "x.ts" in r.stderr


def test_ask_help_lists_allow_code_flag():
    r = _run("ask", "--help")
    assert r.returncode == 0
    assert "--allow-code" in r.stdout


def test_write_help_lists_allow_code_flag():
    r = _run("write", "--help")
    assert r.returncode == 0
    assert "--allow-code" in r.stdout


def test_ask_md_passes_gate_then_fails_at_provider(tmp_path):
    """Gate must NOT block .md; downstream may still fail without API key,
    but the gate decision is independent."""
    md = tmp_path / "x.md"
    md.write_text("# hi")
    r = _run(
        "ask", "--paths", str(md), "--question", "x", "--no-log",
        env={"DEEPSEEK_API_KEY": "fake-key-to-force-provider-stage"},
    )
    # Gate passed → exit code is NOT GATE_EXIT_CODE.
    assert r.returncode != GATE_EXIT_CODE, (
        f"gate should pass for .md; got exit {r.returncode}, stderr: {r.stderr}"
    )


def test_ask_extensionless_license_passes(tmp_path):
    lic = tmp_path / "LICENSE"
    lic.write_text("MIT License")
    r = _run(
        "ask", "--paths", str(lic), "--question", "x", "--no-log",
        env={"DEEPSEEK_API_KEY": "fake"},
    )
    assert r.returncode != GATE_EXIT_CODE, r.stderr


def test_ask_extensionless_unknown_blocked(tmp_path):
    f = tmp_path / "Makefile"
    f.write_text("all:\n\techo ok")
    r = _run("ask", "--paths", str(f), "--question", "x", "--no-log")
    assert r.returncode == GATE_EXIT_CODE


def test_override_flag_emits_warn_and_bypasses(tmp_path):
    py = tmp_path / "x.py"
    py.write_text("print('hi')")
    r = _run(
        "ask", "--paths", str(py), "--question", "x",
        "--allow-code", "--no-log",
        env={"DEEPSEEK_API_KEY": "fake"},
    )
    # Gate passed via override.
    assert r.returncode != GATE_EXIT_CODE
    # WARN emitted to stderr.
    assert "WARNING" in r.stderr
    assert "x.py" in r.stderr


def test_override_env_var_equivalent_to_flag(tmp_path):
    py = tmp_path / "x.py"
    py.write_text("print('hi')")
    r = _run(
        "ask", "--paths", str(py), "--question", "x", "--no-log",
        env={
            "COWORKER_ALLOW_CODE": "1",
            "DEEPSEEK_API_KEY": "fake",
        },
    )
    assert r.returncode != GATE_EXIT_CODE
    assert "WARNING" in r.stderr


def test_stdin_unaffected_by_gate():
    """Stdin path (no --paths) bypasses gate entirely."""
    r = _run(
        "ask", "--question", "summary?", "--no-log",
        env={"DEEPSEEK_API_KEY": "fake"},
        input_text="some text from stdin\n",
    )
    # Gate cannot fire — exit 6 must not appear.
    assert r.returncode != GATE_EXIT_CODE, r.stderr


def test_gate_error_message_lists_allowed_extensions(tmp_path):
    py = tmp_path / "x.py"
    py.write_text("x")
    r = _run("ask", "--paths", str(py), "--question", "x", "--no-log")
    assert ".md" in r.stderr
    assert ".markdown" in r.stderr
    assert ".txt" in r.stderr
