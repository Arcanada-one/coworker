"""Regression: declared urllib3 floor covers pip-audit PYSEC-2026-4175/4176/4177."""
from __future__ import annotations

import re
from pathlib import Path

import urllib3

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def _parse_version(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".")[:3])


def test_installed_urllib3_meets_security_floor() -> None:
    assert _parse_version(urllib3.__version__) >= (2, 8, 0)


def test_pyproject_declares_urllib3_floor() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'"urllib3>=([^"]+)"', text)
    assert match is not None, "pyproject.toml must declare urllib3>=..."
    assert _parse_version(match.group(1)) >= (2, 8, 0)
