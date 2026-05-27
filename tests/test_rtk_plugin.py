"""Unit tests for coworker rtk plugin.

Tests are fixture-driven: each test isolates settings.json under a tmp_path,
verifies enable/disable idempotency by `_managed_by` marker, and confirms
OS-detection paths print expected install instructions.

No network, no real ~/.claude/settings.json mutation. The plugin is invoked
via its public API (coworker.plugins.rtk), not via the CLI shell.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from coworker.plugins import rtk, rtk_codex_shims

# Marker contract — single source of truth in coworker/plugins/rtk.py.
# Tests reference the constants so a rename surfaces here immediately.
MARKER_KEY = rtk.COWORKER_RTK_MARKER
MARKER_VALUE = rtk.COWORKER_RTK_MARKER_VALUE


@pytest.fixture(autouse=True)
def _isolate_runtime_paths(tmp_path, monkeypatch):
    """Redirect every host-runtime path the plugin mutates to tmp_path.

    Without this fixture, ``cmd_enable`` would write the guard wrapper into
    the operator's real ``~/.local/bin`` and seed the operator's real
    ``~/.config/coworker/rtk-passthrough.json``. The fixture pins:

      * RTK_GUARD_INSTALL_PATH      → tmp_path/guard/rtk-signal-guard.sh
      * COWORKER_RTK_PASSTHROUGH_PATH env → tmp_path/pt/rtk-passthrough.json
      * rtk_codex_shims SHIM_DIR    → tmp_path/shims
      * rtk_codex_shims ZPROFILE    → tmp_path/.zprofile (does not exist; skipped)
      * rtk_codex_shims BASH_PROFILE→ tmp_path/.bash_profile (skipped)
    """
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    monkeypatch.setattr(rtk, "RTK_GUARD_INSTALL_PATH", guard_dir / "rtk-signal-guard.sh")

    pt_dir = tmp_path / "pt"
    pt_dir.mkdir()
    monkeypatch.setenv("COWORKER_RTK_PASSTHROUGH_PATH", str(pt_dir / "rtk-passthrough.json"))

    # Codex parity layer — point at tmp dirs and absent profiles so tests
    # never mutate operator's ~/.zprofile / ~/.bash_profile / ~/.local/share.
    monkeypatch.setattr(rtk_codex_shims, "SHIM_DIR", tmp_path / "shims")
    monkeypatch.setattr(rtk_codex_shims, "ZPROFILE", tmp_path / ".zprofile")
    monkeypatch.setattr(rtk_codex_shims, "BASH_PROFILE", tmp_path / ".bash_profile")
    yield


def _baseline_settings() -> dict:
    """Pre-existing settings.json with operator's coworker-hook-guard entry."""
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Read|Write|Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "~/.local/bin/coworker-hook-guard",
                            "timeout": 5,
                        }
                    ],
                }
            ]
        }
    }


def _write_settings(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def _count_rtk_markers(data: dict) -> int:
    pre = data.get("hooks", {}).get("PreToolUse", [])
    count = 0
    for entry in pre:
        for h in entry.get("hooks", []):
            if h.get(MARKER_KEY) == MARKER_VALUE:
                count += 1
    return count


# ----- Test 1: argparse wiring (4 subcommands registered) -----


def test_rtk_subcommand_registered_with_four_actions():
    """`coworker rtk --help` must list install, enable, disable, status."""
    import argparse

    parser = argparse.ArgumentParser(prog="coworker")
    subparsers = parser.add_subparsers(dest="subcommand")
    rtk.register(subparsers)

    # Parse `rtk install` — should succeed
    for action in ("install", "enable", "disable", "status"):
        ns = parser.parse_args(["rtk", action])
        assert ns.subcommand == "rtk"
        assert ns.rtk_action == action


# ----- Test 2: fail-soft when rtk binary missing -----


def test_status_with_rtk_missing_exits_zero(tmp_path, capsys):
    """`coworker rtk status` must exit 0 + advisory when rtk not in PATH."""
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value=None):
        rc = rtk.cmd_status(config_path=settings_path)

    assert rc == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not installed" in combined.lower()
    assert "coworker rtk install" in combined


# ----- Test 3-5: OS-specific install instructions -----


def test_install_macos_prints_brew_instruction(capsys):
    with patch.object(rtk, "_detect_os", return_value="macos"):
        rc = rtk.cmd_install()
    out = capsys.readouterr().out
    assert rc == 0
    assert "brew install rtk" in out


def test_install_linux_prints_curl_or_cargo_instruction(capsys):
    with patch.object(rtk, "_detect_os", return_value="linux"):
        rc = rtk.cmd_install()
    out = capsys.readouterr().out
    assert rc == 0
    assert "install.sh" in out or "cargo install" in out


