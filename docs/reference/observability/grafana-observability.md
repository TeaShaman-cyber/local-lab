# Grafana Cloud observability

Standing up host/service monitoring in Grafana Cloud (free tier) and building
dashboards over it. The whole point of this skill: **every value you need is
reachable from an API or MCP call — do not route the user through a vendor
admin panel to read numbers off the screen.**

## Non-negotiable user rules

- **Extract credentials yourself.** Ask the user only for what cannot be
  obtained programmatically (a secret that has not been created yet). "Open
  My Account → Stack → Details and copy two fields" is a failure, not a step.
- **Secrets never go into chat or Git.** They live in the host's canonical
  secret store (for Hermes: `~/.hermes/.env`) or the service's own runtime
  file outside the repo. The repo carries placeholders only.
- **Create the issue before changing configuration** in a lab that has an
  issue-first contract, and record measured results, not intentions.
- **Report in the user's language, plain, with measured numbers.** No jargon
  dumps; one table of what is true right now.

## Procedure

### 1. Confirm the plan tier costs nothing

`Cloud Free` = permanent ("Always" badge), 10k active series, 14 days retention,
no card. Account creation starts a **14-day Pro trial** that only lifts the
limits; on expiry the account drops back to Free automatically. Do not let the
user buy Pro to "keep" the free tier.

### 2. Get the endpoint and instance ID from the datasource, not the UI

If the Grafana MCP server is connected:

```
mcp__grafana__get_datasource(uid="grafanacloud-prom")
```

Returns:

- `url` — e.g. `https://prometheus-prod-NN-prod-<region>.grafana.net/api/prom`
- `basicAuthUser` — the **metrics instance ID** (a number, not a secret)
- `secureJsonFields.basicAuthPassword: true` — proves a password exists but is not returned (correct)

Remote-write endpoint = the same host, `/api/prom/push`.
Find the stack name from any datasource name (`grafanacloud-<stack>-prom`) or
`list_datasources`; the stack URL is `https://<stack>.grafana.net`.

### 3. Validate the write token with a probe, and read the status correctly

POST a tiny body to the push endpoint with Basic auth `instanceID:token`.

| Response | Meaning |
|---|---|
| `401` | the token really is wrong for this endpoint |
| `400 decompress snappy: snappy: corrupt input` | **auth PASSED**; only the body format is wrong (remote write wants snappy+protobuf) |
| `2xx` | accepted |

A token can be valid for remote write and simultaneously invalid for the
Grafana HTTP API (`/api/datasources` → `401 Invalid API key`). Those are
different credential systems — access-policy tokens vs service-account tokens.
**Never conclude "the token is bad" from one endpoint's 401.**

If the token is already in the host secret store, use it — do not ask for a
new one before probing.

### 4. Install and configure the collector

```bash
winget install --id GrafanaLabs.Alloy --accept-package-agreements --accept-source-agreements
```

Alloy embeds `windows_exporter` — one service, no second binary. On Windows the
service is `Alloy`, start type Automatic, config at
`C:\Program Files\GrafanaLabs\Alloy\config.alloy`.

Minimal working shape:

```alloy
prometheus.exporter.windows "host" {
  enabled_collectors = ["cpu", "logical_disk", "memory", "net", "os", "system", "process"]
  process {
    include = "^(Hermes|python|node|Code|chrome|opera|Docker Desktop|vmmemWSL|powershell|svchost)$"
    exclude = "^$"
  }
}

prometheus.scrape "host" {
  targets         = prometheus.exporter.windows.host.targets
  forward_to      = [prometheus.remote_write.cloud.receiver]
  scrape_interval = "30s"
}

prometheus.remote_write "cloud" {
  endpoint {
    url = "https://prometheus-prod-NN-prod-<region>.grafana.net/api/prom/push"
    basic_auth {
      username = "<instance-id>"
      password = "<token>"
    }
  }
  external_labels = { host = "<name>" }
}
```

Always validate syntax before deploying:

```bash
"C:/Program Files/GrafanaLabs/Alloy/alloy-windows-amd64.exe" fmt "C:/path/config.alloy" >/dev/null
```

