#!/usr/bin/env python3
"""Measure how often the agent truncates its own input (#89).

Why this exists before any hook: a hook that rewrites arguments can break work in
ways nobody notices until something looks wrong. `issue-guard` already cost us that
lesson. Measure first, act on numbers second.

What is measured: every `web_extract` call in the last N hours, and whether it carried
a `char_limit` below the tool's default (15 000). A call below the default is not
tuning — it is reading a fraction of the document and deciding as if it were whole.

Published to Loki as metrics, so Grafana can show a trend instead of a one-off audit:

  metric=web_extract_calls          value=N
  metric=web_extract_with_limit     value=N
  metric=web_extract_below_default  value=N
  metric=web_extract_min_limit      value=N   (only when at least one limit was set)
  metric=char_limit_below_default   value=0|1 (0 when the window has no violations)

Exit codes: 0 = measured and published, 1 = measured, publish failed (numbers printed),
2 = cannot read the database.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import time

DB = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "state.db"
# Hermes' own default for web_extract. Anything lower is self-imposed.
DEFAULT_MIN = 15000


def collect(window_hours: float) -> dict:
    since = time.time() - window_hours * 3600
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL "
            "AND tool_calls != '' AND tool_calls != 'null' AND timestamp >= ?",
            (since,),
        ).fetchall()
    finally:
        con.close()

    calls = 0
    limits: list[int] = []
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        for call in data or []:
            fn = (call.get("function") or {})
            if fn.get("name") != "web_extract":
                continue
            calls += 1
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            if not isinstance(args, dict):
                continue
            value = args.get("char_limit")
            if isinstance(value, (int, float)):
                limits.append(int(value))

    below = [v for v in limits if v < DEFAULT_MIN]
    return {
        "window_hours": window_hours,
        "calls": calls,
        "with_limit": len(limits),
        "below_default": len(below),
        "min_limit": min(limits) if limits else None,
        "median_limit": int(statistics.median(limits)) if limits else None,
        "share_below_pct": round(100 * len(below) / calls, 1) if calls else 0.0,
    }


def publish(stats: dict) -> int | None:
    lines = [
        f"metric=web_extract_calls value={stats['calls']}",
        f"metric=web_extract_with_limit value={stats['with_limit']}",
        f"metric=web_extract_below_default value={stats['below_default']}",
        f"metric=char_limit_below_default value={1 if stats['below_default'] else 0}",
        f"metric=web_extract_share_below_pct value={stats['share_below_pct']}",
    ]
    if stats["min_limit"] is not None:
        lines.append(f"metric=web_extract_min_limit value={stats['min_limit']}")

    try:
        import base64
        import urllib.request

        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "deepinfra_usage"))
        from push_deepinfra_usage import loki_credentials  # noqa: PLC0415

        url, user, password = loki_credentials()
        payload = {"streams": [{
            "stream": {"service_name": "finops", "log_type": "tool_limits",
                       "job": "hermes-tool-limits"},
            "values": [[str(time.time_ns()), line] for line in lines],
        }]}
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except Exception as exc:  # noqa: BLE001 - a metrics outage is not a measurement failure
        print(f"publish failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--window-hours", type=float, default=24)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="measure, do not publish")
    args = ap.parse_args()

    if not DB.is_file():
        print(f"database not found: {DB}", file=sys.stderr)
        return 2

    stats = collect(args.window_hours)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        print(
            f"web_extract за {stats['window_hours']:.0f} ч: вызовов {stats['calls']}, "
            f"с char_limit {stats['with_limit']}, ниже дефолта {stats['below_default']} "
            f"({stats['share_below_pct']}%)"
        )
        if stats["median_limit"] is not None:
            print(f"  медиана лимита: {stats['median_limit']}, минимум: {stats['min_limit']}")

    if args.dry_run:
        return 0

    status = publish(stats)
    if status is None:
        return 1
    if not args.json:
        print(f"  опубликовано: status={status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
