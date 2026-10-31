# Grafana Cloud host monitoring (Theseus laptop)

## When to Use

Use when adding a metric, log stream, dashboard panel or alert to the Theseus laptop
monitoring stack; when a collector stops delivering; or when a Grafana Cloud free-tier
limit (10k series, 14 days retention, 50 GB logs) becomes relevant.

Covers the host-metrics, container-metrics and Hermes-log pipeline: Grafana Alloy on
Windows, OhmGraphite sensors, the OTel collector and Grafana Cloud as the single sink.
Tracking issue: theseus-local-lab #51. Canonical sources: `deploy/alloy/` and
`deploy/docker-exporter/` in that repo.

## Architecture

| Signal | Collector | Sink |
|---|---|---|
| Host cpu/mem/disk/net/process | Alloy `prometheus.exporter.windows` | Prometheus remote_write |
| Real CPU clock, per-core temp, watts | OhmGraphite 0.38 + PawnIO (MSR) | scraped on `127.0.0.1:4445` |
| Latency probes (ICMP + HTTPS) | Alloy `prometheus.exporter.blackbox` + `blackbox.yml` | remote_write |
| Containers | otelcol-contrib `docker_stats` via read-only socket proxy | prometheusremotewrite |
| Container logs | Alloy `loki.source.docker` via the same read-only proxy | Loki push (`job="docker"`) |
| Hermes logs | Alloy `loki.source.file` + `loki.process` | Loki push |
| Backup heartbeat | Alloy `loki.source.file` (its own `job="backup"`) | Loki push |
| LLM latency/tokens | derived from agent.log by `stage.metrics` | scraped from Alloy's own /metrics |

Secrets never enter Git: `GRAFANA_API_TOKEN` in `~/.hermes/.env`, docker-exporter env at
`D:/theseus-monitoring/docker-exporter.env`. Repo configs keep `<PLACEHOLDER>` values.

Provider money and limit metrics (RunInfra, BotHub) ride the same textfile collector and the
same Prometheus; their endpoints, parsing quirks and the fact-vs-estimate rule are in
[references/provider-cost-metrics.md](references/provider-cost-metrics.md).

## Credentials without the admin panel

The Grafana Cloud stack UI hides the ingestion values behind several menus. Read them through
the Grafana MCP instead of hunting: call `get_datasource` with a known UID.

- `grafanacloud-prom` → `url` is the Prometheus host
  (`https://prometheus-prod-<n>-<region>.grafana.net/api/prom`), so remote_write is the same
  host plus `/api/prom/push`; `basicAuthUser` is the metrics instance ID.
- `grafanacloud-logs` → `url` is the Loki host, and `basicAuthUser` is a **different** user ID;
  log and metric ingestion do not share one.

`basicAuthPassword` returns only as `secureJsonFields`, i.e. unreadable by design — the write
token is created by the operator under an access policy whose scope set covers the signal
(`alloy-data-write` covers metrics and logs).

**Before declaring a provider or route unconfigured, enumerate the key names** rather than
grepping for guessed ones: `grep -oE '^[A-Za-z0-9_]+=' ~/.hermes/.env`. Provider credentials
use non-obvious prefixes (`TW_AI_URL`, `TW_AI_API`, `TW_API_KEY`, `RUNINFRA_API_KEY`,
`HINDSIGHT_BOTHUB_KEY`), and a narrow pattern like `*TIMEWEB*` reports a configured account as
missing — which then sends the whole conversation down a wrong branch.

**A token rejected by the Grafana API may still be the correct ingestion token.**
`401 Invalid API key` from `/api/datasources` only proves it is not a service-account token;
access-policy tokens are not Grafana API credentials. Prove it against the push endpoint:
a `400 decompress snappy: corrupt input` from remote_write means auth PASSED and only the
payload format was wrong. Never conclude "the token is broken" from the API 401 alone.

## Deploy procedure (Windows, needs elevation)

1. Edit the scratch working copy of the Alloy config (it holds the real endpoint/token)
   and validate with `alloy-windows-amd64.exe fmt <file>` — never deploy unvalidated.
2. Apply it with `curl -X POST http://127.0.0.1:12345/-/reload` (expect 200). Alloy
   re-reads its config in place — no service restart, no elevation, no dropped scrape.
   Fall back to an elevated restart (`Start-Process -Verb RunAs`, operator approves UAC)
   only when the reload endpoint is disabled.
3. Confirm the new components exist before trusting the change:
   `curl -s http://127.0.0.1:12345/api/v0/web/components` and check the count rose and
   the expected `localID`s are present. A 200 from reload means the file parsed, not that
   the graph changed.
