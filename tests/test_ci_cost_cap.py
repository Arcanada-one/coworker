"""Unit tests for the CI cost-cap helper (dev-tools/ci_cost_cap.py).

The scheduled live-integration workflow runs the shipped `coworker ask` path
against Moonshot + DeepSeek, exports `coworker stats --export json`, and pipes
it here to enforce a weekly USD spend cap. Over the cap the helper must emit a
visible GitHub `::warning::` annotation and exit non-zero (the "mute" signal —
a red scheduled run is what prompts the operator). Under the cap it exits 0
silently. These tests lock that contract, including the `total == cap`
boundary (≥) and the empty-stats ⇒ 0.0 path so the cap can never be silently
false-green on absent data.
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "dev-tools" / "ci_cost_cap.py"

_spec = importlib.util.spec_from_file_location("ci_cost_cap", HELPER)
cc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cc)


# A realistic `coworker stats --export json` shape: keyed by group, each value a
# metrics dict carrying sum_cost_usd (see coworker/stats.py::aggregate_stats).
STATS_TWO = {
    "moonshot": {"count": 1, "sum_cost_usd": 0.0012, "sum_input_tokens": 40},
    "deepseek": {"count": 1, "sum_cost_usd": 0.0003, "sum_input_tokens": 40},
}


def test_helper_file_exists():
    assert HELPER.is_file(), f"missing cost-cap helper at {HELPER}"


def test_total_cost_empty_is_zero():
    assert cc.total_cost({}) == 0.0


def test_total_cost_sums_across_providers():
    assert cc.total_cost(STATS_TWO) == pytest.approx(0.0015)


def test_total_cost_ignores_non_numeric_and_missing():
    stats = {"a": {"sum_cost_usd": 0.5}, "b": {"count": 3}, "c": {"sum_cost_usd": None}}
    assert cc.total_cost(stats) == 0.5


def test_evaluate_under_cap():
    over, msg = cc.evaluate(0.0015, 5.0)
    assert over is False
    assert isinstance(msg, str)


def test_evaluate_at_cap_is_over():
    # boundary: total == cap ⇒ over (>=), so the cap is inclusive.
    over, _ = cc.evaluate(5.0, 5.0)
    assert over is True


def test_evaluate_over_cap():
    over, msg = cc.evaluate(6.0, 5.0)
    assert over is True
    assert "6" in msg and "5" in msg


def _write_stats(tmp_path, obj) -> Path:
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(obj))
    return p


def test_main_under_cap_exit_zero_no_warning(tmp_path, capsys):
    stats = _write_stats(tmp_path, STATS_TWO)
    rc = cc.main(["--stats-json", str(stats), "--cap", "5.0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "::warning::" not in out


def test_main_over_cap_exit_nonzero_with_warning(tmp_path, capsys):
    stats = _write_stats(tmp_path, {"m": {"sum_cost_usd": 9.99}})
    rc = cc.main(["--stats-json", str(stats), "--cap", "5.0"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "::warning::" in out


def test_main_missing_file_is_zero_exit_zero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cc.main(["--stats-json", str(missing), "--cap", "5.0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "::warning::" not in out


def test_main_empty_json_object_exit_zero(tmp_path):
    stats = _write_stats(tmp_path, {})
    rc = cc.main(["--stats-json", str(stats), "--cap", "0.01"])
    assert rc == 0


def test_main_stdin_dash(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"m": {"sum_cost_usd": 1.0}})))
    rc = cc.main(["--stats-json", "-", "--cap", "0.5"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "::warning::" in out
