# Hindsight LLM usage monitor

BotHub exposes no balance or usage API — `/api/v2/{me,user,user/balance,balance,limits,usage}`
all return 404, and the response headers carry only a rate limit. Its spend is
therefore observable only from our own side.

Hindsight already records every LLM request at
`GET /v1/default/banks/<bank>/llm-requests`, including provider, model,
operation, status, `input_tokens`, `output_tokens` and `cached_tokens`. This
collector turns **new** rows into append-only Loki metrics.

## Why a cursor, not a snapshot

The endpoint returns history. Pushing a running total every 15 minutes would
double-count the moment anything sums over time. The collector keeps
`%LOCALAPPDATA%\hermes\cache\hindsight_llm_usage_cursor.json` with the newest
`started_at` it has already published, and pushes only rows strictly newer.
Re-running it is therefore a no-op, which is what the verification checks.

First run with `--baseline` adopts the current newest row and pushes nothing, so
a fresh install does not dump months of history into Loki.

## Metrics

Stream `{service_name="hindsight", log_type="llm_usage", job="hindsight-llm-requests"}`:

| metric | meaning |
|---|---|
| `llm_input_tokens` | prompt tokens for one call |
| `llm_output_tokens` | completion tokens for one call |
| `llm_cached_tokens` | cache-read tokens when the provider reports them |
| `llm_calls` | one per successful paid call, gives a call count |

Each line carries `provider`, `model`, `operation`, `status` and `call_id`.
Local embedding and reranker calls (`provider=local`) are skipped — they cost
nothing and would drown the signal.

## What this measures and what it does not

It measures **tokens**, not rubles. BotHub bills in rubles with its own per-model
pricing that is not published as an API, so the finance figure stays in the
provider cabinet. Tokens and call counts are the honest proxy: they show how
much each `reflect` burns before you open the cabinet.

One trap worth remembering: **a timed-out reflect still bills.** On 2026-10-27 a
reflect returned `504` after 300 seconds while its two BotHub calls had already
completed with 17,254 input and 268 output tokens. The provider charged for the
work; we got no answer. Never treat a failed operation as a free one.

## Runtime

Windows Task Scheduler task `Theseus Hindsight LLM usage`, every 15 minutes, run
with `pythonw.exe` from the Hermes venv so no console window appears.

```bash
python tools/hindsight_usage/push_hindsight_llm_usage.py --baseline   # first install
python tools/hindsight_usage/push_hindsight_llm_usage.py --dry-run
python -m pytest -q tools/hindsight_usage/test_push_hindsight_llm_usage.py
```
