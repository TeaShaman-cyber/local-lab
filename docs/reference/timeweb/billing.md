# Timeweb Cloud Billing

## How to act

All reads go through the Timeweb Cloud MCP (`timewebCloud`):
`search_tools` → `get_tool_definition` → `execute_tool`. Everything in this skill
is read-only — no confirmations needed, but also no way to move money.

## Tool map (namespace `account` unless noted)

- `get_account_finances` — balance, monthly charge estimate, how long the balance
  lasts, deferred payment state. **Primary tool for "сколько у меня денег".**
  Field notes: report `days_left` (there is also `hours_left` — same thing) and
  `monthly_cost`; `*_fee` fields duplicate the `*_cost` ones, `total_balance` is
  `balance` with fractions — don't present both. **`autopay_card_info: null`
  means no card is linked**, so nothing will save the account automatically —
  say so when the balance is short.
- `get_account_services_costs` — every active service with its monthly cost plus
  **`summary_by_type`** (per-type subtotals) and the grand total. **Primary tool
  for "что жрёт деньги".** It takes optional arguments the response depends on:
  `group_by` (`"type"` — default, adds `summary_by_type`; `"project"` — adds
  `summary_by_project`; `"none"` — services only) and `project_id` to narrow to
  one project. **Read `summary_by_type` first instead of aggregating the list
  yourself** — on a large account the services array is hundreds of entries and
  tens of kilobytes, so a client may truncate it; if that happens, use
  `project_id` to work project by project.
- `get_account_status` — blocking flags, prime flag, customer type (person/org),
  region, 2FA method, registration date. Use when charges are fine but something
  is off with the account itself.
- `get_account_auth_access` — country/IP login allowlists (security, not money).
- `get_cloud_service_catalog` (namespace `catalog`) — current prices for NEW
  services: VDS configs, DBMS/K8S/S3/balancer presets, AI models with token
  packages. **The only valid source for "сколько будет стоить X".**
- `get_service_details` (namespace `catalog`) — parameters of one existing service.

## Same numbers without the MCP: REST API

