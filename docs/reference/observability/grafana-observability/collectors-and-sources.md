# Collectors and sources: which one answers which question

Depth behind the signal table in SKILL.md. Every metric name and expression
here was read back from a live Grafana Cloud datasource, not copied from docs.

## Pick by question, not by tool

| The question | Collector | Why that one |
|---|---|---|
| What is the host doing right now / at 16:12 | Alloy `prometheus.exporter.windows` | one service, per-process and host series, no extra install |
| How hot is it | `thermalzone` collector (ACPI zone) | free, no driver; approximate — this is a chassis thermal zone, not a CPU core |
| Which cores, what real clock, what watts | OhmGraphite + PawnIO | only MSR-level source; everything else reports nominal values |
| Is the network path healthy | `prometheus.exporter.blackbox` | ICMP RTT + HTTP phase timings, goes through the real path (VPN included) |
| What are the containers doing | OTel Collector `docker_stats` | works under Docker Desktop; cAdvisor does not |

## windows_exporter (embedded in Alloy)

- `enabled_collectors` is a hand-picked allowlist: anything not listed is off.
- `cs` was removed upstream. Listing it logs
  `the 'cs' collector is removed - its metrics are in the 'os', 'memory', and 'cpu' collectors`
  and `windows_cs_*` never appears.
- `process { include = "^(a|b|c)$" exclude = "^$" }` bounds cardinality to named
  processes. `include`/`exclude` are the current argument names.
- Metric families worth knowing: `windows_cpu_time_total{mode}`, `windows_memory_available_bytes`,
  `windows_memory_physical_total_bytes`, `windows_logical_disk_free_bytes{volume}`,
  `windows_net_bytes_received_total{nic}`, `windows_process_cpu_time_total{process,process_id,mode}`,
  `windows_thermalzone_temperature_celsius{name}`.
- **`windows_cpu_core_frequency_mhz` is nominal.** `max_over_time(...[30m]) - min_over_time(...[30m]) = 0`
  at 1801 MHz on an 1800 MHz-base chip. `windows_cpu_processor_performance_total` exists
  but is a raw counter needing calibration. Do not promise throttling detection from either.

## blackbox exporter (latency, reachability)

Needs a real YAML file — Alloy will not take an inline heredoc. Two modules cover
most of it:

```yaml
modules:
  icmp:
    prober: icmp
    timeout: 5s
    icmp: { preferred_ip_protocol: ip4 }
  https:
    prober: http
    timeout: 5s
    http: { preferred_ip_protocol: ip4, valid_status_codes: [200, 301, 302] }
```

Point at it with `config_file = "C:/Program Files/GrafanaLabs/Alloy/blackbox.yml"`
and scrape the exported targets with their own `prometheus.scrape`.

- `probe_success{target_name}` — 1/0, the alarm.
- `probe_icmp_duration_seconds{phase="rtt"}` — multiply by 1000 for ms.
- `probe_http_duration_seconds{phase}` — phases present: `resolve`, `connect`, `tls`,
  `transfer`, `processing`. There is **no** `total` phase.
- Probes measure the path the traffic actually takes. On a VPN that is the VPN's
  RTT, which is the number the user feels, not a clean-link baseline.

## Container metrics: OTel `docker_stats`, not cAdvisor

cAdvisor under Docker Desktop discovers containers but cannot resolve their
filesystem layers (`/rootfs/var/lib/docker/image/overlayfs/... no such file or directory`)
and emits exactly one series — itself. No mount combination fixed that.

Use the OpenTelemetry Collector contrib image instead, talking to the Docker
Engine API through a read-only socket proxy (no raw `docker.sock`, no privileged):

```yaml
receivers:
  docker_stats:
    endpoint: http://<socket-proxy>:2375
    collection_interval: 30s
    metrics:
      container.cpu.utilization:            { enabled: true }
      container.memory.usage.total:         { enabled: true }
      container.memory.percent:             { enabled: true }
      container.network.io.usage.rx_bytes:  { enabled: true }
      container.network.io.usage.tx_bytes:  { enabled: true }

extensions:
  basicauth/client:
    client_auth: { username: "<instance-id>", password: "${env:GRAFANA_API_TOKEN}" }

exporters:
  prometheusremotewrite:
    endpoint: https://prometheus-prod-NN-prod-<region>.grafana.net/api/prom/push
    auth: { authenticator: basicauth/client }
```

Pin the image by digest. Feed the token with `env_file:` pointing at a runtime
file outside the repo. Resulting families: `container_cpu_utilization_ratio`,
`container_memory_usage_total_bytes`, `container_memory_percent_ratio`,
`container_network_io_usage_{rx,tx}_bytes_total`, `container_pids_count`,
`container_uptime_seconds` — each labelled `container_name`.

## OhmGraphite + PawnIO (real clocks, per-core temperature)

Two pieces, and the second one is what makes the first useful:

1. `github.com/nickbabcock/OhmGraphite/releases/latest` → unzip → configure →
   `OhmGraphite.exe install` / `start` (elevated). Config keys:
   `type=prometheus`, `prometheus_port`, `prometheus_host=127.0.0.1`,
   `prometheus_path=metrics/` — pull model, sensors polled only on scrape.
2. **PawnIO** kernel driver from `github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe`.
   Without it OhmGraphite reports load/volts/watts but **no clock and no CPU
   temperature**. Installer is `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` capable.

Verify the signature before running it (the Security module may not autoload):

```powershell
[System.Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile('C:\Apps\PawnIO_setup.exe').Subject
```

The sample namespace is `/intelcpu/0/{clock,temperature,power,voltage}`. Exposed
families: `ohm_cpu_hertz` (Hz — divide by 1e6 for MHz), `ohm_cpu_celsius`
(sensor labels include `CPU Package`, `Core Max`, `CPU Core #1..#4`),
`ohm_cpu_watts`, `ohm_cpu_volts`, `ohm_cpu_load_percent`.

**First scrape after restart can return only .NET runtime metrics.** Sensors
register asynchronously; re-query after 20–60 s before concluding it is broken.

Throttling read: compare `ohm_cpu_hertz` per core against the chip's base and
turbo. Sitting near base under load with high `ohm_cpu_celsius` is thermal/power
capping — that is the evidence, not a guess.
