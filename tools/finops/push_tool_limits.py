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


def _iter_calls(raw: str):
    """Yield (tool_name, args) for direct calls and for MCP calls wrapped in tool_call.

    MCP tools never appear as a top-level tool name: they arrive inside a `tool_call`
    with `{"calls": [{"name": ..., "arguments": {...}}]}`. The first version of this
    script counted only direct names, so every Exa and Tavily extraction was invisible
    — the exact blindness the script exists to measure.
    """
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return
    for call in data or []:
        fn = call.get("function") or {}
        name = fn.get("name")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        args = args or {}
        if name == "tool_call":
            for item in args.get("calls") or []:
                yield (item.get("name") or "", item.get("arguments") or {})
        elif name:
            yield (name, args)


# Tool -> (limit parameter, that tool's own default). Reported separately, because
# "15000" means nothing for Exa, whose default is 3000.
GUARDED = {
    "web_extract": ("char_limit", 15000),
    "mcp__exa__web_fetch_exa": ("maxCharacters", 3000),
}


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

    per_tool: dict[str, dict] = {t: {"calls": 0, "limits": []} for t in GUARDED}
    for (raw,) in rows:
        for name, args in _iter_calls(raw):
            if name not in GUARDED:
                continue
            param, _default = GUARDED[name]
            per_tool[name]["calls"] += 1
            value = args.get(param)
            if isinstance(value, (int, float)):
                per_tool[name]["limits"].append(int(value))

    stats: dict = {"window_hours": window_hours, "tools": {}}
    total_calls = total_below = 0
    for name, info in per_tool.items():
        _param, default = GUARDED[name]
        limits = info["limits"]
        below = [v for v in limits if v < default]
        total_calls += info["calls"]
        total_below += len(below)
        stats["tools"][name] = {
            "calls": info["calls"],
            "with_limit": len(limits),
            "below_default": len(below),
            "default": default,
            "min_limit": min(limits) if limits else None,
            "median_limit": int(statistics.median(limits)) if limits else None,
        }
    stats["calls"] = total_calls
    stats["below_default"] = total_below
    stats["share_below_pct"] = round(100 * total_below / total_calls, 1) if total_calls else 0.0
    return stats


def publish(stats: dict) -> int | None:
    lines = [
        f"metric=tool_limit_calls_total value={stats['calls']}",
        f"metric=tool_limit_below_default_total value={stats['below_default']}",
        f"metric=tool_limit_share_below_pct value={stats['share_below_pct']}",
        f"metric=char_limit_below_default value={1 if stats['below_default'] else 0}",
    ]
    for name, info in stats["tools"].items():
        short = name.replace("mcp__exa__", "exa_").replace("__", "_")
        lines.append(f"metric={short}_calls value={info['calls']}")
        lines.append(f"metric={short}_below_default value={info['below_default']}")
        if info["min_limit"] is not None:
            lines.append(f"metric={short}_min_limit value={info['min_limit']}")

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
        print(f"окно {stats['window_hours']:.0f} ч: вызовов {stats['calls']}, "
              f"ниже дефолта {stats['below_default']} ({stats['share_below_pct']}%)")
        for name, info in stats["tools"].items():
            if not info["calls"]:
                continue
            line = (f"  {name}: {info['calls']} вызовов, ниже дефолта "
                    f"{info['below_default']}/{info['calls']} (дефолт {info['default']})")
            if info["median_limit"] is not None:
                line += f", медиана {info['median_limit']}, минимум {info['min_limit']}"
            print(line)

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
