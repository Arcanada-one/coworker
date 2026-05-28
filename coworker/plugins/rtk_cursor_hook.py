"""Cursor CLI parity for the RTK plugin — native Cursor Agent hook.

Unlike Codex (no hook surface → PATH-shim in login profiles), cursor-agent
exposes a native hooks API. A `beforeShellExecution` entry in
`~/.cursor/hooks.json` receives every shell command the agent runs as JSON on
stdin and may rewrite it. `rtk hook cursor` is the processor: it rewrites bulk
commands to `rtk <cmd>` (compacted output) and lets signal commands pass
through. This was verified live — inside cursor-agent, `ls -la /tmp` returns
the rtk-compacted form rather than raw `ls` output.

Design contract:

1. We manage ONLY a single `beforeShellExecution` entry whose command runs the
   rtk cursor processor. The operator's other hooks (any event, including other
   `beforeShellExecution` entries) are preserved across enable/disable.
2. The rtk binary is resolved at enable time and the absolute path is written
   into the hook command, so cursor-agent's own PATH cannot shadow it. If rtk
   is not on PATH we fall back to the bare `rtk hook cursor` form.
3. This needs NO shell-rc mutation. cursor-agent builds its own PATH and runs a
   login shell, so a PATH-shim in `~/.zshenv`/`~/.zprofile` does not survive to
   command execution; the native hook is the only reliable surface — and it is
   scoped entirely to cursor, with zero global-PATH or interactive-shell risk.
4. `coworker rtk disable` removes our entry and leaves the file (and any
   operator hooks) intact. A malformed hooks.json is treated as empty
   (fail-soft) rather than crashing enable/disable.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

CURSOR_HOOKS: Path = Path.home() / ".cursor" / "hooks.json"
HOOK_EVENT = "beforeShellExecution"
# Identifies our managed entry regardless of how rtk is spelled (bare vs
# absolute path) so idempotency and removal survive an rtk-path change.
_HOOK_MARKER = "hook cursor"


def _rtk_binary_path() -> str | None:
    return shutil.which("rtk")


def _hook_command() -> str:
    rtk = _rtk_binary_path()
    return f"{rtk} hook cursor" if rtk else "rtk hook cursor"


def _is_rtk_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("command"), str)
        and _HOOK_MARKER in entry["command"]
        and "rtk" in entry["command"]
    )


def _load(path: Path) -> dict:
    """Load hooks.json. Missing or malformed → empty dict (fail-soft)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def _entries(data: dict) -> list:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    entries = hooks.get(HOOK_EVENT)
    return entries if isinstance(entries, list) else []


def enable_cursor_parity(*, verbose: bool = True) -> int:
    """Register the `beforeShellExecution` → rtk hook in ~/.cursor/hooks.json.

    Idempotent: an existing rtk entry is migrated in-place to the current
    (absolute-path) command form; operator hooks are preserved.
    """
    if verbose:
        print("Cursor parity setup:")
    data = _load(CURSOR_HOOKS)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    entries = hooks.get(HOOK_EVENT)
    if not isinstance(entries, list):
        entries = hooks[HOOK_EVENT] = []

    desired = _hook_command()
    # Drop any prior rtk entry (migrate), keep operator entries.
    kept = [e for e in entries if not _is_rtk_entry(e)]
    already_current = len(kept) == len(entries) - 1 and any(
        _is_rtk_entry(e) and e.get("command") == desired for e in entries
    )
    kept.append({"command": desired})
    hooks[HOOK_EVENT] = kept
    _atomic_write_json(CURSOR_HOOKS, data)
    if verbose:
        verb = "already current" if already_current else "registered"
        print(f"  ~/.cursor/hooks.json: {HOOK_EVENT} → `{desired}` {verb}")
    return 0


def disable_cursor_parity(*, verbose: bool = True) -> int:
    """Remove our rtk entry from ~/.cursor/hooks.json. Idempotent."""
    if verbose:
        print("Cursor parity teardown:")
    data = _load(CURSOR_HOOKS)
    entries = _entries(data)
    if not any(_is_rtk_entry(e) for e in entries):
        if verbose:
            print("  ~/.cursor/hooks.json: rtk hook not present (nothing to remove)")
        return 0
    remaining = [e for e in entries if not _is_rtk_entry(e)]
    if remaining:
        data["hooks"][HOOK_EVENT] = remaining
    else:
        del data["hooks"][HOOK_EVENT]
    _atomic_write_json(CURSOR_HOOKS, data)
    if verbose:
        print(f"  ~/.cursor/hooks.json: removed {HOOK_EVENT} → rtk hook")
    return 0


def status() -> dict:
    """Snapshot of current Cursor parity state for `coworker rtk status`."""
    data = _load(CURSOR_HOOKS)
    return {
        "hooks_file": str(CURSOR_HOOKS),
        "hook_present": any(_is_rtk_entry(e) for e in _entries(data)),
        "event": HOOK_EVENT,
    }
