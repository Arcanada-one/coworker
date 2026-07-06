"""Version metadata must match the packaged project version."""

from __future__ import annotations

import pathlib
import re

import tomllib

import coworker


def test_runtime_version_matches_pyproject():
    root = pathlib.Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert coworker.__version__ == data["project"]["version"]


def test_changelog_has_current_version_entry():
    root = pathlib.Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    changelog = (root / "CHANGELOG.md").read_text()
    assert re.search(rf"^## \[{re.escape(version)}\]", changelog, re.MULTILINE)
