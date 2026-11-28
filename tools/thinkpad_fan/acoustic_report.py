"""Read compact local acoustic diagnostics; no microphone or host access."""

import json
from pathlib import Path


def report(root):
    records = []
    rejected = 0
    for p in sorted(root.glob("trace-*.json")):
        try:
            if p.stat().st_size > 65536:
                raise ValueError("oversize")
            r = json.loads(p.read_text(encoding="utf-8"))
            if r["schema"] != 1 or p.name != f"trace-{r['first_utc_ns']}.json":
                raise ValueError("identity")
            records.append((r["first_utc_ns"], r["sample_count"] / r["sample_rate"]))
        except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
            rejected += 1
    return {
        "captures": len(records),
        "rejected": rejected,
        "seconds": round(sum(r[1] for r in records), 2),
        "first_utc_ns": min((r[0] for r in records), default=None),
        "last_utc_ns": max((r[0] for r in records), default=None),
    }


if __name__ == "__main__":
    print(json.dumps(report(Path("/acoustic"))))
