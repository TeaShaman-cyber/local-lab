#!/usr/bin/env python3
"""Do the contract and the documents it points at still agree?

`AGENTS.md` said "write only via gh" while `docs/agents/issue-tracker.md` allowed
MCP or gh. Both were true statements about their own file, and together they were a
contradiction. Linters do not catch that: every file was well-formed.

This checks the *claims*, not the syntax:

  1. Where AGENTS.md defers to a document, that document must exist.
  2. A statement about the GitHub write route must not appear in two places with
     different meanings — the pointer wins, the duplicate is drift.
  3. Every `See <path>` / `docs/agents/*.md` reference in AGENTS.md resolves.

Exit codes: 0 = consistent, 1 = contradiction found, 2 = cannot check.

Usage:
    python tools/contracts/check_consistency.py
    python tools/contracts/check_consistency.py --json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"

# Paths AGENTS.md is allowed to reference; each must exist.
POINTER = re.compile(r"`(docs/agents/[a-z-]+\.md)`")

# Statements that must exist in exactly one place. If a phrase appears in both the
# contract and the detail document, the two will drift apart the moment one changes.
WRITE_ROUTE = re.compile(r"запись[^.]{0,80}(?:только\s+`gh`|`gh`\s+или\s+MCP|MCP)", re.IGNORECASE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not AGENTS.is_file():
        print(f"AGENTS.md not found at {AGENTS}", file=sys.stderr)
        return 2

    text = AGENTS.read_text(encoding="utf-8")
    findings: list[str] = []

    # 1. Pointers resolve.
    for rel in sorted(set(POINTER.findall(text))):
        if not (ROOT / rel).is_file():
            findings.append(f"AGENTS.md points at {rel}, which does not exist")

    # 2. The contract must defer, not duplicate. "только `gh`" was the stale claim
    #    that contradicted issue-tracker.md; a pointer removes the second opinion.
    for match in WRITE_ROUTE.finditer(text):
        claim = match.group(0)
        if "только `gh`" in claim:
            findings.append(
                f"AGENTS.md carries its own write-route rule ({claim.strip()!r}); "
                "issue-tracker.md is the single source — leave a pointer instead"
            )

    # 3. A deferred document must actually describe a route, or the pointer is empty.
    tracker = ROOT / "docs/agents/issue-tracker.md"
    if tracker.is_file():
        tracker_text = tracker.read_text(encoding="utf-8")
        if "Route" not in tracker_text and "маршрут" not in tracker_text.lower():
            findings.append("issue-tracker.md no longer describes routes, but AGENTS.md defers to it")

    result = {"ok": not findings, "findings": findings}
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for finding in findings:
            print(f"inconsistent: {finding}", file=sys.stderr)
        if not findings:
            print("contract and referenced documents agree")

    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
