#!/usr/bin/env python3
"""Deduplicate Honcho memory documents by text similarity.

Why this exists
---------------
Honcho 3.6.0 deduplicates *inside* an observation direction only: the candidate
query is filtered by ``observer``/``observed`` (and by ``session_name`` for
explicit documents) before any similarity comparison happens. Measured on a
live bank: 0 duplicate groups inside a direction against 226 groups and 452
excess rows across directions, with byte-identical content and a cosine
distance of 0.000000. The guard never sees the twin it should reject, so its
0.05 cutoff never gets a chance to matter (upstream: plastic-labs/honcho#729).

Until that scope is fixed, this job cleans what the writer leaves behind. It
contains no algorithm of its own:

* ``exact`` — one SQL ``GROUP BY`` over normalised content (lowercase,
  whitespace collapsed). Nothing to tune, nothing to get wrong.
* ``near``  — candidate pairs from ``pg_trgm`` (PostgreSQL contrib, already in
  the Honcho image), joined into connected components.

Similarity is lexical on purpose. Trigram distance keeps ``proc_1b92…`` and
``proc_4909…`` apart, while embeddings do not: on 211 embedding-near pairs the
mean trigram similarity was 0.971, and the three lowest (0.587) were process
notifications differing only by an id. Merging those would erase history.

Where it runs
-------------
In the compose network, as a scheduled container (see ``maintenance/``). That
is deliberate: a Linux container has no desktop windows, no ``pythonw``, no
Task Scheduler and none of the interpreter-subsystem traps that come with
scheduling Python on Windows. The job reaches ``database`` and ``api`` by
service name and prints to stdout, which the container runtime collects.

Safety
------
* dry-run is the default; ``--apply`` is required to change anything;
* ``--max-delete-pct`` aborts a run that would remove too much, which is what
  catches a mis-set threshold before it eats the bank;
* deletions go through the public API and are soft (``deleted_at``), so a
  mistake stays recoverable until the reconciler prunes;
* the JSON report records every removed id with its keeper, so any run can be
  audited afterwards;
* a second run right after the first must print ``nothing to do`` — that is the
  idempotency check.

Rollback
--------
Deletes are soft: the row keeps its content and only gets ``deleted_at`` set, so
a wrong run is undone without a backup file:

    update documents set deleted_at = null
    where deleted_at > now() - interval '1 day'
      and workspace_name = 'hermes' and level = 'explicit';

Run it inside ``psql`` against the ``database`` service. The reconciler only
prunes soft-deleted rows after ``older_than_minutes`` (5 by default), so the
window to undo is short — check the JSON report first, then decide.

Identifier validation
---------------------
``--workspace`` and ``--level`` are interpolated into SQL, so they are checked
against ``^[A-Za-z0-9_.-]{1,64}$`` before use. Examples, as required for any
parser in this repository:

    input                 result    why
    --------------------  --------  --------------------------------------------
    hermes                accepted  plain name
    local-test            accepted  hyphen
    s1                    accepted  short name
    a.b_c-1               accepted  dot, underscore, hyphen
    ""                    rejected  empty
    "a b"                 rejected  space can break the identifier position
    "x';drop table"       rejected  quote and statement separator
    "hermes\n"            rejected  trailing newline ($ does not match it)
    "a" * 65              rejected  longer than 64 characters

Exit codes: 0 = clean or applied, 1 = guard tripped or deletes rejected, 2 =
usage or DB error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import psycopg

# Identifiers come from deployment config, not from untrusted input, but they
# are still validated: an identifier cannot be parameterised by psycopg, and
# "it is only config" is exactly how an injection reaches production.
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DEFAULT_WORKSPACE = "hermes"
DEFAULT_LEVEL = "explicit"
DEFAULT_MAX_DELETE_PCT = 60.0
DEFAULT_MIN_LENGTH = 40


@dataclass(frozen=True)
class Doc:
    id: str
    observer: str
    observed: str
    content: str
    created_at: datetime


def connect() -> psycopg.Connection:
    """Connect using the standard PG* variables the compose service provides."""
    return psycopg.connect(
        host=os.environ.get("PGHOST", "database"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("PGDATABASE", "postgres"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        connect_timeout=10,
    )


def load_docs(conn: psycopg.Connection, workspace: str, level: str, min_length: int) -> list[Doc]:
    rows = conn.execute(
        "select id, observer, observed, content, created_at from documents "
        "where workspace_name = %s and level = %s and deleted_at is null "
        "and length(content) >= %s order by created_at asc, id asc",
        (workspace, level, min_length),
    ).fetchall()
    return [Doc(*row) for row in rows]


def normalise_key(content: str) -> str:
    return " ".join(content.lower().split())


def exact_groups(docs: Iterable[Doc]) -> list[list[Doc]]:
    """Byte-identical groups after case and whitespace normalisation."""
    buckets: dict[str, list[Doc]] = defaultdict(list)
    for doc in docs:
        buckets[normalise_key(doc.content)].append(doc)
    return [group for group in buckets.values() if len(group) > 1]


def near_pairs(
    conn: psycopg.Connection, workspace: str, level: str, min_length: int, threshold: float
) -> list[tuple[str, str]]:
    """Candidate pairs from pg_trgm.

    ``set_config(..., is_local=false)`` sets the value for the session, which is
    what the ``%`` operator reads. Without it the operator silently falls back
    to the server default of 0.3 — too permissive to mean anything, and easy to
    misread as "dedup found nothing".
    """
    conn.execute("select set_config('pg_trgm.similarity_threshold', %s, false)", (str(threshold),))
    rows = conn.execute(
        "select a.id, b.id from documents a join documents b on b.id > a.id "
        "and a.content %% b.content "
        "where a.workspace_name = %s and b.workspace_name = %s "
        "and a.level = %s and b.level = %s "
        "and a.deleted_at is null and b.deleted_at is null "
        "and length(a.content) >= %s and length(b.content) >= %s",
        (workspace, workspace, level, level, min_length, min_length),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def components(pairs: list[tuple[str, str]], docs: dict[str, Doc]) -> list[list[Doc]]:
    """Connected components over candidate pairs (union-find).

    Single linkage is used knowingly: at this scale clusters stay tight
    (measured minimum pairwise similarity inside every group was 0.802). If a
    run ever produces a chain, the answer is to turn ``--near`` off, not to
    raise the threshold.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)
    buckets: dict[str, list[Doc]] = defaultdict(list)
    for node in list(parent):
        if node in docs:
            buckets[find(node)].append(docs[node])
    return [group for group in buckets.values() if len(group) > 1]


