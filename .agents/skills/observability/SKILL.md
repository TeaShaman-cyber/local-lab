---
name: observability
description: Use for Grafana and Langfuse — where our monitoring is wired, and where the details live. Router only; the substance is in docs/reference/observability/.
version: 1.0.0
author: Semyon Poklad + Svarog
license: MIT
metadata:
  hermes:
    tags: [observability, grafana, langfuse, tempo, loki, metrics]
    related_skills: [local-core-router, hermes-agent]
---

# Observability: one entry, many documents

Our monitoring is one system with two halves: **Grafana** (metrics, logs, traces) and
**Langfuse** (LLM traces, prompts, evals). Both describe *how things are wired*, which
is reference material: read it when the question is about monitoring, not on every
turn.

This skill is the entry point. It does not contain the answers — it says where they are.

## If something is missing here, read upstream — do not improvise

These packs are copies. When a question is not answered below, the answer is in the
official repository, not in a guess:

| Product | Upstream | Read it with |
|---|---|---|
| Grafana | `grafana/skills` | `gh api repos/grafana/skills/contents/skills/grafana-cloud/<topic>` or clone `E:/grafana-skills` |
| Langfuse | `langfuse/skills` | `gh api repos/langfuse/skills/contents/skills/langfuse` or `E:/langfuse-skills` |
| Timeweb | `timeweb-cloud/skills` | `gh api repos/timeweb-cloud/skills/contents/skills` or `E:/timeweb-skills` |
| matt-pocock | `mattpocock/skills` | clone `E:/matt-upstream` |

Local clones are refreshed by `tools/dev/skillpacks`, which also reports when a pack
moved upstream after our copy (`stale` / `UNKNOWN`).

**The rule this replaces:** do not write a skill about an outside product from memory.
Check whether the vendor ships one first — `gh api repos/<org>/skills`. Three of our
four packs turned out to exist upstream for months before we rewrote them
(Langfuse: 8 months, Grafana: 7, Timeweb: 2).

## Where the details live

`docs/reference/observability/` in the lab repository.

| Question | File |
|---|---|
| Host metrics, Alloy on a machine, node exporter, dashboards | `grafana-host-monitoring.md` |
| Sending new telemetry: Prometheus remote_write, Loki, OTLP | `grafana-send-data.md` |
| Adaptive metrics, collectors, log pipelines, alerting rules | `grafana-observability.md` |
| Log pipelines and alerting rules | `grafana-observability/log-pipelines-and-alerting.md` |
| TraceQL queries, Tempo spans, per-phase latency | `grafana-observability/traces-and-tempo.md` |
| Collector topology and scrape sources | `grafana-observability/collectors-and-sources.md` |
| Cost telemetry and provider billing panels | `grafana-observability/provider-cost-telemetry.md` |
| Metric cardinality, dropped series, adaptive metrics | `grafana-observability/adaptive-metrics.md` |
| Langfuse: principles, CLI, when to use which route | `langfuse/overview.md` (upstream, 151 lines) |
| Langfuse: project auth, MCP connection, which project holds what | `langfuse-mcp-reference.md` |
| Langfuse CLI, datasets, evals, judge calibration | `langfuse/*.md` (17 files) |
| Hindsight ↔ Langfuse telemetry link | `langfuse/hindsight-integration.md` |
| Error analysis over traces | `langfuse/error-analysis.md` |

## How to use this

1. Pick the row that matches the question — the table is the index, keep it small.
2. Read **that one file**. Do not load the folder.
3. If no row fits, `rg -n "<term>" docs/reference/observability/` before concluding it
   is undocumented.

## Boundaries

- These documents describe **our** wiring and the official APIs as of the date in each
  file. A vendor changing an endpoint does not update them: verify against the live
  response when the answer matters.
- Querying live data is a different job: use the Grafana MCP tools or Langfuse MCP,
  not these files.
- Grafana alerting cannot use Tempo as a datasource — that limit is why span metrics
  exist. Stated here because it is the one fact people re-discover repeatedly.

## Why this is a router and not three skills

Three Grafana skills (250 + 270 + 457 lines) and a Langfuse skill with 16 reference
files loaded into context whether or not the turn was about monitoring. The content is
unchanged — it moved, and this table replaced the loading.
