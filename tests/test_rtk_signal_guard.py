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


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_guard_is_executable_and_syntactically_valid() -> None:
    """A vendored shell asset is only as good as its syntax on the target host."""
    result = subprocess.run(["/bin/bash", "-n", str(GUARD)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