3. Regenerate the repo copy with placeholders and confirm zero real values:
   `grep -c '<account-id>\|<host>' deploy/alloy/config.alloy` must be 0.
4. Commit and push (own repo: routine, no per-action approval).

## Container logs (Docker Desktop)

Docker Desktop keeps container output inside its own VM, so `docker logs` is
interactive-only and leaves nothing searchable afterwards. Read the same stream through
the read-only socket proxy already used for `docker_stats` — no raw `docker.sock` mount,
no privileged `loki-docker-driver` plugin:

```alloy
discovery.docker "containers" {
  host = "http://127.0.0.1:23960"     // published port of the socket proxy
}

discovery.relabel "containers" {
  targets = []
  rule {
    source_labels = ["__meta_docker_container_name"]
    regex         = "/(.*)"
    target_label  = "container"
  }
  rule {
    source_labels = ["__meta_docker_container_label_com_docker_compose_service"]
    target_label  = "service"
  }
}

loki.source.docker "containers" {
  host             = "http://127.0.0.1:23960"
  targets          = discovery.docker.containers.targets
  labels           = { job = "docker" }
  relabel_rules    = discovery.relabel.containers.rules
  forward_to       = [loki.process.container_logs.receiver]
  refresh_interval = "30s"
}
```

Route it through a `loki.process` that carries the same `stage.replace` secret redaction
as the Hermes pipeline — a container is exactly where a token ends up in a log line by
accident — and drops health-check chatter, which is the bulk of the volume.

Prove the proxy exposes the log endpoint before wiring it: a proxy that answers
`/containers/json` can still refuse `/containers/<id>/logs` depending on its allow-list.
`curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:<port>/containers/<id>/logs?stdout=1&stderr=1&tail=3"`
must return 200.

**Do not install the Grafana Cloud Docker Desktop extension to get this.** It runs a
second Alloy inside an extension container, ships Docker *and* Linux-host metrics next to
the ones already collected, and adds 9 dashboards plus 26 alerts. Two sources for the same
metrics means duplicate active series on a free-tier stack; container logs are the only
genuinely missing signal, and the block above covers them with the existing redaction.

Container logs pay for themselves immediately as a discovery channel: a crash-looping
service whose failure nobody noticed shows up here within minutes, so read
`{job="docker"}` before theorising about why something stopped.

## Pitfalls that actually bit (verified)

- **Alloy rejects heredoc** (`<<-YAML`): parse error `expected expression, got <`.
  Put module lists in a separate file and use `config_file`.
- **`stage.metrics` names get the `loki_process_custom_` prefix** unless `prefix = ""`;
  panels must use the prefixed name or the config must override it.
- **Derived metrics exist only on Alloy's own `/metrics`** (`127.0.0.1:12345`). Without an
  extra `prometheus.scrape "alloy_self"` they never reach Grafana Cloud.
- **`discovery.relabel` labeldrop does not drop metric labels** (it relabels targets).
  Use `write_relabel_config` inside `prometheus.remote_write.endpoint`. Measured:
  5526 -> 977 active series after dropping `process_id`.
- **`windows_cpu_core_frequency_mhz` is a static nominal** (max-min over 30 min = 0).
  Throttling cannot be derived from it; real clock needs OhmGraphite + PawnIO.
- **cAdvisor does not work under Docker Desktop**: it lists containers but cannot resolve
  their filesystem layers and emits one series (itself). Use the OTel `docker_stats`
  receiver against the read-only socket proxy.
- **A config edit alone does not apply.** Alloy validates the file but keeps the old graph
  until it is reloaded; `alloy fmt` returning 0 only proves the syntax parses. After any
  edit, reload and re-read `/api/v0/web/components`, or you will report a change that is
  not running.
- **An absence alert is silent by default.** A rule written as
  `count_over_time({job="..."} |= "status=ok" [26h]) < 1` returns *no data* when the stream
  is empty, and the default `NoData` handling does not fire — so a pipeline that stopped
  writing reads exactly like a healthy one. Set `no_data_state: Alerting` (and
  `exec_err_state: Alerting`) on every heartbeat rule, and keep error rules at `OK` so they
  only fire on a positive match. Prove the wiring by querying the stream by its own job
  label after the collector is reloaded, before trusting the rule.
- **Grafana-managed alert rules need a real folder** (`folderUID cannot be the root
  folder`) and, for Loki queries, `instant` + a `reduce` step before `threshold`
  (`only reduced data can be alerted on`).
- **Datasource-managed rules are unavailable in Grafana Cloud Loki**
  (`no compatible ... ruler datasource`).
- `drive_preview` refuses unless the target session is focused; read page text and
  navigate by URL instead of clicking.
