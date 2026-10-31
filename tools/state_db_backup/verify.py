#!/usr/bin/env python3
"""Verify the newest state.db snapshot as if we were restoring from it.

Separate from snapshot.py on purpose. A backup tool that checks its own output is
marking its own homework: it will report success for exactly the case it just
created. This reads whatever sits on disk, with no knowledge of how it got there.

What is checked, and why each one is not redundant:

  integrity_check  — the file is a database at all, not a truncated copy;
  messages > 0     — an empty but "ok" database restores to nothing;
  gap_hours        — a week-old snapshot can match row counts and still be useless;
  gap_messages     — the snapshot must trail the live database by a bounded amount.

Exit codes: 0 = snapshot is usable, 1 = not usable, 2 = no snapshot found.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time

LIVE_DB = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "state.db"
SNAPSHOT_DIR = pathlib.Path("E:/theseus-backups/state-db/snapshots")

# A snapshot every 12 hours plus margin. Beyond this the replica has stopped
# following the live database — that is a failure of the backup, not a number to
# admire in a report.
MAX_GAP_HOURS = 26.0
# The live database grows by roughly a few rows per minute. Anything larger than
# this means the snapshot predates the last several runs.
MAX_GAP_MESSAGES = 5000


def newest_snapshot() -> pathlib.Path | None:
    candidates = sorted(SNAPSHOT_DIR.glob("state-*.db"))
    return candidates[-1] if candidates else None


def read(path: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{path.as_posix()}?immutable=1", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        messages = con.execute("SELECT count(*) FROM messages").fetchone()[0]
        sessions = con.execute("SELECT count(*) FROM sessions").fetchone()[0]
        newest = con.execute("SELECT max(timestamp) FROM messages").fetchone()[0]
        return {"integrity": integrity, "messages": messages,
                "sessions": sessions, "newest": newest}
    finally:
        con.close()


def live() -> dict | None:
    try:
        con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
        try:
            return {
                "messages": con.execute("SELECT count(*) FROM messages").fetchone()[0],
                "newest": con.execute("SELECT max(timestamp) FROM messages").fetchone()[0],
            }
        finally:
            con.close()
    except sqlite3.Error:
        return None


def evaluate(snap: dict, live_counts: dict | None,
             max_gap_hours: float = MAX_GAP_HOURS,
             max_gap_messages: int = MAX_GAP_MESSAGES) -> tuple[bool, list[str]]:
    """All verdicts in one place, so the self-test can call the same logic."""
    problems: list[str] = []

    if snap["integrity"] != "ok":
        problems.append(f"integrity_check: {snap['integrity']}")
    if not snap["messages"]:
        problems.append("integrity ok but zero messages: nothing to restore")

    if live_counts and snap["newest"] and live_counts.get("newest"):
        gap_hours = (live_counts["newest"] - snap["newest"]) / 3600
        if gap_hours > max_gap_hours:
            problems.append(f"snapshot is {gap_hours:.1f}h behind live (max {max_gap_hours}h)")
        gap_messages = live_counts["messages"] - snap["messages"]
        if gap_messages > max_gap_messages:
            problems.append(
                f"snapshot trails by {gap_messages} messages (max {max_gap_messages})"
            )

    return (not problems), problems


def self_test() -> int:
    """Known inputs must produce known verdicts — including the failing ones."""
    good = {"integrity": "ok", "messages": 100, "sessions": 5, "newest": 1000.0}
    cases = [
        (good, {"messages": 100, "newest": 1000.0}, True, "exact match"),
        (good, {"messages": 120, "newest": 1000.0 + 3600}, True, "small lag, within limits"),
        ({**good, "integrity": "database disk image is malformed"}, None, False, "corrupt"),
        ({**good, "messages": 0}, None, False, "empty database"),
        (good, {"messages": 100, "newest": 1000.0 + 3600 * 40}, False, "stale beyond 26h"),
        (good, {"messages": 100 + 10_000, "newest": 1000.0}, False, "trails by too many rows"),
    ]
    failures = 0
    for snap, live_counts, expected, why in cases:
        ok, problems = evaluate(snap, live_counts)
        good_case = ok == expected
        failures += 0 if good_case else 1
        print(f"{'OK  ' if good_case else 'FAIL'} {why}: ok={ok} "
              f"{'; '.join(problems) if problems else ''}")
    print("self-test:", "all cases pass" if failures == 0 else f"{failures} failed")
    return 0 if failures == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--log", type=str, default="E:/theseus-backups/state-db/drill.log")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    snap_path = newest_snapshot()
    if not snap_path:
        print(f"no snapshots in {SNAPSHOT_DIR}", file=sys.stderr)
        return 2

    t0 = time.time()
    result: dict = {"snapshot": str(snap_path), "checked": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        snap = read(snap_path)
    except sqlite3.Error as exc:
        result.update(ok=False, stage="read", detail=f"{type(exc).__name__}: {exc}")
        snap, problems, ok = None, [result["detail"]], False
    else:
        live_counts = live()
        ok, problems = evaluate(snap, live_counts)
        result.update(ok=ok, **snap)
        if live_counts:
            result["live_messages"] = live_counts["messages"]
            result["gap_messages"] = live_counts["messages"] - snap["messages"]
            if snap["newest"] and live_counts.get("newest"):
                result["gap_hours"] = round((live_counts["newest"] - snap["newest"]) / 3600, 2)
        result["detail"] = "; ".join(problems) if problems else "snapshot is usable"

    result["elapsed_s"] = round(time.time() - t0, 1)

    if args.log:
        log_path = pathlib.Path(args.log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        state = "OK  " if result["ok"] else "FAIL"
        print(f"{state} {snap_path.name} elapsed={result['elapsed_s']}s")
        print(f"  {result['detail']}")
        if "gap_hours" in result:
            print(f"  gap: {result['gap_hours']}h, {result['gap_messages']} messages")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
