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

# A statement that assigns the write route inside the contract. The contract must
# point, not opine: the moment it carries its own rule, issue-tracker.md becomes a
# second opinion and the two drift.
#
# Both languages, because an English sentence is exactly how this defect returns
# (Codex P2 on #73 reproduced it with "Issue writes must use `gh` only").
WRITE_ROUTE = re.compile(
    r"(?:запис[ьи]|write[s]?)[^.\n]{0,90}"
    r"(?:только\s+`?gh`?|only\s+`?gh`?|запрещ|forbidden|MCP\s+(?:or|или)|(?:or|или)\s+MCP)",
    re.IGNORECASE,
)

# The section that must defer rather than decide.
ISSUE_SECTION = "### Issue tracker"
POINTER_TO = "docs/agents/issue-tracker.md"


def issue_tracker_block(text: str) -> str:
    """The `### Issue tracker` section, up to the next heading of the same level."""
    start = text.find(ISSUE_SECTION)
    if start == -1:
        return ""
    rest = text[start + len(ISSUE_SECTION):]
    end = rest.find("\n### ")
    return rest if end == -1 else rest[:end]


def self_test() -> tuple[bool, str]:
    """Fixed examples for both languages: the defect must not return through English.

    Codex reproduced the gap by adding "Issue writes must use `gh` only" to a temp
    copy — the Russian check passed it. A rule with no input/output example is a
    rule nobody can falsify.
    """
    cases = [
        ("### Issue tracker\n\nЗапись — только `gh`. See `docs/agents/issue-tracker.md`.", False,
         "russian claim must be caught"),
        ("### Issue tracker\n\nIssue writes must use `gh` only.", False,
         "english claim must be caught"),
        ("### Issue tracker\n\nДетали и границы — `docs/agents/issue-tracker.md`.", True,
         "pointer without a claim is fine"),
        ("### Issue tracker\n\nЧтение — `gh` или MCP; детали — `docs/agents/issue-tracker.md`.", True,
         "pointing at both routes is still a pointer"),
    ]
    failures = []
    for body, want_clean, note in cases:
        found = bool(WRITE_ROUTE.search(issue_tracker_block(body))) or POINTER_TO not in body
        if found == want_clean:
            failures.append(f"{note}: expected clean={want_clean}, got clean={not found}")
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

    # 1. Pointers resolve.
    for rel in sorted(set(POINTER.findall(text))):
        if not (ROOT / rel).is_file():
            findings.append(f"AGENTS.md points at {rel}, which does not exist")

    # 2. The contract must defer, not duplicate. Checked inside the Issue tracker
    #    section only: elsewhere the same words may legitimately appear as prose.
    block = issue_tracker_block(text)
    if not block:
        findings.append(f"AGENTS.md has no {ISSUE_SECTION!r} section")
    else:
        if POINTER_TO not in block:
            findings.append(f"{ISSUE_SECTION} does not point at {POINTER_TO}")
        for match in WRITE_ROUTE.finditer(block):
            findings.append(
                f"{ISSUE_SECTION} carries its own write-route rule ({match.group(0).strip()!r}); "
                f"{POINTER_TO} is the single source — leave a pointer instead"
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
