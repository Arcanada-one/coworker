# Scheduled live-integration CI

`coworker` ships gated live tests (`tests/test_rtk_live.py`) that call the real
Moonshot and DeepSeek APIs. They are skipped unless `RUN_LIVE_TESTS=1` and the
matching API keys are set, so the offline `ci` workflow never touches them. The
`live-integration` workflow (`.github/workflows/live-integration.yml`) runs those
checks on a schedule so provider drift — a changed `base_url`, an API-shape
break, an auth or pricing regression — is caught without anyone remembering to
run them by hand.

## What it does

On each run the job:

1. Installs the package and seeds `providers.yaml` + `profiles.yaml` from the
   `examples/` templates.
2. Runs the gated live suite with `RUN_LIVE_TESTS=1` (both providers must return
   a real response; the RTK-compression assertions skip when the `rtk` binary is
   absent, which it is on a stock runner).
3. Exercises the shipped `coworker ask` path once per provider, which logs real
   token usage and USD cost.
4. Exports spend with `coworker stats --since 1h --format json` and enforces a
   weekly USD cost cap (`dev-tools/ci_cost_cap.py`). At or above the cap the run
   fails with a `::warning::` annotation — the cue to investigate or disable
   (mute) the schedule.

Typical spend per run is a fraction of a cent.

## When it runs

- **Weekly**, Mondays 04:17 UTC (`schedule` cron).
- **On demand**, via **Run workflow** (`workflow_dispatch`), optionally with a
  one-off `cost_cap_usd` override.

It deliberately does **not** run on pull requests or pushes: real quota must not
be spent on every change, and repo secrets must never be reachable from a fork
pull request. Because the workflow triggers only on `schedule` and
`workflow_dispatch` against the default branch, fork PRs can neither trigger it
nor read its secrets.

## One-time setup (repository maintainer)

1. Add two repository secrets under **Settings → Secrets and variables →
   Actions**:
   - `MOONSHOT_API_KEY`
   - `DEEPSEEK_API_KEY`
2. *(Optional)* Add a repository variable `COWORKER_WEEKLY_COST_CAP_USD` to set
   the cap. Without it the workflow's built-in default (`5.00`) applies. A manual
   dispatch can override the cap for a single run via the `cost_cap_usd` input.

Until step 1 is done the workflow still runs on schedule, but its first step
finds no keys, prints a `::notice::` and skips every live step — the run is
green and free. A missing key is a setup state, not a regression, so it must not
show up as a permanently failing weekly check.

## Cost-cap helper

`dev-tools/ci_cost_cap.py` is a stdlib-only script: it sums `sum_cost_usd` across
every group of a `coworker stats --format json` blob and exits non-zero (with a
GitHub `::warning::`) when the total meets or exceeds `--cap`. A missing or empty
stats file counts as zero spend and exits 0, so an absent-data run never
false-fails.

```bash
coworker stats --since 1h --format json \
  | python dev-tools/ci_cost_cap.py --stats-json - --cap 5.00
```
