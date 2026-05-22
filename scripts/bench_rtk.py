#!/usr/bin/env python3
"""bench_rtk.py — hybrid synthetic-corpus + live-API benchmark for the RTK plugin.

Runs each entry in a hardcoded corpus through two prompt shapes:

  1. **baseline** — raw stdout glued into a "summarise this output" prompt.
  2. **compressed** — same stdout first piped through `rtk pipe --filter <NAME>`,
     then glued into the same prompt template.

For each (provider, corpus) pair the script measures `prompt_tokens` from the
provider's usage block, repeats N times for stability, and emits a Markdown
table with columns:

  | provider | task | prompt_tokens (baseline) | prompt_tokens (compressed) | delta_% |

Required env vars (gated): `MOONSHOT_API_KEY`, `DEEPSEEK_API_KEY`.
Missing-key providers are skipped with a stderr WARN — the script still
exits 0 so CI / pre-flight runs do not fail closed on the operator's
local key state.

Cost: ~$0.01 per full run (5 corpora × 2 providers × N=5 repeats = 50 calls).
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Hardcoded synthetic corpus. Five entries — verbose stdouts representative of
# common dev tooling that RTK is designed to filter. Reproducible across hosts.

CORPUS: list[dict] = [
    {
        "name": "pytest-30-passed",
        "rtk_filter": "pytest",
        "raw": (
            "\n".join(
                f"tests/test_module_{i}.py::test_case_{i} PASSED"
                + " " * 40
                + f"[{i*100//30:>3}%]"
                for i in range(1, 31)
            )
            + "\ntests/test_failure.py::test_buggy FAILED"
            + "\n  AssertionError: expected 42 got 41"
            + "\n  at line 17"
            + "\n========================== 30 passed, 1 failed in 2.34s ==========================\n"
        ),
    },
    {
        "name": "find-output-50-files",
        "rtk_filter": "find",
        "raw": "\n".join(
            f"./src/module_{i}/handler_{i}.py" for i in range(1, 51)
        )
        + "\n./tests/conftest.py\n./README.md\n",
    },
    {
        "name": "git-log-stat-5-commits",
        "rtk_filter": "git-log",
        "raw": "\n".join(
            f"commit {hex(0xa1b2c3d4 + i)[2:]}\n"
            f"Author: dev <dev@example.com>\n"
            f"Date:   Mon May {i+1} 2026 12:00:00 +0300\n\n"
            f"    feat: refactor module {i} for clarity\n\n"
            f" src/module_{i}.py | {10 + i} +++++++---\n"
            f" tests/test_{i}.py | {5 + i} +++---\n"
            f" 2 files changed, {15 + 2*i} insertions(+), {5 + i} deletions(-)\n"
            for i in range(5)
        ),
    },
    {
        "name": "docker-logs-traceback",
        "rtk_filter": None,  # raw passthrough — no perfect filter; baseline-only
        "raw": (
            "INFO  app.startup  binding to 0.0.0.0:8080\n"
            + "INFO  app.startup  loaded 12 routes\n"
            + "\n".join(
                f"DEBUG app.handler  request_id=req-{i:04d} method=GET path=/api/v1/items/{i}"
                for i in range(40)
            )
            + "\nERROR app.handler  Traceback (most recent call last):\n"
            + '  File "/srv/app/handler.py", line 42, in handle\n'
            + "    result = compute(item)\n"
            + '  File "/srv/app/handler.py", line 19, in compute\n'
            + "    return 1/0\n"
            + "ZeroDivisionError: division by zero\n"
        ),
    },
    {
        "name": "ruff-check-violations",
        "rtk_filter": "ruff-check",
        "raw": "\n".join(
            f"src/module_{i}.py:{i*3+1}:5: F401 [*] `os` imported but unused\n"
            f"src/module_{i}.py:{i*3+2}:1: E302 expected 2 blank lines, found 1\n"
            f"src/module_{i}.py:{i*3+3}:80: E501 Line too long (95 > 79)\n"
            for i in range(30)
        )
        + "\nFound 90 errors.\n",
    },
]


@dataclass(frozen=True)
class ProviderCfg:
    name: str
    env_key: str
    base_url: str
    model: str


PROVIDERS: list[ProviderCfg] = [
    ProviderCfg(
        name="moonshot",
        env_key="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
        model="kimi-k2.6",
    ),
    ProviderCfg(
        name="deepseek",
        env_key="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    ),
]


def _rtk_available() -> bool:
    return shutil.which("rtk") is not None


def _compress(raw: str, filt: str | None) -> str:
    """Pipe `raw` through `rtk pipe --filter <filt>`. Fall back to raw on failure."""
    if filt is None or not _rtk_available():
        return raw
    try:
        r = subprocess.run(
            ["rtk", "pipe", "--filter", filt],
            input=raw,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return raw


def _build_prompt(stdout_text: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                "Summarise the following command output in one short sentence. "
                "Be terse — no preamble, no markdown.\n\n"
                "BEGIN OUTPUT\n"
                f"{stdout_text}\n"
                "END OUTPUT"
            ),
        }
    ]


def _call_provider(prov: ProviderCfg, messages: list[dict]) -> int | None:
    """Return prompt_tokens (int) from usage block, or None on failure."""
    api_key = os.environ.get(prov.env_key)
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        print("[bench_rtk] WARN: `openai` package not installed; skipping live calls.", file=sys.stderr)
        return None
    client = OpenAI(api_key=api_key, base_url=prov.base_url)
    try:
        resp = client.chat.completions.create(
            model=prov.model,
            messages=messages,
            max_tokens=64,
        )
    except Exception as e:  # noqa: BLE001 — broad: any provider error → skip
        print(f"[bench_rtk] WARN: {prov.name} call failed: {e}", file=sys.stderr)
        return None
    return getattr(resp.usage, "prompt_tokens", None)


def _run_pair(prov: ProviderCfg, entry: dict, repeats: int) -> tuple[int, int] | None:
    """Return (median baseline_tokens, median compressed_tokens) or None if provider unreachable."""
    base_samples = []
    comp_samples = []
    compressed_text = _compress(entry["raw"], entry["rtk_filter"])
    for _ in range(repeats):
        b = _call_provider(prov, _build_prompt(entry["raw"]))
        c = _call_provider(prov, _build_prompt(compressed_text))
        if b is None or c is None:
            return None
        base_samples.append(b)
        comp_samples.append(c)
        time.sleep(0.1)  # gentle rate-limit cushion
    return int(statistics.median(base_samples)), int(statistics.median(comp_samples))


def _render_table(rows: list[tuple[str, str, int, int]]) -> str:
    out = [
        "| provider | task | prompt_tokens (baseline) | prompt_tokens (compressed) | delta_% |",
        "|----------|------|--------------------------:|----------------------------:|--------:|",
    ]
    for prov, task, base, comp in rows:
        delta_pct = (comp - base) * 100.0 / base if base > 0 else 0.0
        out.append(
            f"| {prov} | {task} | {base} | {comp} | {delta_pct:+.1f}% |"
        )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="/tmp/bench-rtk.md", help="Markdown table output path.")
    ap.add_argument("--repeats", type=int, default=5, help="Repeats per (provider,task) pair (median taken).")
    ap.add_argument("--providers", nargs="*", default=None, help="Provider subset (default: all with keys present).")
    ap.add_argument("--seed", type=int, default=42, help="Reserved for future stochastic extensions.")
    ap.add_argument("--dry-run", action="store_true", help="Skip live API calls; emit baseline-only table from byte counts.")
    args = ap.parse_args(argv)

    if not _rtk_available():
        print("[bench_rtk] WARN: rtk binary not found. Compressed column will equal baseline.", file=sys.stderr)

    selected_providers = [p for p in PROVIDERS if args.providers is None or p.name in args.providers]

    rows: list[tuple[str, str, int, int]] = []
    if args.dry_run:
        # No-cost mode: report byte counts as a proxy. Useful for CI / fixture validation.
        for entry in CORPUS:
            compressed = _compress(entry["raw"], entry["rtk_filter"])
            rows.append(("dry-run", entry["name"], len(entry["raw"]), len(compressed)))
    else:
        for prov in selected_providers:
            if not os.environ.get(prov.env_key):
                print(f"[bench_rtk] WARN: {prov.env_key} not set — skipping {prov.name}.", file=sys.stderr)
                continue
            for entry in CORPUS:
                result = _run_pair(prov, entry, args.repeats)
                if result is None:
                    continue
                base, comp = result
                rows.append((prov.name, entry["name"], base, comp))

    if not rows:
        print(
            "[bench_rtk] No rows produced. Set MOONSHOT_API_KEY and/or DEEPSEEK_API_KEY, "
            "or pass --dry-run for a byte-count proxy table.",
            file=sys.stderr,
        )
        return 0

    table_md = _render_table(rows)
    Path(args.output).write_text(table_md)
    print(table_md)
    print(f"[bench_rtk] wrote {args.output} ({len(rows)} rows).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