When the `timewebCloud` namespace is not registered in the current session, the
account answers read-only over REST with the API key the host already holds
(`TW_API_KEY`, in the environment or the agent's `.env`):

```bash
curl -s -H "Authorization: Bearer $TW_API_KEY" \
  https://api.timeweb.cloud/api/v1/account/finances
```

Path is **singular `account`** — `/api/v1/accounts/finances` and
`/account/finances/summary|expenses|payments` are 404. Response carries
`balance`, `total_balance`, `monthly_cost`, `hourly_cost`, `hours_left`,
`total_paid`, `currency` and `autopay_card_info`. It is the same payload
`get_account_finances` returns, so the reporting rules below apply unchanged.

The per-service breakdown has its own path — this is the REST twin of
`get_account_services_costs` and the one place AI and infrastructure separate:

```bash
curl -s -H "Authorization: Bearer $TW_API_KEY" \
  https://api.timeweb.cloud/api/v1/account/services/cost
```

`services_costs[]` entries look like `{type, total_cost, info:{name}, services:[]}`.
`type: "ai_proxy_key"` is AI spend; every other type (`s3`,
`s3_outgoing_traffic`, servers, …) is infrastructure. Each `ai_proxy_key`
carries `services[]` with `type` in `token_input` / `token_cache_read` /
`token_output`, plus `cost`, `amount` and `model_id`.

Three traps in that payload:

- **`total_cost`, never `cost`.** For `ai_proxy_key`, `cost` reads `0` while the
  real figure sits in `total_cost`. Taking `cost` silently reports free AI.
- **It is a period figure, not a delta.** Never `sum_over_time` it or add
  consecutive samples; use the latest sample only. Otherwise a dashboard
  multiplies the bill by the number of scrapes.
- `meta.total` counts services, not money.

`api.timeweb.ai/v1` (the OpenAI-compatible inference host) still has no
`/usage`, `/balance`, `/tokens` — all 404 — and the Cloud API has no `ai/*`
routes. Per-key daily limits exist but are console-side guardrails, not a
reporting API. Splitting AI from infrastructure therefore goes through
`services/cost`, not through the inference endpoint.

## Decision guide

| User intent | Do this |
|---|---|
| "Сколько на балансе / на сколько хватит" | `get_account_finances`; report balance, monthly burn, days left |
| "Почему так много списывается / что дорогое" | `get_account_services_costs` → start from `summary_by_type`, then name the priciest individual services. Scale the answer to the account: with a handful of services the top few explain everything; with dozens, a per-type breakdown plus a note on "small things that add up" (many IPs, balancers) is the useful shape — a bare top-3 can cover a small share of the bill and mislead |
| "Что тратит проект X" | `get_account_services_costs` with `project_id`, or `group_by: "project"` for the whole picture |
| "Сколько стоит поднять сервер / агента / базу" | `get_cloud_service_catalog` with the right service type; quote exact figures |
| "Аккаунт заблокирован? Почему не могу оплатить" | `get_account_status` for block flags, then `get_account_finances` for debt |
| "Дорого, хочу дешевле" | Costs from `get_account_services_costs` + alternatives from the catalog; propose, don't execute — resizing/deleting is panel-only |
| "Дай отсрочку / обещанный платёж" | `get_account_finances` for deferred-payment state; explain the conditions (short window, cap, once a month) and send to the panel to activate |
| "Нужны закрывающие документы / счёт" | Panel only; closing documents exist only for invoice payments (see how-billing-works.md) |

## Worked example: "на сколько мне хватит денег и что жрёт больше всего?"

1. `execute_tool("get_account_finances", {})` → balance, monthly burn estimate,
   days left, deferred-payment state, and whether a card is linked.
2. `execute_tool("get_account_services_costs", {})` → read `summary_by_type` for
   the shape of the spending, then pick the priciest individual services from
   the list.
3. To the user: «На балансе N ₽, при текущем наборе услуг этого хватит примерно
   на M дней. Больше всего стоят: сервер X (A ₽/мес), база Y (B ₽/мес), агент Z.
   Хотите, разберу, можно ли что-то из этого удешевить?» — all figures verbatim
   from the tools, currency named explicitly.

Why charges may not match the monthly estimate — subscriptions vs hourly token
debits vs per-copy backup storage: see
[how-billing-works.md](billing/how-billing-works.md).

## Not available through MCP — send to the panel

- Topping up the balance, cards, autopay, invoices, closing documents, promo
  codes, activating deferred payment.
- Refunds and moving money between accounts — support-ticket procedures.
- Deleting or downgrading services to cut costs (no delete; resize is panel-only
  and upward-only).

Say it plainly and point to the panel — for money matters that is
https://timeweb.cloud/my/finances ("Баланс и платежи": top-up, cards, invoices,
per-debit detail). Don't hunt for workarounds.

## Reporting rules

- All amounts are in rubles; say the currency explicitly.
- Monthly cost vs. actual charges can differ (hourly billing, mid-month changes,
  token packages bought on top). If the user disputes a specific charge you cannot
  explain from these tools, recommend support — don't guess.
- When the balance won't cover the next month, say so proactively while answering.
- **Infrastructure and AI share one wallet, and `get_account_services_costs`
  (REST: `/account/services/cost`) does split them** — `ai_proxy_key` entries
  are AI, everything else is infrastructure. Report the two sides separately
  when AI is in use. Do not reach for a balance delta instead: a drop mixes
  service burn, AI spend and top-ups, so `balance_delta - monthly_cost` is not
  "сколько ушло на AI". Absence of `/usage` on `api.timeweb.ai` proves nothing
  about the Cloud API — check `services/cost` before telling the user a split
  is impossible.
- Currency stays native. Report rubles as rubles; do not convert to another
  currency inside a report or a dashboard panel — the figure would then depend on
  the rate at reading time.

## Anti-patterns (NEVER)

- NEVER state a price from memory or from training data — catalog only, every time.
- NEVER promise refunds, discounts, or write-offs — you cannot do them and support
  decides them.
- NEVER speculate about "double charging" or bugs from a single number — check
  `get_account_services_costs` totals first, then defer to support.
