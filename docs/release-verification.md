# Verifying a coworker release

coworker releases are signed with [cosign](https://docs.sigstore.dev/) (keyless OIDC via Sigstore) and carry [SLSA L2](https://slsa.dev/) build provenance attestation. Every GitHub Release attaches the wheel, the sdist, a CycloneDX SBOM, and for each a `.cosign.bundle` signature and a `.sha256` checksum. Verifying is optional but recommended for supply-chain assurance.

## Prerequisites

- [`cosign`](https://docs.sigstore.dev/cosign/installation) ≥ 3.0
- [`gh`](https://cli.github.com/) ≥ 2.40 (GitHub CLI)
- `sha256sum`, `jq`

## Verify recipe

```bash
TAG=v0.7.0   # replace with the release you are verifying

# 1. Download all artefacts.
gh release download "$TAG" --repo Arcanada-one/coworker

# 2. Derive the distribution filenames.
VERSION="${TAG#v}"
WHEEL="coworker-${VERSION}-py3-none-any.whl"
SDIST="coworker-${VERSION}.tar.gz"
SBOM="coworker-${TAG}-sbom.cdx.json"

# 3. Verify checksums.
sha256sum -c "${WHEEL}.sha256" "${SDIST}.sha256" "${SBOM}.sha256"

# 4a. Verify cosign signature on the wheel.
cosign verify-blob \
  --bundle "${WHEEL}.cosign.bundle" \
  --certificate-identity "https://github.com/Arcanada-one/coworker/.github/workflows/release.yml@refs/tags/${TAG}" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "$WHEEL"

# 4b. Verify cosign signature on the sdist (same identity binding).
cosign verify-blob \
  --bundle "${SDIST}.cosign.bundle" \
  --certificate-identity "https://github.com/Arcanada-one/coworker/.github/workflows/release.yml@refs/tags/${TAG}" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "$SDIST"

# 4c. Verify cosign signature on the SBOM (same identity binding).
cosign verify-blob \
  --bundle "${SBOM}.cosign.bundle" \
  --certificate-identity "https://github.com/Arcanada-one/coworker/.github/workflows/release.yml@refs/tags/${TAG}" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "$SBOM"

# 5a. Verify SLSA build provenance for the wheel.
gh attestation verify "$WHEEL" --repo Arcanada-one/coworker

# 5b. Verify SLSA build provenance for the sdist.
gh attestation verify "$SDIST" --repo Arcanada-one/coworker
```

All five steps must exit `0` for a trusted release.

## Installing from PyPI

After verification, install the published distribution:

```bash
pip install coworker==0.7.0   # substitute the version you verified
```

Releases are published to PyPI via Trusted Publishing on the same tag.

## Maintainer setup (one-time, before the first tag)

PyPI Trusted Publishing requires a one-time **pending publisher** registration on `pypi.org` before the first release can publish.

Steps:
1. Log in to [pypi.org](https://pypi.org).
2. Go to the project page (or "Your projects" → "Publishing" for a not-yet-existing project to add a pending publisher).
3. Add a GitHub publisher with:
   - **Owner**: `Arcanada-one`
   - **Repository name**: `coworker`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`

After the first successful publish the pending publisher becomes a regular trusted publisher. No API token is stored anywhere — the release workflow authenticates to PyPI with a short-lived OIDC token.

## Notes on rollback

A leaked-credential / critical-vuln release can be yanked from PyPI (`pip` will refuse new installs of a yanked version) and the GitHub Release deleted, but cosign signatures recorded in the public Rekor transparency log remain queryable forever — yanking hides, it does not erase.