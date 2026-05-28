"""Unit tests for the Cursor CLI parity layer (rtk_cursor_hook).

No live cursor calls. ~/.cursor/hooks.json is redirected into tmp_path. The
Cursor layer installs a native `beforeShellExecution` hook that runs
`rtk hook cursor`; operator hooks must survive enable/disable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coworker.plugins import rtk_cursor_hook as mod


@pytest.fixture
def isolated(monkeypatch, tmp_path: Path):
    hooks = tmp_path / ".cursor" / "hooks.json"
    monkeypatch.setattr(mod, "CURSOR_HOOKS", hooks)
    monkeypatch.setattr(mod, "_rtk_binary_path", lambda: "/usr/local/bin/rtk")
    return {"mod": mod, "hooks": hooks}


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def test_enable_creates_hook_when_absent(isolated):
    m = isolated["mod"]
    assert not isolated["hooks"].exists()
    assert m.enable_cursor_parity(verbose=False) == 0
    data = _read(isolated["hooks"])
    assert data["version"] == 1
    entries = data["hooks"]["beforeShellExecution"]
    assert entries == [{"command": "/usr/local/bin/rtk hook cursor"}]


def test_enable_idempotent(isolated):
    m = isolated["mod"]
    m.enable_cursor_parity(verbose=False)
    m.enable_cursor_parity(verbose=False)
    entries = _read(isolated["hooks"])["hooks"]["beforeShellExecution"]
    rtk_entries = [e for e in entries if "hook cursor" in e["command"]]
    assert len(rtk_entries) == 1


def test_enable_preserves_operator_hooks(isolated):
    m = isolated["mod"]
    isolated["hooks"].parent.mkdir(parents=True)
    isolated["hooks"].write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "afterFileEdit": [{"command": "./format.sh"}],
                    "beforeShellExecution": [{"command": "./guard.sh", "matcher": "rm"}],
                },
            }
        )
    )
    assert m.enable_cursor_parity(verbose=False) == 0
    data = _read(isolated["hooks"])
    # Operator's other event untouched.
    assert data["hooks"]["afterFileEdit"] == [{"command": "./format.sh"}]
    # Operator's own beforeShellExecution entry preserved; rtk appended.
    bse = data["hooks"]["beforeShellExecution"]
    assert {"command": "./guard.sh", "matcher": "rm"} in bse
    assert {"command": "/usr/local/bin/rtk hook cursor"} in bse


def test_enable_migrates_bare_to_absolute(isolated):
    m = isolated["mod"]
    isolated["hooks"].parent.mkdir(parents=True)
    isolated["hooks"].write_text(
        json.dumps(
            {"version": 1, "hooks": {"beforeShellExecution": [{"command": "rtk hook cursor"}]}}
        )
    )
    assert m.enable_cursor_parity(verbose=False) == 0
    entries = _read(isolated["hooks"])["hooks"]["beforeShellExecution"]
    rtk_entries = [e for e in entries if "hook cursor" in e["command"]]
    assert rtk_entries == [{"command": "/usr/local/bin/rtk hook cursor"}]


def test_disable_removes_only_rtk_entry(isolated):
    m = isolated["mod"]
    isolated["hooks"].parent.mkdir(parents=True)
    isolated["hooks"].write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "beforeShellExecution": [{"command": "./guard.sh", "matcher": "rm"}],
                },
            }
        )
    )
    m.enable_cursor_parity(verbose=False)
    assert m.disable_cursor_parity(verbose=False) == 0
    bse = _read(isolated["hooks"])["hooks"]["beforeShellExecution"]
    assert bse == [{"command": "./guard.sh", "matcher": "rm"}]


def test_disable_drops_empty_event_key(isolated):
    m = isolated["mod"]
    m.enable_cursor_parity(verbose=False)
    m.disable_cursor_parity(verbose=False)
    data = _read(isolated["hooks"])
    assert "beforeShellExecution" not in data.get("hooks", {})


def test_disable_idempotent_when_absent(isolated):
    m = isolated["mod"]
    # No file at all.
    assert m.disable_cursor_parity(verbose=False) == 0


def test_malformed_json_failsoft(isolated):
    m = isolated["mod"]
    isolated["hooks"].parent.mkdir(parents=True)
    isolated["hooks"].write_text("{ this is not json")
    assert m.enable_cursor_parity(verbose=False) == 0
    data = _read(isolated["hooks"])
    assert data["hooks"]["beforeShellExecution"] == [
        {"command": "/usr/local/bin/rtk hook cursor"}
    ]


def test_status_reflects_state(isolated):
    m = isolated["mod"]
    s = m.status()
    assert s["hook_present"] is False
    assert s["event"] == "beforeShellExecution"

    m.enable_cursor_parity(verbose=False)
    s = m.status()
    assert s["hook_present"] is True


def test_hook_command_falls_back_to_bare_when_rtk_absent(isolated, monkeypatch):
    m = isolated["mod"]
    monkeypatch.setattr(m, "_rtk_binary_path", lambda: None)
    m.enable_cursor_parity(verbose=False)
    entries = _read(isolated["hooks"])["hooks"]["beforeShellExecution"]
    assert entries == [{"command": "rtk hook cursor"}]
