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
    "missing": 0,
    "worker_outcomes": {"NORMAL": 20},
    "survivor_ids": ["job-a", "job-b"],
}


def write_shard(root: Path, shard: int, **overrides) -> None:
    payload = {**SHARD, "shard": shard, **overrides}
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
    assert "job-a" in proc.stdout


def test_missing_shard_fails(tmp_path):
    write_shard(tmp_path, 0)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "shards with no artifact" in proc.stderr


def test_unexecuted_mutants_fail(tmp_path):
    write_shard(tmp_path, 0, missing=3)
    write_shard(tmp_path, 1, missing=2)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "5 mutants were never executed" in proc.stderr


def test_survivor_threshold_fails_only_when_set(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1)

    allowed = run_aggregate(tmp_path, shards=2, max_survivors=10)
    assert allowed.returncode == 0, allowed.stderr

    blocked = run_aggregate(tmp_path, shards=2, max_survivors=4)
    assert blocked.returncode == 1
    assert "exceed the allowed 4" in blocked.stderr
