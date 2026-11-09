#!/usr/bin/env python3
"""Tests for the mutation gate itself.

The aggregate step is the one place where a bug turns "we measured nothing" into
"we are clean", so each branch that decides pass/fail is pinned to a concrete
input here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

AGGREGATE = Path(__file__).with_name("aggregate.py")

SHARD = {
    "shard": 0,
    "shards": 2,
    "session_total": 40,
    "assigned": 20,
    "executed": 20,
    "killed": 15,
    "survived": 5,
    "incompetent": 0,
    "failed": 0,
    "never_run": 0,
    "unmeasured": 0,
    "worker_outcomes": {"NORMAL": 20},
    "survivors": [{"function": "_save_state", "operator": "core/Add_Sub", "location": "221:25"}],
}


def write_shard(root: Path, shard: int, **overrides) -> None:
    job_ids = overrides.pop("job_ids", [f"m{shard * 20 + i}" for i in range(20)])
    payload = {**SHARD, "shard": shard, "job_ids": job_ids, **overrides}
    directory = root / f"shard-{shard}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "shard-result.json").write_text(json.dumps(payload), encoding="utf-8")


def run_aggregate(results: Path, shards: int, max_survivors: int = -1):
    return subprocess.run(
        [
            sys.executable,
            str(AGGREGATE),
            "--results", str(results),
            "--shards", str(shards),
            "--max-survivors", str(max_survivors),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_complete_sweep_passes(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 0, proc.stderr
    assert "| shards reporting | 2 |" in proc.stdout
    assert "| survived | 10 |" in proc.stdout
    assert "_save_state" in proc.stdout
    assert "core/Add_Sub" in proc.stdout


def test_missing_shard_fails(tmp_path):
    write_shard(tmp_path, 0)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "shards with no artifact" in proc.stderr


def test_unexecuted_mutants_fail(tmp_path):
    write_shard(tmp_path, 0, never_run=3, unmeasured=3)
    write_shard(tmp_path, 1, never_run=2, unmeasured=2)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "5 mutants produced no test result" in proc.stderr


def test_worker_failures_are_not_executions(tmp_path):
    # cosmic-ray persists a row for a worker exception with
    # test_outcome=INCOMPETENT, so a row alone is not proof a test ran.
    write_shard(tmp_path, 0, executed=18, failed=2, unmeasured=2)
    write_shard(tmp_path, 1)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "2 worker failures" in proc.stderr


def test_overlapping_shards_fail(tmp_path):
    shared = [f"m{i}" for i in range(20)]
    write_shard(tmp_path, 0, job_ids=shared)
    write_shard(tmp_path, 1, job_ids=shared)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "measured by more than one shard" in proc.stderr


def test_coverage_gap_fails(tmp_path):
    write_shard(tmp_path, 0, job_ids=[f"m{i}" for i in range(20)])
    write_shard(tmp_path, 1, job_ids=[f"m{i}" for i in range(15, 35)])

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "coverage gap" in proc.stderr


def test_shards_from_different_sessions_fail(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1, session_total=41)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "disagree about the session size" in proc.stderr


def test_survivor_threshold_fails_only_when_set(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1)

    allowed = run_aggregate(tmp_path, shards=2, max_survivors=10)
    assert allowed.returncode == 0, allowed.stderr

    blocked = run_aggregate(tmp_path, shards=2, max_survivors=4)
    assert blocked.returncode == 1
    assert "exceed the allowed 4" in blocked.stderr
