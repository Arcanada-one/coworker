# RTK plugin (opt-in)

> **Status:** ships in coworker v0.3.0 · default-off · no behaviour change for existing installs.

[Rust Token Killer (RTK)](https://github.com/rtk-ai/rtk) is a CLI proxy
that strips noise from shell-tool output before it reaches your LLM's
context — typically a 60–90 % reduction in `prompt_tokens` for verbose
commands like `git status`, `pytest`, `find`, or `docker logs`.

Coworker ships a thin convenience plugin around upstream RTK:

- `coworker rtk install` — print OS-specific install instructions for the `rtk` binary.
- `coworker rtk enable`  — register a marker-tagged RTK hook in `~/.claude/settings.json`.
- `coworker rtk disable` — remove the hook (filter by marker, leave operator's other hooks intact).
- `coworker rtk status`  — report the rtk binary state and the hook state.

The plugin **never** installs binaries itself. Operator picks the install
vector. The hook is added with a private marker (`_managed_by:
"coworker-rtk"`) so disable can identify and remove it exactly, without
touching unrelated hook entries.

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

## Known limitations

- **Windows hook surface.** Claude Code's hook system is well-tested on
  macOS and Linux. Windows hook behaviour may evolve — if `rtk hook
  claude` does not fire, fall back to the upstream RTK CLAUDE.md
  injection (`rtk init -g --claude-md`).
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
