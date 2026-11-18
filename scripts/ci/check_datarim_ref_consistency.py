#!/usr/bin/env python3
"""Fail when a caller pins one Datarim commit in uses: and another in datarim_ref.

Issue Arcanada-one/arcanada-workspace#1063: uses: selects the reusable workflow
shell; datarim_ref (or default main) is what the job checks out and executes.
They must be the same 40-hex commit. Tags are not allowed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(
    r"uses:\s*Arcanada-one/datarim/\.github/(workflows|actions)/([^\s@#]+)@([0-9a-f]{7,40})",
    re.I,
)
REF = re.compile(r"^(\s*)datarim_ref:\s*['\"]?([^\s'\"]+)['\"]?", re.I)


def check_text(path: str, text: str) -> list[str]:
    errs: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = USES.search(line)
        if not m:
            continue
        kind, name, use_sha = m.group(1), m.group(2), m.group(3)
        if not re.fullmatch(r"[0-9a-f]{40}", use_sha):
            errs.append(f"{path}:{i+1}: uses pin must be 40-hex (no tags): {use_sha}")
            continue
        ref_sha = None
        ref_line = None
        for j, wl in enumerate(lines[i + 1 : i + 80], start=i + 2):
            if re.match(r"^\s+-\s+uses:", wl):
                break
            if re.match(r"^\s{0,2}[A-Za-z0-9_-]+:\s*$", wl) and len(wl) - len(wl.lstrip()) <= 2:
                break
            rm = REF.match(wl)
            if rm:
                ref_sha = rm.group(2)
                ref_line = j
                break
        if kind != "workflows":
            continue
        if ref_sha is None:
            errs.append(
                f"{path}:{i+1}: uses .../{name}@{use_sha[:12]}… has no datarim_ref "
                f"(would default to moving 'main' inside the reusable workflow)"
            )
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", ref_sha):
            errs.append(f"{path}:{ref_line}: datarim_ref must be 40-hex (no tags): {ref_sha}")
            continue
        if ref_sha != use_sha:
            errs.append(
                f"{path}:{i+1}/{ref_line}: uses_and_ref_disagree uses={use_sha} ref={ref_sha}"
            )
    return errs


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".")
    wf = root / ".github" / "workflows"
    if not wf.is_dir():
        print(f"no workflows dir at {wf}", file=sys.stderr)
        return 2
    errs: list[str] = []
    for path in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
        errs.extend(check_text(str(path.relative_to(root)), path.read_text(encoding="utf-8", errors="replace")))
    if errs:
        print("FAIL check-datarim-ref-consistency:")
        for e in errs:
            print(f"  {e}")
        return 1
    print("PASS check-datarim-ref-consistency")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
