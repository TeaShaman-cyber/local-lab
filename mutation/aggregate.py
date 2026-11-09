#!/usr/bin/env python3
"""Combine shard results and fail when the sweep did not actually happen.

Two failure modes are checked here, and they are different:

  * a shard artifact is absent — the job died before it reported anything;
  * a shard reported `missing > 0` — it ran, but not over all its items.

Both mean "no measurement", and both must fail. Only then does the survivor
count mean what it appears to mean.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate mutation shard results")
    parser.add_argument("--results", default="results", help="directory with shard-result.json files")
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument(
        "--max-survivors",
        type=int,
        default=-1,
        help="fail above this many survivors; -1 reports without failing",
    )
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(args.results, "**", "shard-result.json"), recursive=True))
    results = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            results.append(json.load(handle))

    seen = {item["shard"] for item in results}
    missing_shards = [s for s in range(args.shards) if s not in seen]

    total_assigned = sum(item["assigned"] for item in results)
    total_executed = sum(item["executed"] for item in results)
    total_killed = sum(item["killed"] for item in results)
    total_survived = sum(item["survived"] for item in results)
    total_incompetent = sum(item["incompetent"] for item in results)
    total_missing = sum(item["missing"] for item in results)

    print("## Mutation sweep")
    print()
    print("| metric | value |")
    print("| --- | --- |")
    print(f"| shards expected | {args.shards} |")
    print(f"| shards reporting | {len(seen)} |")
    print(f"| mutants assigned | {total_assigned} |")
    print(f"| mutants executed | {total_executed} |")
    print(f"| killed | {total_killed} |")
    print(f"| survived | {total_survived} |")
    print(f"| incompetent | {total_incompetent} |")
    print(f"| never executed | {total_missing} |")
    print()

    survivors = []
    for item in sorted(results, key=lambda i: i["shard"]):
        for job_id in item["survivor_ids"]:
            survivors.append((item["shard"], job_id))
    if survivors:
        print("### Survivors")
        print()
        for shard, job_id in survivors:
            print(f"- shard {shard}: `{job_id}`")
        print()

    failures = []
    if missing_shards:
        failures.append(f"shards with no artifact: {missing_shards}")
    if total_missing:
        failures.append(f"{total_missing} mutants were never executed")

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1

    if args.max_survivors >= 0 and total_survived > args.max_survivors:
        print(
            f"FAIL: {total_survived} survivors exceed the allowed {args.max_survivors}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
