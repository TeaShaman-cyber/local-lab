# Grafana Cloud host monitoring (Theseus laptop)

## When to Use

Use when adding a metric, log stream, dashboard panel or alert to the Theseus laptop
monitoring stack; when a collector stops delivering; or when a Grafana Cloud free-tier
limit (10k series, 14 days retention, 50 GB logs) becomes relevant.

Covers the host-metrics, container-metrics and Hermes-log pipeline: Grafana Alloy on
Windows, OhmGraphite sensors, the OTel collector and Grafana Cloud as the single sink.
Tracking issue: theseus-local-lab #51 (historical, in the archive). Canonical sources
live in **this** repository: `infra/monitoring/alloy/config.alloy` and
`infra/compose/docker-exporter/docker-compose.yml` (registered in
`infra/registry.yaml`). `theseus-local-lab` is a reference archive — editing its
`deploy/` copies changes nothing that runs.

## Architecture

| Signal | Collector | Sink |
|---|---|---|
| Host cpu/mem/disk/net/process | Alloy `prometheus.exporter.windows` | Prometheus remote_write |
| Real CPU clock, per-core temp, watts | OhmGraphite 0.38 + PawnIO (MSR) | scraped on `127.0.0.1:4445` |
| Latency probes (ICMP + HTTPS) | Alloy `prometheus.exporter.blackbox` + `blackbox.yml` | remote_write |
| Containers | otelcol-contrib `docker_stats` via read-only socket proxy | prometheusremotewrite |
| Container logs | Alloy `loki.source.docker` via the same read-only proxy | Loki push (`job="docker"`) |
| Hermes logs | Alloy `loki.source.file` + `loki.process` | Loki push |
| ThinkPad fan research | shared-read immutable file snapshots -> Alloy | Loki (job="tpfan") |
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
   `grep -c '<account-id>\|<host>' infra/monitoring/alloy/config.alloy` must be 0.
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