def test_install_windows_prints_manual_zip_instruction(capsys):
    with patch.object(rtk, "_detect_os", return_value="windows"):
        rc = rtk.cmd_install()
    out = capsys.readouterr().out
    assert rc == 0
    assert "windows" in out.lower()
    assert "PATH" in out or "zip" in out.lower()


# ----- Test 6: idempotent enable — exactly one marker after N calls -----


def test_enable_idempotent_marker_count_exactly_one(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rc1 = rtk.cmd_enable(config_path=settings_path)
        rc2 = rtk.cmd_enable(config_path=settings_path)
        rc3 = rtk.cmd_enable(config_path=settings_path)

    assert rc1 == 0 and rc2 == 0 and rc3 == 0
    data = json.loads(settings_path.read_text())
    assert _count_rtk_markers(data) == 1, "enable must be idempotent: exactly one marker"


# ----- Test 7: disable removes marker, JSON stays valid -----


def test_disable_removes_marker_and_json_remains_valid(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rtk.cmd_enable(config_path=settings_path)
        rc = rtk.cmd_disable(config_path=settings_path)

    assert rc == 0
    raw = settings_path.read_text()
    data = json.loads(raw)  # raises if invalid
    assert _count_rtk_markers(data) == 0


# ----- Test 8: enable preserves existing unrelated hooks -----


def test_enable_preserves_existing_coworker_hook_guard(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rtk.cmd_enable(config_path=settings_path)

    data = json.loads(settings_path.read_text())
    pre = data["hooks"]["PreToolUse"]
    commands = [h.get("command") for entry in pre for h in entry.get("hooks", [])]
    assert any("coworker-hook-guard" in (c or "") for c in commands), (
        "enable MUST preserve operator's pre-existing coworker-hook-guard entry"
    )


# ----- Test 9: missing settings.json — fail-soft, no crash -----


def test_enable_missing_settings_json_creates_file(tmp_path):
    settings_path = tmp_path / "settings.json"  # does not exist yet

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rc = rtk.cmd_enable(config_path=settings_path)

    assert rc == 0
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert _count_rtk_markers(data) == 1


# ----- Test 10: status detects enabled state -----


def test_status_reports_enabled_when_marker_present(tmp_path, capsys):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"), \
         patch.object(rtk, "_rtk_version", return_value="0.40.0"):
        rtk.cmd_enable(config_path=settings_path)
        rc = rtk.cmd_status(config_path=settings_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert "enabled" in out.lower()


# ----- Test 11: disable on never-enabled config is no-op exit 0 -----


def test_disable_with_no_marker_is_noop(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())
    before = settings_path.read_text()

    rc = rtk.cmd_disable(config_path=settings_path)

    after = settings_path.read_text()
    assert rc == 0
    # JSON-equivalent content; whitespace may normalize, but data unchanged.
    assert json.loads(after) == json.loads(before)


# ----- Test 12: OS detection from platform.system() -----


def test_os_detection_recognises_three_platforms():
    with patch("platform.system", return_value="Darwin"):
        assert rtk._detect_os() == "macos"
    with patch("platform.system", return_value="Linux"):
        assert rtk._detect_os() == "linux"
    with patch("platform.system", return_value="Windows"):
        assert rtk._detect_os() == "windows"


# ----- Test 13: enable adds canonical RTK hook command -----


def test_enable_writes_guard_wrapper_command(tmp_path):
    """Enable v2 (post-signal-vs-bulk classifier): hook command points at
    the vendored guard wrapper, not at bare `rtk hook claude`. The guard
    short-circuits for signal commands and forwards bulk commands to rtk."""
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rtk.cmd_enable(config_path=settings_path)

    data = json.loads(settings_path.read_text())
    rtk_hook_cmds = [
        h.get("command")
        for entry in data["hooks"]["PreToolUse"]
        for h in entry.get("hooks", [])
        if h.get(MARKER_KEY) == MARKER_VALUE
    ]
    assert len(rtk_hook_cmds) == 1
    cmd = rtk_hook_cmds[0]
    assert cmd.endswith("rtk-signal-guard.sh"), (
        f"hook command must point at guard wrapper (basename rtk-signal-guard.sh), got: {cmd}"
    )

    # The wrapper script must actually exist on disk and be executable.
    guard_file = Path(cmd)
    assert guard_file.is_file(), f"guard not installed at {guard_file}"
    assert guard_file.stat().st_mode & 0o111, "guard must be executable"


def test_enable_seeds_passthrough_defaults(tmp_path, monkeypatch):
    """v2 contract: enable creates a passthrough store with the canonical
    default allowlist. Operator can later add/remove patterns via the
    `coworker rtk passthrough` subcommand."""
    from coworker.plugins import rtk_passthrough

    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rtk.cmd_enable(config_path=settings_path)

    # Fixture redirects COWORKER_RTK_PASSTHROUGH_PATH; store should now exist.
    patterns = rtk_passthrough.list_patterns()
    assert "git push" in patterns
    assert "gh pr" in patterns
    assert len(patterns) >= 13  # AC-2 floor


def test_status_reports_passthrough_pattern_count(tmp_path, capsys):
    """AC-8: status surfaces 'passthrough patterns: N' with N >= 13 after enable."""
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"), \
         patch.object(rtk, "_rtk_version", return_value="0.40.0"):
        rtk.cmd_enable(config_path=settings_path)
        rc = rtk.cmd_status(config_path=settings_path)

    out = capsys.readouterr().out
    assert rc == 0
    # Must show "passthrough patterns: N" where N >= 13.
    import re
    m = re.search(r"passthrough patterns:\s*(\d+)", out)
    assert m is not None, f"status must report passthrough count, got: {out!r}"
    assert int(m.group(1)) >= 13


def test_disable_preserves_passthrough_store(tmp_path, monkeypatch):
    """v2 contract: disable removes hook + guard but keeps operator-curated
    passthrough patterns. Operator must explicitly delete the store to lose
    custom patterns."""
    from coworker.plugins import rtk_passthrough

    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, _baseline_settings())

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rtk.cmd_enable(config_path=settings_path)
        rtk_passthrough.add_pattern("operator-custom")
        rtk.cmd_disable(config_path=settings_path)

    # Settings.json: marker block removed.
    data = json.loads(settings_path.read_text())
    assert _count_rtk_markers(data) == 0
    # Guard file: removed.
    assert not rtk.RTK_GUARD_INSTALL_PATH.exists()
    # Passthrough store: preserved with operator-custom intact.
    patterns = rtk_passthrough.list_patterns()
    assert "operator-custom" in patterns


def test_v1_to_v2_block_replacement_on_enable(tmp_path):
    """Upgrade path: a settings.json carrying a v1 block (bare
    `rtk hook claude` command) must be rewritten to the v2 guard-wrapper
    block on next enable — not appended alongside."""
    settings_path = tmp_path / "settings.json"
    # Hand-craft a v1-style block (pre-TUNE-0323).
    v1_data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "rtk hook claude",
                            MARKER_KEY: MARKER_VALUE,
                            "_version": 1,
                        }
                    ],
                }
            ]
        }
    }
    _write_settings(settings_path, v1_data)

    with patch.object(rtk, "_rtk_binary_path", return_value="/usr/local/bin/rtk"):
        rtk.cmd_enable(config_path=settings_path)

    data = json.loads(settings_path.read_text())
    rtk_hook_cmds = [
        h.get("command")
        for entry in data["hooks"]["PreToolUse"]
        for h in entry.get("hooks", [])
        if h.get(MARKER_KEY) == MARKER_VALUE
    ]
    assert len(rtk_hook_cmds) == 1, "v1 block must be replaced, not duplicated"
    assert rtk_hook_cmds[0].endswith("rtk-signal-guard.sh")


# --- TUNE-0279 Phase A — UX-pass tests --------------------------------------


def test_install_method_override_brew_skips_os_detection(capsys):
    """`--method brew` prints only the brew line, regardless of OS."""
    from argparse import Namespace

    ns = Namespace(dry_run=True, method="brew")
    with patch.object(rtk, "_detect_os", return_value="linux"):
        rc = rtk.cmd_install(ns)
    out = capsys.readouterr().out
    assert rc == 0
    assert "brew install rtk" in out
    assert "install.sh" not in out  # OS-detection branch skipped


def test_install_dry_run_default_true_when_args_none(capsys):
    """No args object — current behaviour preserved (always prints OS-default)."""
    with patch.object(rtk, "_detect_os", return_value="macos"):
        rc = rtk.cmd_install(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "brew install rtk" in out


def test_status_telemetry_unavailable_when_rtk_missing(tmp_path, capsys):
    """Telemetry field present in status, fail-soft when rtk binary absent."""
    settings_path = tmp_path / "settings.json"
    with patch.object(rtk, "_rtk_binary_path", return_value=None):
        rc = rtk.cmd_status(config_path=settings_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "telemetry:" in out.lower()
    assert "unavailable" in out.lower()
    assert "rtk missing" in out.lower()
