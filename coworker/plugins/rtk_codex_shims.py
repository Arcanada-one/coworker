"""Codex CLI parity for the RTK plugin.

The `rtk` upstream binary supports `rtk hook claude|cursor|gemini|copilot`
but does not yet ship a `rtk hook codex` handler, and stable Codex CLI does
not currently execute user-defined `PreToolUse` hooks anyway. Until both
land we close the parity gap with a narrowly scoped PATH-shim that lives
only inside the Codex agent process — never in the operator's interactive
shell.

Design contract:

1. Shim directory is user-owned and `0o700`. The directory + every shim
   file refuses to install into a world-writable location and refuses to
   overwrite a non-shim file at the same path.
2. Each shim resolves the real target binary at INSTALL TIME (via
   `command -v` while excluding the shim dir). The resolved absolute path
   is hard-coded into the shim body. Runtime PATH manipulation cannot
   cause shim-in-shim recursion or hijack the wrapper.
3. PATH override is injected into login-shell profiles (`~/.zprofile`,
   `~/.bash_profile`) inside a marker-fenced block, BUT gated on a
   Codex-only PATH marker (`/.codex/tmp/arg0/codex-arg0XXX`) that Codex
   injects into the child shell's PATH before sourcing rc files. Interactive
   Terminal, IDE-embedded shells, Spotlight, cron — none see that marker,
   so the export is a no-op for them. This is the v0.5.0 design (post
   TUNE-0317 dogfood incident); v0.4.x emitted an unconditional `export
   PATH=...` which polluted every shell on the box and could hang macOS
   when interactive `ls`/`grep` etc. cascaded through `rtk`.
4. Single source of truth for on/off is the existing `_managed_by:
   coworker-rtk` marker in `~/.claude/settings.json`. Shims read that
   marker at runtime — `coworker rtk disable` flips Claude+Codex+Cursor
   simultaneously without touching the shim dir.
5. Enable prints every file written and every config key touched.
   Disable verifies removal and prints what it removed.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from pathlib import Path

# Commands that rtk wraps and that shadow real binaries on PATH. Kept
# narrow on purpose — only the ones with bulky token output that an
# AI-agent shell tool routinely calls. Order is install order; runtime
# probe handles missing real binaries gracefully.
SHIM_COMMANDS: tuple[str, ...] = (
    "ls",
    "tree",
    "git",
    "find",
    "grep",
    "diff",
    "gh",
    "glab",
    "psql",
    "pnpm",
    "docker",
    "kubectl",
    "wget",
)

SHIM_DIR: Path = Path.home() / ".local" / "share" / "rtk-shims"
CODEX_CONFIG: Path = Path.home() / ".codex" / "config.toml"
# Login-shell profiles. macOS Codex spawns `/bin/zsh -lc`, Linux Codex usually
# `/bin/bash -lc`. We patch whichever exists so the same install path covers
# both. The marker block is the same shape so disable is a single regex strip.
ZPROFILE: Path = Path.home() / ".zprofile"
BASH_PROFILE: Path = Path.home() / ".bash_profile"
MARKER_BEGIN = "# >>> coworker-rtk-codex-shims (managed) >>>"
MARKER_END = "# <<< coworker-rtk-codex-shims (managed) <<<"


def _resolve_real_binary(cmd: str) -> str | None:
    """Return absolute path to `cmd` BEFORE the shim dir hijacks it.

    We strip the shim dir from PATH so a re-run of `enable` still finds
    the real binary if the shim is already on PATH.
    """
    parts = [
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and Path(p).resolve() != SHIM_DIR.resolve()
    ]
    clean_path = os.pathsep.join(parts)
    found = shutil.which(cmd, path=clean_path)
    return found


# Shim commands that emit control-signal output (canonical markers agents
# parse to make next-step decisions). For these, the shim consults the
# passthrough allowlist before delegating to rtk — match ⇒ exec real binary,
# no match ⇒ exec rtk for token reduction. Other shims always go through rtk.
_PASSTHROUGH_AWARE_CMDS: frozenset[str] = frozenset({"git", "gh"})


def _passthrough_snippet(cmd: str) -> str:
    """Bash snippet that scans the reconstructed command line against the
    passthrough allowlist. Inserted between marker-probe and rtk exec for
    signal-aware shims only. Substring match, falls back to embedded
    defaults when the store is absent or unparseable.
    """
    return f"""# Signal/bulk passthrough check (jq-based; fallback to embedded defaults).
