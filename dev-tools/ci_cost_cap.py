#!/usr/bin/env python3
"""CI cost-cap check for the scheduled live-integration workflow.

The workflow runs the shipped `coworker ask` path against the live providers,
then `coworker stats --since 1h --export json` to capture real USD spend, and
pipes that here. When total spend meets or exceeds the cap this prints a GitHub
Actions `::warning::` annotation plus a human-readable line and exits non-zero,
so the scheduled run goes red — the operator's signal to investigate or mute
(disable) the schedule. Under the cap it exits 0 silently.

Stdlib only. Usage:

    python dev-tools/ci_cost_cap.py --stats-json stats.json --cap 5.00
    coworker stats --since 1h --export json | python dev-tools/ci_cost_cap.py --stats-json - --cap 5.00

`--stats-json -` reads stdin. A missing or empty file is treated as zero spend
(exit 0) — an absent-data run must not false-fail, and the summation path is
unit-tested so it can never silently false-*green* on real data either.
"""

from __future__ import annotations

import argparse
import json
import sys


def total_cost(stats: dict) -> float:
    """Sum `sum_cost_usd` across every group in a `coworker stats --export json` blob.

    Values that are missing or non-numeric contribute 0 — the helper never
    raises on a partial/odd stats shape.
    """
    total = 0.0
    for metrics in (stats or {}).values():
        if not isinstance(metrics, dict):
            continue
        val = metrics.get("sum_cost_usd")
        if isinstance(val, (int, float)):
            total += float(val)
    return total


def evaluate(total: float, cap: float) -> tuple[bool, str]:
    """Return (over, message). `over` is True iff total >= cap (inclusive cap)."""
    over = total >= cap
    if over:
        msg = (
            f"weekly live-integration spend ${total:.4f} >= cap ${cap:.4f} — "
            f"investigate or mute (disable) the scheduled run"
        )
    else:
        msg = f"weekly live-integration spend ${total:.4f} within cap ${cap:.4f}"
    return over, msg


def _load_stats(source: str) -> dict:
    """Load stats JSON from a path or '-' (stdin). Missing/empty ⇒ {}."""
    if source == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(source, encoding="utf-8") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return {}
    raw = raw.strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce a weekly USD cost cap on CI spend.")
    parser.add_argument(
        "--stats-json",
        required=True,
        help="Path to `coworker stats --export json` output, or '-' for stdin.",
    )
    parser.add_argument("--cap", required=True, type=float, help="USD cap (inclusive).")
    args = parser.parse_args(argv)

    stats = _load_stats(args.stats_json)
    total = total_cost(stats)
    over, msg = evaluate(total, args.cap)

    if over:
        print(f"::warning::{msg}")
        print(msg, file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
