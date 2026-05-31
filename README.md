# coworker

Vendor-neutral CLI to delegate bulk I/O off your reasoning model.

You pay top-tier prices for your reasoning model — you don't need to spend those tokens reading 600-line files, summarising git history, or drafting boilerplate. `coworker` routes that work to a cheaper provider while you keep the reasoning seat for the work that actually requires it.

```text
Reasoning model    →    coworker     →    cheap provider
(your top-tier)         (this CLI)        (Moonshot / DeepSeek / Groq / OpenRouter / OpenAI)
```

---

## What

A small Python CLI with four subcommands:

- **`coworker ask`** — ask a question about a corpus of files; cheap model reads, you get the answer.
- **`coworker write`** — generate a complete file from a spec + reference context.
- **`coworker stats`** — local usage / cost / latency / cache-hit aggregates from the JSONL log.
- **`coworker debug`** — inspect a logged corpus blob by sha256 prefix (when corpus logging is enabled).

Five providers built in, all reached through their OpenAI-compatible `chat/completions` endpoint: **Moonshot**, **DeepSeek**, **Groq**, **OpenRouter**, **OpenAI**. Switching is a flag.

## Why

Modern coding agents and reasoning loops burn most of their token budget on **reading**, not thinking. Reading a 600-line file is structurally identical work whether you do it on Claude Opus or DeepSeek-Chat — but the cost differs by an order of magnitude. The cheap model is usually good enough for retrieval-style summarisation; you only need the expensive model for the synthesis step.

`coworker` makes the delegation explicit, configurable per task (via profiles), and observable (every call gets logged with tokens, cost, latency, and cache-hit rate).

It is intentionally **a single CLI binary**, not a library or a server. You wire it into whichever agent harness you use (Claude Code, your own scripts, CI pipelines) by shelling out.

## Quick start

```bash
pip install git+https://github.com/Arcanada-one/coworker

mkdir -p ~/.config/coworker
curl -fsSL https://raw.githubusercontent.com/Arcanada-one/coworker/main/examples/providers.yaml.example \
  > ~/.config/coworker/providers.yaml
curl -fsSL https://raw.githubusercontent.com/Arcanada-one/coworker/main/examples/profiles.yaml.example \
  > ~/.config/coworker/profiles.yaml

export DEEPSEEK_API_KEY="sk-..."   # whichever provider you have a key for

coworker ask --provider deepseek \
             --paths README.md \
             --question "What does this project do, in one sentence?"
```

## Installation

Requires Python 3.10+. No system packages.

```bash
pip install coworker-cli
```

Or pin a specific release: `pip install coworker-cli==0.6.3`. To install straight
from source instead: `pip install git+https://github.com/Arcanada-one/coworker`.

The distribution is named `coworker-cli` on PyPI; the installed command stays
`coworker`.

Releases are signed with cosign and carry SLSA L2 build provenance — see
[`docs/release-verification.md`](docs/release-verification.md) to verify a
download before installing.

For development:

```bash
git clone https://github.com/Arcanada-one/coworker
cd coworker
pip install -e ".[dev]"
pytest -q
```

Config is read from XDG-standard locations:

| File                              | Purpose                              |
| --------------------------------- | ------------------------------------ |
| `~/.config/coworker/providers.yaml` | Provider definitions + pricing      |
| `~/.config/coworker/profiles.yaml`  | Profile system prompts + defaults   |
| `~/.local/state/coworker/log/*.jsonl` | Per-call usage log                |
| `~/.local/state/coworker/blobs/`     | Optional sha256-deduplicated corpora (only if `COWORKER_LOG_CORPUS=1`) |

`$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` are honoured if set. See [`docs/installation.md`](docs/installation.md).

## Provider setup

Each provider needs one environment variable. Set the key for whichever provider you actually use; you don't need keys for all five.

