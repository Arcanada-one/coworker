# Logging & privacy

`coworker` logs in two tiers. Tier 1 is **always on** unless you opt out; tier 2 is **off by default** and you have to opt in explicitly.

## Tier 1 — JSONL metadata (default)

Every call appends one line to today's JSONL log:

```text
~/.local/state/coworker/log/YYYY-MM-DD.jsonl
```

Each record carries operational metadata only — **no message text, no API key, no response body**:

| Field                                | Example                              | Notes                                                        |
| ------------------------------------ | ------------------------------------ | ------------------------------------------------------------ |
| `ts`                                 | `2026-05-08T18:42:13Z`               | UTC, ISO-8601.                                               |
| `gen_ai.system`                      | `deepseek`                           | Provider name (matches `providers.yaml`).                    |
| `gen_ai.request.model`               | `deepseek-chat`                      | Resolved model name.                                         |
| `coworker.profile`                   | `code`                               | Profile that supplied the system prompt.                     |
| `gen_ai.usage.input_tokens`          | `12318`                              | Prompt tokens billed.                                        |
| `gen_ai.usage.output_tokens`         | `421`                                | Completion tokens billed.                                    |
| `gen_ai.usage.cached_tokens`         | `8000`                               | Cached prompt tokens (provider-reported).                    |
| `coworker.cost_usd`                  | `0.001923`                           | Computed locally from `providers.yaml` pricing.              |
| `latency_ms`                         | `2148.7`                             | Wall-clock from request start to response end.               |
| `gen_ai.response.finish_reason`      | `stop`                               | Provider-reported.                                           |
| `coworker.system_hash`               | `sha256:1a2b3c…`                     | sha256(system_prompt) truncated to 16 chars. Lets you correlate calls without storing prompts. |
| `coworker.exit_code`                 | `0`                                  |                                                              |
| `coworker.task_id`                   | `null` or `"FEAT-0001"`              | Whatever you passed via `--task-id`.                          |
| `coworker.subcommand`                | `ask` / `write`                      |                                                              |
| `coworker.corpus_hash`               | `<full sha256>`                      | **Only present** if `COWORKER_LOG_CORPUS=1`.                  |

Field naming follows OpenTelemetry GenAI semantic conventions where they apply. Pipe to `jq`, ship to a TSDB, ingest into your favourite observability stack.

### Disabling tier 1

- One call: `--no-log`.
- All calls: `export COWORKER_NO_LOG=1`.

`coworker stats` and `coworker debug` rely on tier 1; disabling it disables them too.

## Tier 2 — corpus blobs (opt-in)

If you set `COWORKER_LOG_CORPUS=1`, `coworker` additionally serialises the **full request body** (user messages — i.e. the content of any files you passed via `--paths` or `--context`, plus your `--question` / `--spec`) **and the model response**, hashes the result with sha256, and writes it deduplicated under:

```text
~/.local/state/coworker/blobs/sha256/<ab>/<rest>.json
```

The system prompt is NOT stored verbatim in the blob — only `system_hash` (16-char sha256 prefix) makes it into the JSONL line.

### When to enable

- Reproducing a flaky or surprising model output. `coworker debug --hash <prefix>` reads the blob back.
- Measuring cache-hit behaviour empirically.
- Building an offline eval set from real production calls.

### When NOT to enable

- You're processing files that contain **secrets**, **PII**, **client confidential data**, or **anything you don't want sitting in plaintext on disk**. The blob is plaintext JSON. Filesystem encryption is your only protection.
- You don't need the data — disk fills up faster than you'd think on a busy day. Dedup helps but doesn't eliminate growth.

### Wiping the blob store

```bash
rm -rf ~/.local/state/coworker/blobs/
```

Re-running with `COWORKER_LOG_CORPUS=1` will recreate the directory. Removing the blob directory does NOT break tier-1 logs — `coworker stats` keeps working — but `coworker debug` will find no blobs.

## What `coworker` does NOT log

- API keys (only the *name* of the env var that held the key, via `providers.yaml.<provider>.env_key`).
- Raw HTTP requests / responses beyond what tier 2 captures.
- The system prompt verbatim (only its 16-char sha256 prefix).

## What the **provider** sees

Anything you send. The provider's TOS / privacy policy applies. `coworker` doesn't proxy through any Arcanada-operated infrastructure — your API key talks directly to the provider's `base_url`.

If you don't want a particular file's contents going to a particular vendor: don't pass `--paths <that-file>` with `--provider <that-vendor>`. There is no list of automatic redactions.
