"""Timeweb Cloud account finance -> Loki. Rubles, never converted to dollars."""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'deepinfra_usage'))
from push_deepinfra_usage import loki_credentials  # noqa: E402

API = 'https://api.timeweb.cloud/api/v1/account/finances'
COST_API = 'https://api.timeweb.cloud/api/v1/account/services/cost'
TIMEOUT = 30

# field in the API response -> metric suffix
FIELDS = {
    'balance': 'balance_rub',
    'total_balance': 'total_balance_rub',
    'monthly_cost': 'monthly_cost_rub',
    'hourly_cost': 'hourly_cost_rub',
    'hours_left': 'hours_left',
    'total_paid': 'total_paid_rub',
}


def hermes_home() -> Path:
    return Path(os.environ.get('HERMES_HOME', str(Path(os.environ['LOCALAPPDATA']) / 'hermes')))


def env_value(key: str) -> str:
    """Environment first, then the Hermes .env file. Never logs the value."""
    value = os.environ.get(key, '').strip()
    if value:
        return value
    env_file = hermes_home() / '.env'
    if not env_file.exists():
        return ''
    for raw in env_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw.strip()
        if line.startswith(f'{key}=') and '=' in line:
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    return ''


def build_lines(payload, now):
    """Turn the API payload into logfmt metrics. Missing numbers stay unknown."""
    lines = [f'metric=sample_timestamp_seconds value={now}']
    finances = None
    if isinstance(payload, dict):
        finances = payload.get('finances')
    ok = isinstance(finances, dict)
    lines.append(f'metric=read_ok value={int(ok)}')
    if not ok:
        return lines
    lines.append(f'metric=last_success_timestamp_seconds value={now}')
    for field, metric in FIELDS.items():
        value = finances.get(field)
        known = type(value) in (int, float) and math.isfinite(value)
        lines.append(f'metric={metric}_known value={int(known)}')
        if known:
            lines.append(f'metric={metric} value={value:.4f}')
    return lines


def quote(text: str) -> str:
    return '"' + str(text).replace('\\', '/').replace('"', "'") + '"'


def build_cost_lines(payload, now):
    """Per-service cost: this is where AI and infrastructure finally separate."""
    lines = [f'metric=cost_sample_timestamp_seconds value={now}']
    items = None
    if isinstance(payload, dict):
        items = payload.get('services_costs')
    ok = isinstance(items, list)
    lines.append(f'metric=cost_read_ok value={int(ok)}')
    if not ok:
        return lines
    ai_total = 0.0
    infra_total = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        service_type = str(item.get('type') or 'unknown')
        name = (item.get('info') or {}).get('name') or service_type
        total = item.get('total_cost')
        if type(total) not in (int, float) or not math.isfinite(total):
            total = 0.0
        if service_type == 'ai_proxy_key':
            ai_total += total
            lines.append(f'metric=ai_cost_rub value={total:.6f} key={quote(name)}')
            for part in item.get('services') or []:
                if not isinstance(part, dict):
                    continue
                kind = str(part.get('type') or 'unknown')
                amount = part.get('amount')
                cost = part.get('cost')
                if type(amount) in (int, float):
                    lines.append(
                        f'metric=ai_tokens value={amount:.0f} key={quote(name)} '
                        f'kind={kind} model_id={part.get("model_id", "")}'
                    )
                if type(cost) in (int, float):
                    lines.append(
                        f'metric=ai_token_cost_rub value={cost:.6f} key={quote(name)} '
                        f'kind={kind} model_id={part.get("model_id", "")}'
                    )
        else:
            infra_total += total
            lines.append(f'metric=service_cost_rub value={total:.6f} type={service_type} name={quote(name)}')
    lines.append(f'metric=finops_ai_cost_rub value={ai_total:.6f}')
    lines.append(f'metric=finops_infra_cost_rub value={infra_total:.6f}')
    lines.append(f'metric=finops_service_count value={len(items)}')
    return lines


def fetch(url: str, creds: str) -> dict:
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {creds}'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def push(lines: list[str]) -> int:
    url, user, password = loki_credentials()
    payload = {'streams': [{
        'stream': {'service_name': 'timeweb', 'log_type': 'billing', 'job': 'timeweb-account'},
        'values': [[str(time.time_ns()), line] for line in lines],
    }]}
    auth_value = base64.b64encode(f'{user}:{password}'.encode()).decode()
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Basic {auth_value}'},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    creds = env_value('TW_API_KEY')
    payload, error = None, ''
    costs, cost_error = None, ''
    if not creds:
        error = 'missing TW_API_KEY'
    else:
        try:
            payload = fetch(API, creds)
        except urllib.error.HTTPError as exc:
            error = f'http_{exc.code}'
        except Exception as exc:  # noqa: BLE001 - any transport failure is "no reading"
            error = type(exc).__name__
        try:
            costs = fetch(COST_API, creds)
        except urllib.error.HTTPError as exc:
            cost_error = f'http_{exc.code}'
        except Exception as exc:  # noqa: BLE001
            cost_error = type(exc).__name__

    now = int(time.time())
    lines = build_lines(payload, now) + build_cost_lines(costs, now)
    log_path = hermes_home() / 'cache' / 'timeweb_usage.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(('account_read_error=%s\n' % error) if error else 'account_read_ok\n')
        log.write(('cost_read_error=%s\n' % cost_error) if cost_error else 'cost_read_ok\n')

    if args.dry_run:
        print('\n'.join(lines))
    else:
        try:
            print(f'loki={push(lines)} lines={len(lines)}')
        except Exception as exc:  # noqa: BLE001
            print(f'loki_error={type(exc).__name__}', file=sys.stderr)
            return 4
    return 0 if payload else 3


if __name__ == '__main__':
    raise SystemExit(main())