| Provider     | Env var                | Get a key                                |
| ------------ | ---------------------- | ---------------------------------------- |
| Moonshot     | `MOONSHOT_API_KEY`     | https://platform.moonshot.ai             |
| DeepSeek     | `DEEPSEEK_API_KEY`     | https://platform.deepseek.com            |
| Groq         | `GROQ_API_KEY`         | https://console.groq.com                 |
| OpenRouter   | `OPENROUTER_API_KEY`   | https://openrouter.ai                    |
| OpenAI       | `OPENAI_API_KEY`       | https://platform.openai.com              |

Defaults (model, pricing, prefix-cache support) live in `providers.yaml` and are easy to override. See [`docs/provider-setup.md`](docs/provider-setup.md).

## CLI reference

### `coworker ask`

```text
usage: coworker ask [-h] [--provider PROVIDER] [--model MODEL]
                    [--profile PROFILE] [--paths [FILE ...]]
                    --question QUESTION [--max-tokens MAX_TOKENS]
                    [--task-id TASK_ID] [--no-log] [--allow-code]
```

`--question` is required. If `--paths` is omitted and stdin has data, stdin is used as the corpus. The default profile is `code`.

```bash
coworker ask --paths src/main.py src/utils.py \
             --question "Where is the retry policy applied?"
```

### `coworker write`

```text
usage: coworker write [-h] [--provider PROVIDER] [--model MODEL]
                      [--profile PROFILE] --spec SPEC [--context [FILE ...]]
                      --target TARGET [--max-tokens MAX_TOKENS]
                      [--task-id TASK_ID] [--no-log] [--stdout] [--append]
                      [--allow-code]
```

`--spec` and `--target` are required. The model returns ONLY the file contents (code fences are stripped). Use `--stdout` to also echo the result.

```bash
coworker write --spec "MIT LICENSE for project Foo" --target LICENSE
```

By default, `--target` is **truncate-written** — any existing contents
are replaced. Since v0.4.0, pass `--append` to append the generated body
to an existing file instead (mutually exclusive with `--stdout`). If
`--target` doesn't exist yet, `--append` falls back to a normal write.

```bash
coworker write --spec "Add a new release-notes section" \
               --context CHANGELOG.md \
               --target CHANGELOG.md \
               --append
```

### `coworker stats`

```text
usage: coworker stats [-h] [--since SINCE]
                      [--by {provider,profile,model,combined}]
                      [--profile PROFILE] [--provider PROVIDER]
                      [--format {text,json}]
```

Reads `~/.local/state/coworker/log/*.jsonl` and prints aggregates. JSON output has a stable schema — pipe to `jq`.

```bash
coworker stats --since 7d --by provider
coworker stats --since 30d --by combined --format json | jq '.[] | .sum_cost_usd'
```

### `coworker debug`

```text
usage: coworker debug [-h] --hash HASH
```

Replays a logged corpus blob by sha256 prefix (≥2 chars). Only useful if you opted into corpus logging via `COWORKER_LOG_CORPUS=1`.

```bash
COWORKER_LOG_CORPUS=1 coworker ask --question "..." --paths README.md
coworker debug --hash 8a3f
```

## File type gate

Since **0.2.0**, `coworker ask --paths` and `coworker write --context` refuse non-text inputs by default. This is a deliberate policy choice: a delegated I/O worker should see documentation, not source code. Source code belongs at your reasoning model.

**Allowed by default:**

| Surface | Allow rule |
|---------|-----------|
| Extensions | `.md`, `.markdown`, `.txt` (case-insensitive) |
| Extensionless names | `README`, `LICENSE`, `CHANGELOG`, `AUTHORS` (case-insensitive) |
| Stdin | Always — gate applies only to `--paths` / `--context` |

**Blocked:** everything else — including `.py`, `.ts`, `.json`, `.yaml`, `.rs`, `.go`, source code in any language, binary files. The call exits with code **6** and stderr lists each offending path.

**Override** when you genuinely want code at the provider:

```bash
# Per call:
coworker ask --paths src/main.py --question "..." --allow-code

# Per shell session:
export COWORKER_ALLOW_CODE=1
coworker ask --paths src/main.py --question "..."
```

