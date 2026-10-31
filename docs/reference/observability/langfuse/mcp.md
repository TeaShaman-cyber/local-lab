# Langfuse MCP: project authentication and inspection

## Verify the project boundary first

Langfuse public and secret API keys are scoped to one project. Before querying, identify which project the credential pair belongs to and whether the project contains Hermes traces, Hindsight OTEL observations, or both. Do not infer a missing Hermes trace from a query made with Hindsight-only credentials.

When multiple services share a project, query the modern observations endpoint with service/environment/trace-name filters where available. When projects differ, perform one bounded query per credential pair and keep the results labeled by project.

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

Hermes does not derive a Basic header from `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` automatically. If a derived `LANGFUSE_MCP_AUTH` variable is needed, the user owns adding it to the Hermes env; the agent must not write secret values into that file. For a one-off verification, construct the header only in the child process environment and never print it.

After changing an env-backed MCP credential, reload MCP or start a fresh Hermes process. Header placeholders can be expanded and cached at server-task startup, so a long-lived session may continue using the old value.

## Read-only first

Langfuse MCP advertises read and write tools by default. Discover the actual tool names, then configure an explicit read-only allowlist for observations, traces, metrics, scores, dashboards, and prompts. Do not expose write tools until the user approves the exact write payload.

## Read-back protocol

For Langfuse CLI inspection:

1. Query the modern `observations` resource with `core,basic,model,usage,metrics,trace_context` as needed.
2. Parse the CLI HTTP envelope before reading records; the data is nested under `body.data`.
3. Inspect child `GENERATION` observations for model and usage; parent spans may intentionally omit them. To verify an automatic fallback, compare their timestamps, status/error, and provider/model within the same trace or turn: a failed primary generation followed by a successful generation on another route is evidence. Distinguish auxiliary generations and explicit model changes when telemetry allows.
4. Never request or print `input`, `output`, credentials, or unbounded metadata when aggregate latency, usage, and errors answer the question.
5. Report `UNKNOWN` for absent usage/cost fields instead of zero.
