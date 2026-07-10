"""CSV / Markdown export formatters for `coworker stats`."""

import csv
import io

from coworker.stats import format_csv, format_markdown

SAMPLE_AGG = {
    "deepseek": {
        "count": 3,
        "sum_input_tokens": 1500,
        "sum_output_tokens": 400,
        "sum_cost_usd": 0.01234,
        "p50_latency_ms": 900,
        "p95_latency_ms": 1800,
        "cache_hit_rate": 0.25,
    },
    "moonshot": {
        "count": 1,
        "sum_input_tokens": 500,
        "sum_output_tokens": 120,
        "sum_cost_usd": 0.05,
        "p50_latency_ms": 700,
        "p95_latency_ms": 700,
        "cache_hit_rate": 0.0,
    },
}

_HEADER = [
    "key",
    "count",
    "sum_input_tokens",
    "sum_output_tokens",
    "sum_cost_usd",
    "p50_latency_ms",
    "p95_latency_ms",
    "cache_hit_rate",
]


def test_csv_header_and_rows():
    out = format_csv(SAMPLE_AGG)
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == _HEADER
    # sorted by key: deepseek before moonshot
    assert rows[1][0] == "deepseek"
    assert rows[2][0] == "moonshot"
    assert len(rows) == 3


def test_csv_values_roundtrip():
    out = format_csv(SAMPLE_AGG)
    rows = list(csv.DictReader(io.StringIO(out)))
    ds = rows[0]
    assert ds["count"] == "3"
    assert ds["sum_input_tokens"] == "1500"
    assert ds["sum_cost_usd"] == "0.01234"
    assert ds["cache_hit_rate"] == "0.25"


def test_csv_empty():
    out = format_csv({})
    rows = list(csv.reader(io.StringIO(out)))
    assert rows == [_HEADER]


def test_markdown_table_structure():
    out = format_markdown(SAMPLE_AGG)
    lines = out.strip().splitlines()
    assert lines[0].startswith("| Key |")
    assert lines[0].endswith("| CacheHit |")
    # separator row
    assert set(lines[1].replace("|", "").replace(" ", "")) == {"-"}
    # header + separator + 2 data rows
    assert len(lines) == 4
    assert "| deepseek |" in lines[2]
    assert "| moonshot |" in lines[3]


def test_markdown_cell_values():
    out = format_markdown(SAMPLE_AGG)
    lines = out.strip().splitlines()
    cells = [c.strip() for c in lines[2].strip("|").split("|")]
    assert cells[0] == "deepseek"
    assert cells[1] == "3"
    assert cells[4] == "0.01234"


def test_markdown_empty_has_header_only():
    out = format_markdown({})
    lines = out.strip().splitlines()
    assert len(lines) == 2  # header + separator, no data rows
