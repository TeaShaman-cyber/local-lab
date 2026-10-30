"""Nous account credits -> existing Loki transport; issue #8. No inference."""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'deepinfra_usage'))
from push_deepinfra_usage import loki_credentials

FIELDS = ('purchased_credits_remaining', 'subscription_credits_remaining', 'total_usable_credits')


def build_lines(account, now):
    fresh = account is not None and account.source == 'account_api' and account.fresh and not account.error
    lines = [f'metric=read_ok value={int(bool(fresh))}', f'metric=sample_timestamp_seconds value={now}']
    if not fresh:
        return lines
    lines.append(f'metric=last_success_timestamp_seconds value={now}')
    access = account.paid_service_access_info
    for field in FIELDS:
        value = getattr(access, field, None)
        known = type(value) in (int, float) and math.isfinite(value)
        lines.append(f'metric={field}_known value={int(known)}')
        if known:
            lines.append(f'metric={field}_usd value={value:.6f}')
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    home = Path(os.environ.get('HERMES_HOME', str(Path(os.environ['LOCALAPPDATA']) / 'hermes')))
    sys.path.insert(0, str(home / 'hermes-agent'))
    account = None
    error_text = ''
    try:
        from hermes_cli.nous_account import get_nous_portal_account_info
        account = get_nous_portal_account_info(force_fresh=True)
    except Exception as exc:
        error_text = f'account_read_error={type(exc).__name__}'
        print(error_text, file=sys.stderr)
    lines = build_lines(account, int(time.time()))
    log_path = home / 'cache' / 'nous_usage.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        if account is None:
            log.write((error_text or 'account_none') + '\n')
        else:
            log.write('account_read_ok source=%s fresh=%s error=%s error_text=%s\n' % (account.source, account.fresh, bool(account.error), str(account.error)[:160].replace('\n', ' ')))
    if args.dry_run:
        print('\n'.join(lines))
    else:
        try:
            url, user, password = loki_credentials()
            payload = {'streams': [{'stream': {'service_name': 'nous', 'log_type': 'billing', 'job': 'nous-credits'}, 'values': [[str(time.time_ns()), line] for line in lines]}]}
            request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()}, method='POST')
            with urllib.request.urlopen(request, timeout=30) as response:
                print(f'loki={response.status} lines={len(lines)}')
        except Exception as exc:
            print(f'loki_error={type(exc).__name__}', file=sys.stderr)
            return 4
    return 0 if lines[0] == 'metric=read_ok value=1' else 3


if __name__ == '__main__':
    raise SystemExit(main())
