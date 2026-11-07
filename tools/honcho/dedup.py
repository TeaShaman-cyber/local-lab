#!/usr/bin/env python3
"""Deduplicate Honcho memory documents by text similarity.

Why this exists
---------------
Honcho 3.6.0 deduplicates *inside* an observation direction only: the candidate
query is filtered by ``observer``/``observed`` (and by ``session_name`` for
explicit documents) before any similarity comparison happens. Measured on a
live bank: 0 duplicate groups inside a direction, 141 groups and 282 excess
rows across directions, with byte-identical content and a cosine distance of
0.000000. The guard therefore never sees the twin it should reject, and its
0.05 cutoff never gets a chance to matter.

Until upstream fixes the scope (plastic-labs/honcho#729), this wrapper cleans
what the writer leaves behind. It deliberately contains no new algorithm:

* ``exact`` — one SQL ``GROUP BY`` over normalised content (lowercase,
  whitespace collapsed). No similarity maths, nothing to tune.
* ``near``  — candidate pairs from ``pg_trgm`` (PostgreSQL contrib, already
  installed in the Honcho image), joined into connected components.

Similarity is lexical on purpose. Trigram distance keeps ``proc_1b92…`` and
``proc_4909…`` apart, while embeddings do not: measured on 211 embedding-near
pairs, mean trigram similarity was 0.971 and the three lowest (0.587) were
process notifications differing only by an ID. Merging those would destroy
history.

Safety
------
* dry-run is the default; ``--apply`` is required to change anything;
* ``--max-delete-pct`` aborts when a run would remove too much, which is what
  catches a mis-set threshold before it eats the bank;
* deletions go through the public API and are soft (``deleted_at``), so a
  mistake is recoverable until the reconciler prunes;
* the JSON report records every removed id with its group, so a change can be
  audited after the fact.

Usage
-----
    python tools/honcho/dedup.py                      # report only
    python tools/honcho/dedup.py --near               # include paraphrases
    python tools/honcho/dedup.py --apply --json out.json

Exit codes: 0 = clean or applied, 1 = guard tripped, 2 = usage/DB error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

DEFAULT_PSQL = "docker exec -i local-test-database-1 psql -U postgres -d postgres"
DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_WORKSPACE = "hermes"
DEFAULT_LEVEL = "explicit"

# A shared pool for the whole run. Guards against one bad run emptying the bank.
DEFAULT_MAX_DELETE_PCT = 60.0

# Rows shorter than this are too generic to compare: "ok", "yes", "готово".
DEFAULT_MIN_LENGTH = 40


@dataclass(frozen=True)
class Doc:
    id: str
    observer: str
    observed: str
    content: str


SEP = "\x1f"  # unit separator: never occurs in observations, unlike '|'


def psql(sql: str, psql_cmd: str, preamble: tuple[str, ...] = ()) -> list[list[str]]:
    """Run SQL through the deployment's psql and return separated rows.

    ``preamble`` statements are passed as separate ``-c`` arguments on purpose:
    psql runs them in the same connection, so ``SET`` survives into the query.
    The ``SET`` command tag itself is still printed, so rows without a separator
    are dropped rather than parsed — that is also why the separator is a unit
    separator instead of ``|``, which observations may legitimately contain.
    """
    cmd = psql_cmd.split()
    for statement in preamble:
        cmd += ["-c", statement]
    cmd += [f"-tAF{SEP}", "-c", sql]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:400]}")
    rows = []
    for line in proc.stdout.splitlines():
        if SEP in line:
            rows.append(line.split(SEP))
    return rows


def load_docs(args: argparse.Namespace) -> list[Doc]:
    sql = (
        "select id, observer, observed, content from documents "
        f"where workspace_name = '{args.workspace}' "
        f"and level = '{args.level}' "
        "and deleted_at is null "
        f"and length(content) >= {args.min_length} "
        "order by created_at asc, id asc;"
    )
    # content is last and may contain the separator, so rejoin the tail.
    return [Doc(row[0], row[1], row[2], SEP.join(row[3:])) for row in psql(sql, args.psql_cmd)]


def normalise_key(content: str) -> str:
    return " ".join(content.lower().split())


def exact_groups(docs: Iterable[Doc]) -> list[list[Doc]]:
    """Byte-identical groups after case and whitespace normalisation."""
    buckets: dict[str, list[Doc]] = defaultdict(list)
    for doc in docs:
        buckets[normalise_key(doc.content)].append(doc)
    return [group for group in buckets.values() if len(group) > 1]


def near_pairs(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Candidate pairs from pg_trgm.

    The threshold is set in the same session as the query on purpose: the ``%``
    operator reads ``pg_trgm.similarity_threshold`` and silently falls back to
    the server default of 0.3 when it is not set, which is far below what this
    bank needs.
    """
    sql = (
        "select a.id, b.id from documents a join documents b on b.id > a.id "
        "and a.content % b.content "
        f"where a.workspace_name = '{args.workspace}' and b.workspace_name = '{args.workspace}' "
        f"and a.level = '{args.level}' and b.level = '{args.level}' "
        "and a.deleted_at is null and b.deleted_at is null "
        f"and length(a.content) >= {args.min_length} and length(b.content) >= {args.min_length};"
    )
    preamble = (f"set pg_trgm.similarity_threshold = {args.threshold}",)
    return [(row[0], row[1]) for row in psql(sql, args.psql_cmd, preamble)]


