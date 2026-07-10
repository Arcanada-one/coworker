#!/usr/bin/env bash
#
# measure-invocation-token-cost.sh — per-invocation token cost measurement.
#
# Parses coworker's JSONL call log (one record per LLM invocation) and
# aggregates prompt (input) + completion (output) tokens and USD cost,
# optionally filtered by task ID and a trailing time window.
#
# The log lives under coworker's XDG state dir as one file per UTC day:
#   ${XDG_STATE_HOME:-$HOME/.local/state}/coworker/log/<YYYY-MM-DD>.jsonl
# (Historic prose in some backlogs calls this "~/.local/share/coworker/log.jsonl";
#  the real, canonical location is the per-day glob above — see coworker/config.py
#  LOG_DIR and coworker/logger.py write_jsonl_metadata.)
#
# Each record (fields defined by coworker/logger.py log_call) carries:
#   ts                            ISO-8601 UTC, e.g. 2026-07-10T12:34:56Z
#   gen_ai.usage.input_tokens     prompt tokens
#   gen_ai.usage.output_tokens    completion tokens
#   gen_ai.usage.cached_tokens    cached prompt tokens
#   coworker.cost_usd             computed USD cost
#   coworker.task_id              task ID (may be null)
#   coworker.profile / .subcommand, gen_ai.system / .request.model, latency_ms
#
# Usage:
#   measure-invocation-token-cost.sh [--task <ID>] [--window <duration>]
#                                    [--log-dir <dir>] [--format text|json]
#
#   --task    <ID>        Only count invocations whose coworker.task_id == ID.
#   --window  <duration>  Only count invocations within the trailing window,
#                         measured from "now" (UTC). Accepts <N>d, <N>h, <N>m,
#                         or "all" (default: all).
#   --log-dir <dir>       Override the log directory (default: coworker LOG_DIR;
#                         env COWORKER_LOG_DIR also honored).
#   --format  text|json   Output format (default: text).
#
# Exit codes: 0 on success (including "no matching records"), 2 on bad args.

set -euo pipefail

TASK=""
WINDOW="all"
LOG_DIR="${COWORKER_LOG_DIR:-}"
FORMAT="text"

usage() {
  sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task)
      [ "$#" -ge 2 ] || { echo "error: --task needs a value" >&2; exit 2; }
      TASK="$2"; shift 2 ;;
    --window)
      [ "$#" -ge 2 ] || { echo "error: --window needs a value" >&2; exit 2; }
      WINDOW="$2"; shift 2 ;;
    --log-dir)
      [ "$#" -ge 2 ] || { echo "error: --log-dir needs a value" >&2; exit 2; }
      LOG_DIR="$2"; shift 2 ;;
    --format)
      [ "$#" -ge 2 ] || { echo "error: --format needs a value" >&2; exit 2; }
      FORMAT="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

case "$FORMAT" in
  text|json) ;;
  *) echo "error: --format must be text or json" >&2; exit 2 ;;
esac

PY="${PYTHON:-python3}"

# All parsing/aggregation in Python (stdlib only) for robust JSONL handling and
# window arithmetic — no jq dependency. Arguments are passed via env to avoid
# any shell-quoting/injection surface.
COWORKER_MC_TASK="$TASK" \
COWORKER_MC_WINDOW="$WINDOW" \
COWORKER_MC_LOG_DIR="$LOG_DIR" \
COWORKER_MC_FORMAT="$FORMAT" \
"$PY" - <<'PYEOF'
import datetime
import glob
import json
import os
import pathlib
import sys


def resolve_log_dir() -> pathlib.Path:
    override = os.environ.get("COWORKER_MC_LOG_DIR", "")
    if override:
        return pathlib.Path(override)
    state = os.environ.get(
        "XDG_STATE_HOME", str(pathlib.Path.home() / ".local" / "state")
    )
    return pathlib.Path(state) / "coworker" / "log"


def parse_window(win: str):
    """Return a timezone-aware cutoff datetime, or None for 'all'."""
    win = (win or "all").strip()
    if win == "all":
        return None
    unit = win[-1:]
    num = win[:-1]
    try:
        n = int(num)
    except ValueError:
        print(f"error: invalid --window value: {win!r}", file=sys.stderr)
        sys.exit(2)
    now = datetime.datetime.now(datetime.timezone.utc)
    if unit == "d":
        return now - datetime.timedelta(days=n)
    if unit == "h":
        return now - datetime.timedelta(hours=n)
    if unit == "m":
        return now - datetime.timedelta(minutes=n)
    print(f"error: invalid --window unit in {win!r} (use d/h/m or 'all')", file=sys.stderr)
    sys.exit(2)


def parse_ts(rec):
    ts = rec.get("ts", "")
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> int:
    task = os.environ.get("COWORKER_MC_TASK", "") or None
    cutoff = parse_window(os.environ.get("COWORKER_MC_WINDOW", "all"))
    fmt = os.environ.get("COWORKER_MC_FORMAT", "text")
    log_dir = resolve_log_dir()

    count = 0
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    cost = 0.0

    files = sorted(glob.glob(str(log_dir / "*.jsonl")))
    for f in files:
        try:
            lines = pathlib.Path(f).read_text().splitlines()
        except OSError as e:
            print(f"[measure] cannot read {f}: {e}", file=sys.stderr)
            continue
        for line_no, raw in enumerate(lines, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"[measure] malformed log line {f}:{line_no}: {e}", file=sys.stderr)
                continue
            if task is not None and rec.get("coworker.task_id") != task:
                continue
            if cutoff is not None:
                ts = parse_ts(rec)
                if ts is None or ts < cutoff:
                    continue
            count += 1
            input_tokens += int(rec.get("gen_ai.usage.input_tokens", 0) or 0)
            output_tokens += int(rec.get("gen_ai.usage.output_tokens", 0) or 0)
            cached_tokens += int(rec.get("gen_ai.usage.cached_tokens", 0) or 0)
            cost += float(rec.get("coworker.cost_usd", 0) or 0)

    total_tokens = input_tokens + output_tokens
    result = {
        "task": task or "*",
        "window": os.environ.get("COWORKER_MC_WINDOW", "all"),
        "invocations": count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": total_tokens,
        "cost_usd": round(cost, 6),
    }

    if fmt == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"task:          {result['task']}")
        print(f"window:        {result['window']}")
        print(f"invocations:   {result['invocations']}")
        print(f"input_tokens:  {result['input_tokens']}")
        print(f"output_tokens: {result['output_tokens']}")
        print(f"cached_tokens: {result['cached_tokens']}")
        print(f"total_tokens:  {result['total_tokens']}")
        print(f"cost_usd:      {result['cost_usd']:.6f}")
    return 0


sys.exit(main())
PYEOF
