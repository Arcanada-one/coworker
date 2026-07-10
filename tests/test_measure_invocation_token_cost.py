"""Per-invocation token cost measurement (dev-tools/measure-invocation-token-cost.sh).

Runs the shell wrapper as a subprocess against a synthetic JSONL fixture whose
records match coworker/logger.py log_call output, and asserts the aggregation.
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "dev-tools" / "measure-invocation-token-cost.sh"


def _rec(task_id, inp, out, cached=0, cost=0.0, ts=None):
    if ts is None:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ts": ts,
        "gen_ai.system": "deepseek",
        "gen_ai.request.model": "deepseek-v4-flash",
        "coworker.profile": "code",
        "gen_ai.usage.input_tokens": inp,
        "gen_ai.usage.output_tokens": out,
        "gen_ai.usage.cached_tokens": cached,
        "coworker.cost_usd": cost,
        "latency_ms": 100.0,
        "gen_ai.response.finish_reason": "stop",
        "coworker.system_hash": "",
        "coworker.exit_code": 0,
        "coworker.task_id": task_id,
        "coworker.subcommand": "ask",
    }


def _write_log(log_dir, records, name="2026-07-10.jsonl"):
    log_dir.mkdir(parents=True, exist_ok=True)
    f = log_dir / name
    f.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
    return f


def _run(log_dir, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), "--log-dir", str(log_dir), "--format", "json", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def test_script_exists_and_executable():
    assert SCRIPT.exists(), f"missing {SCRIPT}"


def test_aggregates_all_records(tmp_path):
    log_dir = tmp_path / "log"
    _write_log(
        log_dir,
        [
            _rec("TUNE-0145", 1000, 200, cached=100, cost=0.05),
            _rec("TUNE-0145", 500, 50, cached=0, cost=0.01),
            _rec("OTHER-0001", 9000, 900, cost=1.0),
        ],
    )
    out = json.loads(_run(log_dir).stdout)
    assert out["invocations"] == 3
    assert out["input_tokens"] == 10500
    assert out["output_tokens"] == 1150
    assert out["cached_tokens"] == 100
    assert out["total_tokens"] == 11650
    assert abs(out["cost_usd"] - 1.06) < 1e-9


def test_filter_by_task(tmp_path):
    log_dir = tmp_path / "log"
    _write_log(
        log_dir,
        [
            _rec("TUNE-0145", 1000, 200, cost=0.05),
            _rec("TUNE-0145", 500, 50, cost=0.01),
            _rec("OTHER-0001", 9000, 900, cost=1.0),
        ],
    )
    out = json.loads(_run(log_dir, "--task", "TUNE-0145").stdout)
    assert out["task"] == "TUNE-0145"
    assert out["invocations"] == 2
    assert out["input_tokens"] == 1500
    assert out["output_tokens"] == 250
    assert abs(out["cost_usd"] - 0.06) < 1e-9


def test_filter_by_window(tmp_path):
    log_dir = tmp_path / "log"
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_log(
        log_dir,
        [
            _rec("TUNE-0145", 1000, 200, cost=0.05, ts=recent),
            _rec("TUNE-0145", 7777, 777, cost=0.5, ts=old),
        ],
    )
    out = json.loads(_run(log_dir, "--window", "2d").stdout)
    assert out["invocations"] == 1
    assert out["input_tokens"] == 1000
    assert out["output_tokens"] == 200


def test_task_and_window_combined(tmp_path):
    log_dir = tmp_path / "log"
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_log(
        log_dir,
        [
            _rec("TUNE-0145", 1000, 200, cost=0.05, ts=recent),
            _rec("TUNE-0145", 3000, 300, cost=0.3, ts=old),
            _rec("OTHER-0001", 100, 10, cost=0.01, ts=recent),
        ],
    )
    out = json.loads(_run(log_dir, "--task", "TUNE-0145", "--window", "1h").stdout)
    assert out["invocations"] == 1
    assert out["input_tokens"] == 1000


def test_no_matching_records_is_success(tmp_path):
    log_dir = tmp_path / "log"
    _write_log(log_dir, [_rec("OTHER-0001", 100, 10, cost=0.01)])
    proc = _run(log_dir, "--task", "NOPE-9999")
    out = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert out["invocations"] == 0
    assert out["total_tokens"] == 0
    assert out["cost_usd"] == 0.0


def test_missing_log_dir_is_success(tmp_path):
    out = json.loads(_run(tmp_path / "does-not-exist").stdout)
    assert out["invocations"] == 0


def test_malformed_line_skipped(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir(parents=True)
    good = json.dumps(_rec("TUNE-0145", 1000, 200, cost=0.05), separators=(",", ":"))
    (log_dir / "2026-07-10.jsonl").write_text(good + "\n" + "{not json\n" + "\n")
    out = json.loads(_run(log_dir).stdout)
    assert out["invocations"] == 1
    assert out["input_tokens"] == 1000


def test_text_format_smoke(tmp_path):
    log_dir = tmp_path / "log"
    _write_log(log_dir, [_rec("TUNE-0145", 1000, 200, cost=0.05)])
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--log-dir", str(log_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "invocations:   1" in proc.stdout
    assert "cost_usd:      0.050000" in proc.stdout


def test_bad_arg_exits_2(tmp_path):
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_bad_format_exits_2(tmp_path):
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--format", "xml"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


if __name__ == "__main__":
    sys.exit(subprocess.call(["pytest", "-q", __file__]))
