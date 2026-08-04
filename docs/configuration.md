# Configuration reference

`coworker` reads two YAML files. Everything else is environment variables.

## `providers.yaml`

Top-level dict; each key is a provider name you reference with `--provider <name>`.

```yaml
<provider-name>:
  base_url: <string>          # OpenAI-compatible chat-completions root, e.g. https://api.example.com/v1
  env_key: <string>           # Name of the env var that holds the API key
  default_model: <string>     # Model to use if --model is not passed
  pricing:                    # Optional. null disables cost tracking.
    <model-name>:
      input: <float>          # USD per 1M input tokens
      output: <float>         # USD per 1M output tokens
      cache_input: <float>    # Optional. USD per 1M cached-input tokens.
  prefix_cache: <bool|string> # Documentation field — informational only.
```

A model that exists at the API but is missing from `pricing:` will produce a stderr warning and `cost_usd=0` in the log.

## `profiles.yaml`

Top-level dict; each key is a profile you reference with `--profile <name>`.

```yaml
<profile-name>:
  description: <string>           # Optional, free-form.
  system_prompt: <string>         # System role content. Use YAML | block scalar for multi-line.
  default_max_tokens_ask: <int>   # Used when --max-tokens is not passed for `coworker ask`.
  default_max_tokens_write: <int> # Used when --max-tokens is not passed for `coworker write`.
  recommended_provider: <string>  # Provider to use if --provider is not passed.
  recommended_model: <string>     # Optional. Pins the model for this profile (see provider-setup.md).
  fallback_provider: <string>     # Optional. Retry once on this provider when the primary returns
                                  # a retryable error (HTTP 429 or a request timeout). Single-flight,
                                  # at most one hop. Balance (402)/auth/generic errors are NOT retried.
  fallback_model: <string>        # Optional. Model sent to fallback_provider (defaults to its default_model).
  max_retries: <int>              # Optional. Retries per provider on a retryable error (429/timeout),
                                  # with exponential backoff. Default: 2 (i.e. up to 3 attempts).
                                  # 0 disables retries. See "Retry policy" below.
  retry_base_delay: <float>       # Optional. First backoff delay in seconds; doubles per retry
                                  # (base, 2*base, 4*base, ...). Default: 1.0. 0 = no wait.
```

Required fields: `system_prompt` and `recommended_provider`. Everything else has a fallback.

When a profile declares `fallback_provider`, a rate-limit (HTTP 429) or timeout on the
primary provider triggers exactly one automatic retry on the declared fallback. This is a
single-flight hop — the fallback is tried once; if it also fails the error propagates. Errors
that would fail identically on any provider (balance-exhausted 402, auth, malformed request)
are never retried and surface immediately. Omit `fallback_provider` to keep fail-loud behavior.

## Retry policy

Every `coworker ask` / `coworker write` call applies a global retry policy on
retryable errors (HTTP 429 rate limit or a request timeout): each provider gets
`1 + max_retries` attempts with exponential backoff between attempts
(`retry_base_delay`, then doubled per retry: 1s, 2s, 4s, ...). When a profile
declares `fallback_provider`, the fallback hop happens only after the primary's
retry budget is exhausted, and the fallback then gets its own budget.

Resolution chain (profile wins over env, mirroring provider resolution):
profile `max_retries` / `retry_base_delay` → env `COWORKER_MAX_RETRIES` /
`COWORKER_RETRY_BASE_DELAY` → built-in defaults (`2` retries, `1.0`s base).
Invalid values fall through to the next layer.

Fail-loud guarantee: balance-exhausted (402), auth, and malformed-request
errors are never retried. When the retry budget runs out, coworker prints
`provider <name> failed after N attempt(s) — retry budget exhausted (last
error: ...)` to stderr and exits non-zero (exit `8`, or `7` for balance) —
retries never mask a hard failure.

## Environment variables

| Variable                       | Effect                                                                    |
| ------------------------------ | ------------------------------------------------------------------------- |
| `<PROVIDER>_API_KEY`           | Per-provider API key. Required to call that provider. Name comes from `providers.yaml.<provider>.env_key`. |
| `COWORKER_DEFAULT_PROVIDER`    | Fallback provider name when neither `--provider` nor `profile.recommended_provider` is set. Default: `moonshot`; set to `deepseek` for DeepSeek-first installs. |
| `COWORKER_MAX_RETRIES`         | Global default for retries-per-provider on retryable errors (429/timeout). Overridden by a profile's `max_retries`. Default: `2`. |
| `COWORKER_RETRY_BASE_DELAY`    | Global default for the first backoff delay in seconds (doubles per retry). Overridden by a profile's `retry_base_delay`. Default: `1.0`. |
| `COWORKER_NO_LOG=1`            | Globally disable JSONL logging (also disables blob writes). |
| `COWORKER_LOG_CORPUS=1`        | Enable sha256-deduplicated corpus blob writes. **Off by default**. See [`logging-privacy.md`](logging-privacy.md). |
| `XDG_CONFIG_HOME`              | Override config root. Default: `~/.config`. Coworker's dir is `${XDG_CONFIG_HOME}/coworker/`. |
| `XDG_STATE_HOME`               | Override state root. Default: `~/.local/state`. Coworker's dir is `${XDG_STATE_HOME}/coworker/`. |

## Per-call flags

| Flag             | Where applicable | Purpose                                                  |
| ---------------- | ---------------- | -------------------------------------------------------- |
| `--provider`     | `ask`, `write`   | Override provider for this call.                          |
| `--model`        | `ask`, `write`   | Override model name (must exist on the chosen provider). |
| `--profile`      | `ask`, `write`   | Pick a profile from `profiles.yaml`. Defaults: `code` / `write`. |
| `--max-tokens`   | `ask`, `write`   | Override profile's default token budget for this call.   |
| `--task-id`      | `ask`, `write`   | Free-form string written to the JSONL log; useful for grouping calls. |
| `--no-log`       | `ask`, `write`   | Skip JSONL write for this call (corpus blob also skipped). |
| `--stdout`       | `write`          | Echo generated content to stdout in addition to writing the target file. |
| `--since`        | `stats`          | Time window: `7d`, `30d`, `2h`, or `all`. Default: `7d`. |
| `--by`           | `stats`          | Group by `provider`, `profile`, `model`, or `combined`. Default: `provider`. |
| `--format`       | `stats`          | `text` (default) or `json`.                              |
| `--hash`         | `debug`          | sha256 prefix (≥2 chars) of a logged corpus blob.        |

## Validation

`coworker` performs these checks at call time:

- `providers.yaml` and `profiles.yaml` must exist and be parseable as YAML (`yaml.safe_load` only — `!!python/object` tags are rejected).
- Resolved provider must be a key in `providers.yaml`.
- `<env_key>` must be set and non-empty.
- For `coworker debug`, hash prefix must be ≥2 characters.

Failures exit with non-zero codes and a stderr message. There's no silent fallback — if the config is wrong, you'll see the error.
