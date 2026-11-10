#!/usr/bin/env python3
"""Tests for the mutation gate itself.

The aggregate step is the one place where a bug turns "we measured nothing" into
"we are clean", so each branch that decides pass/fail is pinned to a concrete
input here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

AGGREGATE = Path(__file__).with_name("aggregate.py")
REPORT = Path(__file__).with_name("report.py")
MANIFEST_IDS = [f"m{i}" for i in range(40)]
MANIFEST_SHA256 = hashlib.sha256(
    "\n".join(sorted(MANIFEST_IDS)).encode("ascii")
).hexdigest()

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
    "orphaned_results": 0,
    "never_run": 0,
    "unmeasured": 0,
    "worker_outcomes": {"NORMAL": 20},
    "manifest_sha256": MANIFEST_SHA256,
    "manifest_total": 40,
    "source_commit_sha": "a" * 40,
    "source_tree_sha": "b" * 40,
    "run_url": "https://github.com/TeaShaman-cyber/local-lab/actions/runs/111",
    "survivors": [{"function": "_save_state", "operator": "core/Add_Sub", "location": "221:25"}],
}


def write_shard(root: Path, shard: int, **overrides) -> None:
    mutation_ids = overrides.pop(
        "mutation_ids", [f"m{shard * 20 + i}" for i in range(20)]
    )
    payload = {**SHARD, "shard": shard, "mutation_ids": mutation_ids, **overrides}
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


def run_report(
    root: Path,
    name: str,
    job_id: str,
    operator: str = "core/Add_Sub",
    result_job_id: str | None = None,
    expected_returncode: int = 0,
    diff: str = "diff",
):
    session = root / f"{name}.sqlite"
    with sqlite3.connect(session) as con:
        con.executescript(
            """
            create table mutation_specs (
                module_path text, operator_name text, operator_args text,
                occurrence integer, start_pos_row integer, start_pos_col integer,
                end_pos_row integer, end_pos_col integer,
                definition_name text, job_id text
            );
            create table work_results (
                worker_outcome text, output text, test_outcome text,
                diff text, job_id text
            );
            """
        )
        con.execute(
            "insert into mutation_specs values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("module.py", operator, "{}", 0, 10, 2, 10, 3, "function", job_id),
        )
        con.execute(
            "insert into work_results values (?, ?, ?, ?, ?)",
            ("NORMAL", "", "SURVIVED", diff, result_job_id or job_id),
        )

    meta = root / f"{name}-meta.json"
    out = root / f"{name}-result.json"
    meta.write_text(
        json.dumps({"shard": 0, "shards": 1, "total": 1, "items": 1}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(REPORT), str(session), "--meta", str(meta), "--out", str(out),
         "--source-commit", "a" * 40, "--source-tree", "b" * 40,
         "--run-url", "https://github.com/TeaShaman-cyber/local-lab/actions/runs/111"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == expected_returncode, proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_mutation_identity_is_stable_across_cosmic_ray_job_ids(tmp_path):
    first = run_report(tmp_path, "first", "random-job-a")
    second = run_report(tmp_path, "second", "random-job-b")
    changed = run_report(tmp_path, "changed", "random-job-c", operator="core/Add_Mul")

    assert first["mutation_ids"] == second["mutation_ids"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["survivors"][0]["job_id"] != second["survivors"][0]["job_id"]
    assert first["survivors"][0]["mutation_id"] == second["survivors"][0]["mutation_id"]
    assert first["mutation_ids"] != changed["mutation_ids"]
    assert first["manifest_sha256"] != changed["manifest_sha256"]


def test_report_fails_when_result_has_no_mutation_spec(tmp_path):
    result = run_report(
        tmp_path,
        "orphan",
        "known-spec",
        result_job_id="orphan-result",
        expected_returncode=1,
    )

    assert result["executed"] == 0
    assert result["orphaned_results"] == 1
    assert result["unmeasured"] == 1
    assert result["mutation_ids"] == []


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


def test_orphaned_results_fail_aggregation(tmp_path):
    write_shard(tmp_path, 0, executed=19, orphaned_results=1, unmeasured=1)
    write_shard(tmp_path, 1)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "1 results without specs" in proc.stderr


def test_incompetent_from_normal_worker_fails(tmp_path):
    # cosmic-ray catches a failure while launching the test process and records
    # INCOMPETENT under a NORMAL worker: the row exists, the test never ran.
    write_shard(tmp_path, 0, incompetent=1, unmeasured=1)
    write_shard(tmp_path, 1)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "1 incompetent" in proc.stderr


def test_overlapping_shards_fail(tmp_path):
    shared = [f"m{i}" for i in range(20)]
    write_shard(tmp_path, 0, mutation_ids=shared)
    write_shard(tmp_path, 1, mutation_ids=shared)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "mutation specs were measured by more than one shard" in proc.stderr


def test_coverage_gap_fails(tmp_path):
    write_shard(tmp_path, 0, mutation_ids=[f"m{i}" for i in range(20)])
    write_shard(tmp_path, 1, mutation_ids=[f"m{i}" for i in range(15, 35)])

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "coverage mismatch" in proc.stderr


def test_wrong_union_fails_even_when_count_matches(tmp_path):
    write_shard(tmp_path, 0, mutation_ids=[f"m{i}" for i in range(20)])
    wrong_second_half = [f"m{i}" for i in range(20, 39)] + ["not-in-manifest"]
    write_shard(tmp_path, 1, mutation_ids=wrong_second_half)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "measured mutation union does not match" in proc.stderr


def test_shards_from_different_sessions_fail(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1, session_total=41)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "disagree about the session size" in proc.stderr


def test_shards_from_different_manifests_fail(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1, manifest_sha256="manifest-v2")

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "do not share one mutation manifest" in proc.stderr


def test_session_and_manifest_size_must_match(tmp_path):
    write_shard(tmp_path, 0, manifest_total=41)
    write_shard(tmp_path, 1, manifest_total=41)

    proc = run_aggregate(tmp_path, shards=2)

    assert proc.returncode == 1
    assert "session has 40 items but manifest has 41 specs" in proc.stderr


def test_survivor_threshold_fails_only_when_set(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1)

    allowed = run_aggregate(tmp_path, shards=2, max_survivors=10)
    assert allowed.returncode == 0, allowed.stderr

    blocked = run_aggregate(tmp_path, shards=2, max_survivors=4)
    assert blocked.returncode == 1
    assert "exceed the allowed 4" in blocked.stderr


def test_scout_artifact_includes_checkout_and_untruncated_spec(tmp_path):
    long_diff = "long-mutant-diff:" + "x" * 980
    result = run_report(tmp_path, "full-evidence", "random-job-z", diff=long_diff)
    survivor = result["survivors"][0]
    spec = survivor["spec"]
    assert result["source_commit_sha"] == "a" * 40
    assert result["source_tree_sha"] == "b" * 40
    assert result["run_url"].endswith("/111")
    assert spec == {
        "module_path": "module.py",
        "operator_name": "core/Add_Sub",
        "operator_args": {},
        "occurrence": 0,
        "start": [10, 2],
        "end": [10, 3],
        "definition_name": "function",
    }
    from mutation.report import _fingerprint
    assert _fingerprint(spec) == survivor["mutation_id"]
    assert survivor["diff"] == long_diff
    assert survivor["worker_outcome"] == "NORMAL"
    assert survivor["test_outcome"] == "SURVIVED"


def test_aggregate_rejects_mixed_checkout_lineage(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1, source_tree_sha="c" * 40)
    result = run_aggregate(tmp_path, shards=2)
    assert result.returncode == 1
    assert "source tree" in result.stderr


def test_aggregate_rejects_missing_checkout_lineage(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1, source_commit_sha=None)
    result = run_aggregate(tmp_path, shards=2)
    assert result.returncode == 1
    assert "source commit" in result.stderr



def test_aggregate_rejects_mixed_workflow_runs(tmp_path):
    write_shard(tmp_path, 0)
    write_shard(tmp_path, 1, run_url="https://github.com/TeaShaman-cyber/local-lab/actions/runs/222")
    result = run_aggregate(tmp_path, shards=2)
    assert result.returncode == 1
    assert "run URL" in result.stderr
