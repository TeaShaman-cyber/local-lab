#!/usr/bin/env python3
"""Restore drill: prove the backup can actually be restored and read.

Why this exists: a backup that is only checked for presence and size is not a
backup, it is a file. The failures that matter here — truncated archive, missing
WAL segment, incompatible page format — leave the files looking fine.

So this restores into a temporary file and **reads rows out of it**. It also
supports a negative control (``--negative-control``): a drill that cannot fail on
a deliberately corrupt file proves nothing, so that path is exercised on demand.

Exit codes: 0 = restored and readable, 1 = drill failed to restore or read,
2 = configuration problem (missing source / missing tool).

Usage::

    python tools/state_db_backup/drill.py                 # check the Litestream replica
    python tools/state_db_backup/drill.py --negative-control
    python tools/state_db_backup/drill.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time

REPLICA = pathlib.Path("E:/theseus-backups/state-db/litestream")
CONFIG = pathlib.Path("E:/tools/litestream/litestream.yml")
LITESTREAM = pathlib.Path("E:/tools/litestream/litestream.exe")
LIVE_DB = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "state.db"
WORKDIR = pathlib.Path(os.environ.get("TEMP", "E:/")) / "state-db-drill"


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
    if proc.returncode != 0 and not target.exists():
        return False, f"restore failed rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()[:200]}"
    if not target.exists():
        return False, "restore reported success but produced no file"
    return True, target.name


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--negative-control", action="store_true", help="verify the drill can detect a corrupt file")
    args = ap.parse_args()

    started = time.time()
    result: dict = {"slot": "litestream", "ok": False}

    if args.negative_control:
        ok, detail = negative_control()
        result.update({"ok": ok, "stage": "negative_control", "detail": detail})
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
                live_messages, live_newest = live_counts()
                result["live_messages"] = live_messages
                if live_messages is not None and live_newest is not None:
                    restored = int(detail.split("messages=")[1].split()[0])
                    lag_h = (live_newest - float(detail.split("newest=")[1].split()[0])) / 3600 if "newest=" in detail else None
                    result["restored_messages"] = restored
                    result["lag_hours"] = None if lag_h is None else round(lag_h, 2)
        result["ok"] = ok
        # The temporary copy is 1.4 GB; leaving it behind would slowly fill the disk.
        target.unlink(missing_ok=True)

    result["elapsed_s"] = round(time.time() - started, 1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        status = "OK" if result["ok"] else "FAILED"
        print(f"{status} slot={result['slot']} stage={result.get('stage')} elapsed={result['elapsed_s']}s")
        print(f"  {result.get('detail')}")
        if result.get("live_messages") is not None:
            print(f"  live_messages={result['live_messages']} lag_hours={result.get('lag_hours')}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
