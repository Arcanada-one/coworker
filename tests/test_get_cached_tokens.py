"""Unit tests for coworker.logger.get_cached_tokens.

Locks numeric coercion after the UP038 isinstance(int | float) form:
int/float count, non-numeric values become 0, missing usage is 0.
"""

from types import SimpleNamespace

from coworker.logger import get_cached_tokens


def test_none_usage_is_zero():
    assert get_cached_tokens(None) == 0


def test_prompt_tokens_details_int():
    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens=12),
        cached_tokens=99,
    )
    assert get_cached_tokens(usage) == 12


def test_top_level_float_coerces_to_int():
    usage = SimpleNamespace(prompt_tokens_details=None, cached_tokens=7.0)
    assert get_cached_tokens(usage) == 7


def test_non_numeric_cached_tokens_is_zero():
    usage = SimpleNamespace(prompt_tokens_details=None, cached_tokens="nope")
    assert get_cached_tokens(usage) == 0
