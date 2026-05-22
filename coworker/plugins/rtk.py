"""Coworker × RTK (Rust Token Killer) — opt-in plugin.

Thin convenience wrapper around upstream `rtk` (https://github.com/rtk-ai/rtk):

  * `coworker rtk install`  — print OS-specific install instruction (no actual install — supply-chain).
  * `coworker rtk enable`   — append marker-tagged RTK hook block to ~/.claude/settings.json.
  * `coworker rtk disable`  — filter out marker-tagged block, leaving operator's other hooks intact.
  * `coworker rtk status`   — report rtk binary state + hook state, fail-soft if binary absent.

Hook idempotency contract: every block we write carries
``"_managed_by": "coworker-rtk"``. Enable is a no-op when the marker is
already present. Disable filters all blocks bearing the marker. Operator's
other hook entries (e.g. ``coworker-hook-guard``) are never touched.

Settings.json is the single source of truth — no sidecar state file.

Design decision references:
  - creative-TUNE-0271-architecture-hook-installation.md § Decision (Option A)
  - creative-TUNE-0271-architecture-plugin-namespace.md § Decision (Option 1)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Marker contract — keep these as the public stable surface; tests pin both.
COWORKER_RTK_MARKER = "_managed_by"
COWORKER_RTK_MARKER_VALUE = "coworker-rtk"
COWORKER_RTK_VERSION_KEY = "_version"
COWORKER_RTK_VERSION = 1

# Canonical hook command — sourced from `rtk init -g --hook-only --no-patch`
# (captured 2026-05-22, rtk 0.40.0; see datarim/tasks/TUNE-0271-fixtures.md).
RTK_HOOK_COMMAND = "rtk hook claude"
RTK_HOOK_MATCHER = "Bash"

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


# ---------- helpers ----------


def _detect_os() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        return "macos"
    if sysname == "Linux":
        return "linux"
    if sysname == "Windows":
        return "windows"
    return "unknown"


def _rtk_binary_path() -> str | None:
    """Return absolute path to rtk binary if found on PATH, else None."""
    return shutil.which("rtk")


def _rtk_version() -> str | None:
    """Return `rtk --version` short string, or None if unavailable."""
    path = _rtk_binary_path()
    if path is None:
        return None
    try:
        r = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=3, check=False
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _resolve_config_path(config_path: Path | None) -> Path:
    return Path(config_path) if config_path is not None else DEFAULT_SETTINGS_PATH


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"~/.claude/settings.json is not valid JSON ({e}). "
            "Coworker refuses to modify it. Repair manually (try `python3 -m json.tool`)."
        ) from e


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via tempfile + os.replace (cross-platform atomic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _rtk_hook_block() -> dict:
    return {
        "matcher": RTK_HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": RTK_HOOK_COMMAND,
                COWORKER_RTK_MARKER: COWORKER_RTK_MARKER_VALUE,
                COWORKER_RTK_VERSION_KEY: COWORKER_RTK_VERSION,
            }
        ],
    }


def _count_markers(data: dict) -> int:
    pre = data.get("hooks", {}).get("PreToolUse", [])
    count = 0
    for entry in pre:
        for h in entry.get("hooks", []) or []:
            if h.get(COWORKER_RTK_MARKER) == COWORKER_RTK_MARKER_VALUE:
                count += 1
    return count


def _filter_out_marker(data: dict) -> dict:
    """Return a copy of `data` with every marker-tagged hook entry removed.

    A PreToolUse entry whose `hooks` list becomes empty after filtering is
    itself dropped — we never leave skeletal placeholders behind.
    """
    if "hooks" not in data:
        return data
    pre = data.get("hooks", {}).get("PreToolUse", [])
    new_pre = []
    for entry in pre:
        kept_hooks = [
            h
            for h in entry.get("hooks", []) or []
            if h.get(COWORKER_RTK_MARKER) != COWORKER_RTK_MARKER_VALUE
        ]
        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            new_pre.append(new_entry)
        elif not entry.get("hooks"):
            # Entry had no hooks list at all — preserve as-is (operator data).
            new_pre.append(entry)
    if new_pre:
        data["hooks"]["PreToolUse"] = new_pre
    else:
        data["hooks"].pop("PreToolUse", None)
        if not data["hooks"]:
            data.pop("hooks", None)
    return data


# ---------- commands ----------


def cmd_install(args: argparse.Namespace | None = None) -> int:
    """Print OS-specific RTK install instructions. Never executes installers."""
    osname = _detect_os()
    print("RTK install instructions (coworker does not install binaries itself):\n")
    if osname == "macos":
        print("  macOS:   brew install rtk")
        print("           # or: curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh")
        print("           # or: cargo install --git https://github.com/rtk-ai/rtk")
    elif osname == "linux":
        print("  Linux:   curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh")
        print("           # or: cargo install --git https://github.com/rtk-ai/rtk")
    elif osname == "windows":
        print("  Windows: download rtk-x86_64-pc-windows-msvc.zip from")
        print("           https://github.com/rtk-ai/rtk/releases, extract, add to PATH.")
        print("           # or: cargo install --git https://github.com/rtk-ai/rtk")
    else:
        print(f"  Unknown OS ({platform.system()}). See https://github.com/rtk-ai/rtk for instructions.")
    print("\nAfter install: `coworker rtk enable` to register the Claude Code hook.")
    return 0


def cmd_enable(
    args: argparse.Namespace | None = None,
    *,
    config_path: Path | None = None,
) -> int:
    """Append marker-tagged RTK hook to settings.json. Idempotent.

    Fail-soft: if rtk binary missing, print WARN and continue with hook write
    (operator may have rtk installed in a non-default PATH; hook only matters
    when Claude Code runs it).
    """
    target = _resolve_config_path(config_path)

    if _rtk_binary_path() is None:
        print(
            "[coworker rtk] WARN: rtk binary not found on PATH. Hook will be registered "
            "but won't run until rtk is installed. See `coworker rtk install`.",
            file=sys.stderr,
        )

    try:
        data = _load_settings(target)
    except RuntimeError as e:
        print(f"[coworker rtk] ERROR: {e}", file=sys.stderr)
        return 1

    if _count_markers(data) >= 1:
        print(f"RTK hook already enabled in {target} (no changes made).")
        return 0

    data.setdefault("hooks", {}).setdefault("PreToolUse", []).append(_rtk_hook_block())
    _atomic_write_json(target, data)
    print(f"RTK hook enabled in {target}. Run `coworker rtk status` to verify.")
    return 0


def cmd_disable(
    args: argparse.Namespace | None = None,
    *,
    config_path: Path | None = None,
) -> int:
    """Filter out every marker-tagged hook entry. No-op if none present."""
    target = _resolve_config_path(config_path)

    if not target.exists():
        print(f"{target} does not exist — nothing to disable.")
        return 0

    try:
        data = _load_settings(target)
    except RuntimeError as e:
        print(f"[coworker rtk] ERROR: {e}", file=sys.stderr)
        return 1

    before = _count_markers(data)
    if before == 0:
        print(f"RTK hook not enabled in {target} — nothing to disable.")
        return 0

    data = _filter_out_marker(data)
    _atomic_write_json(target, data)
    print(f"RTK hook disabled in {target} (removed {before} marker block(s)).")
    return 0


def cmd_status(
    args: argparse.Namespace | None = None,
    *,
    config_path: Path | None = None,
) -> int:
    """Report rtk binary state + hook state. Always exits 0 (fail-soft)."""
    target = _resolve_config_path(config_path)
    binary = _rtk_binary_path()
    version = _rtk_version() if binary else None

    print("Coworker × RTK plugin status")
    print("----------------------------")
    if binary:
        print(f"  rtk binary:  {binary}")
        print(f"  rtk version: {version or 'unknown'}")
    else:
        print(
            "  rtk binary:  not installed (run `coworker rtk install` for instructions)",
            file=sys.stderr,
        )

    if target.exists():
        try:
            data = _load_settings(target)
            marker_count = _count_markers(data)
            print(f"  settings:    {target}")
            print(f"  RTK hook:    {'enabled' if marker_count >= 1 else 'disabled'}")
            if marker_count > 1:
                print(
                    f"  WARNING: {marker_count} RTK marker blocks found — run "
                    "`coworker rtk disable && coworker rtk enable` to normalise.",
                    file=sys.stderr,
                )
        except RuntimeError as e:
            print(f"  settings:    {target} (INVALID JSON: {e})", file=sys.stderr)
    else:
        print(f"  settings:    {target} (does not exist yet — Claude Code not configured)")
        print("  RTK hook:    disabled")
    return 0


# ---------- argparse registration ----------


def register(subparsers: argparse._SubParsersAction) -> None:
    """Wire `coworker rtk {install,enable,disable,status}` into a parent parser."""
    p_rtk = subparsers.add_parser(
        "rtk",
        help="Manage the optional Rust Token Killer (RTK) plugin.",
        description=(
            "Coworker × RTK opt-in plugin. Manages a marker-tagged Bash hook in "
            "~/.claude/settings.json that pipes shell tool outputs through `rtk` "
            "before they reach Claude Code's context. See docs/rtk-plugin.md."
        ),
    )
    rtk_sub = p_rtk.add_subparsers(dest="rtk_action", required=True)

    p_install = rtk_sub.add_parser("install", help="Print OS-specific RTK install instructions.")
    p_install.set_defaults(rtk_handler=cmd_install)

    p_enable = rtk_sub.add_parser("enable", help="Register the RTK hook in Claude Code settings.")
    p_enable.add_argument(
        "--config-path",
        default=None,
        help="Override Claude Code settings.json path (default: ~/.claude/settings.json).",
    )
    p_enable.set_defaults(rtk_handler=cmd_enable)

    p_disable = rtk_sub.add_parser("disable", help="Remove the RTK hook (filter by marker).")
    p_disable.add_argument(
        "--config-path",
        default=None,
        help="Override Claude Code settings.json path (default: ~/.claude/settings.json).",
    )
    p_disable.set_defaults(rtk_handler=cmd_disable)

    p_status = rtk_sub.add_parser("status", help="Report rtk binary + hook state.")
    p_status.add_argument(
        "--config-path",
        default=None,
        help="Override Claude Code settings.json path (default: ~/.claude/settings.json).",
    )
    p_status.set_defaults(rtk_handler=cmd_status)


def dispatch(args: argparse.Namespace) -> int:
    """Called by coworker.cli.main(). Routes to the chosen rtk_action handler."""
    handler = getattr(args, "rtk_handler", None)
    if handler is None:
        print("[coworker rtk] internal error: no handler bound", file=sys.stderr)
        return 1
    kwargs = {}
    cfg = getattr(args, "config_path", None)
    if cfg is not None:
        kwargs["config_path"] = Path(cfg)
    return handler(args, **kwargs) if kwargs else handler(args)
