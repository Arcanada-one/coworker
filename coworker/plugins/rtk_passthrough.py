"""Passthrough allowlist store for the RTK plugin.

The store is consulted by the `rtk-signal-guard.sh` PreToolUse wrapper and
by the Codex shim layer to decide whether a tool invocation is *signal*
(passthrough — emit RAW stdout to the agent) or *bulk* (delegate to
`rtk hook claude`/`rtk <cmd>` for token reduction).

Single source of truth: ``~/.config/coworker/rtk-passthrough.json``.
Schema v1 — single top-level array of substring patterns:

    {"patterns": ["git push", "gh pr", ...]}

Match algorithm at runtime is substring (`grep -F`) — see
``rtk_signal_guard.sh``. Patterns are case-sensitive.

The store survives `coworker rtk disable` so operator-added entries are
not lost on a temporary teardown. Only `--reset-defaults` rewinds it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Canonical default allowlist — git/gh commands whose output is a control
# signal (single-line markers, short status, structured headers) that
# agents read to make next-step decisions. Order is install order; the
# guard sorts before emit. Substring match — `git push` covers
# `git push -u origin <branch>`, `git push --force-with-lease`, etc.
DEFAULT_PATTERNS: tuple[str, ...] = (
    "git push",
    "git pull",
    "git fetch",
    "git merge",
    "git status",
    "git remote",
    "git rev-parse",
    "git branch",
    "gh pr",
    "gh issue",
    "gh release",
    "gh api",
    "gh run",
)

DEFAULT_STORE_PATH: Path = Path.home() / ".config" / "coworker" / "rtk-passthrough.json"

# Env-var override exists for testability and operator XDG_CONFIG_HOME setups.
_ENV_STORE_PATH = "COWORKER_RTK_PASSTHROUGH_PATH"


# ---------- path resolution ----------


def _store_path(override: Path | None = None) -> Path:
    """Resolve the store path.

    Precedence: explicit ``override`` arg > ``COWORKER_RTK_PASSTHROUGH_PATH``
    env var > ``DEFAULT_STORE_PATH``.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get(_ENV_STORE_PATH)
    if env:
        return Path(env)
    return DEFAULT_STORE_PATH


