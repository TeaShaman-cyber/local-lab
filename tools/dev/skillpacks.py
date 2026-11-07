#!/usr/bin/env python3
"""Are our copies of external skill packs still current?

`tools/dev/skillpack` tracked one source (matt-pocock) because that was the only
one we knew about. Four more turned out to exist upstream:

    grafana/skills        -> our docs/reference/observability/grafana-*.md
    langfuse/skills       -> our docs/reference/observability/langfuse/
    timeweb-cloud/skills  -> our docs/reference/timeweb/
    plastic-labs/honcho   -> our .agents/skills/honcho-{memory,cli} (verbatim)

Every one of them can drift silently. This checks all five the same way: compare
the upstream commit date with the date our copy was last written.

Why date and not hash for the extracted packs: once a skill is rewritten into a doc,
the bytes differ by construction, so a hash comparison would report "diverged" for
every file forever. The honest signal for those is "upstream moved after we copied".

Exit codes: 0 = all current, 1 = at least one stale, 2 = cannot compare.

Usage:
    python tools/dev/skillpacks.py
    python tools/dev/skillpacks.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time

# The repository root. Overridable because the scheduled container mounts the
# clone read-only at /repo instead of baking docs/ and .agents/ into the image
# (they change constantly, and an image would go stale between builds).
REPO = pathlib.Path(os.environ.get("SKILLPACKS_REPO") or pathlib.Path(__file__).resolve().parents[2])
# Sync record: when we last read upstream. mtime cannot serve this — after a fresh
# `git checkout` it is the checkout time, not the read time (Codex P2 on #74).
#
# Both roots are overridable because the same file runs on the host (E:/...) and
# inside the scheduled container (/clones, /state). Hardcoding the Windows path
# would make the container check every pack "clone missing" and still exit 0.
CLONE_ROOT = pathlib.Path(os.environ.get("SKILLPACKS_CLONE_ROOT", "E:/"))
STATE = pathlib.Path(
    os.environ.get("SKILLPACKS_STATE", "E:/skillpacks-state/state.json")
)

# clone dir, upstream repo, where our copy lives, how to compare
SOURCES = [
    {
        "name": "matt-pocock",
        "clone": CLONE_ROOT / "matt-upstream",
        "repo": "mattpocock/skills",
        "copy": None,  # compared by hash in tools/dev/skillpack, not here
        "note": "hash-checked separately by tools/dev/skillpack",
    },
    {
        "name": "grafana",
        "clone": CLONE_ROOT / "grafana-skills",
        "repo": "grafana/skills",
        "copy": REPO / "docs" / "reference" / "observability",
        "note": "extracted into observability docs",
    },
    {
        "name": "langfuse",
        "clone": CLONE_ROOT / "langfuse-skills",
        "repo": "langfuse/skills",
        "copy": REPO / "docs" / "reference" / "observability" / "langfuse",
        "note": "extracted into observability docs",
    },
    {
        "name": "timeweb",
        "clone": CLONE_ROOT / "timeweb-skills",
        "repo": "timeweb-cloud/skills",
        "copy": REPO / "docs" / "reference" / "timeweb",
        "note": "extracted into timeweb docs",
    },
    {
        # Vendor skills kept VERBATIM, not rewritten: they are the author's own
        # description of how Honcho works, and a paraphrase would age worse than a
        # stale copy we can detect. Installed through scripts/sync-skills.sh like
        # any project skill.
        "name": "honcho",
        "clone": CLONE_ROOT / "honcho-upstream",
        "repo": "plastic-labs/honcho",
        "copy": REPO / ".agents" / "skills",
        "note": "vendor skills honcho-memory, honcho-cli installed verbatim",
    },
]


def git(clone: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=clone, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def upstream_date(clone: pathlib.Path) -> float | None:
    """Commit date of the upstream branch, as a unix timestamp."""
    for ref in ("origin/main", "origin/master", "HEAD"):
        out = git(clone, "log", "-1", "--format=%ct", ref)
        if out.isdigit():
            return float(out)
    return None


def fetch(clone: pathlib.Path) -> bool:
    """True only if the fetch actually succeeded.

    The previous version ignored the return code and compared against a stale
    origin/main, printing `current` on a network failure — a silent pass on the one
    question this tool exists to answer (Codex P2 on #74).
    """
    res = subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, capture_output=True)
    return res.returncode == 0


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state: dict) -> None:
    """Write the sync record atomically.

    A plain write_text truncates first, so a crash mid-write leaves a half file
    that load_state() reads as {} — every pack then reports "no sync record" and
    the alert fires on a healthy setup. tmp + os.replace is atomic on the same
    filesystem, which is the case here.
    """
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    tmp.replace(STATE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--emit-event",
        action="store_true",
        help="also print one logfmt line per run (event=skillpacks_check …) for metrics",
    )
    ap.add_argument(
        "--record",
        nargs="*",
        metavar="PACK",
        help="mark packs as synced now (all when no name is given)",
    )
    args = ap.parse_args()

    state = load_state()

    if args.record is not None:
        wanted = args.record or [s["name"] for s in SOURCES]
        now = time.time()
        for src in SOURCES:
            if src["name"] not in wanted:
                continue
            clone: pathlib.Path = src["clone"]
            up = upstream_date(clone) if (clone / ".git").is_dir() else None
            state[src["name"]] = {
                "synced_at": now,
                "synced_at_human": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                "upstream_commit_date": up,
            }
            print(f"recorded: {src['name']}")
        save_state(state)
        print(f"state: {STATE}")
        return 0

    rows = []
    stale_count = 0
    unknown_count = 0

    for src in SOURCES:
        clone: pathlib.Path = src["clone"]
        row = {"name": src["name"], "repo": src["repo"], "status": "unknown", "detail": ""}

        if not (clone / ".git").is_dir():
            row["detail"] = f"clone missing: {clone}"
            unknown_count += 1
            rows.append(row)
            continue

        # A failed fetch leaves origin/main stale; continuing anyway would report
        # `current` on a network error (Codex P2 on #74).
        if not fetch(clone):
            row["detail"] = "git fetch failed — upstream state unknown, not current"
            unknown_count += 1
            rows.append(row)
            continue

        up = upstream_date(clone)
        if up is None:
            row["detail"] = "cannot read upstream date"
            unknown_count += 1
            rows.append(row)
            continue

        row["upstream_commit_date"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(up))

        if src["copy"] is None:
            row["status"] = "hash-checked"
            row["detail"] = src["note"]
            rows.append(row)
            continue

        if not src["copy"].exists():
            row["detail"] = f"our copy missing: {src['copy'].relative_to(REPO)}"
            unknown_count += 1
            rows.append(row)
            continue

        record = state.get(src["name"])
        if not record:
            row["detail"] = "no sync record — run `--record` once the copy is confirmed"
            unknown_count += 1
            rows.append(row)
            continue

        synced = float(record.get("synced_at", 0))
        row["our_sync_date"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(synced))

        # No slack. Two fixed dates are compared, so a 12-hour gap never closes on its
        # own — "current within a day" was a permanent blind spot, not rounding
        # (Codex P2 on #74).
        if up > synced:
            row["status"] = "stale"
            row["detail"] = "upstream moved after our sync — re-read"
            stale_count += 1
        else:
            row["status"] = "current"
            row["detail"] = src["note"]
        rows.append(row)

    # Machine-readable line for the scheduler. Alloy turns it into metrics, so the
    # field names are a contract: renaming one empties the panel and the alert
    # without any error anywhere. One line per run, always, including a run that
    # found nothing and a run that could not compare anything.
    if args.emit_event:
        packs = ",".join(f"{r['name']}:{r['status']}" for r in rows) or "none"
        print(
            f"event=skillpacks_check stale={stale_count} unknown={unknown_count} "
            f'packs="{packs}" ts={int(time.time())}'
        )

    if args.json:
        print(json.dumps({"rows": rows, "stale": stale_count, "unknown": unknown_count}, indent=2, ensure_ascii=False))
    else:
        print(f"{'source':<14} {'upstream':<18} {'synced':<18} status")
        print("-" * 70)
        for r in rows:
            print(
                f"{r['name']:<14} {r.get('upstream_commit_date', '?'):<18} "
                f"{r.get('our_sync_date', '—'):<18} {r['status']}"
            )
            if r["status"] in ("stale", "unknown"):
                print(f"{'':<14} {r['detail']}")
        print()
        if stale_count:
            print(f"STALE: {stale_count} pack(s) moved upstream — re-read and update")
        if unknown_count:
            print(f"UNKNOWN: {unknown_count} pack(s) could not be compared (not 'current')")
        if not stale_count and not unknown_count:
            print("all extracted packs match their upstream date")

    # With --emit-event the caller is a scheduler, and the counters already carry
    # the verdict. A non-zero exit means "the job broke" in supervisor terms, and
    # mixing "found a difference" with "failed" is the same class of error as
    # treating no-data as an incident. So machine-readable mode always exits 0:
    # the alert reads stale/unknown, the human reads the table.
    if args.emit_event:
        return 0
    return 1 if (stale_count or unknown_count) else 0


if __name__ == "__main__":
    raise SystemExit(main())
