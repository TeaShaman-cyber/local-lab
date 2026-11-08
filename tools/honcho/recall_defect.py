#!/usr/bin/env python3
"""Record a recall defect straight into the Honcho bank.

Why a direct conclusion and not a message: `POST /v3/workspaces/{ws}/conclusions`
is the documented way to write a ready-made fact into the bank. It bypasses the
deriver entirely (no LLM call, no batched wait), returns 204 immediately, and
the row is exactly what recall will read later. Sending the same text as a
message instead would go through the deriver, where the schema rewrites it into
"X said that Y" and the defect would look like dialogue.

Convention this tool enforces:

    content      "RECALL-DEFECT <date> | recall said: <what> | reality: <what>"
    observer_id  svarog   (the agent making the remark)
    observed_id  svarog   (the remark is about the agent's own memory, not
                           about the user - a defect must not pollute the
                           user's representation)
    session_id   local-lab unless --session is given

The RECALL-DEFECT prefix is what makes these countable later: one query gives
the weekly defect count without parsing free text.

Usage:
    python tools/honcho/recall_defect.py "recall said X" "reality Y"
    python tools/honcho/recall_defect.py --list
    python tools/honcho/recall_defect.py --help

Exit codes: 0 = written or listed, 1 = API refused, 2 = usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = os.environ.get("HONCHO_URL", "http://127.0.0.1:8000")
DEFAULT_WORKSPACE = os.environ.get("HONCHO_WORKSPACE", "hermes")
DEFAULT_SESSION = os.environ.get("HONCHO_SESSION") or None  # workspace-scoped by default:
# a defect outlives the session that produced it, which matters because deleting a
# session cascades its conclusions away.
AGENT_PEER = "svarog"
PREFIX = "RECALL-DEFECT"
LEVEL = "explicit"


def _request(method: str, url: str, payload: dict | None = None, timeout: int = 20) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except urllib.error.URLError as exc:
        return 0, str(exc)


def record(base: str, workspace: str, session: str | None, claim: str, reality: str,
           observer: str, observed: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content = f"{PREFIX} {today} | recall said: {claim.strip()} | reality: {reality.strip()}"
    body = {"conclusions": [{
        "content": content,
        "observer_id": observer,
        "observed_id": observed,
        "session_id": session,
    }]}
    status, text = _request("POST", f"{base}/v3/workspaces/{workspace}/conclusions", body)
    if status not in (200, 201, 204):
        print(f"FAIL: HTTP {status} {text[:300]}", file=sys.stderr)
        return 1
    print(f"ok  POST /v3/workspaces/{workspace}/conclusions -> HTTP {status}")
    print(f"    {content}")

    # Readback: the tool must prove the row exists, not just that it posted.
    listing, raw = _request("POST", f"{base}/v3/workspaces/{workspace}/conclusions/list",
                            {"session_id": session, "limit": 50})
    if listing != 200:
        print(f"WARN: readback failed HTTP {listing}; row not confirmed", file=sys.stderr)
        return 1
    items = json.loads(raw).get("items", [])
    hit = next((i for i in items if i.get("content") == content), None)
    if hit:
        print(f"    verified in bank: {hit.get('id')} level={hit.get('level')} "
              f"observer={hit.get('observer_id')} observed={hit.get('observed_id')}")
        return 0
    print("WARN: posted but not seen in the last 50 conclusions of this session", file=sys.stderr)
    return 1


def listing(base: str, workspace: str, session: str | None) -> int:
    status, raw = _request("POST", f"{base}/v3/workspaces/{workspace}/conclusions/list",
                           {"session_id": session, "limit": 200})
    if status != 200:
        print(f"FAIL: HTTP {status} {raw[:300]}", file=sys.stderr)
        return 1
    items = [i for i in json.loads(raw).get("items", []) if str(i.get("content", "")).startswith(PREFIX)]
    for item in sorted(items, key=lambda i: i.get("created_at", "")):
        print(f"{item.get('created_at', '')[:19]}  {item.get('id')}  {item.get('content')}")
    print(f"{len(items)} defect(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write a recall defect into the Honcho bank.")
    ap.add_argument("claim", nargs="?", help="what recall claimed")
    ap.add_argument("reality", nargs="?", help="what runtime actually showed")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--observer", default=AGENT_PEER)
    ap.add_argument("--observed", default=AGENT_PEER,
                    help="who the remark is about; defaults to the agent so a defect never "
                         "lands in the user's representation")
    ap.add_argument("--list", action="store_true", help="list recorded defects")
    args = ap.parse_args(argv)

    if args.list:
        return listing(args.url, args.workspace, args.session)
    if not args.claim or not args.reality:
        ap.print_help()
        return 2
    return record(args.url, args.workspace, args.session, args.claim, args.reality,
                  args.observer, args.observed)


if __name__ == "__main__":
    raise SystemExit(main())
