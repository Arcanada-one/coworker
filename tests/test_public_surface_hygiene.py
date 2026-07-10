"""Regression guard for the Public Surface Hygiene Mandate (TUNE-0349).

Shipped OSS content (README.md, CHANGELOG.md, docs/) must never cite internal
Arcanada ecosystem task IDs (e.g. `TUNE-####`) — provenance belongs in git log
/ the ecosystem backlog, not in the public artefact. Also locks the CI wiring:
this repo's own `dev-tools/public-surface-forbidden.regex` must extend the
framework's neutral base set with a project-specific task-prefix pattern, and
`.github/workflows/public-surface.yml` must point the reusable lint at it —
otherwise the gate silently falls back to the framework default, which has no
task-ID pattern at all and would never catch a future regression.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# Same shape as the ecosystem's registered task-prefix pattern (2-6 upper-case
# letters, dash, 4 digits) — mirrors the framework's own consumer-extension
# convention (see dev-tools/public-surface-forbidden.regex header comment).
TASK_ID_RE = re.compile(r"\bTUNE-[0-9]{4}\b")

SCANNED_SURFACE = [REPO / "README.md", REPO / "CHANGELOG.md"]


def _iter_docs():
    docs_dir = REPO / "docs"
    if docs_dir.is_dir():
        yield from sorted(docs_dir.rglob("*.md"))


def test_changelog_and_readme_have_no_internal_task_ids():
    for path in SCANNED_SURFACE:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        hits = TASK_ID_RE.findall(text)
        assert not hits, f"{path.name} leaks internal task ID(s) into shipped OSS content: {hits}"


def test_docs_have_no_internal_task_ids():
    for path in _iter_docs():
        text = path.read_text(encoding="utf-8")
        hits = TASK_ID_RE.findall(text)
        assert not hits, f"{path.relative_to(REPO)} leaks internal task ID(s): {hits}"


def test_own_forbidden_regex_extends_framework_default_with_task_prefix():
    regex_file = REPO / "dev-tools" / "public-surface-forbidden.regex"
    assert regex_file.exists(), (
        "coworker needs its own dev-tools/public-surface-forbidden.regex extending "
        "the framework neutral base with a TUNE-#### pattern (framework default has none)"
    )
    text = regex_file.read_text(encoding="utf-8")
    assert r"TUNE-" in text
    # Base set must still be present (single source of truth per invocation —
    # this file replaces, not supplements, the framework default at CI time).
    assert r"\bPRD-[A-Z]{2,10}-[0-9]{4}\b" in text


def test_public_surface_workflow_wires_project_regex_file():
    workflow = REPO / ".github" / "workflows" / "public-surface.yml"
    data = yaml.safe_load(workflow.read_text())
    jobs = data["jobs"]
    ps_job = jobs["public-surface"]
    with_block = ps_job.get("with", {})
    assert with_block.get("regex_file") == "dev-tools/public-surface-forbidden.regex", (
        "public-surface.yml must pass regex_file so CI actually enforces the "
        "project-specific TUNE-#### pattern instead of silently falling back "
        "to the framework's task-ID-agnostic default"
    )
