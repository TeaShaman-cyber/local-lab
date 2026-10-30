"""Hindsight LLM usage -> Loki, one line per paid call.

BotHub exposes no balance or usage API, so its spend is only observable through
our own call trace. Hindsight records every LLM request (provider, model,
tokens, status) at /v1/<bank>/llm-requests; this collector turns new rows into
append-only metrics, so summing over time cannot double count.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'deepinfra_usage'))
from push_deepinfra_usage import loki_credentials

HINDSIGHT = os.environ.get('HINDSIGHT_URL', 'http://localhost:8888')
BANK = os.environ.get('HINDSIGHT_BANK', 'hermes')
PAGE = 200
TIMEOUT = 60


def hermes_home() -> Path:
    return Path(os.environ.get('HERMES_HOME', str(Path(os.environ['LOCALAPPDATA']) / 'hermes')))


def quote(text: str) -> str:
    return '"' + str(text).replace('\\', '/').replace('"', "'") + '"'


def fetch(limit: int = PAGE) -> dict:
    url = f'{HINDSIGHT}/v1/default/banks/{BANK}/llm-requests?limit={limit}'
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def is_paid(item: dict) -> bool:
    """Local embedding/reranker calls cost nothing; they must not reach Loki."""
    return str(item.get('provider') or '') != 'local'


def number(value):
    if type(value) in (int, float) and math.isfinite(value):
        return value
    return None


def select_new(items: list[dict], cursor: str) -> list[dict]:
    """Rows strictly newer than the cursor, oldest first. Cursor is started_at."""
    fresh = []
    for item in items:
        started = str(item.get('started_at') or '')
        if not started:
            continue
        if cursor and started <= cursor:
            continue
        fresh.append(item)
    fresh.sort(key=lambda i: str(i.get('started_at') or ''))
    return fresh


def build_lines(items: list[dict]) -> list[str]:
    lines = []
    for item in items:
        if not is_paid(item):
            continue
        provider = str(item.get('provider') or 'unknown')
        model = str(item.get('model') or 'unknown')
        operation = str(item.get('operation') or 'unknown')
        status = str(item.get('status') or 'unknown')
        base = f'provider={quote(provider)} model={quote(model)} operation={operation} status={status}'
        call_id = quote(item.get('id') or '')
        wrote = False
        for field, metric in (
            ('input_tokens', 'llm_input_tokens'),
            ('output_tokens', 'llm_output_tokens'),
            ('cached_tokens', 'llm_cached_tokens'),
        ):
            value = number(item.get(field))
            if value is not None:
                lines.append(f'metric={metric} value={value:.0f} {base} call_id={call_id}')
                wrote = True
        if wrote:
            lines.append(f'metric=llm_calls value=1 {base} call_id={call_id}')
    return lines


def push(lines: list[str]) -> int:
    url, user, password = loki_credentials()
    payload = {'streams': [{
        'stream': {'service_name': 'hindsight', 'log_type': 'llm_usage', 'job': 'hindsight-llm-requests'},
        'values': [[str(time.time_ns()), line] for line in lines],
    }]}
    auth = base64.b64encode(f'{user}:{password}'.encode()).decode()
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Basic {auth}'},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--baseline', action='store_true',
                        help='adopt the current newest row as the cursor and push nothing')
    args = parser.parse_args()

    state_path = hermes_home() / 'cache' / 'hindsight_llm_usage_cursor.json'
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001 - a broken cursor must not stop collection
            state = {}
    cursor = str(state.get('last_started_at') or '')

    payload = fetch()
    items = payload.get('items') or []
    if not items:
        print('loki=skipped reason=no_items')
        return 0

    newest = max(str(i.get('started_at') or '') for i in items)
    if args.baseline or not cursor:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({'last_started_at': newest}, indent=1), encoding='utf-8')
        print(f'baseline_cursor={newest} pushed=0')
        return 0

    fresh = select_new(items, cursor)
    lines = build_lines(fresh)
    if args.dry_run:
        print(f'new_calls={len(fresh)} lines={len(lines)} cursor={cursor}')
        print('\n'.join(lines[:20]))
        return 0

    if not lines:
        state_path.write_text(json.dumps({'last_started_at': max(cursor, newest)}, indent=1), encoding='utf-8')
        print(f'loki=skipped reason=no_new_paid_calls cursor={cursor}')
        return 0

    print(f'loki={push(lines)} calls={len(fresh)} lines={len(lines)}')
    state_path.write_text(json.dumps({'last_started_at': newest}, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
