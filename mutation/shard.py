#!/usr/bin/env python3
"""Reduce an initialized cosmic-ray session to one shard.

`cosmic-ray exec` runs every pending work item in the session, so the cheapest
cross-job sharding is to delete the ones that belong to other shards before
`exec`. The selection is `(rowid - 1) % shards == shard`, which maps SQLite's
one-based rowids onto zero-based shard numbers. It is stable: `init` inserts the
same rows in the same order for the same source tree and the same cosmic-ray
version, so shard boundaries are reproducible across jobs and runs.

Writes `shard-meta.json` next to the session so `report.py` can tell a shard
that finished from a shard that never ran.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep only this shard's work items")
    parser.add_argument("session", help="path to session.sqlite")
    parser.add_argument("shard", type=int)
    parser.add_argument("shards", type=int)
    parser.add_argument("--meta", default="shard-meta.json")
    args = parser.parse_args()

    if args.shards < 1 or not 0 <= args.shard < args.shards:
        print(f"invalid shard {args.shard} of {args.shards}", file=sys.stderr)
        return 2

    with sqlite3.connect(args.session) as con:
        total = con.execute("select count(*) from work_items").fetchone()[0]
        con.execute(
            "delete from work_items where (rowid - 1) % ? != ?",
            (args.shards, args.shard),
        )
        con.commit()
        mine = con.execute("select count(*) from work_items").fetchone()[0]

    meta = {"shard": args.shard, "shards": args.shards, "total": total, "items": mine}
    with open(args.meta, "w", encoding="utf-8") as handle:
        json.dump(meta, handle)

    print(
        f"shard {args.shard}/{args.shards}: {mine} of {total} work items "
        f"({100 * mine / total:.0f}%)" if total else "no work items at all"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
