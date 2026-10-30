#!/usr/bin/env python3
"""Append one incident entry to the host-local incident log.

Why a file on the host and not GitHub: during an incident the tracker, the
network and the agent itself may all be unavailable. The one thing that still
works is local disk. RULE-013 allows fixing without an issue on that basis, and
requires this journal as the record that an issue is later written from.

Usage:
    python tools/incidents/log_incident.py \
        --service hindsight --symptom "recall returns 500" \
        --action "restarted hindsight-api; checked health" \
        --verification "health=healthy, recall returned 11 facts"

    python tools/incidents/log_incident.py --list
    python tools/incidents/log_incident.py --pending   # entries without an issue

The log is JSONL, one object per line, append-only.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(os.environ.get("INCIDENT_LOG", r"D:/theseus-ops/incidents.log"))


def append_entry(log: Path, service: str, symptom: str, action: str, verification: str) -> dict:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "incident",
        "service": service,
        "symptom": symptom,
        "action": action,
        "verification": verification,
        "issue": None,
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_entries(log: Path) -> list[dict]:
    if not log.exists():
        return []
    entries = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Record an infrastructure incident locally.")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--service")
    parser.add_argument("--symptom")
    parser.add_argument("--action")
    parser.add_argument("--verification")
    parser.add_argument("--list", action="store_true", help="print the journal")
    parser.add_argument("--pending", action="store_true", help="print entries without an issue")
    args = parser.parse_args()

    log = Path(args.log)

    if args.list or args.pending:
        entries = read_entries(log)
        if args.pending:
            entries = [e for e in entries if not e.get("issue")]
        if not entries:
            print("no entries")
            return 0
        for entry in entries:
            print(json.dumps(entry, ensure_ascii=False))
        return 0

    missing = [k for k in ("service", "symptom", "action", "verification")
               if not getattr(args, k)]
    if missing:
        parser.error(f"missing required fields: {', '.join('--' + m for m in missing)}")

    entry = append_entry(log, args.service, args.symptom, args.action, args.verification)
    print(f"logged: {entry['ts']} {entry['service']}")
    print(f"journal: {log}")
    print("next: write the issue within 24h and put its number into the 'issue' field (RULE-013)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