def components(pairs: list[tuple[str, str]], docs: dict[str, Doc]) -> list[list[Doc]]:
    """Connected components over candidate pairs (union-find).

    Single linkage is knowingly used here: at this scale the clusters stay
    tight (measured minimum pairwise similarity inside every group was 0.802).
    If a run ever produces a chain, ``--near`` is the flag to turn off, not the
    threshold to raise.
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
    longer one usually carries the context that made the fact useful. Ties, and
    the ``earliest``/``latest`` strategies aside, fall back to the direction
    holding the real peer name rather than a system SID — the SID rows are
    migration leftovers.
    """
    def preferred(doc: Doc) -> int:
        return 0 if doc.observer == prefer_observer else 1

    if keep == "longest":
        return max(group, key=lambda d: (len(d.content), -preferred(d)))
    ordered = sorted(group, key=lambda d: (preferred(d), d.id))
    return ordered[0] if keep == "earliest" else ordered[-1]


def delete_conclusion(api: str, workspace: str, doc_id: str, timeout: float) -> int:
    url = f"{api}/v3/workspaces/{workspace}/conclusions/{doc_id}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API unreachable: {exc.reason}") from exc


def build_plan(groups: list[list[Doc]], args: argparse.Namespace) -> list[tuple[Doc, Doc]]:
    """Return (keeper, duplicate) pairs for every group."""
    plan = []
    for group in groups:
        keeper = pick_keeper(group, args.keep, args.prefer_observer)
        for doc in group:
            if doc.id != keeper.id:
                plan.append((keeper, doc))
    return plan


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
    parser.add_argument("--apply", action="store_true", help="perform soft deletes; default is report only")
    parser.add_argument("--json", metavar="PATH", help="write the report to this file")
    parser.add_argument("--psql-cmd", default=DEFAULT_PSQL)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--api-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.mode_near:
        args.mode = "near"
    if not 0.0 < args.threshold <= 1.0:
        print("error: --threshold must be in (0, 1]", file=sys.stderr)
        return 2

    try:
        docs = load_docs(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    by_id = {doc.id: doc for doc in docs}
    groups = exact_groups(docs)
    exact_count = sum(len(group) - 1 for group in groups)

    near_groups: list[list[Doc]] = []
    if args.mode == "near":
        try:
            pairs = near_pairs(args)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        near_groups = components(pairs, by_id)

    # A doc already grouped by exact text must not be planned twice.
    exact_ids = {doc.id for group in groups for doc in group}
    extra_groups = [
        [doc for doc in group if doc.id not in exact_ids]
        for group in near_groups
    ]
    extra_groups = [group for group in extra_groups if len(group) > 1]

    plan = build_plan(groups, args) + build_plan(extra_groups, args)
    share = (100.0 * len(plan) / len(docs)) if docs else 0.0

    print(f"mode            {args.mode}")
    print(f"live documents  {len(docs)}")
    print(f"exact groups    {len(groups)}  (excess {exact_count})")
    if args.mode == "near":
        print(f"near groups     {len(near_groups)}  (extra beyond exact {len(extra_groups)})")
    print(f"to delete       {len(plan)}  ({share:.1f}% of live)")

    if not plan:
        print("nothing to do")
        return 0

    if share > args.max_delete_pct:
        print(
            f"guard: plan removes {share:.1f}% which exceeds --max-delete-pct {args.max_delete_pct}",
            file=sys.stderr,
        )
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

    if args.apply:
        codes: dict[int, int] = defaultdict(int)
        for keeper, dup in plan:
            code = delete_conclusion(args.api, args.workspace, dup.id, args.api_timeout)
            codes[code] += 1
        report["applied"] = True
        report["response_codes"] = {str(k): v for k, v in sorted(codes.items())}
        print(f"deleted         {sum(v for k, v in codes.items() if k == 204)}")
        print(f"response codes  {dict(sorted(codes.items()))}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"report          {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
