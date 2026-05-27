# Changelog

All notable changes to this project are documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

## [0.4.0] — 2026-05-27

### Added

- **Codex CLI parity for `coworker rtk` via generic shim dispatcher.** `coworker rtk enable` now installs a wrapper-shim directory at `~/.local/share/rtk-shims/` containing 12 commands (`ls`, `tree`, `git`, `find`, `grep`, `diff`, `gh`, `glab`, `psql`, `pnpm`, `docker`, `kubectl`, `wget` — filtered to those present on `PATH`). Shims hard-code the absolute path to the real binary at install time, gate execution on the same `_managed_by: coworker-rtk` marker used by the Claude hook, and `exec` `rtk` only when the marker is present. `coworker rtk disable` removes shims and PATH injection byte-for-byte. New module: `coworker/plugins/rtk_codex_shims.py` (~400 LoC). 10 new unit tests in `tests/test_rtk_codex_shims.py`.
- **PATH injection for Codex `bash -lc` login shells.** Adds a marker-fenced block (`# >>> coworker-rtk-codex-shims (managed) >>>`) to `~/.zprofile` and `~/.bash_profile` so the shim directory takes precedence over real binaries inside Codex's login-shell wrapper. Block is regex-stripped on `disable`; idempotent on repeated `enable`.
- **`coworker write --append`** — opt-in append mode for the `write` subcommand. Mutually exclusive with `--stdout`. Falls back to write when the target does not exist; inserts a single newline separator when the existing tail is non-terminated. Closes a long-standing footgun where `coworker write --target Y` with «append» intent silently truncated `Y`. 6 new unit tests in `tests/test_write_append.py`.
- `coworker rtk status` now reports a parity matrix: `Claude: enabled/disabled · Cursor: inherited · Codex: enabled (shims=N, codex_config=patched) | disabled`.

### Security

- Shim directory created with mode `0o700`; install refuses to proceed if the directory pre-exists with world- or group-writable permissions (mitigation against PATH-shim hijacking).
- Shim body uses absolute `REAL_BIN` and `RTK_BIN` paths resolved at install time — runtime `PATH` reordering cannot redirect to a malicious binary.
- Recursion guard via `_COWORKER_RTK_SHIM_ACTIVE` env var prevents infinite loop when `rtk` internally invokes the real binary.

### Known limitations

- macOS / Linux only. Windows shim layout is an explicit follow-up.
- Codex CLI 0.133.0 ignores user-defined `PreToolUse` hooks empirically; native `rtk hook codex` is not viable until upstream Codex ships a working hook contract.
- `rtk cc-economics` is broken against the current `ccusage` JSON schema (`missing field 'month'`). Separate from this release.

### Changed

- None breaking. All additions are opt-in (require explicit `coworker rtk enable` or `coworker write --append`).

### Migration

- No migration required. Operators running `coworker rtk enable` for the first time on this version automatically receive the Codex parity layer; operators with v0.3.x already enabled need a single `coworker rtk disable && coworker rtk enable` cycle to install the new shim directory and PATH block.

## [0.3.1] — 2026-05-23

### Added

- `coworker rtk install` accepts `--dry-run` (always-on for now; reserved for future exec mode) and `--method {brew,curl,cargo,manual}` to override the OS-default install branch when piping into scripts.
- `coworker rtk status` reports a new `telemetry` field. Probes `rtk telemetry status` with a 2-second timeout and parses the `enabled: yes|no` line. Fail-soft semantics: missing binary / timeout / non-zero exit / unparseable output all print `telemetry: unavailable (<reason>)` and keep exit 0.

### Changed

- None breaking. All Phase A additions are additive on top of the four `coworker rtk` actions shipped in v0.3.0.

### Migration

- No migration required. Existing scripts that call `coworker rtk install` without flags keep their previous OS-detection behaviour.

## [0.3.0] — 2026-05-22

### Added

- **`coworker rtk` subcommand** — opt-in plugin that integrates [Rust Token Killer (RTK)](https://github.com/rtk-ai/rtk) with Claude Code. Four actions:
  - `coworker rtk install` — print OS-specific install instructions for RTK (no actual install, supply-chain hygiene).
  - `coworker rtk enable` — register a marker-tagged RTK hook (`_managed_by: "coworker-rtk"`) in `~/.claude/settings.json`. Idempotent — repeated calls produce exactly one hook block. Operator's pre-existing hooks (e.g. `coworker-hook-guard`) are preserved.
  - `coworker rtk disable` — remove the marker-tagged block. Settings.json stays valid JSON.
  - `coworker rtk status` — report `rtk` binary state, version, and hook state.
- `docs/rtk-plugin.md` — cross-platform install guide (macOS / Linux / Windows), enable/disable workflow, known limitations, troubleshooting.
- README "Optional plugins" section linking to the RTK plugin docs.
- `docs/claude-code-integration.md` — new "Combining with RTK" section.
- `scripts/bench_rtk.py` — hybrid synthetic-corpus + live-API benchmark, emits a Markdown table of `tokens_before / tokens_after / delta_%`.
- `tests/test_rtk_plugin.py` — 13 unit tests (idempotency, fail-soft, OS detection, JSON validity, hook preservation).
- `tests/test_rtk_live.py` — gated integration tests against Moonshot + DeepSeek (run with `RUN_LIVE_TESTS=1`).

### Changed

- None breaking. RTK plugin is opt-in (default-off); existing `ask`, `write`, `stats`, `debug` behaviour is unchanged.

### Migration

- **No migration required.** Existing installs continue to work. To activate the RTK plugin: install the upstream `rtk` binary (see `coworker rtk install`), then run `coworker rtk enable`. To deactivate: `coworker rtk disable`.

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
