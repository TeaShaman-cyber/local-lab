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

# `[ \t]+`, not `\s+`: `\s` matches a newline, so "Revision\ninvalid" parsed as the
# revision "invalid" and the probe returned LAB_OK_V1 for a malformed contract. A
# separator that accepts a line break is not a separator.
REVISION = re.compile(r"^Revision[ \t]+(\S+)[ \t]*$", re.MULTILINE)
PROBE = re.compile(r"^Probe[ \t]+LAB_PROBE[ \t]*->[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def self_test() -> tuple[bool, str]:
    """Input/output pairs for both parsers, including the malformed shapes.

    Without these the probe cannot be falsified: it returned ok=true for a contract
    whose Revision line was broken across two lines, and nothing in the file said what
    a valid or an invalid input looks like (Codex P1 on #73).
    """
    cases = [
        # (text, want_revision, want_probe, note)
        ("Revision 2026-10-local-v1\n", "2026-10-local-v1", None, "single space"),
        ("Revision          2026-10-local-v1\n", "2026-10-local-v1", None, "aligned with spaces"),
        ("Revision\t2026-10-local-v1\n", "2026-10-local-v1", None, "tab separator"),
        ("Revision\ninvalid\n", None, None, "revision split across a newline"),
        ("Revision \n", None, None, "revision with no value"),
        ("Probe LAB_PROBE -> LAB_OK_V1\n", None, "LAB_OK_V1", "probe, spaced arrow"),
        ("Probe LAB_PROBE->LAB_OK_V1\n", None, "LAB_OK_V1", "probe, tight arrow"),
        ("Probe\nLAB_PROBE -> LAB_OK_V1\n", None, None, "probe split across a newline"),
    ]
    failures = []
    for text, want_rev, want_probe, note in cases:
        m_rev = REVISION.search(text)
        m_probe = PROBE.search(text)
        got_rev = m_rev.group(1) if m_rev else None
        got_probe = m_probe.group(1) if m_probe else None
        if got_rev != want_rev:
            failures.append(f"{note}: revision want {want_rev!r} got {got_rev!r}")
        if got_probe != want_probe:
            failures.append(f"{note}: probe want {want_probe!r} got {got_probe!r}")
    if failures:
        return False, "; ".join(failures)
    return True, f"{len(cases)} cases, all match"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        ok, detail = self_test()
        print(detail, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

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
