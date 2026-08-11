"""Regression guard for the scheduled live-integration workflow.

Asserts the structural invariants of .github/workflows/live-integration.yml so a
future edit cannot silently: add a PR/push trigger (which would pay real quota on
every change and expose secrets to fork PRs), drop a provider secret, drop the
`RUN_LIVE_TESTS=1` gate, drop the cost-cap step, un-pin a third-party action, or
widen top-level permissions. Parses the workflow as YAML (not fragile line-grep),
mirroring tests/test_release_workflow.py.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "live-integration.yml"


def _load_workflow() -> dict:
    # PyYAML 1.1 parses the unquoted `on:` key as boolean True — look up both.
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers() -> dict:
    wf = _load_workflow()
    trig = wf.get("on", wf.get(True))
    assert trig is not None, "no `on:` trigger block"
    return trig


def _all_steps() -> list[dict]:
    wf = _load_workflow()
    steps: list[dict] = []
    for job in wf.get("jobs", {}).values():
        steps.extend(job.get("steps", []))
    return steps


def _step_blob() -> str:
    parts = []
    for step in _all_steps():
        for k in ("run", "uses", "env", "with"):
            if k in step:
                parts.append(str(step[k]))
    return "\n".join(parts)


def test_workflow_file_exists():
    assert WORKFLOW.is_file(), f"missing live-integration workflow at {WORKFLOW}"


def test_triggers_are_schedule_and_dispatch_only():
    trig = _triggers()
    assert "schedule" in trig, "workflow must run on a schedule"
    assert "workflow_dispatch" in trig, "workflow must be manually dispatchable"
    # Default-off on PR: neither pull_request nor push may trigger the live job.
    assert "pull_request" not in trig, "live job must NOT trigger on pull_request (secret/cost hygiene)"
    assert "push" not in trig, "live job must NOT trigger on push"


def test_schedule_has_a_cron():
    trig = _triggers()
    sched = trig["schedule"]
    assert isinstance(sched, list) and sched, "schedule must list at least one cron entry"
    assert any("cron" in entry for entry in sched), "schedule entry must carry a cron expression"


def test_runs_live_suite_gated():
    blob = _step_blob()
    assert "RUN_LIVE_TESTS" in blob, "workflow must set RUN_LIVE_TESTS to unskip the live suite"
    assert "test_rtk_live.py" in blob, "workflow must run the live integration suite"


def test_references_both_provider_secrets():
    text = WORKFLOW.read_text()
    assert "MOONSHOT_API_KEY" in text, "workflow must wire the Moonshot secret"
    assert "DEEPSEEK_API_KEY" in text, "workflow must wire the DeepSeek secret"
    # Secrets must come from GitHub repo secrets, not be inlined.
    assert "secrets.MOONSHOT_API_KEY" in text and "secrets.DEEPSEEK_API_KEY" in text, (
        "provider keys must be sourced from `secrets.*` (GitHub repo secrets)"
    )


def test_invokes_cost_cap_step():
    blob = _step_blob()
    assert "ci_cost_cap.py" in blob, "workflow must invoke the cost-cap helper after the provider calls"
    assert "--cap" in blob, "cost-cap invocation must pass a --cap value"


def test_third_party_actions_are_sha_pinned():
    # Datarim Security Mandate S4: every external action pinned to a 40-hex commit SHA.
    for step in _all_steps():
        uses = step.get("uses")
        if not uses or uses.startswith("./"):
            continue
        ref = uses.split("@", 1)[1] if "@" in uses else ""
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"action `{uses}` is not SHA-pinned (ref={ref!r}); S4 requires a 40-char commit SHA"
        )


def test_top_level_permissions_are_empty():
    wf = _load_workflow()
    assert wf.get("permissions") == {} or wf.get("permissions") is None, (
        "top-level permissions must be empty; grant per-job least privilege"
    )


def test_job_grants_least_privilege():
    wf = _load_workflow()
    jobs = wf.get("jobs", {})
    assert jobs, "workflow must define at least one job"
    for name, job in jobs.items():
        perms = job.get("permissions")
        assert perms is not None, f"job `{name}` must declare explicit least-privilege permissions"
        assert perms.get("contents") in ("read", None), (
            f"job `{name}` should not need write contents permission"
        )
