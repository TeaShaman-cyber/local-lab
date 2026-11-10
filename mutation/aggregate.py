#!/usr/bin/env python3
"""Combine shard results and fail when the sweep did not actually happen.

Three failure modes are checked here, and they are different:

  * a shard artifact is absent — the job died before it reported anything;
  * a shard reported `unmeasured > 0` — it ran, but did not obtain a test
    verdict for every assigned mutant;
  * stable mutation fingerprints overlap or leave a gap — counters add up, but
    the shards did not partition one mutation manifest.

All mean "no complete measurement", and all must fail. Only then does the
survivor count mean what it appears to mean.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys


def _manifest_fingerprint(mutation_ids) -> str:
    canonical = "\n".join(sorted(mutation_ids))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


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

    # job_id is a random UUID from each independent `cosmic-ray init`, so it
    # cannot prove overlap or support cross-run comparison. Each shard reports
    # stable fingerprints of full mutation specs plus a digest of the complete
    # pre-shard manifest.
    session_totals = {item.get("session_total") for item in results}
    manifest_totals = {item.get("manifest_total") for item in results}
    manifests = {item.get("manifest_sha256") for item in results}
    all_mutation_ids = [
        mutation_id
        for item in results
        for mutation_id in item.get("mutation_ids", [])
    ]
    distinct_mutation_ids = set(all_mutation_ids)
    duplicates = len(all_mutation_ids) - len(distinct_mutation_ids)
    expected_total = next(iter(session_totals)) if len(session_totals) == 1 else None
    manifest_total = next(iter(manifest_totals)) if len(manifest_totals) == 1 else None
    expected_manifest = (
        next(iter(manifests)) if len(manifests) == 1 and None not in manifests else None
    )
    measured_manifest = _manifest_fingerprint(distinct_mutation_ids)
    coverage_mismatch = (
        manifest_total is not None and manifest_total != len(distinct_mutation_ids)
    )

    total_assigned = sum(item["assigned"] for item in results)
    total_executed = sum(item["executed"] for item in results)
    total_killed = sum(item["killed"] for item in results)
    total_survived = sum(item["survived"] for item in results)
    total_incompetent = sum(item["incompetent"] for item in results)
    total_failed = sum(item.get("failed", 0) for item in results)
    total_orphaned = sum(item.get("orphaned_results", 0) for item in results)
    total_never_run = sum(item.get("never_run", 0) for item in results)
    total_unmeasured = sum(item.get("unmeasured", 0) for item in results)

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
    print(f"| worker failures | {total_failed} |")
    print(f"| results without specs | {total_orphaned} |")
    print(f"| never run | {total_never_run} |")
    print(f"| unmeasured | {total_unmeasured} |")
    print(f"| distinct mutation specs seen | {len(distinct_mutation_ids)} |")
    print(f"| duplicated across shards | {duplicates} |")
    print(f"| manifest sha256 | {expected_manifest or 'MISMATCH'} |")
    print(f"| measured union sha256 | {measured_manifest} |")
    print()

    survivors = []
    for item in sorted(results, key=lambda i: i["shard"]):
        for survivor in item.get("survivors", []):
            survivors.append((item["shard"], survivor))
    if survivors:
        print("### Survivors")
        print()
        for shard, survivor in survivors:
            print(
                f"- shard {shard}: `{survivor.get('function')}` "
                f"{survivor.get('operator')} at {survivor.get('location')}"
            )
        print()

    failures = []
    if missing_shards:
        failures.append(f"shards with no artifact: {missing_shards}")
    if expected_total is None:
        failures.append(
            f"shards disagree about the session size: {sorted(map(str, session_totals))}"
        )
    if manifest_total is None:
        failures.append(
            f"shards disagree about manifest size: {sorted(map(str, manifest_totals))}"
        )
    if None in manifests or len(manifests) != 1:
        failures.append(
            f"shards do not share one mutation manifest: {sorted(map(str, manifests))}"
        )
    if expected_total is not None and manifest_total is not None:
        if expected_total != manifest_total:
            failures.append(
                f"session has {expected_total} items but manifest has {manifest_total} specs"
            )
    if duplicates:
        failures.append(f"{duplicates} mutation specs were measured by more than one shard")
    if coverage_mismatch:
        failures.append(
            f"coverage mismatch: {len(distinct_mutation_ids)} of {manifest_total} "
            "mutation specs were measured"
        )
    if expected_manifest is not None and measured_manifest != expected_manifest:
        failures.append(
            "measured mutation union does not match the declared manifest"
        )
    if total_unmeasured:
        failures.append(
            f"{total_unmeasured} mutants produced no test result "
            f"({total_failed} worker failures, {total_incompetent} incompetent, "
            f"{total_orphaned} results without specs, {total_never_run} never run)"
        )

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
