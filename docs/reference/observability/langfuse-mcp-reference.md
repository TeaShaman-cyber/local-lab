# Langfuse MCP: project authentication and inspection

## When to Use

Use this reference when connecting Hermes or another MCP client to the authenticated Langfuse data-platform server, especially when inspecting Hermes and Hindsight telemetry in one or more Langfuse projects.

## Verify the project boundary first

Langfuse public and secret API keys are scoped to one project. Before querying, identify which project the credential pair belongs to and whether the project contains Hermes traces, Hindsight OTEL observations, or both. Do not infer a missing Hermes trace from a query made with Hindsight-only credentials.

When multiple services share a project, query the modern observations endpoint with service/environment/trace-name filters where available. When projects differ, perform one bounded query per credential pair and keep the results labeled by project. Verify the active env scope before interpreting an empty result: the official Hermes plugin may use `HERMES_LANGFUSE_*` variables while CLI/SDK routes may use `LANGFUSE_*`; a missing or stale pair can make a real historical Hermes project look empty. If only Hindsight observations appear, check the credential pair and historical window before concluding that Hermes telemetry was never present.

## Official Cloud endpoint and authentication

For the EU Cloud region use:

```text
https://cloud.langfuse.com/api/public/mcp
```

The transport is Streamable HTTP. Authentication is project-scoped HTTP Basic Auth:

```text
Authorization: Basic base64(public_key:secret_key)
```

Do not replace this with `Bearer <secret-key>` or OAuth unless current Langfuse documentation explicitly says the deployment supports it. Do not store the encoded credential in `config.yaml`, issue text, or a generated artifact.

## Hermes configuration boundary

Keep only an env reference in Hermes config:

```yaml
mcp_servers:
  langfuse:
    url: https://cloud.langfuse.com/api/public/mcp
    headers:
      Authorization: Basic ${env:LANGFUSE_MCP_AUTH}
```

Hermes does not derive a Basic header from `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` automatically. Prefer a user-owned `LANGFUSE_MCP_AUTH` env variable and keep only the reference in config. Do not write or rotate secret values by default; if the user gives explicit approval to create or repair this exact derived variable, compute it locally from the existing keys, append/update it idempotently, never print the value, and verify only its presence.

After changing an env-backed MCP credential, reload MCP or start a fresh Hermes process. Header placeholders can be expanded and cached at server-task startup, so a long-lived session may continue using the old value.

## Toolset and route policy

Langfuse MCP advertises read and write tools by default. Do not impose a read-only allowlist or hide tools unless the user explicitly requests that restriction; preserve the full discovered toolset when the user wants MCP parity. Treat CLI and MCP as complementary routes to the same project: CLI is the scriptable/JSON fallback, while MCP is the interactive inspection and configuration route.

Tool visibility is not action authorization. Before a write operation, still confirm the concrete scope and payload, execute it only within the user's approved workflow, and read back the exact resource.

## Query shapes that work

- `listObservations` caps the time range at **14 days**. Longer ranges are rejected with an
explicit error; split into adjacent windows instead of widening.
- Observation field names are not the obvious ones: use `latency`, `level`,
`statusMessage`, `type`, `name`, `traceId`, `startTime`, `endTime`. `duration` and `status`
are rejected — call `getObservationFieldSchema` rather than guessing.
- `queryMetrics` filter operators are `=`, `contains`, `does not contain`, `starts with`,
`ends with`, `is not empty`. `eq` is rejected.
- Reading traces, observations and metrics is free: queries are not ingestion, so audit and
measurement never consume the plan's unit budget. Only exporters do.

Billing, quota semantics, what deletion does and does not do, and discount-program
eligibility: `langfuse/quota-and-plans.md`.

## Provider-neutral usage reconciliation (open work)

Cross-provider FinOps reconciliation (Langfuse ↔ Hermes/Loki ↔ provider billing) is an
**open feature request** (tracked as [local-lab #82](https://github.com/TeaShaman-cyber/local-lab/issues/82);
historical context — `theseus-local-lab` #122, in the archive), not an implemented
contract. Do not
treat a local edit of the installed Hermes Langfuse plugin/runtime copy in
`%LOCALAPPDATA%\hermes` as the implementation: that copy is Hermes-owned, not canonical.
If a change is needed for this, roll it back locally and file a feature-request issue in
the canonical repo (owner decides by source path, not filename).

## Read-back protocol

For Langfuse CLI inspection:

1. Query the modern `observations` resource with `core,basic,model,usage,metrics,trace_context` as needed.
2. Parse the CLI HTTP envelope before reading records; the data is nested under `body.data`.
3. Inspect child `GENERATION` observations for model and usage; parent spans may intentionally omit them.
4. Never request or print `input`, `output`, credentials, or unbounded metadata when aggregate latency, usage, and errors answer the question.
5. Report `UNKNOWN` for absent usage/cost fields instead of zero.
