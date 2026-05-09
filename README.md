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
pip install git+https://github.com/Arcanada-one/coworker
```

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
                    [--task-id TASK_ID] [--no-log]
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
                      [--task-id TASK_ID] [--no-log] [--stdout]
```

`--spec` and `--target` are required. The model returns ONLY the file contents (code fences are stripped). Use `--stdout` to also echo the result.

```bash
coworker write --spec "MIT LICENSE for project Foo" --target LICENSE
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

## Configuration & logging

- Profiles bind a system prompt to a default provider + token budget. Switch with `--profile`.
- Provider resolution: `--provider` flag → `profile.recommended_provider` → `$COWORKER_DEFAULT_PROVIDER` → `moonshot`.
- Pass `--no-log` to skip the JSONL write for a single call. Set `COWORKER_NO_LOG=1` to disable globally.
- Corpus logging (the actual messages sent to the model) is **off by default**. Set `COWORKER_LOG_CORPUS=1` only if you understand what gets persisted to disk — see [`docs/logging-privacy.md`](docs/logging-privacy.md).

## Use with Claude Code

`coworker` was designed to pair with reasoning agents like Claude Code, where the harness can shell out for bulk I/O instead of spending its own context window. See [`docs/claude-code-integration.md`](docs/claude-code-integration.md) for a delegation pattern.

## Documentation

- [`docs/installation.md`](docs/installation.md)
- [`docs/provider-setup.md`](docs/provider-setup.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/logging-privacy.md`](docs/logging-privacy.md)
- [`docs/claude-code-integration.md`](docs/claude-code-integration.md)
- [`docs/troubleshooting.md`](docs/troubleshooting.md)

## License

MIT — see [`LICENSE`](LICENSE).
