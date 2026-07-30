"""Unit tests for `coworker rtk economics` — the resilient combined
spending-vs-savings view.

Background: upstream `rtk cc-economics` aborts on the current `ccusage`
JSON schema because ccusage renamed each monthly row's key from ``month``
to ``period``. The RTK binary lives in an upstream repo we cannot patch
here, so coworker reconstructs the combined view natively from the two
healthy data sources (``rtk gain --format json`` + ``ccusage monthly
--json``), tolerant to both the current and legacy schema and degrading
gracefully when either half is unavailable.

Tests are payload-driven: no network, no live subprocess. The two data
runners (`_run_rtk_gain_json`, `_run_ccusage_json`) are monkeypatched to
return canned payloads so the command logic and parsers are exercised
deterministically.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from coworker.plugins import rtk

# ---------- fixtures: representative payloads ----------

# Current ccusage schema — monthly rows keyed by `period` (NO `month` field).
CCUSAGE_CURRENT = {
    "monthly": [
        {"agent": "all", "period": "2026-06", "totalCost": 3308.44, "totalTokens": 4560704598},
        {"agent": "all", "period": "2026-07", "totalCost": 16782.60, "totalTokens": 22151197615},
    ],
    "totals": {"totalCost": 20091.04, "totalTokens": 26711902213},
}

# Legacy ccusage schema — monthly rows keyed by `month`, no top-level totals.
CCUSAGE_LEGACY = {
    "monthly": [
        {"month": "2026-05", "totalCost": 100.0, "totalTokens": 1000},
        {"month": "2026-06", "totalCost": 200.0, "totalTokens": 2000},
    ],
}

GAIN_OK = {
    "summary": {
        "total_input": 299644981,
        "total_output": 27500608,
        "total_saved": 276638689,
        "avg_savings_pct": 92.32,
    }
}


# ---------- _period_key: schema tolerance ----------


def test_period_key_prefers_month_legacy():
    assert rtk._period_key({"month": "2026-05", "period": "x"}, 0) == "2026-05"


def test_period_key_falls_back_to_period_current():
    # Current ccusage: no `month`, only `period`. This is the backlog's
    # "missing field 'month'" case that crashes the upstream binary.
    assert rtk._period_key({"period": "2026-07"}, 3) == "2026-07"


def test_period_key_falls_back_to_date():
    assert rtk._period_key({"date": "2026-07-01"}, 3) == "2026-07-01"


def test_period_key_falls_back_to_index_when_all_absent():
    key = rtk._period_key({"totalCost": 1.0}, 4)
    assert "4" in key  # index-based label, never raises


# ---------- _parse_ccusage ----------


def test_parse_ccusage_current_schema_uses_totals():
    out = rtk._parse_ccusage(CCUSAGE_CURRENT)
    assert [r["period"] for r in out["rows"]] == ["2026-06", "2026-07"]
    assert out["total_cost"] == pytest.approx(20091.04)
    assert out["total_tokens"] == 26711902213


def test_parse_ccusage_legacy_schema_sums_rows_when_no_totals():
    out = rtk._parse_ccusage(CCUSAGE_LEGACY)
    assert [r["period"] for r in out["rows"]] == ["2026-05", "2026-06"]
    # No top-level totals → summed from rows.
    assert out["total_cost"] == pytest.approx(300.0)
    assert out["total_tokens"] == 3000


def test_parse_ccusage_row_missing_month_does_not_raise():
    # The exact failure mode of the upstream binary — must be tolerated.
    payload = {"monthly": [{"period": "2026-07", "totalCost": 5.0, "totalTokens": 50}]}
    out = rtk._parse_ccusage(payload)
    assert out["rows"][0]["period"] == "2026-07"
    assert out["total_cost"] == pytest.approx(5.0)


def test_parse_ccusage_tolerates_missing_cost_fields():
    payload = {"monthly": [{"period": "2026-07"}]}
    out = rtk._parse_ccusage(payload)
    assert out["rows"][0]["cost"] == 0
    assert out["total_cost"] == 0


# ---------- _parse_gain ----------


def test_parse_gain_extracts_summary():
    out = rtk._parse_gain(GAIN_OK)
    assert out["total_saved"] == 276638689
    assert out["avg_savings_pct"] == pytest.approx(92.32)


def test_parse_gain_tolerates_missing_fields():
    out = rtk._parse_gain({"summary": {}})
    assert out["total_saved"] == 0
    assert out["avg_savings_pct"] == 0


# ---------- cmd_economics: orchestration + degradation ----------


def _args(fmt="text"):
    return SimpleNamespace(format=fmt)


def test_economics_both_present_text_rc0(monkeypatch, capsys):
    monkeypatch.setattr(rtk, "_run_ccusage_json", lambda: CCUSAGE_CURRENT)
    monkeypatch.setattr(rtk, "_run_rtk_gain_json", lambda: GAIN_OK)
    rc = rtk.cmd_economics(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "2026-07" in out          # spending half rendered
    assert "276638689" in out or "276,638,689" in out  # savings half rendered


def test_economics_ccusage_missing_degrades_savings_only_rc0(monkeypatch, capsys):
    monkeypatch.setattr(rtk, "_run_ccusage_json", lambda: None)
    monkeypatch.setattr(rtk, "_run_rtk_gain_json", lambda: GAIN_OK)
    rc = rtk.cmd_economics(_args())
    captured = capsys.readouterr()
    assert rc == 0
    # savings half present, degradation notice emitted.
    assert "savings" in (captured.out + captured.err).lower()
    assert "ccusage" in (captured.out + captured.err).lower()


def test_economics_gain_missing_degrades_spend_only_rc0(monkeypatch, capsys):
    monkeypatch.setattr(rtk, "_run_ccusage_json", lambda: CCUSAGE_CURRENT)
    monkeypatch.setattr(rtk, "_run_rtk_gain_json", lambda: None)
    rc = rtk.cmd_economics(_args())
    captured = capsys.readouterr()
    assert rc == 0
    assert "2026-07" in captured.out  # spend half present


def test_economics_both_missing_rc1(monkeypatch, capsys):
    monkeypatch.setattr(rtk, "_run_ccusage_json", lambda: None)
    monkeypatch.setattr(rtk, "_run_rtk_gain_json", lambda: None)
    rc = rtk.cmd_economics(_args())
    assert rc == 1


def test_economics_json_format_shape(monkeypatch, capsys):
    monkeypatch.setattr(rtk, "_run_ccusage_json", lambda: CCUSAGE_CURRENT)
    monkeypatch.setattr(rtk, "_run_rtk_gain_json", lambda: GAIN_OK)
    rc = rtk.cmd_economics(_args(fmt="json"))
    out = capsys.readouterr().out
    assert rc == 0
    doc = json.loads(out)
    assert set(doc.keys()) >= {"spending", "savings", "degraded"}
    assert doc["degraded"] is False
    assert doc["spending"]["total_cost"] == pytest.approx(20091.04)
    assert doc["savings"]["total_saved"] == 276638689


def test_economics_json_degraded_flag_true_when_half_missing(monkeypatch, capsys):
    monkeypatch.setattr(rtk, "_run_ccusage_json", lambda: None)
    monkeypatch.setattr(rtk, "_run_rtk_gain_json", lambda: GAIN_OK)
    rc = rtk.cmd_economics(_args(fmt="json"))
    out = capsys.readouterr().out
    assert rc == 0
    doc = json.loads(out)
    assert doc["degraded"] is True
    assert doc["spending"] is None
    assert doc["savings"]["total_saved"] == 276638689
