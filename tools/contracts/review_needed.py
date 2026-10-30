#!/usr/bin/env python3
"""Decide whether changed files require an outside review (RULE-011).

Reads a list of changed paths (stdin, one per line) and answers with an exit
code, so a workflow can branch on it without parsing prose:

    0  review required   (a P1 service or a P1 path is touched)
    1  review not required (P2/P3/T only)

Why not just match folders: `infra/compose/hindsight` and
`infra/compose/docker-exporter` sit side by side, but the first is P1 (memory)
and the second is P2 (metrics). The class lives in infra/registry.yaml, beside
the service, and this script asks the registry instead of guessing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "infra" / "registry.yaml"

# Paths that are P1 by nature, not by registry entry.
ALWAYS_REVIEW = (
    ".github/workflows/",
    "tools/contracts/",
    "AGENTS.md",
)


def service_paths(service: dict) -> list[str]:
    """Filesystem paths a service owns, from its location block."""
    loc = service.get("location") or {}
    paths: list[str] = []
    for value in loc.values():
        if isinstance(value, str) and ("/" in value or "\\" in value):
            paths.append(value.replace("\\", "/"))
        elif isinstance(value, list):
            paths.extend(str(v).replace("\\", "/") for v in value if isinstance(v, str))
    return paths


def main() -> int:
    changed = [line.strip().replace("\\", "/") for line in sys.stdin if line.strip()]
    if not changed:
        print("no changed files; review not required")
        return 1

    for path in changed:
        for prefix in ALWAYS_REVIEW:
            if path == prefix or path.startswith(prefix):
                print(f"review required: {path} is a P1 path ({prefix})")
                return 0

    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {}
    services = data.get("services") or []

    for path in changed:
        for svc in services:
            if svc.get("criticality") != "P1":
                continue
            for owned in service_paths(svc):
                # The registry stores absolute paths; compare on the tail so a
                # repo-relative diff still matches.
                if path in owned or owned.endswith(path) or path.endswith(owned):
                    print(f"review required: {path} belongs to P1 service {svc.get('id')} ({svc.get('name')})")
                    return 0

    print("review not required: no P1 path or P1 service touched")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
