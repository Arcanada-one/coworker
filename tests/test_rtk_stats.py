"""RTK-savings telemetry + stats aggregation (TUNE-0274).

Covers the RTK signal env contract, the additive log_call fields, backwards-compatible
aggregation and the separate "RTK-saved tokens" stats line.
"""

import json
import types

from coworker.logger import log_call, read_rtk_signal
from coworker.stats import aggregate_stats, cmd_stats

# ---------- env signal contract ----------


def test_read_rtk_signal_unset_defaults():
    used, savings = read_rtk_signal(env={})
    assert used is False
    assert savings == 0


def test_read_rtk_signal_truthy_variants():
    for val in ("1", "true", "TRUE", "Yes", "on"):
        used, _ = read_rtk_signal(env={"COWORKER_RTK_USED": val})
        assert used is True, val
    for val in ("0", "false", "no", "", "off"):
        used, _ = read_rtk_signal(env={"COWORKER_RTK_USED": val})
        assert used is False, val


def test_read_rtk_signal_savings_parsed():
    used, savings = read_rtk_signal(
        env={"COWORKER_RTK_USED": "1", "COWORKER_RTK_SAVINGS": "1234"}
    )
    assert used is True
    assert savings == 1234


def test_read_rtk_signal_malformed_savings_is_zero():
    _, s1 = read_rtk_signal(env={"COWORKER_RTK_USED": "1", "COWORKER_RTK_SAVINGS": "abc"})
    _, s2 = read_rtk_signal(env={"COWORKER_RTK_USED": "1", "COWORKER_RTK_SAVINGS": "-99"})
    assert s1 == 0
    assert s2 == 0


def test_read_rtk_signal_savings_without_used_flag():
    # savings present but used flag falsy → still honours the (absent) used flag
    used, savings = read_rtk_signal(env={"COWORKER_RTK_SAVINGS": "500"})
    assert used is False
    assert savings == 500


# ---------- log_call record fields ----------


def _fake_resp(prompt=100, completion=20):
    usage = types.SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=None,
        cached_tokens=0,
    )
    choice = types.SimpleNamespace(finish_reason="stop")
    return types.SimpleNamespace(usage=usage, choices=[choice])


def _read_only_record(log_dir):
    files = sorted(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [ln for ln in files[0].read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


def test_log_call_records_rtk_fields_when_used(tmp_path):
    log_dir = tmp_path / "log"
    log_call(
        _fake_resp(), "deepseek", {}, "deepseek-v4-flash", "code", "ask",
        [], "out", 100.0, "TUNE-0274", log_dir=log_dir,
        rtk_used=True, rtk_savings_estimate=1234,
    )
    rec = _read_only_record(log_dir)
    assert rec["coworker.rtk_used"] is True
    assert rec["coworker.rtk_savings_estimate"] == 1234


def test_log_call_omits_rtk_fields_when_unused(tmp_path):
    log_dir = tmp_path / "log"
    log_call(
        _fake_resp(), "deepseek", {}, "deepseek-v4-flash", "code", "ask",
        [], "out", 100.0, "TUNE-0274", log_dir=log_dir,
    )
    rec = _read_only_record(log_dir)
    # legacy-clean record: no RTK keys emitted at all
    assert "coworker.rtk_used" not in rec
    assert "coworker.rtk_savings_estimate" not in rec


# ---------- aggregate_stats ----------


def _rec(provider="deepseek", rtk_used=None, savings=None):
    r = {
        "gen_ai.system": provider,
        "coworker.profile": "code",
        "gen_ai.request.model": "m",
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.output_tokens": 20,
        "gen_ai.usage.cached_tokens": 0,
        "coworker.cost_usd": 0.01,
        "latency_ms": 100,
    }
    if rtk_used is not None:
        r["coworker.rtk_used"] = rtk_used
    if savings is not None:
        r["coworker.rtk_savings_estimate"] = savings
    return r


def test_aggregate_backwards_compat_zero():
    agg = aggregate_stats([_rec(), _rec()], by="provider")
    m = agg["deepseek"]
    assert m["sum_rtk_savings"] == 0
    assert m["rtk_used_count"] == 0
    # pre-existing metrics unchanged
    assert m["count"] == 2
    assert m["sum_input_tokens"] == 200


def test_aggregate_sums_rtk_savings():
    recs = [
        _rec(rtk_used=True, savings=1000),
        _rec(rtk_used=True, savings=234),
        _rec(rtk_used=False, savings=0),
        _rec(),  # legacy, no fields
    ]
    m = aggregate_stats(recs, by="provider")["deepseek"]
    assert m["sum_rtk_savings"] == 1234
    assert m["rtk_used_count"] == 2


# ---------- cmd_stats output ----------


class _Args:
    def __init__(self, **kw):
        self.since = "all"
        self.provider = None
        self.profile = None
        self.by = "provider"
        self.format = "table"
        self.export = None
        for k, v in kw.items():
            setattr(self, k, v)


def _seed_log(log_dir, recs):
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "2026-07-22.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs)
    )


def test_stats_human_prints_rtk_line(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "log"
    _seed_log(log_dir, [_rec(rtk_used=True, savings=1000), _rec(rtk_used=True, savings=234)])
    monkeypatch.setattr("coworker.stats.LOG_DIR", log_dir)
    cmd_stats(_Args())
    out = capsys.readouterr().out
    assert "RTK-saved tokens: 1234" in out


def test_stats_human_no_rtk_line_when_zero(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "log"
    _seed_log(log_dir, [_rec(), _rec()])
    monkeypatch.setattr("coworker.stats.LOG_DIR", log_dir)
    cmd_stats(_Args())
    out = capsys.readouterr().out
    assert "RTK-saved tokens" not in out


def test_stats_json_has_rtk_key(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "log"
    _seed_log(log_dir, [_rec(rtk_used=True, savings=1000)])
    monkeypatch.setattr("coworker.stats.LOG_DIR", log_dir)
    cmd_stats(_Args(format="json"))
    out = json.loads(capsys.readouterr().out)
    assert out["deepseek"]["sum_rtk_savings"] == 1000
