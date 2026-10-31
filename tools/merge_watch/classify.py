#!/usr/bin/env python3
"""Classify a merged PR: was the merge bound to an issue or a reason?

The unit of search is a **comment**, not a line. A binding written on the line
after ``merge-intent:`` is the natural shape — "what" on one line, "why" on the
next — and the previous line-based grep filtered it out before looking at it,
reporting a correctly declared intent as unbound (#63).

Three outcomes, unchanged from the workflow:

    NO_INTENT   nobody declared the merge        (incident #15)
    UNBOUND     intent declared, no basis given  (process defect)
    BOUND       intent with issue: #N or reason

Input on stdin: the JSON body of ``gh api .../issues/N/comments``, either a
flat list (``--paginate``) or a list of pages (``--paginate --slurp``).

Usage::

    gh api repos/O/R/issues/1/comments --paginate --slurp \\
        | python3 classify.py --head <sha>

    python3 classify.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys

INTENT = r"merge-intent:\s*{sha}"
BINDING = r"issue:\s*#\d+|reason:\s*\S.{4,}"


def flatten(raw: object) -> list[str]:
    """Accept both shapes gh produces, so the caller does not have to care."""
    bodies: list[str] = []
    for page in raw if isinstance(raw, list) else []:
        if isinstance(page, list):
            bodies.extend(str((item or {}).get("body") or "") for item in page)
        elif isinstance(page, dict):
            bodies.append(str(page.get("body") or ""))
    return bodies


def classify(bodies: list[str], head_sha: str) -> str:
    intent = re.compile(INTENT.format(sha=re.escape(head_sha)))
    binding = re.compile(BINDING)
    for body in bodies:
        if intent.search(body):
            return "BOUND" if binding.search(body) else "UNBOUND"
    return "NO_INTENT"


def self_test() -> tuple[bool, str]:
    """Fixed input/output pairs. The two-line case is the reason this exists."""
    sha = "a79cb06ce80d64552efd635c2c217e1499571a37"
    cases = [
        # (bodies, want, note)
        ([f"merge-intent: {sha} issue: #60"], "BOUND", "single line, as documented"),
        ([f"merge-intent: {sha}\nissue: #60"], "BOUND", "binding on the next line (the #63 defect)"),
        ([f"merge-intent: {sha}\n\nreason: hardening the restore drill"], "BOUND", "reason on a later line"),
        ([f"merge-intent: {sha}"], "UNBOUND", "intent with no basis at all"),
        ([f"merge-intent: {sha}\nno basis here"], "UNBOUND", "prose that names neither issue nor reason"),
        (["merge-intent: " + "0" * 40 + "\nissue: #1"], "NO_INTENT", "intent for a different commit"),
        ([f"see merge-intent: {sha} issue: #60"], "BOUND", "intent quoted mid-sentence"),
        (["unrelated comment", f"merge-intent: {sha} issue: #7"], "BOUND", "intent in a later comment"),
        (["issue: #60", f"merge-intent: {sha}"], "UNBOUND", "binding in a different comment does not count"),
        ([], "NO_INTENT", "no comments at all"),
    ]
    failures = []
    for bodies, want, note in cases:
        got = classify(bodies, sha)
        if got != want:
            failures.append(f"{note}: want {want} got {got}")
    if failures:
        return False, "; ".join(failures)
    return True, f"{len(cases)} cases, all match"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--head", help="merged commit sha (pull_request.head.sha)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        ok, detail = self_test()
        print(detail, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if not args.head:
        print("--head is required", file=sys.stderr)
        return 2

    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"cannot parse comment json: {exc}", file=sys.stderr)
        return 2

    print(classify(flatten(raw), args.head))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
