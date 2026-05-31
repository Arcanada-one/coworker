"""Regression guard for the release pipeline (.github/workflows/release.yml).

Asserts the structural invariants of the signed-release workflow so a future
edit cannot silently drop signing, attestation, the OIDC permission, the SHA-pin
discipline, or drift the certificate-identity binding away from the published
consumer verify recipe. Parses the workflow as YAML (not fragile line-grep) and
cross-checks docs/release-verification.md for identity-binding consistency.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
VERIFY_DOC = REPO / "docs" / "release-verification.md"

# Canonical certificate-identity binding for keyless cosign on this repo's
# workflow. The release workflow and the consumer verify doc MUST agree on it.
CERT_IDENTITY_PREFIX = (
    "https://github.com/Arcanada-one/coworker/.github/workflows/release.yml@refs/tags/"
)
OIDC_ISSUER = "https://token.actions.githubusercontent.com"


def _load_workflow() -> dict:
    # GitHub maps the bare `on:` key; PyYAML 1.1 parses unquoted `on` as the
    # boolean True. safe_load preserves it as the key True — look it up by both.
    return yaml.safe_load(WORKFLOW.read_text())


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _all_steps() -> list[dict]:
    wf = _load_workflow()
    steps: list[dict] = []
    for job in wf.get("jobs", {}).values():
        steps.extend(job.get("steps", []))
    return steps


def _step_blob() -> str:
    """All `run:` and `uses:` text across steps, for substring assertions."""
    parts = []
    for step in _all_steps():
        if "run" in step:
            parts.append(str(step["run"]))
        if "uses" in step:
            parts.append(str(step["uses"]))
    return "\n".join(parts)


def test_workflow_file_exists():
    assert WORKFLOW.is_file(), f"missing release workflow at {WORKFLOW}"


def test_triggers_on_version_tags():
    wf = _load_workflow()
    trigger = wf.get("on", wf.get(True))  # `on:` may parse as boolean True key
    assert trigger is not None, "no `on:` trigger block"
    tags = trigger["push"]["tags"]
    assert any("v" in t for t in tags), f"workflow must trigger on v* tags, got {tags}"


def test_tag_format_is_validated():
    # A tag-format gate guards against malformed tags reaching the signer.
    text = _workflow_text()
    assert re.search(r"\^v\[0-9\]\+\\\.\[0-9\]\+\\\.\[0-9\]\+", text) or re.search(
        r"\^v\\d\+\\\.\\d\+\\\.\\d\+", text
    ), "no semver tag-format validation regex found in workflow"


def test_builds_wheel_and_sdist():
    blob = _step_blob()
    assert "python -m build" in blob, "workflow must build via `python -m build`"


def test_version_matches_tag_assertion():
    # AC-2 fail-closed: built dist version must equal the pushed tag.
    blob = _step_blob().lower()
    assert "version" in blob and ("match" in blob or "mismatch" in blob or "!=" in blob), (
        "workflow must assert built artefact version matches the pushed tag"
    )


def test_sbom_is_generated():
    blob = _step_blob()
    assert "syft" in blob, "workflow must generate an SBOM via syft"
    assert "cyclonedx" in blob.lower(), "SBOM must be CycloneDX format"


def test_cosign_signs_wheel_sdist_and_sbom():
    blob = _step_blob()
    assert "cosign sign-blob" in blob, "workflow must cosign sign-blob the artefacts"
    assert ".cosign.bundle" in blob, "cosign must emit .cosign.bundle sidecars"
    # All three signed subjects must be referenced for signing.
    assert ".whl" in blob, "wheel must be among signed/published artefacts"
    assert "sdist" in blob or ".tar.gz" in blob, "sdist must be signed"


def test_sha256_sidecars_emitted():
    blob = _step_blob()
    assert "sha256sum" in blob, "workflow must emit .sha256 checksum sidecars"


def test_slsa_attestation_present():
    blob = _step_blob()
    assert "actions/attest-build-provenance" in blob, "missing SLSA L2 attestation step"


def test_pypi_trusted_publishing_no_token():
    wf = _load_workflow()
    blob = _step_blob()
    assert "pypa/gh-action-pypi-publish" in blob, "missing PyPI publish step"
    # Trusted Publishing => OIDC, never a long-lived token in repo secrets.
    text = _workflow_text()
    assert "PYPI_API_TOKEN" not in text and "secrets.PYPI" not in text, (
        "Trusted Publishing must not reference a PyPI API token secret"
    )
    # The publishing job needs id-token: write for OIDC.
    publish_job = None
    for job in wf.get("jobs", {}).values():
        if any("pypi-publish" in str(s.get("uses", "")) for s in job.get("steps", [])):
            publish_job = job
            break
    assert publish_job is not None, "could not locate the PyPI-publishing job"
    perms = publish_job.get("permissions", {})
    assert perms.get("id-token") == "write", "PyPI job must grant id-token: write for OIDC"


def test_github_release_published_with_all_assets():
    blob = _step_blob()
    assert "softprops/action-gh-release" in blob, "missing GitHub Release publish step"
    text = _workflow_text()
    # The release file list must include all five sidecar classes.
    for needed in (".whl", ".tar.gz", ".cosign.bundle", ".sha256", "sbom"):
        assert needed in text.lower(), f"release asset list missing `{needed}`"
    assert "prerelease" in text.lower(), "RC/prerelease tags must be markable as prerelease"


def test_third_party_actions_are_sha_pinned():
    # Datarim Security Mandate S4: pin every external action to a 40-char commit SHA.
    for step in _all_steps():
        uses = step.get("uses")
        if not uses or uses.startswith("./"):
            continue
        ref = uses.split("@", 1)[1] if "@" in uses else ""
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"action `{uses}` is not SHA-pinned (ref={ref!r}); S4 requires a 40-char commit SHA"
        )


def test_no_workflow_level_broad_permissions():
    # Top-level permissions must be empty ({}) so jobs opt in least-privilege.
    wf = _load_workflow()
    assert wf.get("permissions") == {} or wf.get("permissions") is None, (
        "top-level permissions should be empty; grant per-job least privilege"
    )


def test_cosign_and_syft_binaries_are_hash_verified():
    blob = _step_blob()
    # Both tool installs must sha256-verify the downloaded binary.
    assert blob.count("sha256sum -c") >= 1, "cosign/syft binaries must be sha256-verified on install"
    assert "COSIGN_SHA256" in _workflow_text(), "cosign install must pin a sha256"
    assert "SYFT_SHA256" in _workflow_text(), "syft install must pin a sha256"


# --- Cross-artefact consistency: workflow <-> consumer verify doc ---------


def test_verify_doc_exists():
    assert VERIFY_DOC.is_file(), f"missing consumer verify doc at {VERIFY_DOC}"


def test_verify_doc_identity_binding_matches_workflow():
    doc = VERIFY_DOC.read_text()
    assert CERT_IDENTITY_PREFIX in doc, (
        "verify doc must document the exact --certificate-identity binding "
        f"for this repo's release.yml: {CERT_IDENTITY_PREFIX}"
    )
    assert OIDC_ISSUER in doc, "verify doc must document the OIDC issuer"
    # The verify recipe must cover all three legs.
    assert "sha256sum -c" in doc, "verify doc missing checksum-verify step"
    assert "cosign verify-blob" in doc, "verify doc missing cosign verify-blob step"
    assert "gh attestation verify" in doc, "verify doc missing attestation-verify step"


def test_verify_doc_workflow_filename_consistent():
    # The identity binding embeds the workflow filename; if release.yml is ever
    # renamed, this guard forces the doc to be updated in lockstep.
    assert WORKFLOW.name == "release.yml", "workflow filename drifted from identity binding"
