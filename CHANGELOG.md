# Changelog

All notable changes to this project are documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

## [0.6.0] — 2026-05-28

### Added

- **Canonical signal-vs-bulk passthrough inside `coworker rtk` plugin.** New `rtk_passthrough.py` module persists a JSON store at `~/.config/coworker/rtk-passthrough.json` with 13 default git/gh signal-command patterns (`git push`, `git pull`, `git fetch`, `git merge`, `git status`, `git log`, `gh pr`, `gh release`, and 5 others) plus an `add` / `list` / `remove` CRUD surface (`coworker rtk passthrough add|list|remove`). Idempotent seed on first invocation; fail-safe fallback when the store is missing, malformed, or wrong-shape (returns embedded defaults so the guard never breaks the user's terminal).
- **`rtk_signal_guard.sh` — vendored bash guard shipped with the wheel.** PreToolUse-shaped: reads stdin JSON, substring-matches `tool_input.command` against the allowlist (jq when available, embedded defaults fallback). Match → `permissionDecision: allow` (the signal command runs against the real binary, no RTK rewriting). No match → forwards to `rtk hook claude` as before. `shellcheck -S warning` clean.
- **Codex CLI parity for signal commands.** `rtk_codex_shims.py` git and gh shims now inject the passthrough snippet (mirrors the Claude `PreToolUse` guard). `ls` / `grep` / `find` shims untouched (no signal-bearing overhead). Multi-runtime guarantee: a `git push` issued through Codex sees the same passthrough treatment as one issued through Claude Code.
- **`coworker rtk` settings.json v1 → v2 migration.** `cmd_enable` detects a v1 block (no passthrough guard) and rewrites it in place to v2 (idempotent marker `_managed_by: coworker-rtk`, `_version: 2`). Operators upgrading via `pipx upgrade coworker && coworker rtk enable` get the fix without manual `disable + enable`.
- **`coworker rtk status`** reports `passthrough patterns: N (store: <path>)` alongside the existing binary path / version / hook-on/off display.
- **Test coverage.** +19 passthrough pytest cases (CRUD / seed / fallback / env-var override), +5 Codex-shim parity pytest cases (snippet presence, real-binary on signal, rtk-mock on bulk), +4 `test_rtk_plugin.py` cases (defaults seed, status counter, disable preserves store, v1→v2 block replacement), autouse isolation fixture. Full suite: 138 passed, 1 skipped, ruff clean.

### Changed

- **`pyproject.toml` version 0.5.0 → 0.6.0** (minor — additive signal-vs-bulk passthrough feature; no breaking change to existing v0.5.0 invocations).
- **`pyproject.toml` package-data** now includes `*.sh` so the vendored `rtk_signal_guard.sh` ships inside the wheel and resolves correctly under `importlib.resources`.
- **`COWORKER_RTK_VERSION`** internal constant bumped from `1` to `2` to track the settings.json block schema.

### Docs

- `docs/rtk-plugin.md` § Signal/bulk passthrough — new section: default 13-pattern table, CRUD workflow, fallback behaviour (missing `jq`, malformed store), env-var override, v1→v2 migration semantics.
- README `## Optional plugins` — call out the passthrough surface as the recommended way to use RTK across Claude Code, Codex CLI, and the documented Cursor limitation (no native PreToolUse hook integration, full bulk-read cost — see Datarim release notes for v2.23.0 for cross-runtime context).

## [0.5.0] — 2026-05-27

### Fixed (breaking-class)

- **Codex shim PATH no longer pollutes interactive shells.** v0.4.x emitted an unconditional `export PATH="<shim-dir>:$PATH"` into `~/.zprofile` and `~/.bash_profile`, which meant *every* interactive Terminal/IDE/Spotlight/cron shell on the host went through `rtk` for `ls`/`grep`/`find`/etc. Cascading shim invocations under macOS could hang the system enough that the operator had to force-restart (TUNE-0317 dogfood incident 2026-05-27). The emitted block is now gated on a Codex-only PATH substring (`/Users/.../.codex/tmp/arg0/codex-arg0XXX`) that Codex injects into the child shell's PATH *before* sourcing rc files — empirically the only reliable rc-time marker, since Codex sets `$CODEX_CI` only after rc completes. Interactive shells never see that marker, so the export is a no-op for them.
- **Upgrade migration.** `coworker rtk enable` now detects a stale v0.4.x unconditional block and rewrites it in place to the v0.5.0 gated form. Operators upgrading via `pipx upgrade coworker && coworker rtk enable` get the fix without a manual `disable + enable` cycle.
- Removed dead `_build_path_value()` helper from `rtk_codex_shims.py` (relic from a never-shipped codex-config injection design that the top-of-module docstring still described inaccurately).

### Added

- Two new tests in `tests/test_rtk_codex_shims.py`: `test_block_is_codex_scoped_not_unconditional` (asserts the emitted block contains the case-statement gate and the codex arg0 marker, with no bare unconditional export) and `test_inject_migrates_stale_v04_block` (asserts in-place migration when an old block is found). Suite: 110 passing, ruff clean.

### Docs

- Top-of-module docstring in `coworker/plugins/rtk_codex_shims.py` § Design contract item 3 rewritten to describe the actual mechanism (gated login-profile injection) instead of the abandoned codex-config-toml design.

## [0.4.1] — 2026-05-27

### Fixed

- Pre-existing CI red since v0.2.0: `test_file_gate.py` failed on GitHub Actions runners because `cmd_ask` / `cmd_write` called `load_providers()` before applying the file-type gate. On hosts without a populated `~/.config/coworker/providers.yaml`, the subprocess exited 1 with `FileNotFoundError` before the gate could return exit 6. Moved the gate ahead of provider loading — architecturally cleaner (path validity is independent of provider config) and unblocks CI on minimal environments. No new tests; existing `test_file_gate.py` subprocess tests now pass without a populated providers.yaml.

### Docs

- README `coworker write` section: document the new `--append` flag with a concrete example.
- README §Optional plugins: replace the stale «60–90 % reduction» line with cross-agent parity wording pointing at the empirical effectiveness table.
- `docs/rtk-plugin.md`: full intro rewrite with reduction-by-command-class table; new §Cross-agent parity covering shim mechanics; new §Codex one-time hook approval explanation; expanded §Known limitations (signal-command inflation, `rtk cc-economics` breakage, shim coverage scope, Windows status).
- `docs/troubleshooting.md`: three new entries — `git push`/`git pull` agent hang after RTK enable (with verify-by-state workaround), Codex CLI hook-approval prompt explanation (not a bug), `rtk cc-economics` ccusage incompatibility.

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
