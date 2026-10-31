#!/usr/bin/env python3
"""Probe the contract: does AGENTS.md still say what it claims to say?

`AGENTS.md` declares `Probe: LAB_PROBE -> LAB_OK_V1`. Until now that was a promise
with no implementation — a text asserting its own verification (#52, item 3).

This is deliberately not a "does the file exist" check. It verifies the parts that
carry the contract's meaning:

  * the Revision and Probe lines are present and well-formed;
  * every section the contract promises is still there;
  * the rules file exists and is not empty.

Exit codes: 0 = LAB_OK_V1 printed, 1 = contract drifted, 2 = AGENTS.md missing.

Usage:
    python tools/contracts/lab_probe.py
    python tools/contracts/lab_probe.py --json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
EXPECTED = "LAB_OK_V1"

# Sections whose disappearance would mean the contract lost a promise, not just a
# heading. Order does not matter; presence does.
#
# This list is deliberately the FULL set of substantive obligations, not a sample:
# a probe that checks half the contract reports LAB_OK_V1 for a file that dropped
# `## Permission` or `## Authority`. It cannot be derived from the file under test —
# a deleted section would then vanish from the expectations too (Codex P2 on #73).
REQUIRED_SECTIONS = [
    "## Scope",
    "## Epistemics",
    "## Truth",
    "## WorkingCulture",
    "## OperationalBootstrap",
    "## Precedence",
    "## ChangeControl",
    "## Continuity",
    "## Currentness",
    "## Authority",
    "## Capabilities",
    "## Routing",
    "## QADiscovery",
    "## Fallback",
    "## Permission",
    "## Verification",
    "## Persistence",
    "## RuntimeBoundary",
    "## DegradedMode",
    "## Action",
    "## Agent skills",
]

REVISION = re.compile(r"^Revision\s+(\S+)$", re.MULTILINE)
PROBE = re.compile(r"^Probe\s+LAB_PROBE\s*->\s*(\S+)$", re.MULTILINE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not AGENTS.is_file():
        print(f"AGENTS.md not found at {AGENTS}", file=sys.stderr)
        return 2

    text = AGENTS.read_text(encoding="utf-8")
    findings: list[str] = []

    revision = REVISION.search(text)
    if not revision:
        findings.append("no `Revision <value>` line")
    probe = PROBE.search(text)
    if not probe:
        findings.append("no `Probe LAB_PROBE -> <value>` line")
    elif probe.group(1) != EXPECTED:
        findings.append(f"probe value is {probe.group(1)!r}, helper prints {EXPECTED!r}")

    for section in REQUIRED_SECTIONS:
        if section not in text:
            findings.append(f"missing section: {section}")

    result = {
        "ok": not findings,
        "revision": revision.group(1) if revision else None,
        "probe": probe.group(1) if probe else None,
        "sections_checked": len(REQUIRED_SECTIONS),
        "findings": findings,
    }

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for finding in findings:
            print(f"drift: {finding}", file=sys.stderr)
        if not findings:
            print(EXPECTED)

    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