**Write the regex without escaped dots — use `[.]`.** Inside an HCL string `\` unescapes
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


## ThinkPad fan diagnostic telemetry (local-lab #289, 2026-11-27)

Runtime: TPFanControl f4212a7-full-poll, directory
`E:/tpfan-qa/f4212a7-full-poll`. Both service and console processes
were observed using this directory after the authorized 28 Nov 09:53 local-time switch. EcTelemetryWaitMs=5 preserves the preceding audit configuration. EcTempShadow records the readiness-aware CPU estimate and its provenance, without changing the control input. The additional EcTempShadow gate record reports acquisition age, agreement, epoch and a diagnostic permission to decrease; it is not consumed by fan control. EcPair tach records whole-word RPM readiness, age and agreement; -1 means no usable estimate. This is diagnostic only, with no change to the existing raw shared-state or control path. EcPair full is a file-only numbered 31-field record for each joined audit poll, including failed polls; it is emitted after control handling, without new EC transactions or a database in the control path. DuckDB analysis runs offline on exe.dev. Previous builds (ee6d8f4-ec-tach-quality, 920a4d0-ec-temp-gate, 2b8fe95-ec-temp-shadow, 615ec8c-ec-audit and 82f8dcf-smart-timing), task XML and startup shortcut backups remain available for rollback. No new EC reads, control commands or
changes to fan mode. **Never tail the live TPFan files directly with Alloy.**
In the initial deployment, both logs stopped advancing at 23:18:39 local
time; removing the two live file sources was followed by resumed writing
at 23:20:31–34, without restarting TPFan. The exact Windows sharing
mechanism has not been proved.

The existing Alloy config now reads only closed, immutable copies under
`E:/tpfan-qa/telemetry`. The Windows task `Theseus TPFan Snapshot`
runs every minute while its interactive user is logged in (default
battery restrictions apply). It runs
`tools/thinkpad_fan/snapshot.py --root E:/tpfan-qa/f4212a7-full-poll --spool E:/tpfan-qa/telemetry/spool`.
The script opens original files briefly with FILE_SHARE_READ, WRITE and
DELETE, reads at most 512 KiB per file per invocation, and closes the
handle before creating a complete immutable JSONL batch. File identity,
generation, byte offset and trailing anchor provide restart checkpoints;
the incomplete last line is deferred. The spool is retained locally;
do not delete checkpoint batches before a safe retention design is in
place. Rotation between polls and same-content rewrites may still lose
or duplicate data; this is not a transactional backup of TPFan.

Loki streams (datasource grafanacloud-logs, host theseus-laptop):

- `{job="tpfan",stream="samples_archive"}`: one dated historical CSV
  archive for 27 Nov. 39,324 local lines, 15:51:41–23:18:39 UTC+02.
  Source CSV contains clock time only; date inferred from current session
  and event-log dates, not an intrinsic timestamp.
- `{job="tpfan",stream="samples_snapshot"} | json`: incremental
  copies; Loki timestamp is collection time, raw includes original clock.
- `{job="tpfan",stream="events_snapshot"} | json`: filtered diagnostic
  events, original dated event in raw; Loki timestamp is collection time.
- `stream="events"` is the retired direct-tail experiment. Do not
  aggregate it or the archive with snapshot counts: overlap is expected.

Snapshot batch filenames are dropped as Loki labels to avoid creating
one indexed stream per batch. Unique row identity is (source,file_id,
generation,offset), inside JSON. No credentials in the snapshot script.
Historical Loki count initially read 23,697 versus 39,324 local records:
cloud completeness not verified; preserve local copies. Grafana Cloud
retention/quotas still apply. A live file not advancing or a missing new
snapshot is a collection gap, not a zero RPM measurement.

Check the task result and spool mtime, Alloy component health, and actual
Loki rows. The repo template must match the installed config via
`python tools/contracts/check_alloy_projection.py`. Rollback is to
remove only the tpfan_archive/tpfan_snapshot blocks, validate and reload,
then disable the named task; never restart TPFan as part of telemetry.


## Metered network (local-lab #299)

The laptop has a finite traffic allowance. Do not equate Loki's
uncompressed stored/log bytes with network bytes. Alloy v1.21.2 exposes
loki_write_request_size_bytes_sum (encoded request body), and
prometheus_remote_storage_bytes_total (compressed queue bytes).
Neither includes all connection overhead, retries, downloads or other
applications. Both are process-lifetime counters; use deltas, handle resets.
Baseline 28 Nov 2026: about 249 MB Loki request bodies and 36 MB Prometheus
over the current approximately eight-hour Alloy process lifetime. A separate
60-second window measured 15,637 Loki request bytes in 14 batches and
115,995 Prometheus bytes, 4,542 metric samples. Do not extrapolate startup
backlog as steady-state traffic. Docker source read 14.6 million entries
over that lifetime; precise attribution of the replay/backlog remains open.

First reversible transport reduction:

- Loki batch_wait 15s (formerly default 1s), retains all existing log
  selection and redaction, but increases delivery latency and in-memory
  exposure before a batch is sent.
- Drop only per-file TPFan snapshot collector bookkeeping (component_id
  loki.source.file.tpfan_snapshot with nonempty path) from remote metrics.
  These metrics remain readable locally; their cloud history is intentionally
  no longer extended. The raw research records, snapshot task and EcPair
  logs are unchanged.
- Export two bounded transport counters through transport_accounting:
  prometheus_remote_storage_bytes_total and samples_retried_total.
  Other remote-storage internals remain excluded.

No downsampling of host/sensor readings, no log sampling or change to
Hermes content capture, no deletion of local spool or existing cloud data.
Reload validates configuration but does not prove lossless delivery of
in-flight batches. Verify local counters, cloud freshness and actual logs.
Restore the previous config from config.alloy.bak-metered-181 if needed
and revert the matching template change.

Next investigation: Docker socket-proxy logs record the collector's own
successful GETs; hermes-proxy was observed in a restart loop (524 restarts).
Do not stop services or discard diagnostic events just to reduce traffic.
Check actual dependency, local retention and cloud alert consumers first.
Hermes OTel full capture is a separate audit requirement; measure it
independently. Prefer local complete history plus lossless compressed
batches on demand over dropping research data.


### Docker proxy noise and inactive Hermes subscription proxy (28 Nov)

The two proxies are distinct. The Docker socket proxy is the active read-only
observation route; do not stop it. In a 30-minute Loki window all 1,764
socket-proxy lines matched successful versioned GET access records. The
last 200 local lines also matched. A scoped Loki stage now drops only those
successful GET/200 records for job=docker, service=docker-socket-proxy.
Negative controls (500, POST, generic fatal line) do not match. Error logs,
other services, and source-side Docker logs are unaffected. The actual
Docker log driver was json-file with no explicit rotation configuration;
this is not a verified infinite-retention guarantee. No logs were deleted.
Restore config.alloy.bak-docker-182 and matching template to revert.

The separate hermes-proxy (Hindsight Compose project) was unhealthy, with
539 restarts and exit code 2. Its pinned image accepts the configured CLI
arguments. The retained logs explicitly report Not logged into Nous Portal;
the old config also emits a migration warning. No auth values were read,
replaced or migrated. Hindsight API had been stopped for two weeks; local
ports 8645 and 8888 did not respond and no active host Hermes config reference
to 8645 was found. Honcho/PVE references remain historical possible consumers,
not proof of a functioning route. Stopped only hermes-proxy, preserving its
container, restart policy and dedicated auth volume. Hindsight, other stopped
containers and active GitHub MCP / socket proxy were not changed. This is
an operator runtime stop, not a Compose policy change. On-demand recovery
requires valid Nous authorization and a health check before starting
dependent consumers. Do not pull a new image or start Hindsight just to
investigate the inactive proxy. See issue #299.


### TPFan midnight snapshot repair (28 Nov, issue #289)

TPFan emits unpadded single-digit hours (e.g. 0:00:00). The snapshot CSV
selection originally required two hour digits, yet advanced its checkpoint
past the rejected lines. Accept one or two digits and preserve the original
clock text. Regression test exercises midnight, both hour shapes, header
rejection and restart idempotence. Recovered 2,865 omitted rows from
the original file, using matching contiguous checkpoint intervals and
trailing anchors, excluding previously emitted byte offsets. The separate
immutable csv-recovery batch is not a new checkpoint; recovery rows retain
the source file identity/generation/offset, with collection time distinct
from the original clock. Existing source and checkpoint files are retained.
Cloud delivery of both new and recovered records was observed. No change
to measurement frequency, EC access or cloud destination.


### Local hardware warehouse (issue #289)

The Windows task Theseus Hardware Warehouse samples ETW for 240 seconds every
five minutes while the user is logged in. It never wakes the laptop and ignores
overlapping invocations. The versioned profile includes 20 hardware providers;
the noisy WLAN-driver and processor-power streams are warnings-only, while
WLAN AutoConfig remains informational. No packet capture is enabled. Raw ETL
segments are circular and limited to 16 MiB; do not infer gapless coverage or
absence of events from a circular trace or a period without a trace. The
existing TPFan collection/control path is untouched.

The local DuckDB file is D:/theseus-hardware/hardware.duckdb. ETL uses the
PyETWkit 3.2.0 native read_all API because its CLI calls a missing events method.
DuckDB 1.5.5 and PyArrow are installed in the existing D: virtual environment.
ETL is keyed by source SHA and ordinal; Windows Event Log is keyed by canonical
XML hash, with source channel, record ID and UTC nanoseconds. System imports
only hardware-related providers, plus WLAN AutoConfig Operational; no Security
log or packet payload. First query covers up to seven days; subsequent queries
overlap by at least an hour, capped at 5000 records per channel and explicitly
marked if capped. TPFan rows preserve sequence and reject conflicting data.
Clock anchors allow interval joins; missing sequence numbers remain missing.

The 5 GiB budget is an admission guard, not a filesystem quota: keep 1 GiB
headroom and at least 10 GiB free on D:, stop new work before crossing the
guard, retain prior data without deleting it. ETL decoder is bounded by file
size and event count; DuckDB memory and spill are bounded. last-run.json
reports the latest invocation status. Raw and decoded local data can contain
network identifiers; never upload them by default. Review retention separately;
no automatic deletion. Disable the task to stop scheduled collection.

Windowless correction (issues #67, #150, #289): the scheduled action uses the
dedicated venv's PE subsystem 2 pythonw.exe, verified before installation.
Every native child process uses CREATE_NO_WINDOW. With no standard streams,
the warehouse overwrites its single latest-run diagnostic log and preserves
last-run.json. Docker Desktop currently runs Linux containers; host ETW and
Event Log acquisition and PyETWkit decoding need Windows APIs. Keep that
boundary native; container-side offline analysis is a separate optional
consumer, not a second concurrent writer to hardware.duckdb.
