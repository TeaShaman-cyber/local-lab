#!/usr/bin/env python3
"""Push DeepInfra billing facts to Grafana Cloud Loki (issue #58).

Why Loki and not Prometheus: the Grafana Cloud Loki push endpoint accepts plain
JSON with basic auth, so this stays stdlib-only. Credentials are read from the
running Alloy config (single source of truth) and never printed.

Metrics emitted as logfmt lines so LogQL can `| logfmt | unwrap value`:

    metric=stripe_balance_usd value=-5.0
    metric=spend_month_usd value=0.08
    metric=spend_recent_usd value=0.08
    metric=suspended value=0
    metric=cost_cents value=5 pricing_type=input_tokens model=...
    metric=units value=489318 pricing_type=input_tokens model=...
    metric=usage_period_start_unix value=... period=2026.10
    metric=usage_period_end_unix value=... period=2026.10

DeepInfra semantics: `stripe_balance` is negative for prepaid funds; a positive
value means money owed. `cost` fields are in cents, `rate` is cents per token.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ALLOY_CONFIG = pathlib.Path(r"C:\Program Files\GrafanaLabs\Alloy\config.alloy")
HERMES_ENV = pathlib.Path(os.environ["LOCALAPPDATA"]) / "hermes" / ".env"
API = "https://api.deepinfra.com"
TIMEOUT = 30


def read_env(name: str) -> str:
    for line in HERMES_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def loki_credentials() -> tuple[str, str, str]:
    """Extract the loki.write endpoint url + basic auth from the Alloy config."""
    text = ALLOY_CONFIG.read_text(encoding="utf-8", errors="ignore")
    block = re.search(r'loki\.write\s+"[^"]+"\s*\{(.*?)\n\}', text, re.S)
    if not block:
        raise SystemExit("loki.write block not found in Alloy config")
    body = block.group(1)
    url = re.search(r'url\s*=\s*"([^"]+)"', body)
    user = re.search(r'username\s*=\s*"([^"]+)"', body)
    pwd = re.search(r'password\s*=\s*"([^"]+)"', body)
    if not (url and user and pwd):
        raise SystemExit("loki.write url/username/password not all present")
    return url.group(1), user.group(1), pwd.group(1)


def get_json(path: str, key: str) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.load(response)


def build_lines(checklist: dict, usage: dict) -> list[str]:
    lines: list[str] = []

    raw_balance = checklist.get("stripe_balance")
    if isinstance(raw_balance, (int, float)):
        # The raw value only. Do NOT derive an "available balance": the sign
        # convention did not hold in production - stripe_balance was -5.0 while
        # the account was suspended and every request returned HTTP 402, so the
        # derived 5.0 read as money that was not there. A number that survives
        # only in the happy path is not a measurement.
        lines.append(f"metric=stripe_balance_usd value={float(raw_balance):.4f}")
        if raw_balance > 0:
            lines.append(f"metric=owed_usd value={float(raw_balance):.4f}")
    if isinstance(checklist.get("recent"), (int, float)):
        lines.append(f"metric=spend_recent_usd value={float(checklist['recent']):.4f}")
    if isinstance(checklist.get("limit"), (int, float)):
        lines.append(f"metric=spend_limit_usd value={float(checklist['limit']):.4f}")
    lines.append(f"metric=suspended value={1 if checklist.get('suspended') else 0}")
    if isinstance(checklist.get("overdue_invoices"), (int, float)):
        lines.append(f"metric=overdue_invoices value={float(checklist['overdue_invoices']):.0f}")

    total_cents = 0.0
    for month in usage.get("months") or []:
        period = str(month.get("period") or "unknown")
        interval = month.get("interval") or {}
        interval_from = interval.get("fr")
        interval_to = interval.get("to")
        if isinstance(interval_from, (int, float)):
            lines.append(
                f"metric=usage_period_start_unix value={interval_from / 1000:.3f} period={period}"
            )
        if isinstance(interval_to, (int, float)):
            lines.append(
                f"metric=usage_period_end_unix value={interval_to / 1000:.3f} period={period}"
            )
        for item in month.get("items") or []:
            model = ((item.get("model") or {}).get("model_name") or "unknown").replace(" ", "_")
            kind = item.get("pricing_type") or "unknown"
            cost = float(item.get("cost") or 0)
            units = float(item.get("units") or 0)
            total_cents += cost
            lines.append(f"metric=cost_cents value={cost:.4f} pricing_type={kind} model={model}")
            lines.append(f"metric=units value={units:.0f} pricing_type={kind} model={model}")

    lines.append(f"metric=spend_month_usd value={total_cents / 100:.4f}")
    return lines


def push(url: str, user: str, password: str, lines: list[str]) -> int:
    ts = str(int(time.time() * 1_000_000_000))
    payload = {
        "streams": [
            {
                "stream": {
                    "service_name": "deepinfra",
                    "log_type": "billing",
                    "host": "theseus-laptop",
                    "job": "deepinfra-billing",
                },
                "values": [[ts, line] for line in lines],
            }
        ]
    }
    import base64

    credentials = f"{user}:{password}".encode()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(credentials).decode(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.status


def main() -> int:
    key = read_env("DEEPINFRA_API_KEY")
    if not key:
        print("DEEPINFRA_API_KEY missing in Hermes .env", file=sys.stderr)
        return 2

    try:
        checklist = get_json("/payment/checklist?compute_owed=true", key)
        usage = get_json("/payment/usage?from=current", key)
    except urllib.error.HTTPError as exc:
        body = exc.read()[:200].decode(errors="replace")
        print(f"DeepInfra billing HTTP {exc.code}: {body}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - surface the reason, do not guess
        print(f"DeepInfra billing failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    lines = build_lines(checklist, usage)
    url, user, password = loki_credentials()
    try:
        status = push(url, user, password, lines)
    except urllib.error.HTTPError as exc:
        print(f"Loki push HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"Loki push failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    balance = next((ln.split("=")[-1] for ln in lines if ln.startswith("metric=stripe_balance_usd")), "?")
    month = next((ln.split("=")[-1] for ln in lines if ln.startswith("metric=spend_month_usd")), "?")
    print(f"loki={status} lines={len(lines)} balance_usd={balance} spend_month_usd={month}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
