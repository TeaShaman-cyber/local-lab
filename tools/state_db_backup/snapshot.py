#!/usr/bin/env python3
"""Consistent snapshot of the live Hermes database.

Why this and not Litestream: Litestream does not officially support Windows, and its
`pythonw`-free approach reads WAL segments from the filesystem. On this machine that
combination failed twice — a replica that stopped at 02:01 while the process stayed
alive, and a `sync ltx dir: Access is denied` on every checkpoint. Continuous
replication is the wrong shape for an unsupported platform.

`VACUUM INTO` is the official SQLite answer for copying a *live* database
(https://www.sqlite.org/lang_vacuum.html): "an alternative to the backup API for
generating backup copies of a live database". It reads through SQLite itself, so a
691 MB WAL is not a problem — it never touches the files.

Measured on this machine: 1.52 GB database, 30.8 s, output 1.51 GB, integrity ok,
row count differs from live by 4 (rows written during the vacuum — the snapshot is
consistent as of its start, which is the point).

Exit codes: 0 = snapshot taken and verified, 1 = failed, 2 = cannot run.
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
LOG = SNAPSHOT_DIR.parent / "snapshot.log"


def log(record: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def counts(path: pathlib.Path) -> dict:
    """Read a snapshot exactly as a restore would: immutable, read-only, verified."""
    con = sqlite3.connect(f"file:{path.as_posix()}?immutable=1", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        messages = con.execute("SELECT count(*) FROM messages").fetchone()[0]
        sessions = con.execute("SELECT count(*) FROM sessions").fetchone()[0]
        newest = con.execute("SELECT max(timestamp) FROM messages").fetchone()[0]
        return {
            "integrity": integrity,
            "messages": messages,
            "sessions": sessions,
            "newest": newest,
        }
    finally:
        con.close()


def live_counts() -> dict | None:
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


def rotate(keep: int) -> list[str]:
    """Drop the oldest snapshots beyond `keep`. Only our own naming is touched."""
    existing = sorted(SNAPSHOT_DIR.glob("state-*.db"))
    removed = []
    for old in existing[:-keep] if len(existing) > keep else []:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError:
            pass
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--keep", type=int, default=7, help="snapshots to keep (default 7)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not LIVE_DB.is_file():
        print(f"live database not found: {LIVE_DB}", file=sys.stderr)
        return 2

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = SNAPSHOT_DIR / f"state-{stamp}.db"

    result = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "target": str(target)}
    t0 = time.time()

    try:
        con = sqlite3.connect(str(LIVE_DB), timeout=60)
        try:
            con.execute("PRAGMA busy_timeout=60000")
            con.execute(f"VACUUM INTO '{target.as_posix()}'")
        finally:
            con.close()
        result["elapsed_s"] = round(time.time() - t0, 1)
        result["size_bytes"] = target.stat().st_size
    except (sqlite3.Error, OSError) as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        # A half-written snapshot is worse than none: it looks restorable.
        if target.exists():
            target.unlink()
        log(result)
        print(json.dumps(result, ensure_ascii=False) if args.json else f"FAILED: {result['error']}")
        return 1

    snap = counts(target)
    result.update(snap)
    live = live_counts()
    if live:
        result["live_messages"] = live["messages"]
        result["message_gap"] = live["messages"] - snap["messages"]
        if live["newest"] and snap["newest"]:
            result["gap_hours"] = round((live["newest"] - snap["newest"]) / 3600, 2)

    # A snapshot that does not read back is not a snapshot. Integrity is checked on the
    # copy itself, not assumed from the exit code.
    result["ok"] = snap["integrity"] == "ok" and snap["messages"] > 0

    removed = rotate(max(1, args.keep))
    if removed:
        result["rotated_out"] = removed

    log(result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(
            f"{'OK  ' if result['ok'] else 'FAIL'} {target.name} "
            f"{result['size_bytes']/1e9:.2f} GB in {result['elapsed_s']}s "
            f"messages={snap['messages']} gap={result.get('message_gap', '?')}"
        )
        if not result["ok"]:
            print(f"  integrity={snap['integrity']} messages={snap['messages']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
