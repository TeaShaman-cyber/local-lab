#!/usr/bin/env python3
"""Restore drill: prove the backup can actually be restored and read.

Why this exists: a backup that is only checked for presence and size is not a
backup, it is a file. The failures that matter here — truncated archive, missing
WAL segment, incompatible page format — leave the files looking fine.

So this restores into a temporary file and **reads rows out of it**. Three
controls keep the drill honest:

* ``--negative-control``: a corrupt file must be rejected.
* ``--positive-control``: a clean file with rows missing must be rejected — the
  branch a corrupt-file control never reaches.
* ``--self-test``: fixed input/output pairs for the expectation classifier.

The row count is compared against ``expected.json``, written by the previous
successful run. A failed run never moves the baseline forward.

Exit codes: 0 = restored and readable, 1 = drill failed to restore or read,
2 = configuration problem (missing source / missing tool).

Usage::

    python tools/state_db_backup/drill.py                 # check the Litestream replica
    python tools/state_db_backup/drill.py --negative-control
    python tools/state_db_backup/drill.py --positive-control
    python tools/state_db_backup/drill.py --self-test
    python tools/state_db_backup/drill.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import subprocess
import time

REPLICA = pathlib.Path("E:/theseus-backups/state-db/litestream")
CONFIG = pathlib.Path("E:/tools/litestream/litestream.yml")
LITESTREAM = pathlib.Path("E:/tools/litestream/litestream.exe")
LIVE_DB = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "state.db"
WORKDIR = pathlib.Path(os.environ.get("TEMP", "E:/")) / "state-db-drill"
# The expectation lives outside the repository and outside the snapshot. Numbers
# change daily, so committing them is noise; deriving them from the snapshot
# under test is the bug this file exists to prevent.
EXPECTED = pathlib.Path("E:/theseus-backups/state-db/expected.json")
TOLERANCE = 0.02
# A row count can match while the data is a week old. The drill runs daily, so a lag
# beyond a day plus margin means the replica has stopped following the live database —
# that is a failure of the backup, not a number to admire in the report
# (Shoot's review, 2026-10-31).
MAX_LAG_HOURS = 30.0


def restore(target: pathlib.Path) -> tuple[bool, str]:
    if not LITESTREAM.is_file():
        return False, f"litestream binary not found: {LITESTREAM}"
    if not CONFIG.is_file():
        return False, f"config not found: {CONFIG}"
    if not REPLICA.is_dir():
        return False, f"replica not found: {REPLICA}"

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    proc = subprocess.run(
        [str(LITESTREAM), "restore", "-config", str(CONFIG), "-o", str(target), str(LIVE_DB)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    # Do not trust the exit code in either direction. Litestream on Windows returns
    # rc=1 after "sync restore output dir" (fsync on a directory is unsupported) while
    # writing a complete, readable database — and it can also return 0 with a file that
    # holds nothing. The evidence that matters is read_back(): integrity_check plus row
    # counts. The code is reported, not obeyed (Shoot's review, 2026-10-31).
    if not target.exists():
        detail = (proc.stderr or proc.stdout).strip()[:200]
        return False, f"restore produced no file (rc={proc.returncode}): {detail}"
    suffix = "" if proc.returncode == 0 else f" rc={proc.returncode} (output-dir sync)"
    return True, target.name + suffix


def read_back(path: pathlib.Path) -> tuple[bool, str]:
    """Read rows, not metadata. A file can exist and still hold nothing readable."""
    try:
        # immutable=1 keeps the drill from taking locks or creating -wal files
        # next to a temporary copy.
        conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    except sqlite3.Error as exc:
        return False, f"cannot open: {exc}"
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return False, f"integrity_check: {integrity}"
        messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        sessions = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        newest = conn.execute("SELECT max(timestamp) FROM messages").fetchone()[0]
        # An empty but "ok" database is a failed drill, not a pass.
        if not messages:
            return False, "integrity ok but zero messages: nothing to restore"
        return True, f"messages={messages} sessions={sessions} newest={newest}"
    except sqlite3.Error as exc:
        return False, f"read-back failed: {exc}"
    finally:
        conn.close()


def live_counts() -> tuple[int | None, int | None]:
    if not LIVE_DB.is_file():
        return None, None
    try:
        conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        try:
            return (
                conn.execute("SELECT count(*) FROM messages").fetchone()[0],
                conn.execute("SELECT max(timestamp) FROM messages").fetchone()[0],
            )
        finally:
            conn.close()
    except sqlite3.Error:
        return None, None


def check_expectation(restored: int, expected: int | None, tolerance: float = TOLERANCE) -> tuple[bool, str]:
    """Compare the restored count against a number written down *before* the run.

    ``messages > 0`` catches total loss and nothing else: a snapshot that
    restores 100k rows instead of 158k passes integrity_check and passes any
    "greater than zero" test. The only thing that catches it is a count fixed
    by a different process at an earlier time, so the check cannot re-derive
    its own denominator from the thing it is checking.
    """
    if expected is None:
        return True, f"no prior expectation: baseline ({restored} messages)"
    floor = int(expected * (1 - tolerance))
    if restored < floor:
        return False, f"restored {restored} < expected {expected} (floor {floor}, tolerance {tolerance:.0%})"
    return True, f"restored {restored} >= floor {floor} (expected {expected})"


def read_expected() -> int | None:
    if not EXPECTED.is_file():
        return None
    try:
        return int(json.loads(EXPECTED.read_text(encoding="utf-8"))["messages"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_expected(messages: int) -> None:
    """Only a run that passed may move the baseline forward."""
    EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    payload = {"messages": messages, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    EXPECTED.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def negative_control() -> tuple[bool, str]:
    """A drill that cannot fail proves nothing: corrupt a copy and require failure."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    broken = WORKDIR / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512 + b"garbage" * 64)
    ok, detail = read_back(broken)
    broken.unlink(missing_ok=True)
    if ok:
        return False, "negative control PASSED a corrupt file — the drill cannot detect failure"
    return True, f"correctly rejected corrupt file ({detail[:80]})"


