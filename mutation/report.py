#!/usr/bin/env python3
"""Summarize one mutation shard and refuse to report a green result it did not earn.

The shard fails closed when any assigned mutant lacks a real verdict: never
executed work, worker failures, `INCOMPETENT` test launches, or result rows that
cannot be joined back to a mutation spec. Counting any such row as proof of
execution is how a mutation gate reports success over work it never did.

Survivors carry their mutation spec (operator, function, position) and diff, so
the list can guide a test improvement instead of naming opaque job IDs.

Writes `shard-result.json` for the aggregate job and prints a markdown table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter


def _canonical_operator_args(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _stable_spec(row) -> dict:
    (
        _job_id,
        module_path,
        operator_name,
        operator_args,
        occurrence,
        start_row,
        start_col,
        end_row,
        end_col,
        definition_name,
    ) = row
    return {
        "module_path": module_path,
        "operator_name": operator_name,
        "operator_args": _canonical_operator_args(operator_args),
        "occurrence": occurrence,
        "start": [start_row, start_col],
        "end": [end_row, end_col],
        "definition_name": definition_name,
    }


def _fingerprint(spec: dict) -> str:
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _manifest_fingerprint(mutation_ids) -> str:
    canonical = "\n".join(sorted(mutation_ids))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize one mutation shard")
    parser.add_argument("session", help="path to session.sqlite")
    parser.add_argument("--meta", default="shard-meta.json")
    parser.add_argument("--out", default="shard-result.json")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()

    for label, value in (("source commit", args.source_commit), ("source tree", args.source_tree)):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            parser.error(f"{label} must be a full 40-hex Git SHA")
    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/actions/runs/[0-9]+", args.run_url):
        parser.error("run URL must be a GitHub Actions run URL")

    with open(args.meta, encoding="utf-8") as handle:
        meta = json.load(handle)

    with sqlite3.connect(args.session) as con:
        rows = con.execute(
            "select job_id, worker_outcome, test_outcome, diff from work_results"
        ).fetchall()
        spec_rows = con.execute(
            "select job_id, module_path, operator_name, operator_args, occurrence, "
            "start_pos_row, start_pos_col, end_pos_row, end_pos_col, definition_name "
            "from mutation_specs"
        ).fetchall()
        specs = {}
        for spec_row in spec_rows:
            stable = _stable_spec(spec_row)
            specs[spec_row[0]] = {
                **stable,
                "spec": stable,
                "mutation_id": _fingerprint(stable),
                "operator": stable["operator_name"],
                "function": stable["definition_name"],
                "location": f"{stable['start'][0]}:{stable['start'][1]}",
            }

    manifest_ids = [spec["mutation_id"] for spec in specs.values()]
    manifest_sha256 = _manifest_fingerprint(manifest_ids)
    orphaned = [row for row in rows if row[0] not in specs]
    known_rows = [row for row in rows if row[0] in specs]

    workers = Counter((row[1] or "NONE") for row in known_rows)
    measured = [row for row in known_rows if row[1] == "NORMAL"]
    failed = [row for row in known_rows if row[1] != "NORMAL"]

    tests = Counter((row[2] or "NONE") for row in measured)
    killed = tests.get("KILLED", 0)
    survived = tests.get("SURVIVED", 0)
    incompetent = tests.get("INCOMPETENT", 0) + tests.get("NONE", 0)

    never_run = max(meta["items"] - len(rows), 0)
    # INCOMPETENT from a NORMAL worker is not a verdict about the mutant:
    # cosmic-ray catches a failure while launching the test process and records
    # it anyway. The row says "normal", but no test ran, so it is not measured.
    unmeasured = never_run + len(orphaned) + len(failed) + incompetent

    survivors = []
    for job_id, _worker, test_outcome, diff in measured:
        if test_outcome != "SURVIVED":
            continue
        spec = specs[job_id]
        survivors.append({
            "job_id": job_id,
            "mutation_id": spec["mutation_id"],
            "spec": spec["spec"],
            "worker_outcome": "NORMAL",
            "test_outcome": test_outcome,
            "operator": spec["operator"],
            "function": spec["function"],
            "location": spec["location"],
            "diff": diff or "",
        })

    result = {
        "source_commit_sha": args.source_commit,
        "source_tree_sha": args.source_tree,
        "run_url": args.run_url,
        "shard": meta["shard"],
        "shards": meta["shards"],
        "session_total": meta["total"],
        "assigned": meta["items"],
        "executed": len(measured) - incompetent,
        "failed": len(failed),
        "orphaned_results": len(orphaned),
        "never_run": never_run,
        "unmeasured": unmeasured,
        "killed": killed,
        "survived": survived,
        "incompetent": incompetent,
        "worker_outcomes": dict(workers),
        "survivors": survivors,
        # job_id is run-local. Stable mutation IDs come from the full mutation
        # spec, so independent shard sessions can prove one shared manifest.
        "manifest_sha256": manifest_sha256,
        "manifest_total": len(manifest_ids),
        "mutation_ids": [specs[row[0]]["mutation_id"] for row in known_rows],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    print(f"### shard {result['shard']}/{result['shards']}")
    print()
    print("| metric | value |")
    print("| --- | --- |")
    for key in ("assigned", "executed", "killed", "survived", "incompetent",
                "failed", "orphaned_results", "never_run", "unmeasured"):
        print(f"| {key} | {result[key]} |")
    print()

    if survivors:
        print("Survivors:")
        for item in survivors:
            print(f"- `{item['function']}` {item['operator']} at {item['location']}")
        print()

    if unmeasured:
        print(
            f"FAIL: {unmeasured} of {meta['items']} work items produced no test result "
            f"({len(failed)} worker failures, {incompetent} incompetent, "
            f"{len(orphaned)} results without specs, {never_run} never run); "
            "this shard measured less than it promised.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
