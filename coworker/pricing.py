"""USD cost calculation per provider/model/tokens."""

import sys


def calc_cost(
    provider_cfg: dict,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Return USD cost. Returns 0.0 if model not in provider's pricing dict."""
    pricing_map = provider_cfg.get("pricing") or {}
    if not pricing_map or model not in pricing_map:
        if pricing_map and model not in pricing_map:
            print(
                f"[coworker] warning: model '{model}' not in pricing dict -> cost=0",
                file=sys.stderr,
            )
        return 0.0
    p = pricing_map[model]
    cost = (
        input_tokens * p.get("input", 0)
        + output_tokens * p.get("output", 0)
        + cached_tokens * p.get("cache_input", 0)
    ) / 1_000_000
    return cost