### 5. Deploy a config into Program Files (needs elevation)

Write a `.ps1` containing the `Copy-Item` + `Restart-Service`, then elevate the
script (one UAC prompt the user approves):

```powershell
Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\path\deploy.ps1'
```

### 6. Verify with a read-back, never with service status

```
mcp__grafana__query_prometheus(datasourceUid="grafanacloud-prom", expr='count({__name__=~".+"})', queryType="instant", endTime="now")
```

`[]` means nothing arrived, regardless of "service Running". Then prove the
data is *useful*, e.g. `topk(6, rate(windows_process_cpu_time_total[3m]))`.

### 7. Build the dashboard

`mcp__grafana__update_dashboard` with `uid`, `title`, `panels`, `overwrite: true`;
it returns the dashboard URL. Then **query each panel expression once** — a
dashboard whose panels are empty is not a deliverable.

Verified expressions (windows_exporter):

| Panel | PromQL |
|---|---|
| CPU busy % | `100 - (avg(rate(windows_cpu_time_total{mode="idle"}[5m])) * 100)` |
| Memory used % | `100 * (1 - (windows_memory_available_bytes / windows_memory_physical_total_bytes))` |
| Top processes by CPU (cores) | `topk(8, sum by (process) (rate(windows_process_cpu_time_total[5m])))` |
| Disk free | `windows_logical_disk_free_bytes` (label `volume`) |
| Network | `rate(windows_net_bytes_received_total[5m])`, `..._sent_total` (label `nic`) |

### 8. Logs to Loki

The Loki datasource answers the same question as Prometheus, with a **different
credential identity**:

```
mcp__grafana__get_datasource(uid="grafanacloud-logs")
```

Returns `url` (e.g. `https://logs-prod-NNN.grafana.net`) and `basicAuthUser` —
which is **not** the metrics instance ID. Push endpoint = `<url>/loki/api/v1/push`,
Basic auth `<loki-user-id>:<token>`, body
`{"streams":[{"stream":{...labels},"values":[["<ns-epoch>","<line>"]]}]}`.
`204` means accepted.

In Alloy, file tails are `local.file_match` (one `path_targets` entry per file,
carrying your own `log_type` label) feeding `loki.source.file`, then
`loki.write` with an `endpoint { url, basic_auth }` block and `external_labels`.
Add any new file to the `path_targets` list; the scrape target is the match
component's `.targets`.

Verify by counting per label, not by eyeballing one line:

```
mcp__grafana__query_loki_logs(datasourceUid="grafanacloud-logs",
  logql='sum by (log_type) (count_over_time({job="hermes"}[1h]))',
  queryType="instant", endRfc3339="now")
```

Logs the agent file already carries per model call — model, tokens in/out/cached,
latency — can be turned into Prometheus metrics with a `loki.process` metrics
stage, which is how LLM telemetry and host telemetry end up on one timeline.
Those metrics are exposed on Alloy's own `/metrics` endpoint; scrape Alloy
itself (`127.0.0.1:12345`) or they never leave the host.

### 9. Redact secrets in-flight, then alert on the marker

Any stream that can carry a credential must be redacted **on the host** before
shipping. A persisted log is the artefact that gets bought and grepped later, so
alerting after the fact is already too late: replace key-shaped strings with a
fixed marker, and let only the marker travel. Then alert on the marker — the
value never leaves, the attempt still surfaces.

Prove redaction with a canary file, and prove it by **absence**: write a fake key
into a dedicated canary stream, confirm the line reads `[REDACTED]`, then search
the whole stream for the literal value and require zero hits.

Patterns, the `stage.metrics` recipe and the alert-rule shape:
`grafana-observability/log-pipelines-and-alerting.md`.

## Alert policy: events, never silence

A host that is switched off at night, at weekends, or while its owner is at work
is not a fault. Any rule whose condition is "no data arrived" measures the
owner's schedule and will page on every good night.

- **Alert only on events that cannot happen while the host sleeps**: a request
  that left and was refused (`402`, insufficient balance), a backup that started
  and failed, a restore that failed, a secret that reached the log. Each requires
  the machine to have been awake and working.
