# RTK plugin (opt-in)

> **Status:** ships in coworker v0.3.0 (Claude/Cursor hook) and v0.4.0 (Codex CLI parity via PATH-shim layer) · default-off · no behaviour change for existing installs.

[Rust Token Killer (RTK)](https://github.com/rtk-ai/rtk) is a CLI proxy
that strips noise from shell-tool output before it reaches your LLM's
context. Effectiveness varies sharply by command type:

| Command class | Typical reduction | Notes |
|---|---|---|
| Verbose long-form (`ls -la`, `find`, `ps aux`, `docker logs`) | **−50 % to −99 %** | Best case. The dominant RTK win. |
| Bulk listings on heavily-modified state (`git status` on a dirty repo, large `grep -n`) | **−20 % to −50 %** | Solid win. |
| Already-compact tools (`tree -L 2`, idempotent listings) | ≈ 0 % | No-op. |
| Signal commands on a clean state (`git status` / `git log --oneline` on a clean repo) | **Can inflate** | Avoid — see § Known limitations. |

Empirical adoption from live telemetry across active sessions: 33–52 %
of `Bash` tool calls get rewritten. Run `rtk gain` and `rtk discover` for
your own profile.

Coworker ships a thin convenience plugin around upstream RTK:

- `coworker rtk install` — print OS-specific install instructions for the `rtk` binary.
- `coworker rtk enable`  — register a marker-tagged RTK hook in `~/.claude/settings.json` **AND** (since v0.4.0) install a PATH-shim layer at `~/.local/share/rtk-shims/` for Codex CLI parity, with marker-fenced PATH-injection blocks in `~/.zprofile` and `~/.bash_profile`.
- `coworker rtk disable` — remove the hook **AND** the shim directory **AND** the PATH-injection blocks byte-for-byte.
- `coworker rtk status`  — report rtk binary state + per-agent parity matrix (Claude / Cursor / Codex).

The plugin **never** installs binaries itself. Operator picks the install
vector. The hook is added with a private marker (`_managed_by:
"coworker-rtk"`) so disable can identify and remove it exactly, without
touching unrelated hook entries. Shim directory and shell-profile blocks
use marker fences (`# >>> coworker-rtk-codex-shims (managed) >>>` …
`# <<<`) for the same reason.

## Cross-agent parity (Claude / Cursor / Codex)

Since v0.4.0, `coworker rtk enable` activates RTK for all three major
agentic CLIs:

| Agent | Mechanism | Verified |
|---|---|---|
| Claude Code | `PreToolUse` hook (`rtk hook claude`) in `~/.claude/settings.json` | empirical, byte-count probe on `ls -la <large-dir>` |
| Cursor | Inherited via the shared `~/.claude/settings.json` channel | empirical, same probe |
| Codex CLI | PATH-shim dispatcher at `~/.local/share/rtk-shims/` (12 wrapped commands), injected into login-shell `PATH` via marker-fenced block in `~/.zprofile` + `~/.bash_profile`. Codex's `bash -lc` wrapper picks up the shim ahead of the real binary. | empirical, `codex exec` byte-count probe (requires one-time hook approval — see below) |

### Codex CLI: one-time hook approval

Codex CLI 0.133.0+ ships a hook-trust system that hashes every
PreToolUse / session hook and prompts the operator on first encounter
(or after the hash changes). The first time you launch `codex exec` after
`coworker rtk enable`, you will be asked to approve the new hook trust
hashes. **This is normal**, not a bug. Approve once; subsequent sessions
run without prompts until the hooks change.

The PATH-shim itself does not appear in this prompt — Codex treats
shell-environment `PATH` changes as ordinary user config, not as
hooks. The prompt you see relates to whatever's in
`~/.codex/hooks.json` at session start.

### Why a PATH-shim rather than a Codex hook

Empirical findings during v0.4.0 implementation:

- Codex CLI 0.133.0 stable does not execute user-defined `PreToolUse`
  hooks in the same way Claude Code does. The native-hook path is
  blocked on upstream changes.
- The shim layer is the workaround. Each shim hard-codes the absolute
  path to the real binary at install time, gates execution on the
  Claude-side marker (`_managed_by: coworker-rtk` in
  `~/.claude/settings.json`), and `exec`'s `rtk` only when the marker is
  present. Without the marker, the shim is a no-op pass-through to the
  real binary.

This means: `coworker rtk disable` flips the marker; the shims are
still on `PATH` but functionally inert.

---

## Install the RTK binary

### macOS

```bash
brew install rtk
# or:
curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh
# or:
cargo install --git https://github.com/rtk-ai/rtk
```

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh
# or:
cargo install --git https://github.com/rtk-ai/rtk
```

### Windows

Native Windows is supported via standalone binary:

1. Download `rtk-x86_64-pc-windows-msvc.zip` from
   [github.com/rtk-ai/rtk/releases](https://github.com/rtk-ai/rtk/releases).
2. Extract to a directory on your `PATH` (e.g. `C:\Users\<you>\bin\`).
3. Open a fresh shell; `rtk --version` should print the version.

Alternatively, via Rust:

```powershell
cargo install --git https://github.com/rtk-ai/rtk
```

After install, confirm with:

```bash
coworker rtk status
```

You should see the absolute path to `rtk` plus its version.

---

## Enable / disable the hook

### Enable

```bash
coworker rtk enable
```

This appends a marker-tagged block to `~/.claude/settings.json`:

```json
{
  "matcher": "Bash",
  "hooks": [
    {
      "type": "command",
      "command": "rtk hook claude",
      "_managed_by": "coworker-rtk",
      "_version": 1
    }
  ]
}
```

The `_managed_by` marker is **the disable contract**. Without it, `coworker
rtk disable` cannot identify which block to remove — it never guesses by
matcher or command string.

Repeated `enable` calls are idempotent: the marker block appears exactly
once regardless of how many times you call.

### Disable

```bash
coworker rtk disable
```

Filters out every block bearing the marker; leaves all other entries
untouched (including your `coworker-hook-guard` block if you have one).
Settings.json stays valid JSON.

### Status

```bash
coworker rtk status
```

Prints:

- `rtk binary` — absolute path (or `not installed`).
- `rtk version` — short version string.
- `settings` — path checked.
- `RTK hook` — `enabled` / `disabled`.

`status` always exits 0 (fail-soft); errors land on stderr.

---

## Signal / bulk passthrough (v0.6.0+)

The signal-command inflation problem described in § Known limitations
below is resolved natively from v0.6.0 onward — `coworker rtk enable`
installs a passthrough guard that runs **before** the RTK hook and
short-circuits signal-bearing commands so they execute against the
real binary without RTK rewriting.

### Default allowlist (13 patterns)

| Pattern         | Why it's a signal                                              |
|-----------------|----------------------------------------------------------------|
| `git push`      | Canonical marker (`To github.com:…`) confirms upload happened. |
| `git pull`      | Confirms fast-forward / merge result; not a bulk listing.      |
| `git fetch`     | Confirms refs updated; very short output.                      |
| `git merge`     | Conflict markers are signal; reformat breaks parsing.          |
| `git status`    | Clean-tree marker is signal; bloating it confuses agents.      |
| `git remote`    | Remote URL list — short and parseable.                         |
| `git rev-parse` | Single-SHA output; rewriting it = always-empty.                |
| `git branch`    | Branch list with `*` current-marker; signal for decision-trees.|
| `gh pr`         | PR state machine (open / merged / closed) is signal.           |
| `gh issue`      | Issue state and number — short signal output.                  |
| `gh release`    | Release URL output is signal.                                  |
| `gh api`        | Raw API response; rewriting breaks JSON parsing downstream.    |
| `gh run`        | CI run status is signal.                                       |

Substring match against `tool_input.command` (case-sensitive). A
single hit anywhere in the command string is enough: e.g.
`cd /tmp/repo && git push origin main` matches `git push`.

### CRUD workflow

```bash
coworker rtk passthrough list                  # print active patterns
coworker rtk passthrough add 'docker compose'  # add custom signal pattern
coworker rtk passthrough remove 'git tag'      # remove default if not wanted
```

Store: `~/.config/coworker/rtk-passthrough.json`. Idempotent — re-running
`add` for an existing pattern is a no-op. `remove` against a non-present
pattern is a no-op (exit 0, single stderr note). The file is rewritten
atomically (temp + rename).

### Fallback behaviour

- **`jq` not installed.** The bash guard falls back to embedded defaults
  (the 13 patterns above), warns once on stderr, and continues
  unchanged. The user does not see a broken terminal.
- **Store missing or malformed.** Same fallback. Operators can repair
  with `coworker rtk passthrough list` (regenerates from defaults if
  the file is unrecoverable) or by deleting the file (next CRUD call
  reseeds).
- **Env-var override.** `COWORKER_RTK_PASSTHROUGH_STORE=/path/to/json`
  redirects the guard to an alternate store (useful for CI sandboxes,
  multi-user hosts, or per-project allowlists).

### Codex CLI parity

The same passthrough allowlist applies to commands invoked through
Codex CLI: the `git` and `gh` shims in `rtk_codex_shims.py` inject the
passthrough snippet before delegating to the real binary. A `git push`
issued through Codex sees the same passthrough treatment as one
issued through Claude Code. Bulk-bearing shims (`ls`, `grep`, `find`)
are untouched — no per-call overhead, since none of those carry
signal markers anyway.

### v1 → v2 settings.json migration

`coworker rtk enable` detects a stale v1 block in `~/.claude/settings.json`
(no passthrough guard, no idempotent marker) and rewrites it in place
to v2: `_managed_by: coworker-rtk`, `_version: 2`, two-step
`PreToolUse` chain (passthrough guard → RTK hook). Operators upgrading
via `pipx upgrade coworker && coworker rtk enable` get the migration
without a manual `disable + enable` cycle. `disable` preserves the
passthrough store (CRUD state is durable across enable/disable cycles).

---

## Known limitations

- **Signal-command inflation on clean state.** RTK rewrites `git push`,
  `git pull`, `git fetch`, `git merge`, `git status`, `git log` and
  similar control-signal commands the same way it rewrites bulk
  listings. On a clean repo where the canonical output is short
  (`Everything up-to-date`, `To github.com:...`, branch SHA markers),
  RTK can either strip the canonical marker or add wrapper boilerplate.
  Agents that wait for the canonical marker will treat the rewritten
  output as «nothing happened» and hang or retry. **Workaround:** after
  any `git push` / `git pull`, verify by state — compare
  `git rev-parse HEAD` against `git rev-parse @{u}` rather than parsing
  stdout. Native upstream fix is tracked as a feature-request to
  rtk-ai/rtk: signal-vs-bulk command classification. If you hit this in
  practice, you can disable RTK for the session (`coworker rtk
  disable`), run the git op, then re-enable.
- **`rtk cc-economics` is broken on current `ccusage`.** Other rtk
  subcommands (`gain`, `discover`, `session`) work fine; `cc-economics`
  crashes with `Invalid JSON structure for monthly data: missing field
  'month'`. Filed upstream / in-coworker backlog. Workaround: read
  `rtk gain` + `ccusage` separately for now.
- **Codex CLI prompts on first session after enable.** See
  § «Codex CLI: one-time hook approval» above. Not a regression — a
  feature of Codex's hook-trust system.
- **Codex shim coverage is fixed.** The shim directory wraps 12
  commands (`ls`, `tree`, `git`, `find`, `grep`, `diff`, `gh`, `glab`,
  `psql`, `pnpm`, `docker`, `kubectl`, `wget`) — those whose real
  binary is on `PATH` at install time. Commands not in this list
  bypass RTK entirely when invoked through Codex. The wrapped set
  matches RTK's own built-in command surface; expanding requires both
  upstream RTK support and a coworker re-enable.
- **Windows hook surface.** Claude Code's hook system is well-tested on
  macOS and Linux. Windows hook behaviour may evolve — if `rtk hook
  claude` does not fire, fall back to the upstream RTK CLAUDE.md
  injection (`rtk init -g --claude-md`). Codex shim layer on Windows is
  an explicit follow-up — not shipped in v0.4.0.
- **Claude Code built-in tools bypass the hook.** Tool calls executed by
  Claude Code's `Read` / `Edit` / `Write` tools do not produce shell
  stdout, so they pass through unchanged. The hook benefits the `Bash`
  tool surface — i.e. `git`, `pytest`, `find`, `docker`, and similar
  noisy commands.
- **No bundled RTK binary.** Coworker stays pure-Python with zero Rust
  dependencies. RTK is a separately-installed runtime tool — by design.

---

## Combining with `coworker-hook-guard`

If you already have a `coworker-hook-guard` block in your settings.json
(the standard Coworker guard rail), `coworker rtk enable` appends the RTK
block alongside it — never overwrites it. Both hooks fire on Bash tool
invocations; the guard runs first, RTK transforms output afterwards. To
confirm:

```bash
coworker rtk status
# RTK hook: enabled

grep -c coworker-hook-guard ~/.claude/settings.json
# 1
```

---

## Troubleshooting

### `rtk` not found after install

Ensure your shell PATH includes the install location. For Homebrew on
Apple Silicon: `/opt/homebrew/bin`. For cargo: `~/.cargo/bin`. Restart
your terminal (or `hash -r` in zsh / `rehash` in bash).

### Hook doesn't appear to run

1. Check the hook is registered: `grep _managed_by ~/.claude/settings.json` — exactly one match expected.
2. Restart Claude Code so the new hook is loaded.
3. Test with a noisy command: ask Claude Code to run `git log --stat`
   and inspect the response. If you see the full uncompressed output,
   the hook is not active.

### Settings.json corruption

`coworker rtk enable` writes atomically (`tempfile` + `os.replace`). If
your editor's autosave races with the write, re-run `enable`; the
operation is idempotent. As a safety net, the previous content of
`settings.json` is retained until the rename completes.

### Removing every trace

```bash
coworker rtk disable                # remove the hook block
brew uninstall rtk                  # or: cargo uninstall rtk
```

---

## Upstream

- RTK source: <https://github.com/rtk-ai/rtk>
- RTK docs: <https://www.rtk-ai.app/>
- RTK license: Apache-2.0 (compatible with coworker's MIT for runtime use).
