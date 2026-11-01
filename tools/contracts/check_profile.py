#!/usr/bin/env python3
"""Check that the review mirrors of MEMORY.md and USER.md match the live files.

The live files are the source. A mirror that silently drifts is worse than no
mirror: it looks like a record of what the agent believes while the agent
believes something else. This check compares them after normalisation and exits
non-zero on drift, so the drift is caught by a command instead of by memory.

The live files are only ever read. Nothing here can write them — the whole point
is that memory changes in one place, and the copy follows.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
HOME = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"

# (mirror in repo, live file, label for messages)
PAIRS = [
    (REPO / "docs/agents/memory.md", HOME / "memories/MEMORY.md", "MEMORY.md"),
    (REPO / "docs/agents/user.md", HOME / "memories/USER.md", "USER.md"),
]


def strip_header(text: str) -> str:
    """Drop the draft header: everything up to and including the first ``---``."""
    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if re.fullmatch(r"-{3,}", line.strip()):
            return "\n".join(lines[i + 1:])
    return text


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main() -> int:
    failures = 0
    for mirror, live, label in PAIRS:
        if not mirror.exists():
            print(f"FAIL {label}: mirror missing at {mirror.relative_to(REPO)}")
            failures += 1
            continue
        if not live.exists():
            print(f"FAIL {label}: live file missing at {live}")
            failures += 1
            continue

        want = normalise(strip_header(mirror.read_text(encoding="utf-8")))
        have = normalise(live.read_text(encoding="utf-8"))

        if want == have:
            print(f"OK   {label}: mirror matches live ({len(have)} chars)")
            continue

        failures += 1
        print(f"FAIL {label}: mirror drifted from live "
              f"({len(want)} vs {len(have)} chars)")
        wl, hl = want.split("\n"), have.split("\n")
        shown = 0
        for i in range(max(len(wl), len(hl))):
            a = wl[i] if i < len(wl) else "<missing in mirror>"
            b = hl[i] if i < len(hl) else "<missing in live>"
            if a != b:
                print(f"  line {i + 1}")
                print(f"    mirror: {a[:100]}")
                print(f"    live:   {b[:100]}")
                shown += 1
                if shown >= 5:
                    print("  ... (first 5 differing lines only)")
                    break

    if failures:
        print(f"\n{failures} mirror(s) out of sync. The live file is the source: "
              f"copy it into the mirror, do not edit the mirror.")
        return 1
    print("\nall profile mirrors match their live files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
