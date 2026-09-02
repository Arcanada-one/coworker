"""The signal guard runs inside an agent hook, where PATH is not the user's PATH.

Regression cover for the defect measured on three hosts on 2026-08-31: the guard
invoked `rtk` by bare name, so under the stripped environment Claude Code hands a
PreToolUse hook it exited 127 with empty stdout. The agent saw
`rtk: command not found` on every bulk command and token reduction was silently
off, while `coworker rtk status` still reported the hook as enabled.

These tests run the shell asset directly with a controlled PATH, which is the
only way to reproduce the hook's environment from Python.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "coworker" / "plugins" / "rtk_signal_guard.sh"

BULK_INPUT = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat /etc/hostname"}})


def bash_input(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def run_guard(env: dict[str, str], stdin: str = BULK_INPUT) -> subprocess.CompletedProcess[str]:
    """Invoke the guard with a fully controlled environment (no inheritance)."""
    return subprocess.run(
        ["/bin/bash", str(GUARD)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture
def fake_rtk(tmp_path: Path) -> Path:
    """A stand-in rtk that emits a recognisable PreToolUse response."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rtk = bindir / "rtk"
    rtk.write_text(
        "#!/bin/bash\n"
        "cat >/dev/null\n"
        'printf \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecisionReason":"FAKE-RTK-RAN"}}\\n\'\n'
    )
    rtk.chmod(0o755)
    return rtk


def test_guard_finds_rtk_when_path_is_stripped(tmp_path: Path, fake_rtk: Path) -> None:
    """The defect itself: a hook's PATH lacks the user bin dir where rtk lives.

    Placing rtk in ~/.local/bin and omitting it from PATH is exactly the shape
    measured in production. Before the fix this exited 127 with empty stdout.
    """
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "rtk").write_bytes(fake_rtk.read_bytes())
    (home / ".local" / "bin" / "rtk").chmod(0o755)

    result = run_guard({"PATH": "/usr/bin:/bin", "HOME": str(home)})

    assert result.returncode == 0, f"guard must not fail the hook; stderr={result.stderr}"
    assert result.stdout.strip(), "guard must emit a hook response, not an empty stdout"
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


def test_guard_degrades_to_allow_when_rtk_is_absent(tmp_path: Path) -> None:
    """When rtk genuinely is not installed, the hook must not break the agent.

    Token reduction is an optimisation; failing the hook closed over a missing
    optional binary would stop every Bash call the agent makes. The warning goes
    to stderr so the condition stays visible rather than silent.
    """
    home = tmp_path / "empty-home"
    home.mkdir()

    result = run_guard({"PATH": "/usr/bin:/bin", "HOME": str(home), "COWORKER_RTK_BIN": ""})

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "rtk" in result.stderr.lower(), "the degraded path must announce itself on stderr"


def test_invalid_explicit_pin_degrades_without_global_discovery(tmp_path: Path) -> None:
    """An invalid explicit pin must not fall through to a host-global binary."""
    home = tmp_path / "invalid-pin-home"
    home.mkdir()
    missing = home / "missing" / "rtk"

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(missing),
        }
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "rtk" in result.stderr.lower()


def test_guard_honours_an_explicit_binary_pin(tmp_path: Path, fake_rtk: Path) -> None:
    """COWORKER_RTK_BIN is the operator's escape hatch for a non-standard install."""
    home = tmp_path / "home-pin"
    home.mkdir()

    result = run_guard(
        {"PATH": "/usr/bin:/bin", "HOME": str(home), "COWORKER_RTK_BIN": str(fake_rtk)}
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


def test_allowlisted_command_never_reaches_rtk(tmp_path: Path) -> None:
    """Regression guard: the passthrough path must stay independent of rtk.

    A signal command is answered with `allow` directly, so it must work even
    with no rtk anywhere -- this is what keeps `git push` output reaching the
    agent verbatim.
    """
    home = tmp_path / "home-allow"
    home.mkdir()
    stdin = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})

    result = run_guard({"PATH": "/usr/bin:/bin", "HOME": str(home)}, stdin=stdin)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "signal command" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_guard_preserves_signal_command_after_shell_separator(tmp_path: Path) -> None:
    """The documented ``cd ... && git push`` direct form remains passthrough."""
    home = tmp_path / "compound-command-home"
    home.mkdir()

    result = run_guard(
        {"PATH": "/usr/bin:/bin", "HOME": str(home), "COWORKER_RTK_BIN": ""},
        stdin=bash_input("cd /tmp/repo && git push origin main"),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "signal command" in payload["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    (
        "git -C /repo push origin main",
        "git -C /repo status --short",
        "gh --repo owner/repo pr list",
        "gh --hostname github.example api user",
    ),
)
def test_guard_allows_signal_subcommand_after_global_options(
    tmp_path: Path, command: str
) -> None:
    """Global options must not hide the git/gh signal subcommand."""
    home = tmp_path / "global-option-home"
    home.mkdir()

    result = run_guard(
        {"PATH": "/usr/bin:/bin", "HOME": str(home), "COWORKER_RTK_BIN": ""},
        stdin=bash_input(command),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "signal command" in payload["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    ("git -C /repo log -p", "git -C '/repo push' log -p"),
)
def test_guard_delegates_bulk_subcommand_after_git_global_option(
    tmp_path: Path, fake_rtk: Path, command: str
) -> None:
    """Skipping ``-C`` and its whole value must leave bulk log on RTK."""
    home = tmp_path / "global-option-bulk-home"
    home.mkdir()

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(fake_rtk),
        },
        stdin=bash_input(command),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


