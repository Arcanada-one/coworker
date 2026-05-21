# Changelog

All notable changes to this project are documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

## [0.2.0] — 2026-05-22

### Changed (BREAKING)

- **File type gate enabled by default.** `coworker ask --paths` and `coworker write --context` now refuse non-text inputs. The allowed extension list is `.md`, `.markdown`, `.txt` (case-insensitive). Files named `README`, `LICENSE`, `CHANGELOG`, or `AUTHORS` without an extension are also allowed. Anything else is rejected with **exit code 6** and an error message pointing at the override mechanism.
- Rationale: external LLM providers see only documentation-grade content by default. Source code (`.py`, `.ts`, `.json`, etc.) stays local — that workload belongs to your reasoning model, not a delegated I/O worker.

### Added

- `--allow-code` CLI flag on both `ask` and `write` subcommands — bypasses the gate for a single invocation.
- `COWORKER_ALLOW_CODE=1` environment variable — same effect as the flag, scoped to the shell.
- Override audit trail: when the gate is bypassed, the call log gains `coworker.gate_override: true` and `coworker.gate_overridden_files: [<paths>]`. A WARN line is also emitted to stderr per overridden file.
- New `extra: dict | None` optional kwarg on `logger.log_call` for additive metadata enrichment (used internally by the gate audit trail; safe for external callers to ignore).

### Migration

If you were piping code into `coworker` for analysis (the design intent was always docs/text, but the gate is new), choose one:
1. **Recommended**: stop delegating code reads — use your reasoning model's native file-reading tool.
2. Pass `--allow-code` per call when you genuinely want code at the provider.
3. Export `COWORKER_ALLOW_CODE=1` in your shell rc to restore prior default-allow behavior globally (not recommended — defeats the purpose).

## [0.1.0] — 2026-05-08

Initial public release. Extracted from internal vendor-neutral CLI work.

### Added

- Four subcommands: `ask`, `write`, `stats`, `debug`.
- Five built-in providers (OpenAI-compatible chat-completions): Moonshot, DeepSeek, Groq, OpenRouter, OpenAI.
- Four example profiles: `code`, `datarim`, `social`, `write` — each with own system prompt, recommended provider, and default token budgets.
- Two-tier logger: per-call JSONL metadata (always) and opt-in sha256-deduplicated corpus blobs (`COWORKER_LOG_CORPUS=1`).
- Cost calculation per provider/model with cached-token discount support.
- Stats aggregation: count, input/output tokens, USD cost, p50/p95 latency, cache-hit rate, grouped by provider / profile / model / combined.
- XDG Base Directory layout (`$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` honoured; falls back to `~/.config` / `~/.local/state`).
