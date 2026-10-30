# Timeweb Cloud account monitor

`push_timeweb_usage.py` reads `GET https://api.timeweb.cloud/api/v1/account/finances`
and pushes Ruble metrics to the existing Grafana Loki endpoint.

Runs as Windows Task Scheduler task `Theseus Timeweb billing`, every 30 minutes,
using Hermes' Python environment:

`%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe`

## Metrics (`{service_name="timeweb", log_type="billing", job="timeweb-account"}`)

| metric | meaning |
|---|---|
| `balance_rub` | money left on the shared account wallet |
| `total_balance_rub` | same wallet including bonuses, as the API reports it |
| `monthly_cost_rub` | current fixed monthly subscription cost |
| `hourly_cost_rub` | hourly burn of that subscription |
| `hours_left` | hours the balance covers at the current burn |
| `total_paid_rub` | lifetime top-ups |
| `read_ok` | 1 = last read succeeded, 0 = data stale, do not trust the balance |

Each `*_rub` metric is paired with `<name>_known`, because a missing field in the
API response must stay unknown instead of silently becoming zero.

## Rubles stay rubles

The dashboard shows Timeweb in `currencyRUB` and never converts to USD. A
converted number would depend on the exchange rate at render time and would
quietly invite comparison with dollar-priced inference providers.

## Infrastructure and AI share the wallet, but the API can separate them

The balance is one wallet, yet `GET /api/v1/account/services/cost` returns the
cost of every active service, and it does distinguish the two worlds:

```json
{"services_costs": [
  {"type": "s3", "total_cost": 79, "info": {"name": "theseus-lab-backups"}},
  {"type": "s3_outgoing_traffic", "total_cost": 1},
  {"type": "ai_proxy_key", "total_cost": 2.084, "info": {"name": "hermes"},
   "services": [{"type": "token_output", "cost": 1.73, "amount": 25671, "model_id": 259}]}
]}
```

So `ai_proxy_key` entries are AI spend, with a per-model breakdown by
`token_input`, `token_cache_read` and `token_output`, and everything else is
infrastructure. The collector pushes both sides separately:

| metric | meaning |
|---|---|
| `finops_ai_cost_rub` | all `ai_proxy_key` services summed |
| `finops_infra_cost_rub` | every other service type summed |
| `ai_cost_rub{key=...}` | AI cost per API key |
| `ai_tokens{key,kind,model_id}` | token amount per key, kind and model |
| `ai_token_cost_rub{...}` | Ruble cost of those tokens |
| `service_cost_rub{type,name}` | infrastructure cost per service |

Two traps:

- **`total_cost` is a period figure**, not a per-sample delta. Never sum it over
  time; use `last_over_time` for trends.
- `cost` and `total_cost` differ for `ai_proxy_key` (`cost` was `0` while
  `total_cost` held the real number). Use `total_cost`.

`monthly_cost_rub` from the finances endpoint stays the fixed subscription view;
it is not the same quantity as the summed service costs.

### What is still not exposed

`https://api.timeweb.ai/v1` is the OpenAI-compatible inference surface: `GET
/models` works, `/usage`, `/balance`, `/tokens`, `/account`, `/limits` all
return 404. Per-key daily limits exist but are configured in the console
(rubles or tokens, with 80%/100% notifications) — they are a guardrail, not a
reporting API.

### Webhooks instead of polling

Timeweb emits billing webhook events: `low_balance_info` (`hours_left`,
`monthly_fee`), `money_transfer`, `auto_payment_success`, `auto_payment_error`,
`account_blocked`, `data_will_be_deleted_soon`. They need a publicly reachable
endpoint, so the current design polls every 30 minutes instead. If a webhook
receiver ever exists, these events are the better source for top-ups and
low-balance warnings.

## Credentials

`TW_API_KEY` is read from the environment first, then from
`%LOCALAPPDATA%\hermes\.env`. The value is never logged.

## Verify

```bash
python tools/timeweb_usage/push_timeweb_usage.py --dry-run
python -m pytest -q tools/timeweb_usage/test_push_timeweb_usage.py
```