- **Silence belongs on the dashboard.** Expose `read_ok` and
  `last_success_timestamp_seconds`; the operator reads staleness when they sit
  down instead of being paged for having slept.
- **Persistent known states do not belong in email.** An account suspended for
  weeks re-notifies every repeat interval about a fact already known. Pause the
  rule, keep the state on a panel, and write in the description when to unpause.
- **Long-absence guards get a weekend-sized window.** A 26h "no successful
  backup" rule fires every Sunday; 72h tolerates a weekend and still catches a
  genuinely dead schedule. Pair it with an event rule (`status=fail`) so a real
  failure is reported the moment it happens rather than only by absence.
- **Renaming a metric silently kills its alert.** A rule still querying the old
  name evaluates as `NoData`, and with `no_data_state: OK` it reads healthy
  forever. Grep every alert expression for any metric name you change.
- **A rule that has never fired is CONFIGURED, not VERIFIED.** A freshly created
  alert is a hypothesis about the log shape, not proof of it — the filter may
  match nothing and read healthy forever. Report it as CONFIGURED and say it
  becomes VERIFIED only on the first real firing; do not present a never-fired
  rule as working protection.

## Pitfalls

- **`discovery.relabel` `labeldrop` does not drop metric labels** — it relabels
  *target* labels only. Dropping `process_id` there silently does nothing, so
  series are keyed per PID and grow on every process restart until the free
  tier's 10k cap is hit. Use `write_relabel_config` inside the
  `prometheus.remote_write` `endpoint` block instead.
- **Native Windows tools cannot read MSYS paths.** `alloy.exe`, `git.exe`, and
  PowerShell fail on `/c/Users/...`; pass `C:/Users/...`.
- **The Grafana *instance* UI needs a separate sign-in** from the `grafana.com`
  account, so an authenticated portal session still shows
  `Welcome to Grafana Cloud → Sign in with Grafana.com`. Do not read that as
  "the user is not logged in".
- **Do not force-install a skill/plugin past a security scan.** If the scan
  blocks it, read the content, note the blocker, move on.
- **Check the series budget early.** `count({__name__=~".+"})` right after the
  first scrape tells you whether cardinality is safe before it becomes urgent.
- **Alloy does not parse heredocs.** `config = <<-YAML … YAML` fails
  `alloy fmt` with `expected expression, got <`. Put the YAML in a real file
  beside the config and point at it with `config_file = "C:/.../blackbox.yml"`.
  Always run `alloy fmt` *before* the elevated deploy: a parse error stops the
  whole service, so every signal dies, not just the new one.
- **`cs` is no longer a `windows_exporter` collector.** Listing it makes Alloy
  log `the 'cs' collector is removed` and `windows_cs_*` stays at zero series.
  Its data now lives in `os`, `memory` and `cpu`.
- **`windows_cpu_core_frequency_mhz` is the nominal clock, not the live one.**
  It returns a constant (max−min over 30 min = 0), so boost and throttling
  cannot be derived from it. Real clock and per-core temperature need an
  MSR-reading source — see the collectors reference.
- **Enumerate metric families with `count by (__name__)`.**
  `count by (__name__) ({__name__=~"windows_memory.*"})` returns every matching
  family with its series count in one instant query — cheaper and more complete
  than the paginated `list_prometheus_metric_names`, which silently truncates.
- **Loki and Prometheus use different basic-auth user IDs** on the same stack,
  and one access-policy token can be valid for both. Take each from its own
  datasource; do not reuse the metrics instance ID for the log push.
- **The Loki query tool takes `startRfc3339` / `endRfc3339`,** not `endTime`
  (that spelling belongs to `query_prometheus`). Passing `endTime` fails
  argument validation before the query runs.
- **A redaction test that only checks for `[REDACTED]` proves nothing.** The
  marker can appear while the value also still appears further along the line.
  The pass condition is a literal search for the secret returning zero hits.
- **Loki alert rules cannot be created datasource-managed on Grafana Cloud**
  (`no compatible … ruler datasource found`); use Grafana-managed, in a real
  folder (root is rejected), with a `reduce` step between the instant log query
  and the threshold, or evaluation fails with `only reduced data can be alerted on`.
