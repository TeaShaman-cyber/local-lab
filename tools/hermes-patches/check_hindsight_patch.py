#!/usr/bin/env python3
"""Check whether the local Hindsight recall-date patch is still applied.

Why this exists: the patch lives in an installed Hermes plugin, not in a package we
control. A Hermes or plugin update silently overwrites `__init__.py`, and the dates
disappear from the memory block without any error. This script turns that silent
regression into a loud one.

Exit codes: 0 = patch present, 1 = patch missing (plugin was replaced), 2 = plugin dir absent.

Usage:
    python deploy/hermes-patches/check_hindsight_patch.py
    python deploy/hermes-patches/check_hindsight_patch.py --path <plugin_dir>
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys

DEFAULT_PLUGIN_DIR = pathlib.Path.home() / "AppData/Local/hermes/plugins/hindsight"

# Each patch names the markers that prove it is present. A patch is reported
# separately, so one missing patch does not hide another.
PATCHES = {
    "recall-dates": (
        "def _fmt_recall_line(",
        "def _dedupe_recall(",
        "self._dedupe_recall(",
        'self._fmt_recall_line(r, f"{i}. ")',
    ),
    "anchor-normalise": (
        "_GITHUB_REF = re.compile",
        "def _current_repo_slug(",
        "def _normalize_github_refs(",
        "_normalize_github_refs(content, self._cwd)",
    ),
    # Desktop and gateway sessions hand providers no cwd (upstream #144869), so
    # normalisation is dead without this fallback: the markers above can all be
    # present while the feature does nothing.
    "anchor-fallback": (
        "_NAMED_REPO = re.compile",
        "def _repo_named_in_text(",
        "_current_repo_slug(cwd) or _repo_named_in_text(text)",
    ),
    # Reflect must not inherit the recall budget. On a large bank the synthesis input
    # grows with the graph, and a recall-grade budget pushes the call past the server's
    # wall: measured 56 s at `low` against 228 s at `mid`. Ported from the sibling
    # integration `coding-agents`, which already has `reflectBudget`.
    "reflect-budget": (
        'self._reflect_budget = "low"',
        '"key": "reflect_budget"',
        'reflect_budget = cfg.get("reflect_budget")',
        '"budget": self._reflect_budget',
        # The startup line must name it too: a setting that works but is invisible turns
        # "did it take effect?" into archaeology — which is exactly what happened here,
        # and the answer had to come from reading the code instead of the log.
        "reflect_budget=%s",
    ),
}


def check(plugin_dir: pathlib.Path) -> int:
    target = plugin_dir / "__init__.py"
    if not target.is_file():
        print(f"FAIL: {target} not found")
        return 2

    text = target.read_text(encoding="utf-8", errors="replace")
    missing_total = 0
    for name, markers in PATCHES.items():
        missing = [m for m in markers if m not in text]
        if missing:
            missing_total += 1
            print(f"FAIL: patch '{name}' is NOT applied in {target}")
            for m in missing:
                print(f"  missing marker: {m}")
        else:
            print(f"OK:   patch '{name}' present")

    if missing_total:
        print("The plugin was probably updated. Re-apply from deploy/hermes-patches/.")
        return 1

    version = "?"
    manifest = plugin_dir / "plugin.yaml"
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("version:"):
                version = line.split(":", 1)[1].strip()
                break

    print(f"plugin version {version}; file written "
          f"{dt.datetime.fromtimestamp(target.stat().st_mtime):%Y-%m-%d %H:%M:%S}")
    return 0


def hermes_started_at() -> dt.datetime | None:
    """Start time of a running Hermes process, or None when it cannot be read.

    A patch written into a running process changes nothing until restart: Python
    loads the plugin module once at start. This is how a patched file looked
    "applied" while the process still ran the old code.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name Hermes,HermesAgent -ErrorAction SilentlyContinue | "
             "Sort-Object StartTime | Select-Object -First 1).StartTime.ToString('o')"],
            capture_output=True, text=True, timeout=20,
        )
        raw = (out.stdout or "").strip()
        return dt.datetime.fromisoformat(raw) if raw else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=pathlib.Path, default=DEFAULT_PLUGIN_DIR,
                        help="plugin directory (default: the Hermes install)")
    parser.add_argument("--runtime", action="store_true",
                        help="also verify the patch is live in the running process")
    args = parser.parse_args()

    rc = check(args.path)
    if rc != 0 or not args.runtime:
        return rc

    target = args.path / "__init__.py"
    written = dt.datetime.fromtimestamp(target.stat().st_mtime)
    started = hermes_started_at()
    if started is not None and started.tzinfo is not None:
        started = started.astimezone().replace(tzinfo=None)

    if started is None:
        print("WARN: Hermes start time unknown; runtime state UNKNOWN")
        return 0
    if started < written:
        print(f"STALE: Hermes started {started:%Y-%m-%d %H:%M:%S}, "
              f"file written {written:%Y-%m-%d %H:%M:%S}")
        print("The running process still executes the OLD code. Restart Hermes.")
        return 3

    print(f"LIVE: Hermes started {started:%Y-%m-%d %H:%M:%S}, "
          f"after the file write — patches are active")
    return 0


if __name__ == "__main__":
    sys.exit(main())
