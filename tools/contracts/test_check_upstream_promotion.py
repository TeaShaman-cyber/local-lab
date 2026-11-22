"""Deterministic tests for the exact-SHA upstream promotion receipt."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from check_upstream_promotion import ReceiptError, validate  # noqa: E402


def run(*args: str, cwd: pathlib.Path) -> str:
    proc = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def candidate(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    repo = tmp_path / "candidate"
    repo.mkdir()
    run("git", "init", "-q", cwd=repo)
    run("git", "config", "user.name", "test", cwd=repo)
    run("git", "config", "user.email", "test@example.invalid", cwd=repo)
    target = repo / "hermes_otel" / "live_store.py"
    target.parent.mkdir()
    target.write_text("candidate\n", encoding="utf-8")
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-q", "-m", "candidate", cwd=repo)
    sha = run("git", "rev-parse", "HEAD", cwd=repo)
    digest = hashlib.sha256(b"candidate\n").hexdigest()
    return repo, sha, digest


def receipt(tmp_path: pathlib.Path, sha: str, digest: str) -> pathlib.Path:
    path = tmp_path / f"{sha}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "local-lab.upstream-live-acceptance.v1",
                "status": "VERIFIED",
                "candidate": {
                    "repo": "TeaShaman-cyber/hermes-otel",
                    "sha": sha,
                    "files": {"hermes_otel/live_store.py": digest},
                },
                "runtime": {
                    "gateway_pid": 14804,
                    "plugin_doctor_rc": 0,
                    "plugin_hooks": 34,
                    "tempo_trace_id": "d16c2673f5b5ff002a55637a5b7125eb",
                    "tempo_process_pid": 14804,
                    "loki": {
                        "start": "2026-11-22T08:40:00Z",
                        "end": "2026-11-22T08:50:00Z",
                        "queries": ["WinError 32", "hermes_otel_live.db"],
                        "matches": 0,
                    },
                },
                "verified_at": "2026-11-22T08:51:00Z",
                "issue_url": "https://github.com/TeaShaman-cyber/local-lab/issues/281",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_valid_exact_sha_receipt_passes(tmp_path):
    repo, sha, digest = candidate(tmp_path)
    result = validate(receipt(tmp_path, sha, digest), repo, sha)
    assert result["status"] == "VERIFIED"
    assert result["candidate_sha"] == sha
    assert result["files_verified"] == 1


def test_receipt_from_previous_head_fails_closed(tmp_path):
    repo, sha, digest = candidate(tmp_path)
    with pytest.raises(ReceiptError, match="STALE: receipt sha"):
        validate(receipt(tmp_path, sha, digest), repo, "0" * 40)


def test_candidate_file_hash_drift_fails_closed(tmp_path):
    repo, sha, _digest = candidate(tmp_path)
    with pytest.raises(ReceiptError, match="STALE: hermes_otel/live_store.py hash"):
        validate(receipt(tmp_path, sha, "0" * 64), repo, sha)


def test_doctor_failure_is_not_live_acceptance(tmp_path):
    repo, sha, digest = candidate(tmp_path)
    path = receipt(tmp_path, sha, digest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runtime"]["plugin_doctor_rc"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReceiptError, match="plugin_doctor_rc must be 0"):
        validate(path, repo, sha)


def test_tempo_pid_must_be_current_gateway(tmp_path):
    repo, sha, digest = candidate(tmp_path)
    path = receipt(tmp_path, sha, digest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runtime"]["tempo_process_pid"] = 18880
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReceiptError, match="Tempo PID must equal gateway PID"):
        validate(path, repo, sha)


def test_loki_window_must_be_bounded(tmp_path):
    repo, sha, digest = candidate(tmp_path)
    path = receipt(tmp_path, sha, digest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runtime"]["loki"]["end"] = "2026-11-22T10:50:00Z"
    data["verified_at"] = "2026-11-22T10:51:00Z"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReceiptError, match="interval must be bounded"):
        validate(path, repo, sha)
