# Sending Data to Grafana Cloud

> **Docs**: https://grafana.com/docs/grafana-cloud/send-data/

## Quick Start: Find Your Credentials

In Grafana Cloud portal → **My Account** → **Stack** → **Details**:

| Signal | Credential Fields |
|--------|------------------|
| Metrics | Prometheus remote write URL, username, password/API key |
| Logs | Loki URL, username, password/API key |
| Traces | Tempo OTLP endpoint, username, password/API key |
| Profiles | Pyroscope URL, username, password/API key |

## Credential types — never mix them up

Grafana Cloud issues two unrelated credentials and they are not interchangeable:

| Credential | Shape | Used for |
|---|---|---|
| Service-account token | `glsa_…` | Grafana HTTP API, dashboards, the Grafana MCP server, queries |
| Access-policy token + instance ID | `glc_…` + a numeric username | `basic_auth` for Prometheus remote write, Loki push, OTLP |

A service-account token placed in `basic_auth` yields silent 401s on every push
while the Grafana API keeps working, which reads as "the collector is broken."
Take the push pair from **Details**, not from the service-accounts page, and
create the write key with role `MetricsPublisher` (read-only: `Viewer`).

### The numeric username differs per signal — OTLP is the odd one

Every data source carries its **own** numeric instance ID: Prometheus, Tempo and
Loki each show a different number, and a Tempo datasource's `basicAuthUser` is
**not** the OTLP username. The OTLP gateway authenticates with the **Stack ID**
— the number embedded in the policy name `stack-<id>-…` or in the portal URL.
A correct Tempo user plus a valid `glc_` token still returns
`401 invalid authentication credentials` when the username is that of another
signal.

Source it from the **Connections → OpenTelemetry** tile, or from the "Troubleshoot
Grafana Cloud Traces" page: *"Use the instance ID (numeric) as the username and a
Cloud Access Policy token with the traces:write scope."* Diagnose before editing
anything: if `/otlp/v1/metrics`, `/otlp/v1/logs` **and** `/otlp/v1/traces` all
return 401 with the same credentials, the username/endpoint pair is wrong, not
the scope. A one-span probe settles it in seconds.

### Editing an access policy's scopes

Scopes live on the **policy**, and the console renders them as a
resource × READ/WRITE/DELETE matrix plus an "Add scope" dropdown for sets.
Editing the policy changes what its existing tokens may do — a new token is
usually unnecessary.

- Ticking a matrix box **replaces a scope set with the single scope**: a policy
  holding `set:alloy-data-write` becomes `traces:write`, and log and metric
  pushes start returning 401 within seconds. Re-read the `Scopes:` line on the
  list page after saving.
- A set already contains its members, so adding `set:alloy-data-write` while
  `traces:write` is also ticked is rejected with
  `an access policy must not have duplicate scopes (traces:write)`. Prefer the
  set alone.

Allow up to 15 minutes for propagation, and verify with a real push per signal
rather than trusting the save confirmation.

## Account tier — free is not a trial

Sign-up starts a **14-day Pro trial** with unlimited usage. If it ends without
an upgrade the org drops to **Cloud Free**, which is permanent (10k active
series, 14 days metric retention, no credit card required). Do not tell a user
the free tier expires in 14 days — the trial and the tier are different things.
Nonexistent credit-card details in the UI's "Subscription method" row do not
imply a charge will occur.

## Alloy (Recommended — All Signals)