# ---------- IO helpers ----------


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".rtk-passthrough.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _load_raw(path: Path) -> list[str]:
    """Read patterns from disk. Returns empty list when absent/invalid.

    Never raises — fail-safe so the guard always has a list to work with.
    Invalid JSON or wrong shape ⇒ stderr warning + empty list (which causes
    the guard to fall back to DEFAULT_PATTERNS).
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text() or "{}")
    except (OSError, json.JSONDecodeError) as e:
        print(f"[coworker rtk] WARN: passthrough store unreadable ({e}); using defaults", file=sys.stderr)
        return []
    if not isinstance(data, dict):
        print("[coworker rtk] WARN: passthrough store wrong shape; using defaults", file=sys.stderr)
        return []
    patterns = data.get("patterns")
    if not isinstance(patterns, list):
        return []
    return [p for p in patterns if isinstance(p, str) and p.strip()]


# ---------- public API ----------


def load_patterns(*, store_path: Path | None = None) -> list[str]:
    """Return the current allowlist, sorted, deduped.

    When the store is absent or empty, returns ``DEFAULT_PATTERNS`` — the
    guard never sees an empty list under normal operation.
    """
    raw = _load_raw(_store_path(store_path))
    if not raw:
        return sorted(set(DEFAULT_PATTERNS))
    return sorted(set(raw))


def seed_default(*, store_path: Path | None = None, force: bool = False) -> bool:
    """Create store seeded with ``DEFAULT_PATTERNS`` if it does not exist.

    Idempotent: returns ``False`` when the store already has any patterns
    (preserving operator-added entries on re-enable). ``force=True`` rewinds
    the store to the canonical defaults regardless of current content.
    """
    path = _store_path(store_path)
    if not force and path.exists() and _load_raw(path):
        return False
    _atomic_write_json(path, {"patterns": sorted(set(DEFAULT_PATTERNS))})
    return True


def add_pattern(pattern: str, *, store_path: Path | None = None) -> bool:
    """Add a pattern. Returns ``True`` if newly added, ``False`` if duplicate.

    Empty/whitespace-only patterns rejected with stderr warning + return
    ``False`` (operator typo guard; not an error path).
    """
    p = pattern.strip()
    if not p:
        print("[coworker rtk] WARN: empty pattern ignored", file=sys.stderr)
        return False
    path = _store_path(store_path)
    current = _load_raw(path) or list(DEFAULT_PATTERNS)
    if p in current:
        return False
    current.append(p)
    _atomic_write_json(path, {"patterns": sorted(set(current))})
    return True


def remove_pattern(pattern: str, *, store_path: Path | None = None) -> bool:
    """Remove a pattern. Soft-fail (return ``False`` + warn) if absent."""
    p = pattern.strip()
    path = _store_path(store_path)
    current = _load_raw(path) or list(DEFAULT_PATTERNS)
    if p not in current:
        print(f"[coworker rtk] WARN: pattern not in store: {p!r}", file=sys.stderr)
        return False
    current = [c for c in current if c != p]
    _atomic_write_json(path, {"patterns": sorted(set(current))})
    return True


def list_patterns(*, store_path: Path | None = None) -> list[str]:
    """Return patterns as they should be displayed (sorted, deduped)."""
    return load_patterns(store_path=store_path)


def count(*, store_path: Path | None = None) -> int:
    return len(load_patterns(store_path=store_path))


# ---------- CLI dispatch ----------


def cmd_passthrough(args: argparse.Namespace) -> int:
    """Handle `coworker rtk passthrough {add,remove,list}`."""
    action = getattr(args, "passthrough_action", None)
    store_path = getattr(args, "store_path", None)
    store_path = Path(store_path) if store_path else None

    if action == "list":
        for p in list_patterns(store_path=store_path):
            print(p)
        return 0
    if action == "add":
        added = add_pattern(args.pattern, store_path=store_path)
        if added:
            print(f"added: {args.pattern}")
        else:
            print(f"already present: {args.pattern}")
        return 0
    if action == "remove":
        removed = remove_pattern(args.pattern, store_path=store_path)
        # Soft-fail: exit 0 even when pattern absent (idempotent UX).
        if removed:
            print(f"removed: {args.pattern}")
        return 0
    print("[coworker rtk passthrough] internal error: no action bound", file=sys.stderr)
    return 1


def register_passthrough(rtk_sub: argparse._SubParsersAction) -> None:
    """Wire `coworker rtk passthrough {add,remove,list}` under the rtk parser."""
    p_pass = rtk_sub.add_parser(
        "passthrough",
        help="Manage the signal/bulk passthrough allowlist (git/gh control-marker commands).",
        description=(
            "Patterns added here are substring-matched against the Bash tool "
            "command. Matches bypass `rtk hook claude` (raw stdout reaches the "
            "agent). Defaults cover git/gh control-signal commands."
        ),
    )
    p_pass.add_argument(
        "--store-path",
        default=None,
        help="Override passthrough store path (default: ~/.config/coworker/rtk-passthrough.json).",
    )
    pass_sub = p_pass.add_subparsers(dest="passthrough_action", required=True)

    p_list = pass_sub.add_parser("list", help="Print current allowlist (one pattern per line, sorted).")
    p_list.set_defaults(rtk_handler=cmd_passthrough)

    p_add = pass_sub.add_parser("add", help="Add a substring pattern to the allowlist.")
    p_add.add_argument("pattern", help="Substring pattern (e.g. 'glab mr', 'git tag').")
    p_add.set_defaults(rtk_handler=cmd_passthrough)

    p_remove = pass_sub.add_parser("remove", help="Remove a substring pattern from the allowlist.")
    p_remove.add_argument("pattern", help="Exact pattern to remove.")
    p_remove.set_defaults(rtk_handler=cmd_passthrough)