Every override writes `coworker.gate_override: true` and `coworker.gate_overridden_files: [...]` to the per-call log entry. Pipe `coworker stats --format json` or grep `~/.local/state/coworker/log/$(date +%F).jsonl` to audit.

**Limitations.** The gate inspects the path string (`.suffix`), not the resolved target — a `.md` symlink to `.py` would pass. This is intent-based gating, not an adversarial sandbox. For adversarial scenarios, sanitize inputs upstream.

## Configuration & logging

- Profiles bind a system prompt to a default provider + token budget. Switch with `--profile`.
- Provider resolution: `--provider` flag → `profile.recommended_provider` → `$COWORKER_DEFAULT_PROVIDER` → `moonshot`.
- Pass `--no-log` to skip the JSONL write for a single call. Set `COWORKER_NO_LOG=1` to disable globally.
- Corpus logging (the actual messages sent to the model) is **off by default**. Set `COWORKER_LOG_CORPUS=1` only if you understand what gets persisted to disk — see [`docs/logging-privacy.md`](docs/logging-privacy.md).

## Use with Claude Code

`coworker` was designed to pair with reasoning agents like Claude Code, where the harness can shell out for bulk I/O instead of spending its own context window. See [`docs/claude-code-integration.md`](docs/claude-code-integration.md) for a delegation pattern.

## Optional plugins

- **`coworker rtk`** — opt-in integration with [Rust Token Killer (RTK)](https://github.com/rtk-ai/rtk). Default-off. `coworker rtk enable` (since v0.6.0) installs a two-step `PreToolUse` chain: a vendored passthrough guard that short-circuits 13 default signal-bearing git/gh commands (`git push`, `git status`, `gh release`, …) so they execute against the real binary without RTK rewriting, then the standard RTK hook for everything else. CRUD surface: `coworker rtk passthrough add|list|remove`. See [`docs/rtk-plugin.md`](docs/rtk-plugin.md) § Signal / bulk passthrough for the full default allowlist and effectiveness table. One-time Codex hook approval prompt on first session after enable.

### Runtime parity

| Runtime       | Install command                | Hook integration                         | Bulk-read economy via RTK | Status        |
|---------------|--------------------------------|------------------------------------------|---------------------------|---------------|
| Claude Code   | `coworker rtk install` + `enable` | Native `PreToolUse` hook              | Full (with passthrough guard) | Primary |
| Codex CLI     | same                           | PATH-shim layer (vendored)               | Full (with passthrough guard) | Parity  |
| Cursor        | `coworker rtk install` + `enable` | Native `beforeShellExecution` hook (`rtk hook cursor`) | Full (with passthrough guard) | Parity |

**Cursor.** Cursor Agent has no Claude-style `PreToolUse` hook, but it exposes a native `beforeShellExecution` hook. `coworker rtk enable` installs a `beforeShellExecution` entry in `~/.cursor/hooks.json` that runs `rtk hook cursor`, which rewrites bulk commands to `rtk <cmd>` (compacted output) and passes signal commands through — the same token economy as Claude Code and Codex CLI. Verified live: inside cursor-agent, `ls -la /tmp` returns the rtk-compacted form rather than raw `ls` bytes. No shell-rc mutation is involved — cursor-agent builds its own login-shell PATH, so a PATH-shim would not survive to command execution; the native hook is the correct, scoped surface.

## Documentation

- [`docs/installation.md`](docs/installation.md)
- [`docs/provider-setup.md`](docs/provider-setup.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/logging-privacy.md`](docs/logging-privacy.md)
- [`docs/claude-code-integration.md`](docs/claude-code-integration.md)
- [`docs/rtk-plugin.md`](docs/rtk-plugin.md)
- [`docs/release-verification.md`](docs/release-verification.md)
- [`docs/troubleshooting.md`](docs/troubleshooting.md)

## License

MIT — see [`LICENSE`](LICENSE).
