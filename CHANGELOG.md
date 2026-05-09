# Changelog

All notable changes to this project are documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

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