PASSTHROUGH_STORE="${{COWORKER_RTK_PASSTHROUGH_PATH:-$HOME/.config/coworker/rtk-passthrough.json}}"
DEFAULT_PASSTHROUGH='git push
git pull
git fetch
git merge
git status
git remote
git rev-parse
git branch
gh pr
gh issue
gh release
gh api
gh run'
FULL_CMDLINE={cmd!r}' '"$*"
if command -v jq >/dev/null 2>&1 && [ -f "$PASSTHROUGH_STORE" ]; then
    _patterns=$(jq -re '.patterns[]?' "$PASSTHROUGH_STORE" 2>/dev/null || echo "$DEFAULT_PASSTHROUGH")
    [ -z "$_patterns" ] && _patterns="$DEFAULT_PASSTHROUGH"
else
    _patterns="$DEFAULT_PASSTHROUGH"
fi
while IFS= read -r _pat; do
    [ -z "$_pat" ] && continue
    case "$FULL_CMDLINE" in
        *"$_pat"*) exec "$REAL_BIN" "$@" ;;
    esac
done <<PASSTHROUGH_EOF
$_patterns
PASSTHROUGH_EOF
"""


def _shim_body(cmd: str, real_binary: str, rtk_binary: str) -> str:
    """Render shim body. Real binary resolved at install time.

    Behaviour at runtime:
      - Recursion guard via `_COWORKER_RTK_SHIM_ACTIVE` env var (rtk
        internally calls the real binary; this prevents fork-bomb).
      - Probe Claude settings.json marker — single on/off source of truth.
      - If marker absent OR rtk binary missing — exec real binary.
      - For signal-aware shims (git, gh): consult passthrough allowlist;
        match ⇒ exec real binary (raw stdout reaches the agent).
      - Otherwise — exec `rtk <cmd> "$@"`.
    """
    passthrough = _passthrough_snippet(cmd) if cmd in _PASSTHROUGH_AWARE_CMDS else ""
    return f"""#!/bin/bash
# Coworker RTK shim for `{cmd}` — Codex CLI parity layer.
# Managed by `coworker rtk enable/disable`. Do not hand-edit.
# Real binary path resolved at install time; runtime PATH cannot hijack it.

set -u

REAL_BIN={real_binary!r}
RTK_BIN={rtk_binary!r}
GREP_BIN='/usr/bin/grep'
MARKER_FILE="$HOME/.claude/settings.json"

# Recursion guard (rtk wraps real binary internally).
if [ -n "${{_COWORKER_RTK_SHIM_ACTIVE:-}}" ]; then
    exec "$REAL_BIN" "$@"
fi

# On/off probe — Claude settings.json marker is single source of truth.
if [ ! -x "$GREP_BIN" ] || [ ! -f "$MARKER_FILE" ] || ! "$GREP_BIN" -q '_managed_by.*coworker-rtk' "$MARKER_FILE" 2>/dev/null; then
    exec "$REAL_BIN" "$@"
fi

# rtk binary must be executable.
if [ ! -x "$RTK_BIN" ]; then
    exec "$REAL_BIN" "$@"
fi

