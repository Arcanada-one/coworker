"""Unit tests for coworker rtk plugin (TUNE-0271 Phase 1).

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

from coworker.plugins import rtk

# Marker contract — single source of truth in coworker/plugins/rtk.py.
# Tests reference the constants so a rename surfaces here immediately.
MARKER_KEY = rtk.COWORKER_RTK_MARKER
MARKER_VALUE = rtk.COWORKER_RTK_MARKER_VALUE


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


def test_enable_writes_canonical_rtk_hook_command(tmp_path):
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
    assert rtk_hook_cmds == ["rtk hook claude"], (
        "canonical RTK hook command per upstream `rtk init -g --hook-only --no-patch` output"
    )
