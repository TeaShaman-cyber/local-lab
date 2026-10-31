# Provider cost and limit telemetry

How to get money figures out of model providers into the same Prometheus that
carries host metrics, so "what did this cost" and "what was the machine doing"
sit on one timeline.

## The one rule that matters

Keep two classes of number apart and label them differently everywhere:

| Class | Source | Meaning |
|---|---|---|
| **FACT** | provider billing/account API | what the provider actually charged or still holds |
| **ESTIMATE** | our tokens x a price table | a model of the cost, never an invoice |

Merging them into one "spend" panel produces false precision the operator will
rightly distrust. If a provider has no billing API, say ESTIMATE out loud
instead of quietly presenting it as money.

## Pipeline shape

Provider API -> small script -> `.prom` file -> `windows_exporter` **textfile**
collector -> existing host scrape target -> remote_write.

No bespoke daemon, no scraping of vendor admin pages. The collector rides the
`prometheus.exporter.windows` component, so its metrics arrive with the host
target and need no extra `prometheus.scrape`.

```alloy
prometheus.exporter.windows "host" {
  enabled_collectors = ["cpu", "memory", "net", "process", "textfile"]

  textfile {
    directories = ["C:/Apps/theseus-metrics"]
  }
}
```

Schedule the writers (Windows, runs as the logged-on user so `PATH`/`npx` resolve):

```powershell
schtasks /Create /SC MINUTE /MO 5 /TN "Theseus Provider Metrics" `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Apps\theseus-metrics\provider-metrics.ps1" `
  /RU "$env:USERDOMAIN\$env:USERNAME" /F
```

One wrapper script calls every provider collector, each in its own `try`, so a
single failing provider cannot stop the others.

### When the host already ships Loki

If the box already pushes logs to Grafana Cloud, skip the `.prom` file entirely:
push one `logfmt` line per metric into a dedicated stream
(`{service_name="<provider>", log_type="billing"}`) and query it with
`| logfmt | metric="…" | unwrap value`. No Alloy edit, no reload, no textfile
collector — the collector script just needs the Loki push credentials already in
`loki.write`.

Whichever transport you pick, two things are mandatory and shared:

- a `<field>_known` gauge beside every value the API can omit, so a missing
  number renders as unknown instead of `0`;
- a `read_ok` sample (plus `last_success_timestamp_seconds`) so a stale
  dashboard looks stale instead of looking like a real zero balance.

## Writing the .prom file

- **A trailing newline is mandatory.** Without it the collector fails that file
  with `text format parsing error in line N: unexpected end of input stream`
  while *other* files keep working, so the metric silently never appears and
  nothing else looks wrong. Always check the failure in the Alloy log before
  debugging the query.
- Write to `file.prom.tmp`, then `Move-Item`/`Path.replace()` — never let the
  collector read a half-written file.
- No `# HELP`/`# TYPE` is tolerated, but include them: an untyped metric is easy
to misread later.
- Verify at the sink, not at the file: `topk(5, <provider>_<metric>)` through
  `query_prometheus`.

## RunInfra

```bash
npx -y @runinfra/cli@latest account --json
```

Useful fields (all inside `account`):

- `credits.balance_cents`, `credits.available_cents`, `credits.period.spent_cents`,
  `credits.period.start`
- `spend_cap.limit_cents` / `used_cents` / `gates_inference`
- `frozen.frozen`
- `limits.hostedConcurrencyCap`, `hostedTpmCap`, `rpmCap`, `chatTurnToolSpendCapCents`

There is **no `usage` command** (the key scope `usage:read` exists, the CLI
command does not) — `account` is the only money source, and it is
account-level, not per-model.

Key comes from the host secret store (`RUNINFRA_API_KEY`), never from argv.

## BotHub (bothub.chat)

Do not confuse it with **bothub.center**, an unrelated OSINT data-leak-search
vendor that also publishes an API with per-method credit costs. Same word,
different company; fetching the wrong docs wastes a whole cycle.

Token: the access token from the panel, sent as
`Authorization: Bearer <token>`. Two endpoints matter:

| Endpoint | Returns |
|---|---|
| `GET /api/v2/auth/me` | `subscription.balance`, `subscription.availableBalance` (caps), `subscription.plan {type, tokens, price, currency}` |
| `GET /api/v2/transaction/usage-report?from=…&to=…` | xlsx, **one row per request**: `CAPS`, `model_id`, `Prompt tokens`, `Cached Tokens`, `Cache Creation Tokens`, `Completion Tokens`, `Provider` |

