# Traces into Grafana Cloud Tempo

## OTLP endpoint and the username nobody expects

```env
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-<region>.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64(stackId:glc_token)>
OTEL_SERVICE_NAME=<service>
```

The OTel **SDKs** append `/v1/traces`, `/v1/logs`, `/v1/metrics` to the base endpoint,
so with a standard SDK never set the signal path yourself.

### Not every exporter normalizes — check yours

Some exporters pass `endpoint` through **verbatim**, with no signal path appended.
Hermes' `hermes_otel` plugin is one of them: configured with `.../otlp` it POSTs to
`.../otlp` and logs `Failed to export spans batch code: 404`. Give that family the
full signal URL:

```yaml
backends:
  - type: otlp
    endpoint: https://otlp-gateway-prod-<region>.grafana.net/otlp/v1/traces
    headers:
      Authorization: "Basic <base64(stackId:glc_token)>"
```

Distinguish the two cases with one probe before blaming credentials — a wrong path
answers **404**, a wrong user/token answers **401**, and a body-format problem on a
working URL answers **400**. `404` never means "bad token".

**The Basic-auth username for the OTLP gateway is the Stack ID** — the number
inside the policy name `stack-<id>-…` or the portal URL. It is *not*:

- the Tempo datasource's `basicAuthUser` (a different numeric ID),
- the Prometheus instance ID,
- the Loki user ID,
- the org ID,
- the stack slug.

Each of those returns `401 invalid authentication credentials` even with a valid
`glc_` token. Source the real values from **Connections → OpenTelemetry**, or from
the "Troubleshoot Grafana Cloud Traces" page: *"Use the instance ID (numeric) as
the username and a Cloud Access Policy token with the traces:write scope."*

Diagnostic that separates the two failure modes:

| Observation | Meaning |
|---|---|
| `/otlp/v1/metrics`, `/logs` and `/traces` all `401` | wrong username/endpoint pair, not a scope problem |
| traces `401` while Loki push returns `204` with the same token | the policy lacks `traces:write` |
| `200` on a one-span probe | credentials are right; wire the collector |

`traces:write` is already inside the `set:alloy-data-write` scope set, so a
policy carrying that set needs no extra scope for traces.

## Editing an access policy: the matrix replaces a scope set

Scope sets are not additive with the matrix. Ticking `traces → WRITE` on a policy
whose scopes are `set:alloy-data-write` **replaces the set** with the single
`traces:write`, and logs/metrics stop authenticating immediately (Loki push flips
to `401`) while traces still work — so the failure looks unrelated to the edit.
Trying to have both is rejected outright:

```
an access policy must not have duplicate scopes (traces:write)
```

because the set already contains that scope. Leave the set alone and add nothing
from the matrix; when a different scope is genuinely needed, confirm what the set
already covers before touching it. Also expect propagation latency — Grafana
states up to 15 minutes — so re-probe both signals rather than concluding from
the first `401` seconds after saving:

| Signal | Probe | Pass |
|---|---|---|
| logs | `POST <loki>/loki/api/v1/push` | `204` |
| traces | `POST <otlp-gateway>/otlp/v1/traces` | `200` |

After any policy edit, re-probe **every** signal the policy serves; a traces-only
check will not notice that logs broke.

## Prove ingestion before building anything on top

```
mcp__grafana__search_tempo_traces(datasourceUid="grafanacloud-traces",
  query='{ resource.service.name = "<service>" }')
```

Compare the returned `durationMs` with the wall-clock time of the request you
just made. A trace that exists but does not match the request you triggered is
not proof. Then `get_tempo_trace(trace_id=...)` for the span breakdown — that is
where a latency story becomes readable (which phase actually consumed the time).

## TraceQL metrics: latency percentiles without a metrics pipeline

Grafana Cloud runs a metrics generator over Tempo, so percentiles are queryable
from traces alone:

```
{ resource.service.name = "<service>" && name = "<span>" } | quantile_over_time(span:duration, 0.95)
```