def test_guard_does_not_match_signal_text_inside_argument(
    tmp_path: Path, fake_rtk: Path
) -> None:
    """An argument containing ``git push`` is data, not a push subcommand."""
    home = tmp_path / "argument-text-home"
    home.mkdir()

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(fake_rtk),
        },
        stdin=bash_input("git log '--format=git push'"),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


@pytest.mark.parametrize(
    "command",
    (
        "git 'push --help'",
        "git -c push status",
        "gh --repo '' pr list",
    ),
)
def test_guard_rejects_word_boundary_and_malformed_option_false_positives(
    tmp_path: Path, fake_rtk: Path, command: str
) -> None:
    home = tmp_path / "structural-negative-home"
    home.mkdir()

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(fake_rtk),
        },
        stdin=bash_input(command),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


def test_guard_custom_pattern_compares_complete_argument_words(
    tmp_path: Path, fake_rtk: Path
) -> None:
    home = tmp_path / "custom-word-boundary-home"
    home.mkdir()
    store = tmp_path / "passthrough.json"
    store.write_text(json.dumps({"patterns": ["git log --format=%H"]}))

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(fake_rtk),
            "COWORKER_RTK_PASSTHROUGH_PATH": str(store),
        },
        stdin=bash_input("git log '--format=%H %s'"),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


def test_guard_applies_custom_pattern_after_git_global_option(tmp_path: Path) -> None:
    """Operator patterns remain structural matches after option normalisation."""
    home = tmp_path / "custom-pattern-home"
    home.mkdir()
    store = tmp_path / "passthrough.json"
    store.write_text(json.dumps({"patterns": ["git tag"]}))

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": "",
            "COWORKER_RTK_PASSTHROUGH_PATH": str(store),
        },
        stdin=bash_input("git -C /repo tag v1.2.3"),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "signal command" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_guard_malformed_store_falls_back_for_global_option_signal(tmp_path: Path) -> None:
    """Malformed operator data must retain the embedded global-option defaults."""
    home = tmp_path / "malformed-store-home"
    home.mkdir()
    store = tmp_path / "passthrough.json"
    store.write_text("{not-json")

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": "",
            "COWORKER_RTK_PASSTHROUGH_PATH": str(store),
        },
        stdin=bash_input("git -C /repo push origin main"),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "signal command" in payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert "using defaults" in result.stderr


def test_guard_never_executes_custom_pattern_text(tmp_path: Path, fake_rtk: Path) -> None:
    """Shell metacharacters in an operator pattern remain inert literal data."""
    home = tmp_path / "literal-pattern-home"
    home.mkdir()
    canary = tmp_path / "pattern-was-executed"
    store = tmp_path / "passthrough.json"
    store.write_text(json.dumps({"patterns": [f"$(touch {canary})"]}))

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(fake_rtk),
            "COWORKER_RTK_PASSTHROUGH_PATH": str(store),
        },
        stdin=bash_input("git log -p"),
    )

    assert result.returncode == 0
    assert not canary.exists()
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


def test_guard_ignores_newline_pattern_that_would_broaden_to_git(
    tmp_path: Path, fake_rtk: Path
) -> None:
    """One JSON string containing a newline must not become pattern ``git``."""
    home = tmp_path / "newline-pattern-home"
    home.mkdir()
    store = tmp_path / "passthrough.json"
    store.write_text(json.dumps({"patterns": ["git\npush"]}))

    result = run_guard(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "COWORKER_RTK_BIN": str(fake_rtk),
            "COWORKER_RTK_PASSTHROUGH_PATH": str(store),
        },
        stdin=bash_input("git log -p"),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecisionReason"] == "FAKE-RTK-RAN"


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_guard_is_executable_and_syntactically_valid() -> None:
    """A vendored shell asset is only as good as its syntax on the target host."""
    result = subprocess.run(["/bin/bash", "-n", str(GUARD)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
