# Troubleshooting

## "env var 'X_API_KEY' not set"

```bash
[coworker] env var 'DEEPSEEK_API_KEY' not set
```

Either the env var is missing or it's set in a shell `coworker` cannot see (e.g. you exported it in `.zshrc` but `coworker` is invoked from cron / a different shell). Verify with `echo $DEEPSEEK_API_KEY`. Set it where `coworker` actually runs.

## "unknown provider 'X'"

```bash
[coworker] unknown provider 'foo'
```

Either `--provider foo` was passed but `foo` is not a key in `~/.config/coworker/providers.yaml`, or your profile's `recommended_provider` references a missing entry. List configured providers:

```bash
python -c "from coworker.config import load_providers; print(list(load_providers()))"
```

## `FileNotFoundError: providers.yaml not found`

```text
FileNotFoundError: providers.yaml not found at /Users/me/.config/coworker/providers.yaml
```

You haven't bootstrapped the config yet. Copy from the example:

```bash
mkdir -p ~/.config/coworker
curl -fsSL https://raw.githubusercontent.com/Arcanada-one/coworker/main/examples/providers.yaml.example \
  > ~/.config/coworker/providers.yaml
curl -fsSL https://raw.githubusercontent.com/Arcanada-one/coworker/main/examples/profiles.yaml.example \
  > ~/.config/coworker/profiles.yaml
```

## "401 Unauthorized" from the provider

```bash
openai.AuthenticationError: Error code: 401
```

Your key is wrong, expired, revoked, or — if you recently rotated — the cache layer (`~/.local/state/coworker/`) has nothing to do with it; the call goes straight to the provider. Get a fresh key from the provider's console and re-export it.

## "429 Too Many Requests"

```bash
openai.RateLimitError: Error code: 429
```

You're hitting the provider's rate limit. `coworker` does not retry — by design (silent retries hide cost). Options:

- Wait and re-run.
- Switch provider for that call: `--provider <other>`.
- Reduce `--max-tokens`.
- For Groq specifically, free-tier accounts hit limits fast — consider DeepSeek for high-volume work.

## Empty response

```bash
[coworker] empty response — try raising --max-tokens.
```

The model returned `""`. Usually means `max_tokens` is too low — the model wrote a thinking preamble (you can't see it for non-reasoning models) and ran out of budget before the actual answer. Raise it:

```bash
coworker ask --max-tokens 32000 ...
```

## "no blob found for hash prefix"

```bash
[coworker] no blob found for hash prefix '8a3f'
```

You're running `coworker debug --hash 8a3f` but no corpus blob with that prefix exists. Two common causes:

- You never set `COWORKER_LOG_CORPUS=1` for the call you're trying to inspect — only metadata was logged, not the corpus.
- You wiped `~/.local/state/coworker/blobs/` since the call.

`coworker stats --format json | jq '.[] | .corpus_hash // empty'` shows which logged calls have a blob.

## `pip install` fails with "No matching distribution"

You're on Python < 3.10. `coworker` requires 3.10+. Check with `python --version`. If your system Python is older, install via `pyenv` or `conda` and retry.

## `coworker` not on $PATH after install

Some pip configurations install console scripts into a directory that isn't on `$PATH` (commonly `~/.local/bin` on Linux without `~/.local/bin` in `$PATH`, or per-user macOS Python installs). Workarounds:

- Add the install location to `$PATH`. `pip show -f coworker | grep coworker$` to find it.
- Run via `python -m coworker.cli` instead — works regardless of `$PATH`.

## CI wants `pip-audit` and it fails on a CVE

```bash
pip-audit --strict
# Found 1 vulnerability ...
```

A new CVE was published against one of the runtime deps (`openai`, `pyyaml`, `httpx`, etc.). Check if a fix is available; bump the lower bound in `pyproject.toml`. If no fix exists yet, document the accepted-risk in the CI pin and revisit weekly.

## "yaml.YAMLError" loading config

You hand-edited `providers.yaml` or `profiles.yaml` and made a syntax mistake. `coworker` uses `yaml.safe_load` — typical errors are unbalanced quotes, mis-indented block scalars, or tabs (YAML wants spaces). Validate:

```bash
python -c "import yaml; yaml.safe_load(open('$HOME/.config/coworker/providers.yaml'))"
```

The traceback points at the offending line.

## `git push` / `git pull` agent hangs after `coworker rtk enable`

Symptom: your agent runs `git push origin <branch>`, the tool result
comes back unusually short (or missing the canonical
`To github.com:...` line), and the agent then re-runs the command in the
background and sits for minutes treating it as hung when the push
actually succeeded on the first try.

Cause: the RTK rewrite hook treats `git push` / `git pull` / `git fetch`
/ `git merge` / `gh pr create` like bulk listings and can strip or
reshape the canonical control-signal markers an agent waits for. On a
clean repo this is especially visible — RTK can inflate already-tiny
output (`git status` on a clean repo goes from ~59 bytes raw to ~123
bytes rewritten) or, depending on rtk version, replace the canonical
marker with a stub.

Workarounds:

1. **Verify by state.** Teach your agent to compare
   `git rev-parse HEAD` against `git rev-parse @{u}` after every push.
   If they match, the push succeeded regardless of what RTK did with
   stdout. This is the most robust fix and applies to any future
   output-rewriter.
2. **Temporary disable for the session.** Run `coworker rtk disable`,
   do the git operation, then `coworker rtk enable` again. The marker
   flip is instant; no PATH change.
3. **Permanent passthrough** (manual, until v0.4.x ships a flag): edit
   `~/.claude/settings.json` and add a guard before the `rtk hook
   claude` command that bypasses the rewrite for signal commands. Track
   the canonical fix at the project backlog (signal-vs-bulk command
   classification in upstream RTK).

## Codex CLI prompts to approve hooks on first session after `coworker rtk enable`

Codex CLI 0.133.0+ hashes every PreToolUse / session hook and prompts
the operator on first encounter (or after the hash changes). This is
Codex's hook-trust mechanism, not a regression in coworker. Approve
once; subsequent sessions run without prompts until the hooks change.

The shim layer that gives Codex RTK parity does **not** appear in this
prompt — Codex treats shell-environment `PATH` changes as ordinary user
config. The prompt relates to whatever's in `~/.codex/hooks.json`, which
coworker does not modify in v0.4.0.

## `rtk cc-economics` crashes with «missing field 'month'»

Known incompatibility between rtk 0.40.0 and current `ccusage` JSON
schema. Other rtk subcommands (`gain`, `discover`, `session`) work
fine. Workaround: read `rtk gain` and `ccusage` separately for now.

## Still stuck

Open an issue at https://github.com/Arcanada-one/coworker/issues with:

- `coworker --help` output (proves install).
- The exact command you ran.
- Full stderr + first 20 lines of any traceback.
- Output of `python --version` and `pip show coworker | head -5`.

**Do NOT paste your API keys, your `~/.config/coworker/providers.yaml` with real keys substituted in, or full corpus blobs.** Sanitise first.