- **The Prometheus textfile format needs a trailing newline.** A `.prom` file without one
  fails alone with `text format parsing error ... unexpected end of input stream` while every
  other file in the directory keeps being collected, so one missing metric reads like a wiring
  fault. Always write `"\n".join(lines) + "\n"` (or `Set-Content`), never the bare join.
- **PowerShell cmdlet modules can fail to autoload** here (`Expand-Archive`,
  `Get-AuthenticodeSignature` → "module could not load"). Use .NET directly
  (`[System.Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile`)
  or Python (`zipfile`) instead of debugging PSModulePath mid-task.
- **Grafana rejects samples stamped in the future**, and a host whose clock has
  drifted ahead fills the Alloy WAL with rejected samples until nothing new ships.
  A node with NTP off is the classic trigger. Symptom looks like a push/network
  fault: fix the clock (NTP on), then clear the stale WAL before trusting the
  stream again. "Push endpoint only answers 4xx on *auth* but the data still does
  not appear" — check clock skew before blaming the route.
- **The Alloy `filesystems` attribute was removed in newer releases.** A config
  that worked on an older Alloy (Windows laptop) fails to load on a freshly
  installed newer binary (e.g. on a Linux node) with a component-build error;
  drop the attribute rather than hunting a syntax variant.
- **A drop is not verified by `count()`.** After a relabel drop, `count(metric)`
  still shows the old series for up to the staleness window (5 min), so it reads
  as "the drop did nothing". Verify with `last_over_time(metric[1m])` — only
  writes in the last minute. And an empty result is not "dropped": a dead
  exporter also returns nothing, so pair the emptiness check with a liveness
  check (`up{job=...} == 1`) or the green is unfalsifiable. Two numbers are
  comparable only when they carry the same observation window.

## Secret-leak guard (keep it)

`loki.process` runs `stage.replace` before `loki.write`, replacing GitHub / Grafana /
OpenAI-style / AWS / JWT / Google OAuth key shapes with `[REDACTED]`. An alert rule on
`count_over_time({job="hermes"} |= "[REDACTED]")` reports the *attempt*, not the value.

**Write the regex without escaped dots — use `[.]`.** Inside an HCL string `\\` unescapes
to two characters, which the regex engine reads as *a literal backslash*, so `eyJ…\.…`
silently matches nothing and the shape passes through unredacted while the stage looks
healthy. A character class removes the question entirely: `eyJ[A-Za-z0-9_-]{10,}[.]…`.
The same edit added the two Google shapes that were missing outright —
`ya29[.][A-Za-z0-9_-]{20,}` (access) and `1//[A-Za-z0-9_-]{20,}` (refresh). Any tool that
prints an OAuth token into its output lands in the agent log, and from there in Loki.

**`alloy fmt` does not compile regular expressions.** A malformed `stage.replace` passes
`fmt` with exit 0 and fails only at reload with `Failed to build component: … could not
compile regular expression`. A 400 from `/-/reload` is the real validation — read the
response body, it names the component and echoes the offending expression. Rewriting an
expression with a plain string replace also drops the closing paren easily; the reload
error is what catches it.

Prove changes with the canary, never with reasoning: write a fake key into
`~/.hermes/logs/secret-canary.log`, confirm Loki shows `[REDACTED]`, confirm a full-text
search for the key returns nothing, then truncate the file.

## Verification standard

"Service running" and HTTP 200 are not evidence. Read back the exact object:
- Prometheus: query the series through the Grafana MCP (`query_prometheus`).
- Loki: `query_loki_logs` on the expected stream labels.
- Alerting: the rule must reach `firing` on a planted canary **and the notification must
  actually be delivered**. Firing and delivered are different states, and only the second
  one is worth anything.
- Alert delivery: assert a receiver **per rule**, not just on the global policy. A
  Grafana-managed policy whose default receiver is `empty` swallows every rule that does
  not override it, so half the rules fire into nothing while the other half email normally
  and the channel looks healthy. Read `/api/v1/provisioning/alert-rules` and check
  `notification_settings.receiver` on each rule; set it with `alerting_rules_write` (the
  MCP token accepts rule writes) or fix the default policy in the instance UI under
  Alerting → Notification policies. Confirm delivery against the real inbox, not the
  config: search the mail for the provider's alert sender.
- **Do not spend time trying to fix the default policy through the API.**
  `PUT /api/v1/provisioning/policies` answers 403 naming the missing scopes
  (`alert.provisioning:write`, `alert.notifications.routes:write`): the Grafana MCP
  authenticates by OAuth *under the user* and never requested those scopes, and an
  access-policy token in `.env` cannot grant scopes to itself. Edit the policy in the
  instance UI, then read it back with `GET /api/v1/provisioning/policies` — the UI edit
  is the change, the read is the proof.
