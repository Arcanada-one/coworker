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
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import rtk_codex_shims, rtk_cursor_hook, rtk_passthrough

# Marker contract — keep these as the public stable surface; tests pin both.
COWORKER_RTK_MARKER = "_managed_by"
COWORKER_RTK_MARKER_VALUE = "coworker-rtk"
COWORKER_RTK_VERSION_KEY = "_version"
# v2: hook command wraps `rtk hook claude` with a signal-vs-bulk
# passthrough guard. Operators upgrading from v1 retain their data; the
# block is rewritten on the next `coworker rtk enable`.
COWORKER_RTK_VERSION = 2

# Guard wrapper — vendored bash script copied into PATH on enable.
RTK_GUARD_FILENAME = "rtk-signal-guard.sh"
RTK_GUARD_INSTALL_PATH = Path.home() / ".local" / "bin" / RTK_GUARD_FILENAME
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


def _vendored_guard_path() -> Path:
    """Path to the bundled guard inside the installed package.

    On-disk filename uses Python's underscore convention; the operator-facing
    install filename (``RTK_GUARD_FILENAME``) keeps the kebab-case used
    elsewhere in the rtk plugin surface.
    """
    return Path(__file__).resolve().parent / "rtk_signal_guard.sh"


def _install_guard(install_path: Path | None = None) -> Path:
    """Copy the vendored guard into the operator's PATH. Returns the install path.

    Always overwrites — guard semantics are owned by the plugin version, not
    by accumulated operator edits.
    """
    src = _vendored_guard_path()
    if not src.exists():
        raise RuntimeError(f"vendored guard missing: {src}")
    dst = Path(install_path) if install_path is not None else RTK_GUARD_INSTALL_PATH
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(0o755)
    return dst


def _remove_guard(install_path: Path | None = None) -> bool:
    """Remove the installed guard if present. Idempotent."""
    dst = Path(install_path) if install_path is not None else RTK_GUARD_INSTALL_PATH
    if dst.exists():
        dst.unlink()
        return True
    return False


def _rtk_hook_block(guard_path: Path | None = None) -> dict:
    """Build the PreToolUse hook block. The command points at the guard
    wrapper (which forwards to `rtk hook claude` for bulk commands and
    short-circuits for signal commands).
    """
    cmd_path = str(guard_path) if guard_path is not None else str(RTK_GUARD_INSTALL_PATH)
    return {
        "matcher": RTK_HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": cmd_path,
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


_METHOD_LINES = {
    "brew": "  macOS:   brew install rtk",
    "curl": "  Linux:   curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh",
    "cargo": "  Any OS:  cargo install --git https://github.com/rtk-ai/rtk   # requires `cargo` in PATH",
    "manual": (
        "  Manual:  download rtk-<target>.zip from https://github.com/rtk-ai/rtk/releases,\n"
        "           extract, place `rtk` on PATH."
    ),
}


def cmd_install(args: argparse.Namespace | None = None) -> int:
    """Print OS-specific RTK install instructions. Never executes installers.

    Honours `args.method` (one of brew/curl/cargo/manual) to override the
    OS-default branch. `args.dry_run` is currently always True (print-only);
    the flag exists for future exec-mode opt-in.
    """
    method = getattr(args, "method", None)
    print("RTK install instructions (coworker does not install binaries itself):\n")
    if method:
        print(_METHOD_LINES[method])
        print("\nAfter install: `coworker rtk enable` to register the Claude Code hook.")
        return 0
    osname = _detect_os()
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
    guard_install_path: Path | None = None,
    passthrough_store_path: Path | None = None,
) -> int:
    """Install signal-vs-bulk guard wrapper, register it in settings.json,
    seed the passthrough allowlist with defaults. Idempotent.

    Fail-soft: if rtk binary missing, print WARN and continue (hook still
    written; operator may install rtk later).
    """
    target = _resolve_config_path(config_path)

    if _rtk_binary_path() is None:
        print(
            "[coworker rtk] WARN: rtk binary not found on PATH. Hook will be registered "
            "but won't run until rtk is installed. See `coworker rtk install`.",
            file=sys.stderr,
        )

    # Install vendored guard before the settings.json write — if guard
    # install fails, we want settings.json untouched.
    try:
        guard_path = _install_guard(guard_install_path)
    except (OSError, RuntimeError) as e:
        print(f"[coworker rtk] ERROR: guard install failed: {e}", file=sys.stderr)
        return 1
    print(f"Signal/bulk guard installed at {guard_path}.")

    # Seed passthrough allowlist with defaults (idempotent — operator
    # additions preserved across re-enable).
    if rtk_passthrough.seed_default(store_path=passthrough_store_path):
        store = rtk_passthrough._store_path(passthrough_store_path)
        print(f"Passthrough allowlist seeded with {len(rtk_passthrough.DEFAULT_PATTERNS)} defaults at {store}.")
    else:
        store = rtk_passthrough._store_path(passthrough_store_path)
        print(f"Passthrough allowlist already present at {store} ({rtk_passthrough.count(store_path=passthrough_store_path)} patterns).")

    try:
        data = _load_settings(target)
    except RuntimeError as e:
        print(f"[coworker rtk] ERROR: {e}", file=sys.stderr)
        return 1

    # v1 block (bare `rtk hook claude` command) must be replaced by the
    # v2 guard-wrapper block. Filter out any prior marker-tagged entries
    # before appending the new one.
    if _count_markers(data) >= 1:
        data = _filter_out_marker(data)

    data.setdefault("hooks", {}).setdefault("PreToolUse", []).append(_rtk_hook_block(guard_path))
    _atomic_write_json(target, data)
    print(f"RTK hook (guard wrapper) registered in {target}.")

    # Codex CLI parity layer (PATH-shim — Codex 0.x does not exec user hooks).
    print()
    rtk_codex_shims.enable_codex_parity()

    # Cursor CLI parity layer (native beforeShellExecution hook in ~/.cursor/hooks.json).
    print()
    rtk_cursor_hook.enable_cursor_parity()

    print("\nRun `coworker rtk status` to verify.")
    return 0