- **A fresh alert rule reads `state: unknown` / `last_evaluation: null`.** That
  is not-yet-evaluated, not broken; wait one group interval before judging it.
- **Delivery is decided per rule, and the global receiver can be `empty`.**
  A rule created without `notification_settings.receiver` inherits the global
  notification policy — and a stack whose policy points at a receiver literally
  named `empty` will evaluate the rule, show `firing`, and page nobody. Check
  delivery by reading the rules back (`GET /api/v1/provisioning/alert-rules`)
  and confirming a receiver on each one; a contact point existing in the stack
  proves nothing about routing. Fix the global policy once so new rules inherit
  it, **and** leave explicit receivers on the existing rules as a second layer.
- **Changing the notification policy needs the instance UI, not the API.**
  `PUT /api/v1/provisioning/policies` answers `403 … alert.provisioning:write`
  for the Grafana MCP OAuth token, and a `glc_` access-policy token cannot grant
  itself scopes. Edit it at `<stack>.grafana.net/alerting/routes` (More → Edit →
  Default contact point → Update policy), then verify by reading
  `/api/v1/provisioning/policies` back — the selected value of a `<select>` does
  not appear in extracted page text, so the page will keep looking unchanged.
  Per-rule `notification_settings` *is* writable through `alerting_rules_write`.
- **`update_dashboard` has two modes — never mix them.** `dashboard` alone means
  "new or full replacement"; `uid` + `operations` means "patch". Sending
  `dashboard` alongside `uid`/`operations` is read as a replacement and fails with
  `Dashboard spec is missing required title property`.
- **A threshold line is invisible until you ask for the style.**
  `fieldConfig.defaults.thresholds` only colors values. For a visible reference
  line add `fieldConfig.defaults.custom.thresholdsStyle.mode = "dashed"` and pin
  `min`/`max`, or the axis auto-scales around current data and the threshold never
  appears in frame.
- **An empty panel is not automatically a broken query.** `histogram_quantile`
  over `rate(...[5m])` returns `NaN` whenever no request occurred in the window.
  Generate one real request and re-query before hunting for a bug.
- **`format: "table"` breaks reduction panels.** A bargauge/stat whose target is
  an instant vector must stay a plain instant vector; adding `"format": "table"`
  makes the reducer see a table shape and render `0` or `1` while the metric holds
  millions of series. Drop `format`, keep `instant: true`, and set
  `options.reduceOptions.calcs = ["lastNotNull"]`.
- **A saved dashboard is not a working dashboard — render it.** Panel JSON can be
  valid, the expression can return data, and the panel can still draw nothing or
  the wrong thing. Fetch the rendered panel and look at it:
  `mcp__grafana__get_panel_image(dashboardUid=..., panelId=...)`, then inspect the
  image. Do this after any panel change: values in the legend are the proof that
  the query and the reducer agree.
- **A config append Alloy already owns does not need a UAC restart.** When the
  `config.alloy` file is writable by the current account, append the block and
  `curl -X POST http://127.0.0.1:12345/-/reload` (expect `200`), then confirm the
  new component via `/api/v0/web/components`. Reserve the elevated
  `Restart-Service` path for when the file itself is not writable.
- **A `glc_` access-policy token cannot write dashboards.** `POST /api/dashboards/db`
  answers `401 Unauthorized` even while that same token happily writes logs and
  reads metrics. Create and patch dashboards through `update_dashboard` on the
  Grafana MCP server, which carries its own OAuth session — do not burn a cycle
  building a curl path out of an env token found on the host.
- **`update_dashboard` patch paths take array indices, not JSONPath.**
  `$.panels[?(@.id==25)].targets[0].expr` is rejected (`JSONPath filter
  expressions are not supported`). Appended panels land at the end of the array
  in append order, so take the index from `get_dashboard_summary` and patch
  `$.panels[N]…`.
