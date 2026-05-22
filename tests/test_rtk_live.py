"""Live integration tests against Moonshot + DeepSeek (gated).

Run with: RUN_LIVE_TESTS=1 MOONSHOT_API_KEY=... DEEPSEEK_API_KEY=... pytest tests/test_rtk_live.py -q

These tests make real outbound HTTPS calls and consume real API quota
(~$0.001 total). They are skipped by default. Their purpose is to confirm
that:

  1. Both providers respond to a minimal prompt.
  2. `prompt_tokens` from the provider's usage block is strictly lower
     when the corpus passes through `rtk pipe` than when it doesn't.

If the binary `rtk` is not on PATH the compression-effect tests skip with
a clear message — they do not fail closed.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

LIVE = os.environ.get("RUN_LIVE_TESTS") == "1"
if not LIVE:
    pytest.skip("RUN_LIVE_TESTS=1 not set; skipping live integration tests.", allow_module_level=True)

# Load the bench helpers — the live tests reuse its corpus + provider config.
REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_PATH = REPO_ROOT / "scripts" / "bench_rtk.py"
spec = importlib.util.spec_from_file_location("bench_rtk", BENCH_PATH)
bench = importlib.util.module_from_spec(spec)
sys.modules["bench_rtk"] = bench
assert spec.loader is not None
spec.loader.exec_module(bench)


def _has_key(env_key: str) -> bool:
    return bool(os.environ.get(env_key))


def _rtk_available() -> bool:
    return shutil.which("rtk") is not None


@pytest.mark.skipif(not _has_key("MOONSHOT_API_KEY"), reason="MOONSHOT_API_KEY unset")
def test_live_moonshot_basic_response():
    prov = next(p for p in bench.PROVIDERS if p.name == "moonshot")
    tokens = bench._call_provider(prov, [{"role": "user", "content": "Reply with the single word OK."}])
    assert isinstance(tokens, int) and tokens > 0, "moonshot did not return prompt_tokens"


@pytest.mark.skipif(not _has_key("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY unset")
def test_live_deepseek_basic_response():
    prov = next(p for p in bench.PROVIDERS if p.name == "deepseek")
    tokens = bench._call_provider(prov, [{"role": "user", "content": "Reply with the single word OK."}])
    assert isinstance(tokens, int) and tokens > 0, "deepseek did not return prompt_tokens"


def _compression_reduces_tokens(prov_name: str):
    if not _rtk_available():
        pytest.skip("rtk binary not on PATH; cannot exercise compression branch.")
    prov = next(p for p in bench.PROVIDERS if p.name == prov_name)
    if not _has_key(prov.env_key):
        pytest.skip(f"{prov.env_key} unset")

    # Use the verbose pytest corpus — its compression ratio is ~95 % at byte level.
    entry = next(e for e in bench.CORPUS if e["name"] == "pytest-30-passed")
    base_tokens = bench._call_provider(prov, bench._build_prompt(entry["raw"]))
    comp_tokens = bench._call_provider(
        prov, bench._build_prompt(bench._compress(entry["raw"], entry["rtk_filter"]))
    )
    assert isinstance(base_tokens, int) and isinstance(comp_tokens, int)
    assert comp_tokens < base_tokens, (
        f"RTK compression failed to reduce prompt_tokens on {prov_name}: "
        f"baseline={base_tokens}, compressed={comp_tokens}"
    )


def test_live_rtk_compression_reduces_tokens_moonshot():
    _compression_reduces_tokens("moonshot")


def test_live_rtk_compression_reduces_tokens_deepseek():
    _compression_reduces_tokens("deepseek")
