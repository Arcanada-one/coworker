"""Unit tests for scripts/bench_rtk.py — dry-run path, table rendering, compression call.

Live API calls are not exercised here (see tests/test_rtk_live.py). Focus:
deterministic logic that runs offline.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_PATH = REPO_ROOT / "scripts" / "bench_rtk.py"

# Load bench_rtk.py as a module (it isn't installed as a package).
spec = importlib.util.spec_from_file_location("bench_rtk", BENCH_PATH)
bench = importlib.util.module_from_spec(spec)
sys.modules["bench_rtk"] = bench
assert spec.loader is not None
spec.loader.exec_module(bench)


def test_corpus_has_five_entries_with_required_fields():
    assert len(bench.CORPUS) >= 5
    for entry in bench.CORPUS:
        assert {"name", "rtk_filter", "raw"} <= set(entry.keys())
        assert entry["raw"], f"corpus entry {entry['name']} has empty raw"


def test_compress_with_rtk_pipe_calls_subprocess():
    """When rtk is on PATH and filter is given, _compress invokes `rtk pipe --filter <f>`."""
    fake_cp = subprocess.CompletedProcess(args=[], returncode=0, stdout="SHORT", stderr="")
    with patch.object(bench, "_rtk_available", return_value=True), \
         patch("subprocess.run", return_value=fake_cp) as run_mock:
        out = bench._compress("LONG INPUT", "pytest")
    assert out == "SHORT"
    args, kwargs = run_mock.call_args
    assert args[0][:2] == ["rtk", "pipe"]
    assert "--filter" in args[0] and "pytest" in args[0]


def test_compress_falls_back_to_raw_when_rtk_missing():
    with patch.object(bench, "_rtk_available", return_value=False):
        out = bench._compress("RAW PAYLOAD", "pytest")
    assert out == "RAW PAYLOAD"


def test_compress_passthrough_when_filter_none():
    """Some corpus entries (e.g. docker-logs) have rtk_filter=None — must be raw passthrough."""
    out = bench._compress("VERBATIM", None)
    assert out == "VERBATIM"


def test_render_table_emits_markdown_with_headers_and_rows():
    rows = [
        ("moonshot", "pytest-30-passed", 1200, 50),
        ("deepseek", "find-output-50-files", 800, 80),
    ]
    md = bench._render_table(rows)
    assert "| provider |" in md
    assert "|----------|" in md
    assert "| moonshot | pytest-30-passed | 1200 | 50 |" in md
    assert "| deepseek | find-output-50-files | 800 | 80 |" in md
    # delta_% is computed and signed
    assert "-95.8%" in md  # (50-1200)/1200 = -95.83%
    assert "-90.0%" in md


def test_dry_run_mode_emits_table_without_api_calls(tmp_path, monkeypatch):
    """`--dry-run` must produce a byte-count proxy table, exit 0, never touch openai."""
    output = tmp_path / "bench.md"

    # Make sure no API keys leak in
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with patch.object(bench, "_call_provider", side_effect=AssertionError("must not call")):
        rc = bench.main(["--dry-run", "--output", str(output)])

    assert rc == 0
    md = output.read_text()
    assert "| dry-run |" in md
    # one row per corpus entry
    assert md.count("| dry-run |") == len(bench.CORPUS)


def test_main_no_keys_no_dryrun_returns_zero_and_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    rc = bench.main(["--output", str(tmp_path / "bench.md")])

    assert rc == 0  # fail-soft: missing keys → exit 0, no table
    err = capsys.readouterr().err
    assert "MOONSHOT_API_KEY" in err or "DEEPSEEK_API_KEY" in err
