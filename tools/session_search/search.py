#!/usr/bin/env python3
"""Search Hermes session history straight from state.db.

Why this exists: ``messages_fts_trigram`` is an FTS5 **external content** index
over the live ``messages`` table, kept current by insert/update/delete triggers.
So the index is never stale, and no export, adapter or rebuild is involved.

What it deliberately does NOT do: summarise. It returns raw hits with their
coverage, because a summary is interpretation, and interpretation is what the
semantic memory layer already does badly.

Usage::

    python tools/session_search/search.py "nemotron" --limit 5
    python tools/session_search/search.py '"issue-guard"' --json
    python tools/session_search/search.py 'ретрансляция OR relay' --since 2026-10-01

FTS5 syntax notes that cost real tries:
  - quote anything with a hyphen or dot: "issue-guard", not issue-guard;
  - AND is implicit, so mixed languages need an explicit OR;
  - trigram tokenizer means fragments of 3+ characters match, shorter do not.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import time

DEFAULT_DB = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "state.db"
SNIPPET_CHARS = 240


def connect(db: pathlib.Path) -> sqlite3.Connection:
    if not db.is_file():
        raise SystemExit(f"database not found: {db}")
    # Read-only: searching must never be able to change history.
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 5,
    since: str | None = None,
    session: str | None = None,
    recent: bool = False,
) -> list[dict]:
    # bm25 by default, not recency. Ordering by timestamp buried older hits under
    # whatever the current session had just written: 1254 matches for one term,
    # and the top two were both from the last minute. Rank first, then diversify.
    order = "m.timestamp DESC, m.id DESC" if recent else "rank"
    sql = f"""
        SELECT m.id, m.session_id, m.role, m.timestamp, m.tool_name,
               m.message_uid, m.compacted,
               substr(m.content, 1, ?) AS snippet,
               bm25(messages_fts_trigram) AS rank
        FROM messages_fts_trigram f
        JOIN messages m ON m.id = f.rowid
        WHERE messages_fts_trigram MATCH ?
          AND m.content IS NOT NULL AND trim(m.content) <> ''
    """
    params: list = [SNIPPET_CHARS, query]

    if since:
        sql += " AND m.timestamp >= strftime('%s', ?)"
        params.append(since)
    if session:
        sql += " AND m.session_id = ?"
        params.append(session)

    sql += f" ORDER BY {order} LIMIT ?"
    params.append(max(limit * 6, 30))

    rows = [dict(r) for r in conn.execute(sql, params)]

    # Collapse compaction duplicates and cap hits per session.
    # Deduplicate on role+text, not on message_uid: after context compression the
    # same message is stored several times with distinct uids, and uid-based dedup
    # let identical snippets through.
    seen: set[tuple[str, str]] = set()
    per_session: dict[str, int] = {}
    out: list[dict] = []
    for row in rows:
        key = (str(row["role"]), (row["snippet"] or "").strip())
        if key in seen:
            continue
        count = per_session.get(row["session_id"], 0)
        if count >= 2:
            continue
        seen.add(key)
        per_session[row["session_id"]] = count + 1
        row.pop("message_uid", None)
        row.pop("rank", None)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="FTS5 query; quote tokens containing - or .")
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--since", help="ISO date, e.g. 2026-10-01")
    ap.add_argument("--session", help="restrict to one session id")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--recent", action="store_true", help="order by time instead of bm25 relevance")
    args = ap.parse_args()

    started = time.perf_counter()
    conn = connect(args.db)
    try:
        hits = search(conn, args.query, args.limit, args.since, args.session, args.recent)
    except sqlite3.OperationalError as exc:
        # The common failure is bare FTS5 syntax, and the message alone is cryptic.
        print(f"FTS5 error: {exc}", flush=True)
        print('Hint: quote tokens with hyphens or dots — "issue-guard", not issue-guard.', flush=True)
        return 2
    elapsed_ms = (time.perf_counter() - started) * 1000

    if args.json:
        print(json.dumps({"query": args.query, "hits": len(hits), "elapsed_ms": round(elapsed_ms, 1), "results": hits}, ensure_ascii=False, indent=1))
        return 0

    print(f"query={args.query!r} hits={len(hits)} elapsed={elapsed_ms:.0f}ms (index is live, no export step)")
    for hit in hits:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(hit["timestamp"])) if hit["timestamp"] else "?"
        tool = f" tool={hit['tool_name']}" if hit["tool_name"] else ""
        print(f"\n[{stamp}] session={hit['session_id']} role={hit['role']}{tool}")
        print("  " + (hit["snippet"] or "").replace("\n", " ").strip())
    if not hits:
        print("\nNo hits. That is a search miss, not proof the event never happened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
