# Hindsight integration and model evaluation

Use this reference when Hindsight is an LLM-backed memory service behind Hermes or another agent.

## Evidence layers

| Layer | Proves | Does not prove |
|---|---|---|
| Provider `/models` | Catalog visibility | Data-plane availability, price, schema compatibility |
| Direct completion | Route can answer one request | Hindsight extraction/consolidation quality |
| Tool/canary protocol | Agent/tool contract behavior | Hindsight-specific memory quality |
| Strict extraction | JSON/schema and fact/negation behavior | Persistence or consolidation health |
| Disposable Hindsight bank | Retain, consolidation, recall, stats, cleanup | Production-bank safety unless the bank is isolated |
| Langfuse read-back | OTEL export and visible operation hierarchy | Correctness of model facts or privacy unless fields are inspected |

## Candidate test packet

Before testing candidates, record the pinned Hindsight image and check the current upstream release/model leaderboard. Newer upstream versions must remain visible even when production stays pinned; evaluate an upgrade in a separate disposable compatibility track.

Use the same sanitized corpus and prompt/schema for every candidate. Store:

- provider and exact base URL;
- exact model ID and image/version;
- route boundary (host, container network, or proxy);
- corpus hash;
- first/second tool status where the generic gate applies;
- strict JSON validity, output records, facts, negations, uncertainty notes;
- retain/recall/consolidation latency;
- tokens/cost when supplied by the provider or Hindsight;
- pending/processing/failed operation counts;
- deletion and absence read-back.

Classify each candidate independently as `PASS`, `PARTIAL`, `BLOCKED`, or `INSUFFICIENT_EVIDENCE`. Do not collapse provider errors, stale model IDs, rate limits, schema failures, and semantic quality failures into one model verdict.

## Route pitfalls

- Treat a catalog-present model that returns provider 404 as stale route/data-plane mismatch, not as a successful model.
- Test Nous through the configured Hermes proxy when Hindsight uses that proxy; a host-direct bearer or x402 response is a different route.
- Preserve an independent fallback route only after its actual data-plane and Hindsight-specific behavior are verified.
- Keep the current production route stable while candidates run in disposable instances or isolated banks.
- Parallelize direct provider probes when helpful, but do not start more embedded Hindsight candidates than the live memory budget and startup watchdog can sustain. A candidate that fails readiness is `BENCHMARK_INFRA_BLOCKED`, not a model-quality verdict.
- A successful production retain is an availability signal only; require semantic quality, consolidation drain, recall, latency, and cleanup evidence before selecting a model.

## Direct Hindsight OTEL to Langfuse

For a user-approved private-project direct path, use Hindsight's native OTEL exporter and Langfuse's OTLP/HTTP ingestion. Keep project keys in a dedicated external env projection; never copy an entire Hermes `.env` into the Hindsight service.

Required postconditions:

1. Hindsight logs show tracing initialized with the expected service and endpoint.
2. Hindsight health, worker, and database remain healthy.
3. A synthetic disposable bank produces retain/recall/consolidation activity.
4. Langfuse CLI read-back shows Hindsight operation observations such as `hindsight.retain`, extraction, recall retrieval/fusion/rerank, and verification.
5. Inspect model identity, token usage, cost, latency, errors, and captured content; mark absent fields `UNKNOWN`.
6. Delete the synthetic bank and verify it is absent; Langfuse traces may remain as the intended observability evidence.

### Generation-level read-back

Use the modern Langfuse `observations` API, not the deprecated traces endpoint, and request `core,basic,model,usage,metrics` fields. When using `langfuse-cli`, parse its HTTP envelope as `{status, headers, body: {data, meta}}`; the observation list is under `body.data`.

Inspect child LLM observations, not only the parent operation span. Hindsight parent observations such as `hindsight.retain` and `hindsight.recall` may correctly be `SPAN` records with no model or usage. LLM children such as `hindsight.retain_extract_facts` and `hindsight.consolidation` should be `GENERATION` records with `model` and `usageDetails` when native Hindsight OTEL is working.

For Hindsight `0.8.6`, verify this native path before changing configuration: its LLM spans already emit `gen_ai.request.model` and `gen_ai.usage.*` attributes. If child generations contain model and usage, do not add a collector or invent another Hindsight flag; the remaining task is Langfuse model pricing/cost configuration. If child generations lack those fields, distinguish OTEL export failure, Langfuse mapping failure, and read-back/query error before mutating production.

Do not infer cost from `total_tokens` alone. Compare Hindsight usage with Langfuse `usageDetails`, account for cached-token semantics without double counting, and keep `costDetails`/`totalCost` as `UNKNOWN` until a verified custom model price or provider reconciliation exists.

If the user has accepted the private-project capture risk, do not silently replace this route with a collector. Still keep credentials and unbounded unrelated transcripts out of the telemetry payload.

## Upgrade boundary

Treat a Hindsight image upgrade as a separate task and keep it separate from model selection, backlog cleanup, and telemetry enablement. Do not discard newer upstream releases merely because the current pinned image is stable; inventory every crossed version and run the compatibility matrix before choosing the target. Snapshot and verify pg0, read release/migration notes for every crossed version, reproduce the model and OTEL smoke tests on disposable data, and require live data-plane plus Langfuse read-back before changing production. A healthy container alone is not an upgrade proof.