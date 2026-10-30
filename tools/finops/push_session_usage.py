"""Hermes session tokens with readable titles -> Loki.

The agent log only carries opaque session ids, so the money dashboard can show
`local-a347ac1f...` and nothing else. state.db knows the title of every session,
so this collector joins the two at the source and publishes cumulative totals
labelled with the name.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'deepinfra_usage'))
from push_deepinfra_usage import loki_credentials  # noqa: E402

MAX_AGE_DAYS = 30
FIELDS = (
    ('input_tokens', 'session_input_tokens'),
    ('output_tokens', 'session_output_tokens'),
    ('cache_read_tokens', 'session_cache_read_tokens'),
    ('api_call_count', 'session_api_calls'),
)


def hermes_home() -> Path:
    return Path(os.environ.get('HERMES_HOME', str(Path(os.environ['LOCALAPPDATA']) / 'hermes')))


def quote(text: str) -> str:
    flat = ' '.join(str(text).split())
    return '"' + flat.replace('\\', '/').replace('"', "'") + '"'


def read_sessions(db_path: Path, now: float) -> list[dict]:
    """Read-only query; the live database is in WAL mode and stays untouched."""
    if not db_path.exists():
        return []
    cutoff = now - MAX_AGE_DAYS * 86400
    con = sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True, timeout=10)
    try:
        cur = con.cursor()
        cur.execute(
            'SELECT id, title, model, input_tokens, output_tokens, cache_read_tokens,'
            ' api_call_count, last_activity_at FROM sessions'
            ' WHERE last_activity_at > ? AND title IS NOT NULL AND title <> ""',
            (cutoff,),
        )
        rows = cur.fetchall()
    finally:
        con.close()
    keys = ('id', 'title', 'model', *[f for f, _ in FIELDS], 'last_activity_at')
    return [dict(zip(keys, row)) for row in rows]


def build_lines(sessions: list[dict]) -> list[str]:
    lines = []
    for session in sessions:
        title = quote(session.get('title'))
        model = quote(session.get('model') or 'unknown')
        sid = quote(session.get('id'))
        for column, metric in FIELDS:
            value = session.get(column)
            if type(value) in (int, float) and value > 0:
                lines.append(
                    f'metric={metric} value={value:.0f} session={sid} title={title} model={model}'
                )
    return lines


def push(lines: list[str]) -> int:
    url, user, password = loki_credentials()
    payload = {'streams': [{
        'stream': {'service_name': 'finops', 'log_type': 'session_usage', 'job': 'hermes-sessions'},
        'values': [[str(time.time_ns()), line] for line in lines],
    }]}
    auth = base64.b64encode(f'{user}:{password}'.encode()).decode()
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Basic {auth}'},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    now = time.time()
    sessions = read_sessions(hermes_home() / 'state.db', now)
    lines = build_lines(sessions)

    if args.dry_run:
        print(f'sessions={len(sessions)} lines={len(lines)}')
        print('\n'.join(lines[:6]))
        return 0
    if not lines:
        print('loki=skipped reason=no_sessions')
        return 0
    print(f'loki={push(lines)} sessions={len(sessions)} lines={len(lines)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