So caps are **not** cabinet-only: balance comes from the account endpoint and
per-model spend from the usage report. Only ask the operator for a token that
does not exist yet.

Date parameters must be `YYYY-MM-DDTHH:MM:SSZ`. An ISO string with a `+00:00`
offset is rejected with `400 Bad Request`.

The account endpoints sit under an **`auth` prefix the inference base URL does
not reveal**: `/api/v2/auth/me` answers, while `/api/v2/me`, `/api/v2/user`,
`/api/v2/balance`, `/api/v2/usage` are 404. Probing the obvious short paths and
declaring "no balance API" is the standard mistake — the balance is right there
once the prefix is right.

**A 401 from one consumer does not mean the key is dead.** When a direct call
with the same key answers `200` and a consumer still logs `401 UNAUTHORIZED`,
compare the key's hash inside the consumer's environment with the host's before
touching credentials: identical hashes mean the consumer's state is wrong (stale
process after a rotation, a different variable substituted), and rotating the key
would break the caller that works.

### Parsing the xlsx without dependencies

An xlsx is a zip of XML; the standard library is enough and keeps the scheduled
task dependency-free:

1. `xl/sharedStrings.xml` -> list of strings (`<si>` -> concatenated `<t>`).
2. First sheet under `xl/worksheets/` -> rows; a cell with `t="s"` indexes into
   the shared strings, otherwise the `<v>` is the literal.
3. Column letter -> index: `n = n*26 + (ord(ch.upper()) - 64)`, minus 1.
4. Row 1 is the header; map every later row to `{header: value}`.

Aggregate into `Counter` keyed by `model_id` and emit gauges over the window:
**the window is the truth** — a "total" with no `from`/`to` stated is a bug.

## DeepInfra

Two read-only endpoints carry everything:

| Endpoint | Returns |
|---|---|
| `GET /payment/checklist?compute_owed=true` | `stripe_balance`, `limit`, `suspended`, `overdue_invoices`, `recent` |
| `GET /payment/usage?from=current` | `months[].items[]`: `model.model_name`, `pricing_type`, `units`, `rate`, `cost` |

`cost` and `rate` are in **cents** (rate per token). `suspended` is the fact that
matters most: it is `1` exactly when inference is refused, and no balance
arithmetic predicts it. `stripe_balance` is a payment-ledger number, not the
spendable remainder — negative does not mean "no money left" and zero does not
mean "empty". Derive nothing from it; read `suspended` and the per-model `cost`.

## Nous Portal

Remaining credits are reachable through the agent's own account helper — no
inference call, no cookie harvesting, no guessing at private endpoints:

```python
from hermes_cli.nous_account import get_nous_portal_account_info
a = get_nous_portal_account_info(force_fresh=True)
a.paid_service_access_info.purchased_credits_remaining
a.paid_service_access_info.subscription_credits_remaining
a.paid_service_access_info.total_usable_credits
```

Run it with the agent's **own** interpreter; a bare system Python may lack the
modules that package imports, and the failure surfaces as an import error that
looks like "the API is unavailable".

What it does **not** give: per-period spend history, and numeric included
allowances for paid plans — a free account's response has no usage window, so it
cannot answer a paid-tier question. Emit a `*_known` flag beside every credit
gauge and omit the value when it is `None`: a missing balance rendered as `0` is
the one lie that costs real money.

Subscription credits and pay-as-you-go credits are separate purses, and API-key
traffic spends only the PAYG one. Do not sum them before checking which purse
the workload actually drains.

**The refusal phrase is the alert key.** Nous returns `out of funds` for both an
empty balance and a blocked key, so one literal `|= "out of funds"` filter
catches both. It is event-based: it can only appear when a request actually left
the host, so it never pages on a sleeping laptop. Each provider has its own
phrase (DeepInfra says `positive balance`) — grep the client's error string, not
a status code, and confirm the phrase against a real refusal before trusting the
rule.

## Timeweb Cloud

Finances live on the **singular** account path:

| Endpoint | Returns |
|---|---|
| `GET https://api.timeweb.cloud/api/v1/account/finances` | `balance`, `total_balance`, `monthly_cost`, `hourly_cost`, `hours_left`, `total_paid`, `currency` |