def cmd_disable(
    args: argparse.Namespace | None = None,
    *,
    config_path: Path | None = None,
    guard_install_path: Path | None = None,
) -> int:
    """Filter out every marker-tagged hook entry + remove guard wrapper.

    The passthrough allowlist is preserved (operator data) — only `--reset-defaults`
    on `coworker rtk passthrough` rewinds it.
    """
    target = _resolve_config_path(config_path)

    settings_changed = False
    if target.exists():
        try:
            data = _load_settings(target)
        except RuntimeError as e:
            print(f"[coworker rtk] ERROR: {e}", file=sys.stderr)
            return 1
        before = _count_markers(data)
        if before > 0:
            data = _filter_out_marker(data)
            _atomic_write_json(target, data)
            print(f"RTK hook disabled in {target} (removed {before} marker block(s)).")
            settings_changed = True
    if not settings_changed:
        print(f"RTK hook not enabled in {target} — nothing to filter.")

    # Remove vendored guard wrapper.
    if _remove_guard(guard_install_path):
        dst = Path(guard_install_path) if guard_install_path else RTK_GUARD_INSTALL_PATH
        print(f"Signal/bulk guard removed at {dst}.")

    # Tear down Codex parity layer.
    print()
    rtk_codex_shims.disable_codex_parity()

    # Tear down Cursor parity layer.
    print()
    rtk_cursor_hook.disable_cursor_parity()
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

    print(f"  telemetry:   {_rtk_telemetry_state(binary)}")

    # Signal/bulk passthrough store summary.
    store_path = getattr(args, "passthrough_store_path", None)
    store_path = Path(store_path) if store_path else None
    pass_count = rtk_passthrough.count(store_path=store_path)
    pass_path = rtk_passthrough._store_path(store_path)
    print(f"  passthrough patterns: {pass_count} (store: {pass_path})")
    # Warn when allowlist is configured but guard wrapper is not in
    # settings.json — operator probably ran `coworker rtk disable` but kept
    # custom patterns; the patterns will not apply until next `enable`.
    if pass_count > 0 and target.exists():
        try:
            _data_now = _load_settings(target)
            if _count_markers(_data_now) == 0:
                print(
                    "  WARNING: passthrough patterns configured but guard not installed; "
                    "run `coworker rtk enable`.",
                    file=sys.stderr,
                )
        except RuntimeError:
            pass

    # Cross-agent parity matrix.
    cx = rtk_codex_shims.status()
    cur = rtk_cursor_hook.status()
    print()
    print("  agent parity (read marker in Claude settings.json):")
    print(f"    Claude:   {'enabled' if target.exists() and _count_markers(_load_settings(target) if target.exists() else {}) >= 1 else 'disabled'}")
    print(
        f"    Cursor:   {'enabled' if cur['hook_present'] else 'disabled'}"
        f" (hooks.json {cur['event']} -> rtk hook cursor)"
    )
    print(
        f"    Codex:    {'enabled' if cx['shims_present'] and cx['codex_block_present'] else 'disabled'}"
        f" (shims={cx['shim_files_count']}, codex_config={'patched' if cx['codex_block_present'] else 'clean'})"
    )
    return 0


_TELEMETRY_ENABLED_RE = re.compile(r"^\s*enabled:\s*(\w+)\s*$", re.MULTILINE)


def _rtk_telemetry_state(binary: str | None) -> str:
    """Probe `rtk telemetry status` and return enabled|disabled|unavailable.

    Fail-soft: any failure (missing binary, timeout, non-zero exit, unparseable
    output) returns a human-readable `unavailable (<reason>)` string. Never
    raises; caller's exit code stays 0.
    """
    if binary is None:
        return "unavailable (rtk missing)"
    try:
        r = subprocess.run(
            [binary, "telemetry", "status"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "unavailable (timeout > 2s)"
    except OSError as e:
        return f"unavailable (exec error: {e.__class__.__name__})"
    if r.returncode != 0:
        return f"unavailable (rtk exit {r.returncode})"
    m = _TELEMETRY_ENABLED_RE.search(r.stdout)
    if not m:
        return "unavailable (unparseable output)"
    return "enabled" if m.group(1).lower() == "yes" else "disabled"


# ---------- argparse registration ----------


def _add_config_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config-path",
        default=None,
        help="Override Claude Code settings.json path (default: ~/.claude/settings.json).",
    )


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
    p_install.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print install command without executing (default; coworker never runs installers).",
    )
    p_install.add_argument(
        "--method",
        choices=["brew", "curl", "cargo", "manual"],
        default=None,
        help="Override OS-default install method. 'cargo' requires `cargo` in PATH.",
    )
    p_install.set_defaults(rtk_handler=cmd_install)

    p_enable = rtk_sub.add_parser("enable", help="Register the RTK hook in Claude Code settings.")
    _add_config_path(p_enable)
    p_enable.set_defaults(rtk_handler=cmd_enable)

    p_disable = rtk_sub.add_parser("disable", help="Remove the RTK hook (filter by marker).")
    _add_config_path(p_disable)
    p_disable.set_defaults(rtk_handler=cmd_disable)

    p_status = rtk_sub.add_parser("status", help="Report rtk binary + hook state.")
    _add_config_path(p_status)
    p_status.set_defaults(rtk_handler=cmd_status)

    # Signal/bulk passthrough allowlist subcommand tree.
    rtk_passthrough.register_passthrough(rtk_sub)


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
