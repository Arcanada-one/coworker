"""Cost calculation correctness — math invariants."""

from coworker.pricing import calc_cost

PROV_WITH_PRICING = {
    "pricing": {
        "kimi-k2.6": {"input": 0.95, "output": 4.00, "cache_input": 0.10},
    }
}


def test_zero_when_pricing_dict_missing():
    assert calc_cost({}, "any-model", 1000, 1000) == 0.0


def test_zero_when_model_not_in_pricing(capsys):
    assert calc_cost(PROV_WITH_PRICING, "unlisted-model", 1000, 1000) == 0.0
    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert "unlisted-model" in captured.err


def test_basic_cost_math():
    # 1M input * 0.95 + 1M output * 4.00 = 0.95 + 4.00 = 4.95
    cost = calc_cost(PROV_WITH_PRICING, "kimi-k2.6", 1_000_000, 1_000_000)
    assert cost == 4.95


def test_cache_discount_applied():
    # 1M cached @ 0.10 vs 1M regular @ 0.95 -> 0.10 + (0 output)
    cost = calc_cost(PROV_WITH_PRICING, "kimi-k2.6", 0, 0, cached_tokens=1_000_000)
    assert cost == 0.10


def test_combined_input_output_cached():
    # 100k in @ 0.95 + 50k out @ 4.0 + 200k cached @ 0.10 = 0.095 + 0.20 + 0.02 = 0.315
    cost = calc_cost(PROV_WITH_PRICING, "kimi-k2.6", 100_000, 50_000, cached_tokens=200_000)
    assert abs(cost - 0.315) < 1e-9