Call it with `query_tempo_metrics` (`type: range` for a series, `instant` for a
single value; keep instant windows under ~15 minutes). Results carry exemplars
back to concrete traces.

Bucket boundaries are coarse relative to a real observation, so the percentile
reads higher than the actual measurement — treat it as a trend signal, not a
stopwatch.

### The 25-hour ceiling is server-side

A metrics query covering more than 25 hours fails outright:

```
tempo API returned 400: metrics query time range exceeds the maximum allowed duration of 25h0m0s
```

`query_frontend.metrics.max_duration` is a server setting; no query hint or
syntax lifts it. For a week you have two options, and only one is honest:

- **Span metrics → PromQL** (the documented path). The metrics generator counts
  spans at ingest and writes `traces_spanmetrics_calls_total` to Mimir, where no
  25-hour limit applies: `sum(increase(traces_spanmetrics_calls_total[7d]))`.
  Check the name first — Alloy/OTel emits `traces_span_metrics_calls_total`.
  These numbers drift slightly from TraceQL: the generator counts at ingest, so
  spans arriving later than `metrics_ingestion_time_range_slack` (30 s default)
  are missing.
- **Seven separate ≤24 h queries, summed.** A workaround, not a fix. Say so in
  the panel description rather than letting the next reader assume it is a limit
  of the data.

Confirm which case applies before building: `list_prometheus_metric_names`
with regex `traces_span.*calls_total`. An empty result means span metrics are
not enabled on the stack, and the week window is genuinely unavailable.

## Attribute names containing dots

TraceQL parses `.` as a path separator, so a literal attribute like
`hermes.tool.command` is read as "attribute `hermes`, field `tool`" and fails:

```
{ hermes.tool.command =~ ".*" }
  → parse error at line 1, col 3: unknown identifier: hermes
```

Quote the whole name and prefix the scope:

```
{ span."hermes.tool.command" =~ ".*" }
```

Same for `langfuse.observation.input`, `hermes.turn.tool_commands`, and any
other dotted attribute. The scope prefix (`span.`, `resource.`) is not optional
here — it tells the parser that what follows is one attribute name.

## Consulting Grafana Assistant from the CLI

`gcx` ships an Assistant client over A2A:

```
gcx assistant prompt "<question>" --agent
```

It streams JSON events (`status`, `tool_call`, `message`, `stream_end`); the
answer is the `text` field of the `message` event. Requires OAuth (`gcx login`)
and is **billed per token** — worth it for a question about Grafana's own
semantics (limits, datasource behaviour), not for something a probe answers.

Two paths to the same binary, and they are not interchangeable:

| path | sees local files | use for |
|---|---|---|
| `mcp__grafana__exec_gcx` | no (sandbox) | reads, discovery, `help-tree`, `skills get` |
| local `gcx` in PATH | yes | `resources validate`, `resources push -p <file>` |

On Windows the local binary lives in `%LOCALAPPDATA%\gcx\bin`. Adding it to
`.bashrc` needs the **MSYS** form — a native path with backslashes loses them to
escape processing and `which gcx` comes back empty:

```bash
export PATH="$PATH:/c/Users/<user>/AppData/Local/gcx/bin"   # works
export PATH="$PATH:C:\Users\<user>\AppData\Local/gcx/bin"   # silently broken
```

## Panels over Tempo

- datasource type `tempo`, uid of the tempo datasource, `queryType: "traceql"`,
  and the TraceQL string as `query`;
- set the field unit to seconds, otherwise the axis is unitless;
- **Grafana alerting cannot use Tempo as a datasource.** If traces are the only
  signal, an alert on latency is impossible — add a metric scrape or accept
  dashboards only.

## Quota

Tempo's free tier is **50 GB of traces per month**, an order of magnitude more
room than event-count-limited APM products. When a trace-heavy service exhausts
an event quota, moving its OTLP export to Tempo is cheaper than sampling a
service that is not the problem.