`/api/v1/accounts/finances` (plural) is a dead end, and
`/account/finances/summary`, `/expenses`, `/payments` return 404. Key comes from
`TW_API_KEY` (environment, then the agent's `.env`), never from argv.

**Rubles stay rubles.** Publish `currencyRUB` and never convert inside a panel: a
converted figure depends on the exchange rate at render time and invites a false
comparison against dollar-priced inference providers.

**Infrastructure and AI share one wallet, and `/account/services/cost` splits
them** — this is the endpoint to reach for, not the inference host:

| Endpoint | Returns |
|---|---|
| `GET https://api.timeweb.cloud/api/v1/account/services/cost` | `services_costs[]`: `type`, `total_cost`, `info.name`, `services[]` |

`type: "ai_proxy_key"` entries are AI; every other type (`s3`,
`s3_outgoing_traffic`, servers…) is infrastructure. Each AI entry carries
`services[]` with `type` in `token_input` / `token_cache_read` / `token_output`
plus `cost`, `amount` and `model_id` — a per-model, per-token-kind breakdown in
rubles.

- **Read `total_cost`, never `cost`.** For `ai_proxy_key`, `cost` reads `0`
  while the real figure sits in `total_cost`; taking `cost` reports free AI.
- **It is a period figure, not a delta.** Latest sample only — `sum_over_time`
  or adding samples multiplies the bill by the scrape count. In a panel:
  `last_over_time(...[$__range])`.
- Still never publish `delta(balance) - monthly_cost` as "AI spend": a balance
  drop mixes infra burn, AI spend and top-ups. Use `services/cost` for the split
  and keep the delta only as a cross-check.

The inference host `api.timeweb.ai/v1` has no `/usage`, `/balance`, `/tokens` —
all 404 — which says nothing about the Cloud API. Per-key daily limits there are
console-side guardrails, not reporting.

Timeweb also emits billing webhook events (`low_balance_info` with `hours_left`,
`money_transfer`, `auto_payment_success`/`_error`, `account_blocked`,
`data_will_be_deleted_soon`) — better than polling when a publicly reachable
receiver exists; state the requirement instead of polling forever by default.

## Per-provider usage from the agent's own logs

When a provider has no money API, its traffic is still fully described in the
agent log line:

```
API call #1: model=M provider=P in=112425 out=205 total=112630 latency=5.1s cache=101376/112425 (99%)
```

LogQL turns that into per-provider figures with no collector at all:

```logql
sum by (provider) (sum_over_time({service_name="hermes", log_type="agent"}
  |= "API call #" | regexp "provider=(?P<provider>[a-zA-Z0-9_-]+) in=(?P<input>[0-9]+)"
  | unwrap input [$__range]))
```

- `in` **includes** cached tokens; never add `in` and `cache` together.
- For a costing expression use `|= "provider=<value> in="` as a cheap line
  filter instead of a full regexp — `out=` sits on the same line, so one filter
  serves both halves of the sum.
- Labels are the *config* names, not brand names: a custom OpenAI-compatible
  endpoint appears as `custom`, and a ChatGPT-subscription route as
  `openai-codex`. Map them before labelling a panel, or the dashboard silently
  attributes money to the wrong vendor. A subscription route belongs in a token
  panel only — it spends no dollars, just a rolling time-window quota, and that
  quota can be exhausted by a couple of dozen large-context turns.

### Exact cost from the per-call cache split

The line already carries the cached share (`cache=101376/112425`), so cost is
determined rather than bounded:

```
cost = (in - cached) * price_in + cached * price_cache + out * price_out
```

Compute it with a server-side math expression over three log targets (input,
cached, output) and mark those three `hide: true` — otherwise the stat draws the
raw token counts in currency units beside the answer. Verify by rendering the
panel: every query can be individually correct while the panel shows four
numbers, the three helpers included.

Where the formula degrades, and say so next to the panel:

- a provider label spanning several prices averages them with no error bar —
  group by `model` when the logs allow it;
- a stale price table makes the panel quietly wrong, so keep the token queries
  one click away instead of deleting them;
- lines without a `cache=` field fall to the uncached side, which overestimates —
  the safe direction, but state it.

Keep the bounding form only when `cache=` is genuinely absent for the whole
window: **lower** = all input at cache price, **upper** = all input at full
price. A mid-point presented as the bill is worse than the interval, because the
operator cannot tell it apart from a measurement.

### Per-session attribution, not just per-provider

A provider total hides what actually drains a quota: one chat carrying a huge
context. The same `API call #` rows group by the session id that prefixes the log
line:

```logql
sum by (session) (sum_over_time({service_name="hermes", log_type="agent"}
  |= "API call #"
  | regexp "\\[(?P<session>local-[0-9a-f]+)\\].*in=(?P<input>[0-9]+)"
  | unwrap input [$__range]))
```

Read input together with output: input is mostly cache (cheap per token, still
consumes quota and wall-clock time), output is what bills at full price. A
session with enormous input and almost no output is a chat re-reading its own
history — which is how a rolling time-window quota disappears in twenty minutes
while every provider-level panel looks normal.

Watch the quantifier: `local-[0-9a-f]+` matches, `local-[0-9a-f]{8}` returned
zero rows in this stack. See the regexp pitfall in SKILL.md.

### Session names, and two counters called "input"

The log carries only `local-<sha256>`, which is useless when deciding where the
money went. The agent's own database knows the title (`sessions.title`), so a
small collector can join them and publish cumulative counters labelled with the
name. Read that database **read-only** — `sqlite3.connect("file:…?mode=ro",
uri=True)` — because the live file runs in WAL mode and a scheduler must never
open it for writing.

Titles are **not unique**. One title covered several separate chats, and
`sum by (title)` merged them into a single 157M-token bar. Group by
`(title, session)` and use the title only for the legend.

Two counters both called "input" measure different things, and mixing them
produces contradictory answers to the same question:

| Source | "input" means | Cache |
|---|---|---|
| agent log line (Loki) | all prompt tokens | own `cache=` share, **included** in `in` |
| agent database (`sessions.input_tokens`) | prompt tokens **excluding** cache | own `cache_read_tokens` column |

The same chat was the top consumer at 252M in one view and 15M in the other.
Both were correct. Label each panel for what it actually shows ("input without
cache") or the two views will be read as a contradiction.

## When the provider exposes nothing: read the consumer's trace

A local service that fronts a paid provider records its own calls. Hindsight
exposes them at `GET /v1/default/banks/<bank>/llm-requests` — the
`/default/banks/` segment is mandatory, and the shorter `/v1/<bank>/llm-requests`
is a plain 404 that looks like the endpoint does not exist. Each row carries
`provider`, `model`, `operation`, `status`, `input_tokens`, `output_tokens`,
`cached_tokens` and a call id.

That turns a provider with no usage API into a metered one, but only if you
respect two things:

- **The endpoint returns history, not a delta.** Pushing a running total every N
  minutes double counts the moment anything sums over time. Keep a cursor of the
  newest `started_at` already published, select only rows strictly newer, and
  write the cursor back after a successful push. Make the first run a baseline
  that adopts the newest row and publishes nothing, so a fresh install does not
  dump months of history into the sink.
- **Skip the free tiers.** Local embedding and reranker rows (`provider=local`)
  cost nothing and swamp the signal; filter them out in the collector, not in the
  panel.

Emit one line per call carrying its call id, so a re-run is a verifiable no-op
and a duplicate is visible instead of silently folded into a total. The call id is
also the join key back to the application's own trace when a number is disputed.

### A failed operation is not a free one

A 504, 500 or timeout at the orchestration layer says nothing about what the
provider already billed. A `reflect` that ended in a 300-second timeout had
already completed two provider calls — tens of thousands of input tokens — before
it gave up and returned no answer; the provider charged for both.

Consequences for costing:

- When an operation fails, count the calls it made before concluding it was
  cheap. The trace, not the response code, is the record of what was spent.
- Cost alarms and caps must key on **completed provider calls**, not on our
  success. A run that fails after doing the expensive part is exactly the run a
  latency- or error-based dashboard will under-report.
- "The request failed" is never a synonym for "nothing was spent", and saying so
  makes an expensive bug look free.

## Reading the numbers

A per-model spend breakdown finds routing failures that latency graphs hide: if
the share-of-spend is ~all on the single paid member while the pool looks
healthy, the paid fallback is carrying the traffic (a primary that fails fast
still routes every request to the fallback). Compare shares of *requests* with
shares of *money* — with weighted routing they differ by design, and the money
follows the paid member only.

Report caps-per-request alongside the total; it is the number that tells you
whether a model is simply expensive or being called far too often.
