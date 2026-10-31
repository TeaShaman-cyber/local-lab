#!/usr/bin/env python3
"""Audit every repository of the account for the merge-watch guard.

Why Grafana and not a GitHub Action: the alerting contour already exists
(Alloy -> Prometheus -> Grafana -> email). One scheduled task writes a .prom
file, Alloy scrapes it, the alert does the rest. No new service, no new
workflow in 43 repositories just to count them.

Writes Prometheus textfile metrics to --out (default: the metrics drop dir
Alloy already reads).

    python tools/audit/repo_guard_audit.py            # write metrics
    python tools/audit/repo_guard_audit.py --dry-run  # print, write nothing

Metrics:
    repo_guard_total                      repositories inspected
    repo_guard_present                    have .github/workflows/merge-watch.yml
    repo_guard_missing                    do not have it
    repo_guard_missing_repo{repo="..."}   1 for each repository missing the guard
    repo_guard_check_failed               API calls that could not be answered
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

GUARD_PATH = ".github/workflows/merge-watch.yml"
DEFAULT_OUT = Path(os.environ.get("REPO_GUARD_METRICS", r"C:/Apps/theseus-metrics/repo-guard.prom"))


def gh(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def list_repos(owner: str) -> list[dict]:
    code, out = gh([
        "repo", "list", owner,
        "--limit", "500",
        "--json", "name,isArchived,isFork,visibility,updatedAt",
    ])
    if code != 0:
        raise SystemExit(f"gh repo list failed: {out}")
    return json.loads(out)


def has_guard(owner: str, repo: str) -> bool | None:
    """True/False, or None when the API answer was inconclusive."""
    code, _ = gh([
        "api", "--silent",
        f"repos/{owner}/{repo}/contents/{GUARD_PATH}",
    ])
    if code == 0:
        return True
    # 404 in stderr means absent; anything else is inconclusive
    return False if code == 1 else None


def render(owner: str, repos: list[dict]) -> tuple[str, dict]:
    present, missing, failed, lines_extra = 0, 0, 0, []
    missing_names: list[str] = []

    for repo in repos:
        name = repo["name"]
        state = has_guard(owner, name)
        if state is True:
            present += 1
        elif state is False:
            missing += 1
            missing_names.append(name)
            lines_extra.append(f'repo_guard_missing_repo{{repo="{owner}/{name}"}} 1')
        else:
            failed += 1

    now = int(datetime.now(UTC).timestamp())
    lines = [
        "# repo guard audit (merge-watch presence)",
        f"# generated {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"repo_guard_total {len(repos)}",
        f"repo_guard_present {present}",
        f"repo_guard_missing {missing}",
        f"repo_guard_check_failed {failed}",
        f"repo_guard_audit_timestamp {now}",
        *lines_extra,
    ]
    summary = {
        "total": len(repos),
        "present": present,
        "missing": missing,
        "failed": failed,
        "missing_names": missing_names,
    }
    return "\n".join(lines) + "\n", summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit repositories for the merge-watch guard.")
    parser.add_argument("--owner", default="TeaShaman-cyber")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    repos = list_repos(args.owner)
    payload, summary = render(args.owner, repos)

    if not args.quiet:
        print(f"total={summary['total']} present={summary['present']} "
              f"missing={summary['missing']} failed={summary['failed']}")
        for name in summary["missing_names"]:
            print(f"  missing: {args.owner}/{name}")

    if args.dry_run:
        print("--- dry-run, nothing written ---")
        print(payload)
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically: Alloy may scrape at any moment.
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out)
    print(f"written: {out}")
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