- **A bare `| unwrap` cannot end a LogQL metric query.** `{…} | logfmt |
  metric="x" | unwrap value` answers `400 … unexpected unwrap`. Wrap it in a
  range function: `last_over_time(…[$__interval])` for a timeseries,
  `last_over_time(…[$__range])` for a stat that should show the latest sample.
  The same expression is fine in a log-line query, so the error only shows up
  when the panel renders — test each panel expression as a *metric* query.
- **A scheduled collector must run the interpreter that owns the agent's imports.**
  A `python` resolved from `PATH` can be a different install missing the package
  the collector imports; the job then exits non-zero with an import error that
  reads like the provider API failed. Point the task at the agent's venv
  interpreter explicitly, then require `LastTaskResult=0` **and** one fresh row
  at the sink — `State: Ready` proves only that the task exists.
- **A recurring task must launch `pythonw.exe`, not `python.exe`.** A console
  interpreter attached to a task that fires every few minutes flashes a black
  window on every run; the user reads that as a crash and reports it before
  anything else. Take `pythonw.exe` from the same venv that owns the imports, and
  after switching interpreters re-run the task once — the window disappears but
  so does stderr, so `LastTaskResult=0` plus a fresh sink row is the only proof
  it still works.
- **A regexp that parses can still match nothing.** In this Loki path both
  `| regexp "\\[(?P<s>local-[0-9a-f]{8})\\]"` and
  `|~ "(?i)402.*(positive balance|insufficient balance)"` returned zero rows on
  lines where `|= "positive balance"` matched 8 and `|~ "402.*positive"` matched
  32. A deployed alert built on the grouped form evaluates as `NoData`, and with
  `no_data_state: OK` it reads as healthy while protecting nothing. Prefer literal
  `|=` filters, one per concept, and `+`/`.*` over `{n}` quantifiers and
  alternation groups; when a critical query returns nothing, widen the window to
  a moment the event is known to have happened before doubting the data.
- **Helper queries in a stat panel render as their own values.** A math
  expression over three log queries still draws input, cached and output
  alongside the result, formatted in the panel's unit — token counts appeared as
  `$665.29M`, `$653.49M`, `$1.16M` next to the true `$9.00`. Set `hide: true` on
  every helper target and re-render: the panel looked plausible at a glance, so
  only the image catches it.
- **Never count an error class by a bare status number.** A `grep -c "401"` or a
  LogQL line filter for `401` matches digits inside unrelated fields —
  `rss_mb=2401`, a timestamp, a port — so a clean run reports a wall of auth
  failures and a real regression hides in the noise. Count the exact error string
  the client emits (`Error code: 401`, `UNAUTHORIZED`, `Insufficient Balance`).
  When the count and the log tail disagree, distrust the filter first.
- **Pinning background/aux models to one provider is a silent single point of
  failure.** Review, title generation and delegation have no visible output, so
  when the provider dies or the balance empties they fail silently while the chat
  looks fine. Guard with dashboard liveness counters (runs, empty results,
  last-hour) and an event-based alert on the provider's refusal phrase — never on
  silence, which only measures the laptop being off.
- **A 404 sweep proves the path is wrong, not that the data does not exist.**
  Account and billing endpoints routinely live under a prefix the inference base
  URL never shows (`/api/v2/auth/me`, not `/api/v2/me`). Read
  `grafana-observability/provider-cost-telemetry.md` before telling a user that a split or a
  balance is impossible — that file already carries the verified path for every
  provider in the lab, and a confident "the API cannot do this" costs a whole
  cycle plus the user's trust when it turns out to be a wrong path.
- **A table panel puts series in COLUMNS, and a bargauge draws no names at all.**
  Ten series in a `table` with only an `organize` transformation render as one
  row whose column headers are the series names — it reads like a single item,
  not ten. Add the `seriesToRows` transformation (reducer `last`) *before*
  `organize`, then rename `Series`/`Value`. A `bargauge` is the opposite trap: it
  draws bars and omits series names, so a by-name breakdown must be a table, and
  any long label list wants `topk(N, …)` so the rows fit.
- **`$__range` and `$__interval` are Grafana macros, not LogQL.** Passing them to
  `query_loki_logs` directly answers `400 … not a valid duration string`. Test
  the expression through the MCP tool with a concrete window (`[6h]`, `[24h]`) and
  leave the macro only inside the panel definition.
