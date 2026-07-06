"""Version metadata must match the packaged project version."""

from __future__ import annotations

import pathlib
import re

import coworker


def _project_version(root: pathlib.Path) -> str:
    pyproject = (root / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_runtime_version_matches_pyproject():
    root = pathlib.Path(__file__).resolve().parents[1]
    assert coworker.__version__ == _project_version(root)


def test_changelog_has_current_version_entry():
    root = pathlib.Path(__file__).resolve().parents[1]
    version = _project_version(root)
    changelog = (root / "CHANGELOG.md").read_text()
    assert re.search(rf"^## \[{re.escape(version)}\]", changelog, re.MULTILINE)
