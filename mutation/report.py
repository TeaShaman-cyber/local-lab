#!/usr/bin/env python3
"""Summarize one mutation shard and refuse to report a green result it did not earn.

A shard is only meaningful if every work item it kept was actually executed.
A cancelled job, a crashed worker or an `exec` that stopped early leaves items
without results, and counting those as "no survivors" is how a mutation gate
ends up green over work it never did. So this exits non-zero when the executed
count does not match the shard size.

Writes `shard-result.json` for the aggregate job and prints a markdown table.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize one mutation shard")
    parser.add_argument("session", help="path to session.sqlite")
    parser.add_argument("--meta", default="shard-meta.json")
    parser.add_argument("--out", default="shard-result.json")
    args = parser.parse_args()

    with open(args.meta, encoding="utf-8") as handle:
        meta = json.load(handle)

    with sqlite3.connect(args.session) as con:
        rows = con.execute(
            "select test_outcome, worker_outcome, job_id from work_results"
        ).fetchall()

    tests = Counter((row[0] or "NONE") for row in rows)
    workers = Counter((row[1] or "NONE") for row in rows)

    killed = tests.get("KILLED", 0)
    survived = tests.get("SURVIVED", 0)
    incompetent = tests.get("INCOMPETENT", 0) + tests.get("NONE", 0)
    executed = len(rows)
    missing = max(meta["items"] - executed, 0)

    result = {
        "shard": meta["shard"],
        "shards": meta["shards"],
        "session_total": meta["total"],
        "assigned": meta["items"],
        "executed": executed,
        "killed": killed,
        "survived": survived,
        "incompetent": incompetent,
        "missing": missing,
        "worker_outcomes": dict(workers),
        "survivor_ids": [row[2] for row in rows if row[0] == "SURVIVED"],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    print(f"### shard {result['shard']}/{result['shards']}")
    print()
    print("| metric | value |")
    print("| --- | --- |")
    for key in ("assigned", "executed", "killed", "survived", "incompetent", "missing"):
        print(f"| {key} | {result[key]} |")
    print()

    if missing:
        print(
            f"FAIL: {missing} of {meta['items']} work items were never executed; "
            "this shard measured less than it promised.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
