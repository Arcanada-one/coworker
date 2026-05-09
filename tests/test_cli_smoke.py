"""Smoke tests: --help exits 0 with expected structure for all 4 subcommands."""

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "coworker.cli", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def test_top_help_lists_four_subcommands():
    r = _run("--help")
    assert "{ask,write,stats,debug}" in r.stdout
    for sub in ("ask", "write", "stats", "debug"):
        assert sub in r.stdout


def test_top_help_does_not_leak_internal_task_id():
    r = _run("--help")
    assert "TUNE-" not in r.stdout
    assert "TUNE-" not in r.stderr


def test_ask_help_required_question_flag():
    r = _run("ask", "--help")
    for flag in ("--provider", "--model", "--profile", "--paths", "--question", "--max-tokens"):
        assert flag in r.stdout, f"missing flag {flag} in ask --help"


def test_write_help_required_flags():
    r = _run("write", "--help")
    for flag in ("--spec", "--target", "--context", "--stdout"):
        assert flag in r.stdout, f"missing flag {flag} in write --help"


def test_stats_help_choices():
    r = _run("stats", "--help")
    for choice in ("provider", "profile", "model", "combined"):
        assert choice in r.stdout
    assert "--since" in r.stdout
    assert "--format" in r.stdout


def test_debug_help_required_hash():
    r = _run("debug", "--help")
    assert "--hash" in r.stdout
    assert "min 2 chars" in r.stdout
