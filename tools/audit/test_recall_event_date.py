"""RED test: recall must read event_date, which is populated on every row.

Measured on the live bank (2026-11-01):

    fact_type     total   no occurred_start   no event_date
    world          5507        3127 (57%)            0
    experience     4215        2066 (49%)            0
    observation    2891        1266 (44%)            0

So `occurred_start` is legitimately absent for half the facts — it means "when the
claim became true", which is often unknown. But `event_date` is present for all
12 613 rows, and the plugin never reads it.

Instead it falls back to `mentioned_at` — the date the memory was *written*, not the
date the fact was *true*. That is the distinction the whole age feature depends on,
and the current code erases it.

Run: python tools/audit/test_recall_event_date.py
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

# The plugin lives inside the Hermes profile, not in this repo, so import the
# renderer by path rather than by package name.
PLUGIN = (
    r"C:\Users\s.poklad\AppData\Local\hermes\plugins\hindsight\__init__.py"
)


class FakeRecord:
    """Minimal stand-in for a Hindsight memory record."""

    def __init__(self, text: str, **fields):
        self.text = text
        for k, v in fields.items():
            setattr(self, k, v)


def load_renderer():
    src = open(PLUGIN, encoding="utf-8").read()
    # Pull just the method body out of the class so we do not import the whole plugin.
    start = src.index("    def _fmt_recall_line(")
    end = src.index("    @staticmethod\n    def _recall_key(", start)
    body = src[start:end]
    # Dedent one level and make it a plain function.
    body = "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines())
    ns: dict = {"re": __import__("re")}
    exec(compile(body, "<plugin>", "exec"), ns)
    return ns["_fmt_recall_line"]


def render(rec) -> str:
    fn = load_renderer()
    return fn(types.SimpleNamespace(), rec)


def main() -> int:
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    old_event = now - timedelta(days=80)
    recent_written = now - timedelta(days=1)

    failures = []

    # 1. event_date must win over mentioned_at when occurred_start is absent.
    rec = FakeRecord(
        "у DeepSeek fit дорогой и публичного нет",
        occurred_start=None,
        occurred_end=None,
        mentioned_at=recent_written,
        event_date=old_event,
        metadata={},
        tags=[],
    )
    line = render(rec)
    if "2026-08-13" not in line:
        failures.append(
            f"event_date ignored: expected 2026-08-13 in line, got {line!r}"
        )
    if "2026-10-31" in line:
        failures.append(
            f"mentioned_at used instead of event_date: {line!r}"
        )

    # 2. occurred_start still wins when it is present (explicit beats inferred).
    rec2 = FakeRecord(
        "факт",
        occurred_start=datetime(2026, 10, 20, tzinfo=timezone.utc),
        occurred_end=None,
        mentioned_at=recent_written,
        event_date=old_event,
        metadata={},
        tags=[],
    )
    line2 = render(rec2)
    if "2026-10-20" not in line2:
        failures.append(f"occurred_start should win when present: {line2!r}")

    # 3. mentioned_at is the last resort and must be labelled as the written date.
    rec3 = FakeRecord(
        "факт",
        occurred_start=None,
        occurred_end=None,
        mentioned_at=recent_written,
        event_date=None,
        metadata={},
        tags=[],
    )
    line3 = render(rec3)
    if "2026-10-31" not in line3:
        failures.append(f"mentioned_at fallback broken: {line3!r}")

    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("PASS: event_date precedence correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