def pick_keeper(group: list[Doc], keep: str, prefer_observer: str) -> Doc:
    """Choose the row that survives.

    ``longest`` is the default because these records are observations: the
    longer one usually carries the context that made the fact useful.
    ``earliest`` and ``latest`` follow ``created_at`` — not observer preference,
    which is only a tie-break — because the caller asked for a time-based choice.
    """
    def preferred(doc: Doc) -> int:
        return 0 if doc.observer == prefer_observer else 1

    if keep == "longest":
        return max(group, key=lambda d: (len(d.content), -preferred(d), d.created_at))
    if keep == "earliest":
        return min(group, key=lambda d: (d.created_at, preferred(d), d.id))
    return max(group, key=lambda d: (d.created_at, -preferred(d), d.id))


def build_plan(groups: list[list[Doc]], keep: str, prefer_observer: str) -> list[tuple[Doc, Doc]]:
    """Return (keeper, duplicate) pairs for every group."""
    plan = []
    for group in groups:
        keeper = pick_keeper(group, keep, prefer_observer)
        for doc in group:
            if doc.id != keeper.id:
                plan.append((keeper, doc))
    return plan


def delete_conclusion(api: str, workspace: str, doc_id: str, timeout: float) -> int:
    url = f"{api}/v3/workspaces/{workspace}/conclusions/{doc_id}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API unreachable at {api}: {exc.reason}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deduplicate Honcho documents by text similarity.")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--level", default=DEFAULT_LEVEL)
    parser.add_argument("--mode", choices=("exact", "near"), default="exact",
                        help="exact: identical after normalisation; near: add pg_trgm components")
    parser.add_argument("--near", action="store_true", dest="mode_near",
                        help="shorthand for --mode near")
    parser.add_argument("--threshold", type=float, default=0.8,
                        help="pg_trgm similarity threshold for --near (0..1)")
    parser.add_argument("--keep", choices=("longest", "earliest", "latest"), default="longest")
    parser.add_argument("--prefer-observer", default="semyon",
                        help="direction to prefer when content ties")
    parser.add_argument("--min-length", type=int, default=DEFAULT_MIN_LENGTH)
    parser.add_argument("--max-delete-pct", type=float, default=DEFAULT_MAX_DELETE_PCT,
                        help="abort when the plan would delete more than this share of live rows")
    parser.add_argument("--apply", action="store_true", help="perform soft deletes; default is a report")
    parser.add_argument("--json", metavar="PATH", help="write the report to this file")
    parser.add_argument("--api", default=os.environ.get("HONCHO_API", "http://api:8000"))
    parser.add_argument("--api-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.mode_near:
        args.mode = "near"
    for name in ("workspace", "level"):
        if not IDENTIFIER.match(getattr(args, name)):
            print(f"error: --{name} must match {IDENTIFIER.pattern}", file=sys.stderr)
            return 2
    if not 0.0 < args.threshold <= 1.0:
        print("error: --threshold must be in (0, 1]", file=sys.stderr)
        return 2

    try:
        conn = connect()
    except psycopg.Error as exc:
        print(f"error: database connection failed: {exc}", file=sys.stderr)
        return 2

    try:
        docs = load_docs(conn, args.workspace, args.level, args.min_length)
        groups = exact_groups(docs)
        exact_excess = sum(len(group) - 1 for group in groups)

        near_groups: list[list[Doc]] = []
        merged_groups: list[list[Doc]] = []
        if args.mode == "near":
            pairs = near_pairs(conn, args.workspace, args.level, args.min_length, args.threshold)
            # Exact duplicates must join the same graph as near ones. Otherwise a
            # row that is an exact twin of A and a near twin of C is dropped from
            # the component (both its exact partners are excluded), survives as a
            # duplicate, and the next run has to delete it again.
            for group in groups:
                for other in group[1:]:
                    pairs.append((group[0].id, other.id))
            merged_groups = components(pairs, {doc.id: doc for doc in docs})
            near_groups = merged_groups
    finally:
        conn.close()

    # In near mode the graph already contains exact twins, so every group is
    # planned once and the keeper is chosen across the whole component.
    plan = build_plan(merged_groups if args.mode == "near" else groups,
                      args.keep, args.prefer_observer)
    share = (100.0 * len(plan) / len(docs)) if docs else 0.0

    print(f"mode            {args.mode}")
    print(f"live documents  {len(docs)}")
    print(f"exact groups    {len(groups)}  (excess {exact_excess})")
    if args.mode == "near":
        print(f"near groups     {len(near_groups)}  (exact twins merged into the graph)")
    print(f"to delete       {len(plan)}  ({share:.1f}% of live)")

    if not plan:
        print("nothing to do")
        return 0

    if share > args.max_delete_pct:
        print(f"guard: plan removes {share:.1f}% which exceeds --max-delete-pct {args.max_delete_pct}",
              file=sys.stderr)
        return 1

    for keeper, dup in plan[:5]:
        print(f"  keep {keeper.id} ({keeper.observer}->{keeper.observed}, {len(keeper.content)} chars)")
        print(f"  drop {dup.id} ({dup.observer}->{dup.observed}, {len(dup.content)} chars)")

    report = {
        "workspace": args.workspace,
        "level": args.level,
        "mode": args.mode,
        "threshold": args.threshold if args.mode == "near" else None,
        "keep": args.keep,
        "live_documents": len(docs),
        "exact_groups": len(groups),
        "near_groups": len(near_groups),
        "planned_deletions": len(plan),
        "plan_share_pct": round(share, 2),
        "applied": False,
        "results": [{"keeper": k.id, "removed": d.id} for k, d in plan],
    }

    def write_report() -> None:
        if not args.json:
            return
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"report          {args.json}")

    failures: list[dict[str, object]] = []
    if args.apply:
        # Persist the plan before mutating anything. If the process dies mid-run,
        # the audit still names what was about to be removed and what was.
        write_report()
        codes: dict[int, int] = defaultdict(int)
        current_id = ""
        try:
            for _keeper, dup in plan:
                current_id = dup.id
                code = delete_conclusion(args.api, args.workspace, dup.id, args.api_timeout)
                codes[code] += 1
                if code != 204:
                    failures.append({"id": dup.id, "status": code})
                report["applied_count"] = sum(v for k, v in codes.items() if k == 204)
                report["response_codes"] = {str(k): v for k, v in sorted(codes.items())}
                report["failures"] = failures
        except Exception as exc:  # noqa: BLE001 - the report must survive any abort
            failures.append({"id": current_id, "status": "exception", "error": str(exc)})
            report["applied"] = True
            report["applied_count"] = sum(v for k, v in codes.items() if k == 204)
            report["response_codes"] = {str(k): v for k, v in sorted(codes.items())}
            report["failures"] = failures
            write_report()
            print(f"error: run aborted at {current_id}: {exc}", file=sys.stderr)
            return 2
        report["applied"] = True
        report["applied_count"] = sum(v for k, v in codes.items() if k == 204)
        report["response_codes"] = {str(k): v for k, v in sorted(codes.items())}
        report["failures"] = failures
        print(f"deleted         {report['applied_count']}")
        print(f"response codes  {dict(sorted(codes.items()))}")

    write_report()

    if failures:
        # An unattended job that exits 0 after a rejected delete reports success
        # while the bank is unchanged. Signal it instead.
        print(f"error: {len(failures)} deletions were not accepted; run is incomplete",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