```alloy
// METRICS
prometheus.scrape "app" {
  targets    = [{"__address__" = "localhost:8080"}]
  forward_to = [prometheus.remote_write.cloud.receiver]
}

prometheus.remote_write "cloud" {
  endpoint {
    url = "https://prometheus-prod-xx.grafana.net/api/prom/push"
    basic_auth {
      username = sys.env("PROM_USER")
      password = sys.env("GRAFANA_CLOUD_API_KEY")
    }
  }
}

// LOGS
loki.source.file "app" {
  targets = [{__path__ = "/var/log/app/*.log", job = "app"}]
  forward_to = [loki.write.cloud.receiver]
}

loki.write "cloud" {
  endpoint {
    url = "https://logs-prod-xx.grafana.net/loki/api/v1/push"
    basic_auth {
      username = sys.env("LOKI_USER")
      password = sys.env("GRAFANA_CLOUD_API_KEY")
    }
  }
}

// TRACES (OTLP receive → forward)
otelcol.receiver.otlp "default" {
  grpc { endpoint = "0.0.0.0:4317" }
  http { endpoint = "0.0.0.0:4318" }
  output {
    traces = [otelcol.exporter.otlp.cloud.input]
  }
}

otelcol.exporter.otlp "cloud" {
  client {
    endpoint = "tempo-prod-xx.grafana.net:443"
    auth = otelcol.auth.basic.cloud.handler
  }
}

otelcol.auth.basic "cloud" {
  username = sys.env("TEMPO_USER")
  password = sys.env("GRAFANA_CLOUD_API_KEY")
}
```

## Direct Prometheus Remote Write

```yaml
# prometheus.yml
remote_write:
  - url: https://prometheus-prod-xx.grafana.net/api/prom/push
    basic_auth:
      username: "123456"
      password: "your-api-key"
    write_relabel_configs:
      - source_labels: [__name__]
        regex: "go_.*"
        action: drop   # optional: drop high-cardinality metrics
```

## Direct Loki Push (curl)

```bash
curl -X POST https://logs-prod-xx.grafana.net/loki/api/v1/push \
  -H "Content-Type: application/json" \
  -u "123456:your-api-key" \
  -d '{
    "streams": [{
      "stream": { "app": "myapp", "env": "prod" },
      "values": [
        ["1706745600000000000", "application started"]
      ]
    }]
  }'
```

## OpenTelemetry SDK → Grafana Cloud

```bash
# Environment variables for OTLP export
export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-xx.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(echo -n '123456:your-api-key' | base64)"
export OTEL_SERVICE_NAME="my-service"
```

Python example:
```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
exporter = OTLPSpanExporter()  # reads OTEL_EXPORTER_OTLP_* env vars
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
```

## Cloud Integrations

Pre-built integrations for common infrastructure (install from Grafana Cloud UI or API):

```bash
# List available integrations
curl https://integrations-api-prod.grafana.net/api/v1/integrations \
  -H "Authorization: Bearer <api-key>"

# Install AWS CloudWatch integration
curl -X POST https://integrations-api-prod.grafana.net/api/v1/integrations/cloudwatch \
  -H "Authorization: Bearer <api-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "aws-prod", "config": {...}}'
```

Popular integrations: AWS CloudWatch, Azure Monitor, GCP, Kubernetes, Docker, MySQL, PostgreSQL, Redis, Nginx, Apache, JVM, Node.js, Python, .NET.

## Kubernetes Agent Operator

```yaml
# values.yaml for grafana/k8s-monitoring Helm chart
cluster:
  name: production

externalServices:
  prometheus:
    host: https://prometheus-prod-xx.grafana.net
    basicAuth:
      username: "123456"
      password:
        secretName: grafana-cloud-secret
        secretKey: api-key

  loki:
    host: https://logs-prod-xx.grafana.net
    basicAuth:
      username: "234567"
      password:
        secretName: grafana-cloud-secret
        secretKey: api-key

metrics:
  enabled: true
  podMonitors:
    enabled: true
  serviceMonitors:
    enabled: true

logs:
  pod_logs:
    enabled: true

traces:
  enabled: true
```

```bash
helm install k8s-monitoring grafana/k8s-monitoring \
  --version 4.1.4 \
  -n monitoring --create-namespace \
  -f values.yaml
```

## API Key Management

```bash
# Create API key via Grafana API
curl -X POST https://yourstack.grafana.net/api/auth/keys \
  -H "Content-Type: application/json" \
  -u "admin:adminpassword" \
  -d '{"name": "alloy-writer", "role": "MetricsPublisher", "secondsToLive": 0}'
```

Roles for data ingestion: `MetricsPublisher`, `LogsPublisher`, `TracesPublisher`, `ProfilesPublisher`