- **Use the published Grafana endpoint from an external host; the datasource
  `url` can be the internal one.** `get_datasource` for Loki can report
  `logs-prod-NNN-local-nlb.internal.grafana.net`, which resolves only *inside*
  the Grafana Cloud stack. Pushing from a bare-metal/on-prem/PVE host against it
  times out every batch while the same config 2xx's from inside the stack. Use
  the external host `logs-prod-NNN.grafana.net` (Prometheus remote write is the
  same host, `/api/prom/push`). When the host reaches Grafana only through a
  narrow VPN/AmneziaWG tunnel, add the destination's actual A-record IP as a
  `/32` in `AllowedIPs` — a wildcard internal hostname resolves to an IP the
  narrow route drops, so the symptom reads as "Grafana down" when it is a
  missing route. A diagnostic curl bound to the tunnel interface
  (`curl --interface awg0`) distinguishes "route missing / timeout" from "auth
  rejected" (fast 401) in one probe.
- **One signal returning 2xx does not validate the others on the same host.**
  Loki push `204` while Prometheus remote_write returns `401 invalid token` is
  the normal single-config shape: different endpoints, different per-signal
  usernames, and each component may read its credential from a different source
  (one `sys.env("...")`, another a literal in the config file). Do not conclude
  "a token was lost" or rotate a shared token on that evidence. Classify, read
  only, where each pipeline gets its credential and which datasource it belongs
  to before touching anything; and never rotate a token that a working pipeline
  is already authenticating with, or you break the green signal to "fix" the red
  one. `sum by (label) (count_over_time({…} |= "marker" | regexp … [24h]))`
  answers the same "how many times did X happen" with zero changes to the live
  collector — a config edit needs `alloy fmt` plus a reload that, on a parse
  error, kills every signal, not just the new one. Promote to a stage only when
  the counter must outlive log retention, join host metrics on one timeline
  before the fact, or feed an alert that needs a metric.

## Sources by signal

| Signal | Source | Depth |
|---|---|---|
| host CPU/mem/disk/net, per-process | Alloy `prometheus.exporter.windows` | this file |
| ACPI thermal-zone temperature | collector `thermalzone` | `grafana-observability/collectors-and-sources.md` |
| network latency / reachability | blackbox exporter (`icmp`, `http`) | `grafana-observability/collectors-and-sources.md` |
| per-container CPU/mem/net/pids | OTel Collector `docker_stats` | `grafana-observability/collectors-and-sources.md` |
| real CPU clock, per-core temp, package watts | OhmGraphite + PawnIO | `grafana-observability/collectors-and-sources.md` |
| did a credential try to leave the host | `loki.process` redaction marker + Grafana alert | `grafana-observability/log-pipelines-and-alerting.md` |
| LLM latency / tokens next to host load | `loki.process` metrics from agent log lines | `grafana-observability/log-pipelines-and-alerting.md` |
| per-phase latency of one request (where the time went) | OTLP traces → Tempo + TraceQL metrics | `grafana-observability/traces-and-tempo.md` |
| provider billing, remaining credits, cost estimate | provider API → `.prom` or Loki; LogQL over the agent's own log lines | `grafana-observability/provider-cost-telemetry.md` |
| trim active-series cost (cardinality budget) | Adaptive Metrics web UI, aggregation rules | `grafana-observability/adaptive-metrics.md` |

## Reference: where official material lives

- Credentials page in the portal: `My Account → Stack → Details` (use this only
  as a fallback when MCP is unavailable).
- Official Grafana agent skills: `github.com/grafana/skills`, laid out as
  `skills/<bundle>/<sub-skill>/SKILL.md`. Relevant bundles: `grafana-cloud`
  (`send-data`, `infrastructure`, `assistant-mcp`,
  `prometheus-cardinality-troubleshooter`), `grafana-datasources`.
  It is **not** in the Hermes registry — `hermes skills search grafana --source official`
  legitimately returns nothing.
- Grafana Cloud MCP server: `https://mcp.grafana.com/mcp`, OAuth 2.1, hosted,
  no manual token.