{passthrough}
export _COWORKER_RTK_SHIM_ACTIVE=1
exec "$RTK_BIN" {cmd!r} "$@"
"""


def _rtk_binary_path() -> str | None:
    out = shutil.which("rtk")
    return out


def _check_dir_perms(p: Path) -> str | None:
    """Return error string if directory perms are unsafe; else None."""
    if not p.exists():
        return None
    st = p.stat()
    mode = stat.S_IMODE(st.st_mode)
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return (
            f"refusing to use world/group-writable directory {p} "
            f"(mode {oct(mode)}). Fix with: chmod 700 {p}"
        )
    if st.st_uid != os.geteuid():
        return (
            f"refusing to use directory {p} owned by uid={st.st_uid} "
            f"(current uid={os.geteuid()})."
        )
    return None


def _is_shim_file(p: Path) -> bool:
    if not p.exists() or p.is_dir():
        return False
    try:
        head = p.read_text(errors="replace").splitlines()[:3]
    except Exception:
        return False
    return any("Coworker RTK shim" in line for line in head)


def install_shims(*, verbose: bool = True) -> tuple[int, list[str]]:
    """Install shim dir + each shim file. Idempotent.

    Returns (count_written, touched_files).
    """
    touched: list[str] = []

    rtk_bin = _rtk_binary_path()
    if rtk_bin is None:
        raise RuntimeError(
            "rtk binary not found on PATH. Run `coworker rtk install` first."
        )

    # Refuse to use a pre-existing dir with unsafe perms. We only chmod
    # dirs we created ourselves.
    if SHIM_DIR.exists():
        err = _check_dir_perms(SHIM_DIR)
        if err:
            raise RuntimeError(err)
    else:
        SHIM_DIR.mkdir(parents=True, exist_ok=False)
    SHIM_DIR.chmod(0o700)

    written = 0
    for cmd in SHIM_COMMANDS:
        real = _resolve_real_binary(cmd)
        if real is None:
            if verbose:
                print(
                    f"  [skip] {cmd}: real binary not on PATH (shim not created)",
                    file=sys.stderr,
                )
            continue
        target = SHIM_DIR / cmd
        if target.exists() and not _is_shim_file(target):
            if verbose:
                print(
                    f"  [skip] {target}: exists but is not a managed shim — "
                    f"refusing to overwrite",
                    file=sys.stderr,
                )
            continue
        target.write_text(_shim_body(cmd, real, rtk_bin))
        target.chmod(0o755)
        touched.append(str(target))
        written += 1
        if verbose:
            print(f"  wrote shim: {target} -> rtk {cmd} -> {real}")
    return written, touched


def remove_shims(*, verbose: bool = True) -> tuple[int, list[str]]:
    """Remove every managed shim file. Idempotent. Returns (count, paths)."""
    touched: list[str] = []
    if not SHIM_DIR.exists():
        return 0, []
    removed = 0
    for entry in sorted(SHIM_DIR.iterdir()):
        if entry.is_file() and _is_shim_file(entry):
            entry.unlink()
            touched.append(str(entry))
            removed += 1
            if verbose:
                print(f"  removed shim: {entry}")
    # Try removing dir if empty.
    try:
        SHIM_DIR.rmdir()
        if verbose:
            print(f"  removed empty dir: {SHIM_DIR}")
    except OSError:
        pass
    return removed, touched


def _profile_block_text() -> str:
    """Marker-fenced shell profile block — Codex-scope-only PATH injection.

    The block gates the `export PATH=...` on a PATH-substring match for the
    Codex-only `arg0` directory (`/Users/.../.codex/tmp/arg0/codex-arg0XXX`).
    Codex injects this entry into the child shell's PATH BEFORE sourcing
    `.zprofile`/`.bash_profile`, so the gate fires only for codex-launched
    shells. Interactive Terminal sessions, IDE shells, Spotlight, cron — they
    never see that marker, so the export is a no-op there.

    Why not `[ -n "$CODEX_CI" ]`: empirically (TUNE-0317-fix, 2026-05-27),
    Codex sets CODEX_CI AFTER rc files run, so a CODEX_CI gate never fires
    from rc context. The `arg0` PATH entry is set before rc and is therefore
    the only reliable rc-time codex marker we have today.
    """
    return (
        f"{MARKER_BEGIN}\n"
        f"# coworker rtk Codex CLI parity. Activates ONLY inside codex-launched\n"
        f"# child shells (Codex injects /Users/.../.codex/tmp/arg0/codex-arg0XXX\n"
        f"# into PATH before sourcing rc files). Interactive Terminal, IDE, cron,\n"
        f"# Spotlight shells stay untouched — no recursion, no global PATH invasion.\n"
        f"# Removed by `coworker rtk disable`.\n"
        f'case ":$PATH:" in\n'
        f'    *":/Users/"*"/.codex/tmp/arg0/codex-arg0"*)\n'
        f'        if [ -d "{SHIM_DIR}" ]; then\n'
        f'            export PATH="{SHIM_DIR}:$PATH"\n'
        f"        fi\n"
        f"        ;;\n"
        f"esac\n"
        f"{MARKER_END}\n"
    )


def _profile_targets() -> list[Path]:
    """Return existing login-shell profiles to patch. We patch only files
    that already exist so we don't create unfamiliar dotfiles in $HOME.
    `~/.zprofile` is also created on macOS if absent because Codex on
    macOS unconditionally spawns zsh login shells — without the file the
    parity layer wouldn't work at all.
    """
    targets: list[Path] = []
    if sys.platform == "darwin" or ZPROFILE.exists():
        targets.append(ZPROFILE)
    if BASH_PROFILE.exists():
        targets.append(BASH_PROFILE)
    return targets


def inject_codex_path(*, verbose: bool = True) -> bool:
    """Add marker-fenced PATH prefix to login shell profile(s).

    Idempotent: re-running enable does not duplicate the block.
    """
    touched = False
    desired = _profile_block_text()
    block_re = re.compile(
        re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    for profile in _profile_targets():
        existing = profile.read_text() if profile.exists() else ""
        match = block_re.search(existing)
        if match:
            # Block present. Same as desired → no-op. Different (e.g. v0.4.x
            # unconditional `export PATH=...`) → in-place migrate to current
            # gated form. This is the upgrade path that retroactively fixes
            # operator boxes where a prior `coworker rtk enable` wrote the
            # broken block.
            if match.group(0).rstrip("\n") + "\n" == desired:
                if verbose:
                    print(f"  shell profile: shim block already current in {profile}")
                touched = True
                continue
            new = block_re.sub(desired, existing, count=1)
            profile.write_text(new)
            touched = True
            if verbose:
                print(f"  shell profile: shim block migrated in-place in {profile}")
            continue
        # Append at end so it runs AFTER any existing setup (e.g. path_helper
        # on macOS, which is sourced from /etc/zprofile before ~/.zprofile).
        new = (existing.rstrip() + "\n" if existing else "") + "\n" + desired
        profile.write_text(new)
        touched = True
        if verbose:
            print(f"  shell profile: shim block appended to {profile}")
    if not touched and verbose:
        print(
            "  [skip] no login-shell profile found (Codex on macOS needs ~/.zprofile)",
            file=sys.stderr,
        )
    return touched


def remove_codex_path(*, verbose: bool = True) -> bool:
    """Strip the marker-fenced block from all login profiles. Idempotent."""
    removed_any = False
    pattern = re.compile(
        re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    for profile in (ZPROFILE, BASH_PROFILE):
        if not profile.exists():
            continue
        text = profile.read_text()
        if not pattern.search(text):
            continue
        new = pattern.sub("", text)
        # Tidy: collapse any trailing blank-line spam we may have introduced.
        new = re.sub(r"\n{3,}", "\n\n", new).rstrip() + "\n"
        profile.write_text(new)
        removed_any = True
        if verbose:
            print(f"  shell profile: shim block removed from {profile}")
    if not removed_any and verbose:
        print("  shell profile: shim block not present (nothing to remove)")
    return removed_any


def status() -> dict:
    """Snapshot of current Codex parity state for `coworker rtk status`."""
    shims_present = (
        SHIM_DIR.exists() and any(SHIM_DIR.iterdir()) if SHIM_DIR.exists() else False
    )
    profile_block_present = False
    profile_targets = []
    for profile in (ZPROFILE, BASH_PROFILE):
        if profile.exists() and MARKER_BEGIN in profile.read_text():
            profile_block_present = True
            profile_targets.append(str(profile))
    rtk_bin = _rtk_binary_path()
    return {
        "shim_dir": str(SHIM_DIR),
        "shim_dir_present": SHIM_DIR.exists(),
        "shim_files_count": (
            sum(1 for f in SHIM_DIR.iterdir() if _is_shim_file(f))
            if SHIM_DIR.exists()
            else 0
        ),
        "shims_present": shims_present,
        "profile_patched": profile_block_present,
        "profile_targets": profile_targets,
        # Back-compat for callers expecting old keys (kept narrow).
        "codex_config": str(CODEX_CONFIG),
        "codex_block_present": profile_block_present,
        "rtk_binary": rtk_bin or "MISSING",
    }


def enable_codex_parity(*, verbose: bool = True) -> int:
    """Install shims + inject codex PATH. Returns 0 on success."""
    if verbose:
        print("Codex parity setup:")
    try:
        written, _ = install_shims(verbose=verbose)
    except RuntimeError as e:
        print(f"[coworker rtk codex] ERROR: {e}", file=sys.stderr)
        return 1
    try:
        injected = inject_codex_path(verbose=verbose)
    except RuntimeError as e:
        print(f"[coworker rtk codex] ERROR: {e}", file=sys.stderr)
        return 1
    if verbose:
        print(
            f"Codex parity: {written} shims installed; "
            f"codex config {'updated' if injected else 'skipped (codex not installed)'}."
        )
    return 0


def disable_codex_parity(*, verbose: bool = True) -> int:
    """Remove shims + strip codex PATH block. Idempotent."""
    if verbose:
        print("Codex parity teardown:")
    removed_count, _ = remove_shims(verbose=verbose)
    removed_block = remove_codex_path(verbose=verbose)
    if verbose:
        print(
            f"Codex parity: {removed_count} shims removed; "
            f"codex block {'removed' if removed_block else 'absent'}."
        )
    return 0
