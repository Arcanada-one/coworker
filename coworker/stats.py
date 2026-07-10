"""Stats helpers + cmd_stats subcommand."""

import csv
import datetime
import io
import json
import pathlib
import sys

from .config import LOG_DIR


def parse_logs(
    log_dir: pathlib.Path = LOG_DIR,
    since_dt: datetime.datetime | None = None,
) -> list[dict]:
    """Glob JSONL files and parse records; skip malformed lines with warning."""
    records: list[dict] = []
    if not log_dir.exists():
        return records
    for f in sorted(log_dir.glob("*.jsonl")):
        for line_no, raw in enumerate(f.read_text().splitlines(), 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
                if since_dt:
                    ts = datetime.datetime.fromisoformat(
                        rec.get("ts", "").replace("Z", "+00:00")
                    )
                    if ts < since_dt:
                        continue
                records.append(rec)
            except Exception as e:
                print(f"[coworker] malformed log line {f}:{line_no}: {e}", file=sys.stderr)
    return records


def aggregate_stats(records: list[dict], by: str = "provider") -> dict:
    """Aggregate records by field; returns nested dict of metrics."""
    groups: dict[str, list] = {}
    for rec in records:
        if by == "provider":
            key = rec.get("gen_ai.system", "unknown")
        elif by == "profile":
            key = rec.get("coworker.profile", "unknown")
        elif by == "model":
            key = rec.get("gen_ai.request.model", "unknown")
        else:
            key = f"{rec.get('gen_ai.system','?')}:{rec.get('coworker.profile','?')}"
        groups.setdefault(key, []).append(rec)

    result = {}
    for key, recs in groups.items():
        latencies = sorted(r.get("latency_ms", 0) for r in recs)
        input_tokens_list = [r.get("gen_ai.usage.input_tokens", 0) for r in recs]
        cached_tokens_total = sum(r.get("gen_ai.usage.cached_tokens", 0) for r in recs)
        sum_input = sum(input_tokens_list)

        n = len(latencies)
        p50 = latencies[n // 2] if latencies else 0
        p95 = latencies[int(n * 0.95)] if latencies else 0

        result[key] = {
            "count": len(recs),
            "sum_input_tokens": sum_input,
            "sum_output_tokens": sum(r.get("gen_ai.usage.output_tokens", 0) for r in recs),
            "sum_cost_usd": round(sum(r.get("coworker.cost_usd", 0) for r in recs), 6),
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "cache_hit_rate": round(cached_tokens_total / sum_input, 3) if sum_input else 0.0,
        }
    return result


def parse_since(since_str: str) -> datetime.datetime | None:
    if since_str == "all":
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    if since_str.endswith("d"):
        return now - datetime.timedelta(days=int(since_str[:-1]))
    if since_str.endswith("h"):
        return now - datetime.timedelta(hours=int(since_str[:-1]))
    return None



_COLUMNS = [
    ("key", "Key"),
    ("count", "Count"),
    ("sum_input_tokens", "InputTok"),
    ("sum_output_tokens", "OutTok"),
    ("sum_cost_usd", "Cost$"),
    ("p50_latency_ms", "P50ms"),
    ("p95_latency_ms", "P95ms"),
    ("cache_hit_rate", "CacheHit"),
]


def _rows(agg: dict) -> list[dict]:
    """Flatten aggregate dict into per-key rows (sorted by key)."""
    rows = []
    for key, m in sorted(agg.items()):
        row = {"key": key}
        row.update(m)
        rows.append(row)
    return rows


def format_csv(agg: dict) -> str:
    """Render aggregate as CSV (header + one row per key)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([field for field, _ in _COLUMNS])
    for row in _rows(agg):
        writer.writerow([row.get(field, "") for field, _ in _COLUMNS])
    return buf.getvalue()


def format_markdown(agg: dict) -> str:
    """Render aggregate as a GitHub-flavored Markdown table."""
    headers = [label for _, label in _COLUMNS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in _rows(agg):
        cells = [str(row.get(field, "")) for field, _ in _COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return '\n'.join(lines) + '\n'


def cmd_stats(args) -> int:
    since_dt = parse_since(args.since)
    records = parse_logs(log_dir=LOG_DIR, since_dt=since_dt)

    if args.provider:
        records = [r for r in records if r.get("gen_ai.system") == args.provider]
    if args.profile:
        records = [r for r in records if r.get("coworker.profile") == args.profile]

    export = getattr(args, "export", None)

    if not records:
        if export == "csv":
            print(format_csv({}), end="")
        elif export == "markdown":
            print(format_markdown({}), end="")
        elif args.format == "json":
            print("{}")
        else:
            print("(no records)")
        return 0

    agg = aggregate_stats(records, by=args.by)

    if export == "csv":
        print(format_csv(agg), end="")
        return 0
    if export == "markdown":
        print(format_markdown(agg), end="")
        return 0

    if args.format == "json":
        print(json.dumps(agg, indent=2))
        return 0

    hdr = (
        f"{'Key':<40} {'Count':>6} {'InputTok':>10} {'OutTok':>8} "
        f"{'Cost$':>8} {'P50ms':>7} {'P95ms':>7} {'CacheHit':>9}"
    )
    print(hdr)
    print("-" * len(hdr))
    for key, m in sorted(agg.items()):
        print(
            f"{key:<40} {m['count']:>6} {m['sum_input_tokens']:>10} "
            f"{m['sum_output_tokens']:>8} {m['sum_cost_usd']:>8.5f} "
            f"{int(m['p50_latency_ms']):>7} {int(m['p95_latency_ms']):>7} "
            f"{m['cache_hit_rate']:>9.3f}"
        )
    return 0