def positive_control() -> tuple[bool, str]:
    """Poison the *output*, not the input: a clean snapshot with rows missing.

    The corrupt-file control only protects the branch it poisons. A snapshot
    that restores cleanly with half the rows travels the success branch and
    never meets the corrupt one, so it needs its own control.
    """
    WORKDIR.mkdir(parents=True, exist_ok=True)
    shrunk = WORKDIR / "shrunk.db"
    shrunk.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(shrunk)
        try:
            conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, timestamp REAL)")
            conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
            conn.executemany("INSERT INTO messages (timestamp) VALUES (?)", [(1.0,)] * 50)
            conn.commit()
        finally:
            conn.close()
        ok, detail = read_back(shrunk)
        if not ok:
            return False, f"control fixture is not readable: {detail}"
        # The fixture holds 50 rows; the expectation says 100. A drill that
        # trusts its own snapshot accepts this, which is the defect.
        verdict, why = check_expectation(50, 100)
        return (not verdict), f"{'correctly flagged' if not verdict else 'WRONGLY accepted'} 50 of 100 rows ({why})"
    finally:
        shrunk.unlink(missing_ok=True)


def self_test() -> tuple[bool, str]:
    """Fixed input/output pairs for the classifier: no network, no restore."""
    cases = [
        # (restored, expected, tolerance, want_ok)
        (1000, None, 0.02, True),    # baseline run, nothing to compare
        (1000, 1000, 0.02, True),    # exact match
        (985, 1000, 0.02, True),     # inside tolerance
        (979, 1000, 0.02, False),    # just below the floor
        (500, 1000, 0.02, False),    # silent partial loss: the case that motivated this
        (0, 1000, 0.02, False),      # total loss
        (0, None, 0.02, True),       # empty baseline is caught by read_back, not here
        (2000, 1000, 0.02, True),    # growth is not a failure
    ]
    failures = []
    for restored, expected, tolerance, want in cases:
        got, why = check_expectation(restored, expected, tolerance)
        if got != want:
            failures.append(f"restored={restored} expected={expected} tolerance={tolerance}: want ok={want} got ok={got} ({why})")
    if failures:
        return False, "; ".join(failures)
    return True, f"{len(cases)} cases, all match"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", type=pathlib.Path, help="also append the JSON result to this file")
    ap.add_argument("--negative-control", action="store_true", help="verify the drill can detect a corrupt file")
    ap.add_argument("--positive-control", action="store_true", help="verify the drill rejects a clean snapshot with rows missing")
    ap.add_argument("--self-test", action="store_true", help="check the expectation classifier against fixed cases")
    args = ap.parse_args()

    started = time.time()
    result: dict = {"slot": "litestream", "ok": False}

    if args.self_test:
        ok, detail = self_test()
        result.update({"ok": ok, "stage": "self_test", "detail": detail})
    elif args.negative_control:
        ok, detail = negative_control()
        result.update({"ok": ok, "stage": "negative_control", "detail": detail})
    elif args.positive_control:
        ok, detail = positive_control()
        result.update({"ok": ok, "stage": "positive_control", "detail": detail})
    else:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        target = WORKDIR / "drill-restore.db"
        ok, detail = restore(target)
        result["stage"] = "restore"
        result["detail"] = detail
        if ok:
            ok, detail = read_back(target)
            result["stage"] = "read_back"
            result["detail"] = detail
            if ok:
                restored = int(detail.split("messages=")[1].split()[0])
                newest_raw = detail.split("newest=")[1].split()[0] if "newest=" in detail else "None"
                newest = None if newest_raw == "None" else float(newest_raw)

                # Read the live side first: the baseline has to come from the database
                # being backed up, not from the last restore. A replica that stopped
                # growing keeps passing a baseline taken from itself otherwise
                # (Shoot's review, 2026-10-31).
                live_messages, live_newest = live_counts()
                result["live_messages"] = live_messages
                if live_messages is not None and live_newest is not None and newest is not None:
                    result["lag_hours"] = round((live_newest - newest) / 3600, 2)

                expected = live_messages if live_messages is not None else read_expected()
                ok, why = check_expectation(restored, expected)
                result["stage"] = "expectation"
                result["detail"] = why
                result["restored_messages"] = restored
                result["expected_messages"] = expected

                # Staleness is its own failure, not a number in the report: a backup
                # from last week can match the row count and still be useless.
                lag = result.get("lag_hours")
                if ok and lag is not None and lag > MAX_LAG_HOURS:
                    ok = False
                    result["detail"] = f"{why}; but lag {lag}h exceeds {MAX_LAG_HOURS}h"
                    result["stage"] = "staleness"

                # A failed run must never move the baseline.
                if ok and expected is not None:
                    write_expected(expected)
                    result["baseline_written"] = expected
        result["ok"] = ok
        # The temporary copy is 1.4 GB; leaving it behind would slowly fill the disk.
        target.unlink(missing_ok=True)

    result["elapsed_s"] = round(time.time() - started, 1)

    line = json.dumps(result, ensure_ascii=False)
    if args.log:
        # Written from inside the process: pythonw has no console, so shell
        # redirection is not available and a launcher script would have to pass
        # the file path through cmd quoting. One line per run, append-only.
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")

    if args.json:
        print(line)
    else:
        status = "OK" if result["ok"] else "FAILED"
        print(f"{status} slot={result['slot']} stage={result.get('stage')} elapsed={result['elapsed_s']}s")
        print(f"  {result.get('detail')}")
        if result.get("live_messages") is not None:
            print(f"  live_messages={result['live_messages']} lag_hours={result.get('lag_hours')}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
