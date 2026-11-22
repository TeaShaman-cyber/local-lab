#!/usr/bin/env python3
"""Fail-closed validator for exact-SHA upstream live acceptance receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = "local-lab.upstream-live-acceptance.v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TRACE32 = re.compile(r"^[0-9a-f]{32}$")


class ReceiptError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptError(message)


def parse_time(value: Any, field: str) -> datetime:
    require(isinstance(value, str) and value, f"{field}: required ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptError(f"{field}: invalid ISO-8601 value") from exc
    require(parsed.tzinfo is not None, f"{field}: timezone is required")
    return parsed


def git(*args: str, repo: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=False,
    )
    if proc.returncode:
        detail = proc.stderr.decode(errors="replace").strip()
        raise ReceiptError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.decode().strip()


def git_blob(repo: Path, sha: str, relative_path: str) -> bytes:
    proc = subprocess.run(
        ["git", "show", f"{sha}:{relative_path}"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if proc.returncode:
        detail = proc.stderr.decode(errors="replace").strip()
        raise ReceiptError(f"candidate file unavailable: {relative_path}: {detail}")
    return proc.stdout


def validate(receipt_path: Path, candidate_repo: Path, expected_sha: str | None) -> dict[str, Any]:
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"receipt unreadable: {exc}") from exc

    require(data.get("schema") == SCHEMA, f"schema must be {SCHEMA}")
    require(data.get("status") == "VERIFIED", "status must be VERIFIED")

    candidate = data.get("candidate")
    require(isinstance(candidate, dict), "candidate: object required")
    repo_name = candidate.get("repo")
    require(isinstance(repo_name, str) and "/" in repo_name, "candidate.repo: owner/repo required")
    sha = candidate.get("sha")
    require(isinstance(sha, str) and SHA40.fullmatch(sha) is not None, "candidate.sha: 40 lowercase hex required")
    if expected_sha is not None:
        require(sha == expected_sha, f"STALE: receipt sha {sha} != expected {expected_sha}")
    require(receipt_path.stem == sha, f"receipt filename must be {sha}.json")

    resolved = git("rev-parse", f"{sha}^{{commit}}", repo=candidate_repo)
    require(resolved == sha, f"candidate commit unavailable or ambiguous: {sha}")

    files = candidate.get("files")
    require(isinstance(files, dict) and files, "candidate.files: non-empty object required")
    for relative_path, expected_hash in files.items():
        require(
            isinstance(relative_path, str)
            and relative_path
            and not Path(relative_path).is_absolute()
            and ".." not in Path(relative_path).parts,
            f"candidate.files path is unsafe: {relative_path!r}",
        )
        require(
            isinstance(expected_hash, str) and SHA256.fullmatch(expected_hash) is not None,
            f"candidate.files[{relative_path}]: sha256 required",
        )
        observed = hashlib.sha256(git_blob(candidate_repo, sha, relative_path)).hexdigest()
        require(observed == expected_hash, f"STALE: {relative_path} hash {observed} != {expected_hash}")

    runtime = data.get("runtime")
    require(isinstance(runtime, dict), "runtime: object required")
    gateway_pid = runtime.get("gateway_pid")
    require(isinstance(gateway_pid, int) and gateway_pid > 0, "runtime.gateway_pid: positive integer required")
    require(runtime.get("plugin_doctor_rc") == 0, "runtime.plugin_doctor_rc must be 0")
    hooks = runtime.get("plugin_hooks")
    require(isinstance(hooks, int) and hooks > 0, "runtime.plugin_hooks: positive integer required")
    trace_id = runtime.get("tempo_trace_id")
    require(isinstance(trace_id, str) and TRACE32.fullmatch(trace_id) is not None, "runtime.tempo_trace_id: 32 lowercase hex required")
    require(runtime.get("tempo_process_pid") == gateway_pid, "runtime Tempo PID must equal gateway PID")

    loki = runtime.get("loki")
    require(isinstance(loki, dict), "runtime.loki: object required")
    start = parse_time(loki.get("start"), "runtime.loki.start")
    end = parse_time(loki.get("end"), "runtime.loki.end")
    require(end > start, "runtime.loki: end must be after start")
    require((end - start).total_seconds() <= 3600, "runtime.loki: interval must be bounded to <= 1 hour")
    queries = loki.get("queries")
    require(isinstance(queries, list) and all(isinstance(q, str) and q for q in queries), "runtime.loki.queries: non-empty strings required")
    matches = loki.get("matches")
    require(isinstance(matches, int) and matches >= 0, "runtime.loki.matches: non-negative integer required")

    verified_at = parse_time(data.get("verified_at"), "verified_at")
    require(verified_at >= end, "verified_at must be at or after the Loki interval")
    issue_url = data.get("issue_url")
    require(
        isinstance(issue_url, str)
        and issue_url.startswith("https://github.com/TeaShaman-cyber/local-lab/issues/"),
        "issue_url must point to a local-lab issue",
    )

    return {
        "status": "VERIFIED",
        "candidate_repo": repo_name,
        "candidate_sha": sha,
        "files_verified": len(files),
        "gateway_pid": gateway_pid,
        "tempo_trace_id": trace_id,
        "loki_seconds": int((end - start).total_seconds()),
        "receipt": str(receipt_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--candidate-repo", type=Path, required=True)
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    if args.expected_sha is not None and SHA40.fullmatch(args.expected_sha) is None:
        print("BLOCKED: --expected-sha must be 40 lowercase hex", file=sys.stderr)
        return 2
    try:
        result = validate(args.receipt, args.candidate_repo, args.expected_sha)
    except ReceiptError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
