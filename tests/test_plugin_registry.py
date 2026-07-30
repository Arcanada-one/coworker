"""Tests for the plugin registry, manifest contract, and `plugins` meta-command.

Covers:
  * manifest schema (frozen dataclass),
  * discovery + graceful degradation (bad/non-plugin modules),
  * generic dispatch routing (no hard-coded plugin names in cli.py),
  * `coworker plugins list` (text + json) and `install` (dry-run + errors),
  * backward compatibility of the pre-existing `coworker rtk` surface.

The registry is invoked via its public API and via `coworker.cli.main` with a
patched argv; no network and no real host mutation.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from coworker.cli import main
from coworker.plugins import registry
from coworker.plugins.manifest import PluginManifest

REPO = Path(__file__).resolve().parent.parent


def _main(argv, monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["coworker", *argv])
    return main()


# ---------- manifest schema ----------


def test_manifest_is_frozen():
    m = PluginManifest(name="x", summary="s", version="1.0")
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.name = "y"  # type: ignore[misc]


def test_manifest_requires_defaults_none():
    assert PluginManifest(name="x", summary="s", version="1.0").requires is None


# ---------- discovery ----------


def test_discover_finds_rtk_with_valid_manifest():
    names = registry.plugin_names()
    assert "rtk" in names
    lp = registry.get("rtk")
    assert lp is not None
    assert isinstance(lp.manifest, PluginManifest)
    assert lp.manifest.name == "rtk"
    assert lp.manifest.summary
    assert lp.manifest.requires == "rtk"
    assert callable(lp.module.register)
    assert callable(lp.module.dispatch)


def test_is_plugin_and_get():
    assert registry.is_plugin("rtk") is True
    assert registry.is_plugin("does-not-exist") is False
    assert registry.get("does-not-exist") is None


def test_load_one_missing_module_is_graceful(capsys):
    """An import failure is caught, warned, and returns None (CLI stays alive)."""
    result = registry._load_one("definitely_not_a_real_module_zzz")
    assert result is None
    err = capsys.readouterr().err
    assert "failed to load and was skipped" in err


def test_load_one_non_plugin_module_is_silent(capsys):
    """A sibling helper module without a MANIFEST is a non-plugin, no warning."""
    result = registry._load_one("registry")
    assert result is None
    assert capsys.readouterr().err == ""


def test_helper_modules_not_registered_as_plugins():
    names = registry.plugin_names()
    for helper in ("rtk_passthrough", "rtk_codex_shims", "rtk_cursor_hook", "manifest"):
        assert helper not in names


# ---------- `plugins list` ----------


def test_plugins_list_text_shows_rtk(monkeypatch, capsys):
    rc = _main(["plugins", "list"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert "rtk" in out
    assert "v1.0" in out


def test_plugins_list_json_shape(monkeypatch, capsys):
    rc = _main(["plugins", "list", "--format", "json"], monkeypatch)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    rtk_entry = next(p for p in payload if p["name"] == "rtk")
    assert set(rtk_entry) == {"name", "version", "summary", "requires", "status"}
    assert rtk_entry["requires"] == "rtk"
    assert rtk_entry["version"] == "1.0"


def test_plugins_list_status_reflects_binary_presence(monkeypatch, capsys):
    monkeypatch.setattr(registry.shutil, "which", lambda _n: None)
    _main(["plugins", "list", "--format", "json"], monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    rtk_entry = next(p for p in payload if p["name"] == "rtk")
    assert "needs 'rtk' on PATH" in rtk_entry["status"]

    monkeypatch.setattr(registry.shutil, "which", lambda _n: "/usr/bin/rtk")
    _main(["plugins", "list", "--format", "json"], monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    rtk_entry = next(p for p in payload if p["name"] == "rtk")
    assert rtk_entry["status"] == "ready"


# ---------- `plugins install` ----------


def test_plugins_install_rtk_prints_instructions_dry_run(monkeypatch, capsys):
    rc = _main(["plugins", "install", "rtk"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    # rtk's install hook prints instructions only — it must never run an installer.
    assert "install" in out.lower()
    assert "coworker rtk enable" in out


def test_plugins_install_unknown_plugin_errors(monkeypatch, capsys):
    rc = _main(["plugins", "install", "no-such-plugin"], monkeypatch)
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown plugin" in err
    assert "rtk" in err  # lists available plugins


# ---------- backward compatibility + generic wiring ----------


def test_rtk_subcommand_still_dispatches(monkeypatch):
    """Pre-existing `coworker rtk status` surface must keep working."""
    rc = _main(["rtk", "status"], monkeypatch)
    assert rc == 0


def test_cli_has_no_hardcoded_plugin_dispatch():
    """cli.py must route plugins generically via the registry, not by name.

    Guards the 'add a plugin without ad-hoc CLI edits' contract: a per-plugin
    `if args.subcommand == "<name>"` branch would reintroduce the coupling.
    """
    src = (REPO / "coworker" / "cli.py").read_text(encoding="utf-8")
    assert 'args.subcommand == "rtk"' not in src
    assert "registry.is_plugin(args.subcommand)" in src


def test_top_help_survives_and_lists_plugins():
    r = subprocess.run(
        [sys.executable, "-m", "coworker.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "plugins" in r.stdout
    assert "rtk" in r.stdout
