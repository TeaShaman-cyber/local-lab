# FinOps dashboard

Grafana: <https://tealaphid71.grafana.net/d/hermes-finops> (uid `hermes-finops`, folder `ag2b9ny7raxogc`).
Tracking issue: #70.

One page for every money source, with the single rule that matters: **fact and
estimate never mix**. A number produced by multiplying our own token counts by a
price list is not a bill and must not be displayed like one.

## Source inventory

| Source | Money source | Tokens from logs | Note |
|---|---|---|---|
| DeepInfra | real billing API | yes | `provider=deepinfra` |
| Nous | remaining credits only | yes | `provider=nous`; no per-period spend |
| RunInfra | none confirmed | yes | appears as `provider=custom` |
| OpenAI / Codex | none — ChatGPT subscription | yes | `provider=openai-codex`; burns the 5h limit |
| Timeweb | MCP `get_account_finances`, rubles | n/a | infrastructure, not inference |
| BotHub | none | none in inference logs | $0 while Hindsight runs on a free model |

Only `deepinfra` and `nous` publish `log_type="billing"` streams to Loki.

## Prices used in the estimate panels

USD per 1M tokens, verified 2026-10-27 against the provider catalogues:

| Model | input | cache read | output |
|---|---:|---:|---:|
| DeepSeek V4.1 Flash 1017 @ RunInfra | 0.15 | 0.01 | 0.60 |
| DeepSeek V4.1 Flash 1017 @ Nous | 0.022 | 0.0124 | 1.21 |
| DeepSeek V4 Flash 0731 @ Nous | 0.07 | 0.014 | 0.14 |

Prices change. Re-verify before trusting a bound; a stale price silently turns a
bound into a lie.

## Exact cost, not a range

The agent log line carries both the total input and how much of it the provider
served from cache:

```
API call #1: model=X provider=Y in=112425 out=205 total=112630 latency=5.1s cache=112004/112488 (100%)
```

So the cost is determined, not bounded:

```
cost = (in - cached) * price_in + cached * price_cache + out * price_out
```

The dashboard computes it with a server-side math expression over three LogQL
queries (input, cached, output). The three token queries are `hide: true` so the
stat shows one dollar figure instead of four series.

Worked example, 7 days of RunInfra: 665.29M input, 653.49M cached, 1.16M output.

```
(665.29 - 653.49) * 0.15 + 653.49 * 0.01 + 1.16 * 0.60 = 9.00
```

Verified against the rendered panel (`$9.00`), and Nous with the same shape:
204.82M / 200.54M / 0.34M → `$2.99`.

### Where the formula degrades

- **Mixed models behind one provider.** A provider is not a price. If two models
  with different rates share the `provider=` value, the sum is an average with no
  error bar. Group by `model` when in doubt.
- **Stale prices.** The numbers are constants in the dashboard. A price change
  makes the panel quietly wrong, so the token queries stay one click away.
- **Missing `cache=` field.** Lines without it are treated as fully uncached by
  the subtraction, which is the safe direction — it overestimates.
- **Non-token billing** (per-request fees, minimums, subscriptions) is invisible
  here. DeepInfra is excluded from this panel entirely because it has a real
  billing API, and fact beats formula.

## Recreating or changing the dashboard

Queries live in the dashboard JSON in Grafana. Keep in mind:

- LogQL arithmetic is allowed: `sum(...) * 0.00000001` is a valid price multiply.
- The provider filter `|= "provider=custom in="` also captures `out=` on the same
  line, so both halves of a cost expression reuse one line filter.
- Provider aggregates use `sum by (provider)`; a per-model view is a deliberate
  next step, not a missing default.

## Alert policy: never alert on silence

This lab runs on a laptop that is switched off at night, at weekends, and
whenever the owner is at work. An alert on missing telemetry therefore measures
the owner's schedule, not the health of anything.

Rules:

- **Silence is not a failure.** No "pusher has been quiet for an hour" alerts.
  Staleness is shown on the dashboard as `read_ok` and `last_success_timestamp_seconds`
  and is read when someone sits down.
- **Alert only on events that cannot happen while the machine sleeps.** A request
  that went out and was refused (`402`, insufficient balance), a backup that ran
  and failed, a restore that failed, a secret that reached the log. Each of these
  requires the host to have been awake and working.
- **Persistent known states are dashboard material, not email.** A suspended
  account with no top-up will re-notify every repeat interval about a condition
  already known. Pause it and put the state on the panel.
- **Long-absence guards get a weekend-sized window.** "No successful backup in
  26h" fires every Sunday; 72h tolerates a weekend and still catches a genuinely
  broken schedule.
- **A renamed metric breaks its alert silently.** Changing `balance_usd` to
  `stripe_balance_usd` turned the pusher-silence query into a permanent
  no-match. Grep alert queries for every metric name you rename.

## Per-session attribution

A provider total hides the thing that actually drains a quota: one chat carrying a
huge context. The agent log line starts with the session id, so the same `API call #`
rows can be grouped by it:

```logql
sum by (session) (sum_over_time({service_name="hermes", log_type="agent"} |= "API call #"
  | regexp "\\[(?P<session>local-[0-9a-f]+)\\].*provider=(?P<provider>[a-z0-9_-]+) in=(?P<input>[0-9]+)"
  | unwrap input [$__range]))
```

Read it together with output: input is mostly cache (cheap but still consumes
quota and time), output is what gets billed at full price. A session with 250M
input and almost no output is a chat re-reading its own history, not a chat that
produced a lot of work.

## Chat names instead of hashes

The agent log identifies a session as `local-<sha256>`, which is useless when
deciding where the money went. `state.db` has the title, so
`push_session_usage.py` reads it (read-only, WAL-safe) and publishes cumulative
per-session counters with the title attached:

| metric | meaning |
|---|---|
| `session_input_tokens` | prompt tokens for the life of the session |
| `session_output_tokens` | generated tokens |
| `session_cache_read_tokens` | cache reads |
| `session_api_calls` | API calls |

Stream `{service_name="finops", log_type="session_usage", job="hermes-sessions"}`,
windows limited to sessions active in the last 30 days, untitled sessions skipped.

**Titles are not unique.** `sum by (title)` merges separate chats that share a
name - one title covered 157M input across several sessions. Always group by
`(title, session)` so distinct chats stay distinct.

Cumulative counters, not windowed ones: the panel answers "which chat has cost
the most overall", while the Loki-derived per-session panel answers "which chat
is burning right now".

## LogQL trap: valid regex that never matches

In this stack two regex shapes parse fine and then return **zero rows** where
literal filters return plenty:

| shape | result |
|---|---|
| `\|= "provider=deepinfra" \|= "positive balance"` | 8 |
| `\|~ "402.*positive"` | 32 |
| `\|~ "(?i)402.*(positive balance\|insufficient balance)"` | 0 |
| `\| regexp "\\[(?P<s>local-[0-9a-f]{8})\\]"` | 0 |
| `\| regexp "\\[(?P<s>local-[0-9a-f]+)\\]"` | matches |

Rule: when a deployment-critical query returns no data, test it against a time
range where the event is known to exist, and fall back to literal `|=` filters or
`+` quantifiers. An alert built on a never-matching query looks healthy —
`NoData` with `no_data_state=OK` — and protects nothing.

## Access note

`GRAFANA_API_TOKEN` in this workspace is a `glc_` access-policy token: it writes
logs and reads metrics, but it is **not** valid for the Grafana HTTP API
(`/api/dashboards/db` returns 401). Dashboard edits therefore go through the
Grafana MCP server, which holds its own OAuth session.
