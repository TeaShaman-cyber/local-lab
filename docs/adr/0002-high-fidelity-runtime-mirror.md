# ADR-0002: High-fidelity runtime mirror and canonical model

- Status: Accepted
- Date: 2026-11-21
- Issue: [local-lab#230](https://github.com/TeaShaman-cyber/local-lab/issues/230)
- Supersedes: the assumption that `sessions` and `messages` are the complete subject model

## Context

The first context-backend slice proved a safe path from a verified Hermes snapshot to a
rebuildable PostgreSQL projection. Its `sessions` and `messages` bundle was an
implementation substrate, not a complete model of agent-runtime history.

A future context driver needs tool, delegation, execution, usage, lineage, receipt and
other runtime evidence that cannot be reconstructed from message text. The same evidence
may eventually support a project-owned model/agent harness. That future is a design
constraint, not a current commitment or authority change.

The verified Hermes v0.23.3 schema-33 snapshot contains 48 tables, 2 views, 14 triggers
and 68 indexes. Source DDL and snapshot metadata match. The source also contains two
classes that must not be collapsed:

- historical and operational evidence worth retaining;
- live control state such as leases, PIDs, heartbeats and retry queues that must never be
  replayed as portable runtime state.

A single include/exclude flag cannot express that distinction.

## Decision

The backend has two explicit data layers and two independent coverage decisions.

```text
verified immutable source snapshot
  -> schema manifest and drift gate
  -> private source-specific mirror
  -> runtime-neutral canonical model
  -> reconciliation and rebuildable projections
```

### Private source-specific mirror

The mirror preserves exact rows from explicitly included Hermes tables. A table with
`mirror: include_full_row` retains every declared column verbatim, including fields that
are source-specific, sensitive or transient. Mirror records are immutable snapshot
evidence; they are not commands and are never replayed into Hermes or another runtime.

SQLite/FTS/Litestream mechanics are excluded or derived because they are rebuildable
engine state rather than Hermes logical state.

### Runtime-neutral canonical model

Canonical records expose project-owned semantic entities and relations. They do not
carry Hermes table names as their interface. Each source table independently declares
whether canonical state is included, derived, deferred, or excluded as
transient/source-specific/mechanic.

Operational evidence such as admissions, delegations, worker executions and receipts is
valuable private mirror history, but canonicalization is deferred until a decoder can
separate historical evidence from live control fields. Locks, leases, PIDs, heartbeats
and retry queues remain mirror-only evidence. Every current table has a machine-checked
`replay: forbidden` decision.

### Coverage matrix

`tools/context_backend/coverage_matrix.yaml` is the single source of truth for the
schema-33 coverage decision. For every table it records:

- semantic family and source authority class;
- private-mirror decision;
- canonical decision;
- replay policy and sensitivity class;
- primary-key identity;
- rationale.

The checked-in schema fixture contains metadata only: object names, `table_xinfo`
column contracts (including defaults and generated/hidden state), primary-key ordinals,
foreign keys, full table-DDL hashes and hashes of view/trigger/index definitions. It does
not contain application rows, prompts, messages, tool arguments, credentials or DSNs.

The validator requires all 48 tables observed in the inspected profile snapshot, both
views and all 14 triggers to be classified. Primary keys must match the schema fixture.
This is not a complete schema-33 migration universe: lazy and install-level shared-state
tables may be absent from this profile fixture and require separate manifests.

### Fail-closed drift

The manifest observes all objects present in the inspected database. Included tables
preserve all currently declared columns. A new, removed or changed table, column, default,
generated-column contract, full table DDL, foreign key, view, trigger or index is reported
as schema drift by `compare_schema_manifests` and the `--observed` CLI exits nonzero.

The repository gate currently validates the fixture/matrix contract. Live observed
comparison is a required manual gate in this slice; it is not yet wired into
`export_snapshot.py` or promotion. Automatic promotion blocking is therefore deferred
rather than claimed as an existing mechanism.

This gives additive source evolution a visible review path without silently dropping data
or silently adopting an upstream meaning.

### Sensitive data

The mirror is private and local. Prompt bodies, message content, reasoning, tool payloads,
delegation payloads and receipts may contain credentials or personal data. Their inclusion
in a private mirror does not authorize logging, publication, cloud export or use in a query
response.

Any external target or redacted export requires a separate decision and verification
contract. ADR-0001's secret boundary remains in force.

### Current authority

Hermes `state.db` remains the source authority. PostgreSQL and mirror artifacts remain
read-only, disposable and rebuildable projections. A future custom harness may become a
new authority only through a separate ADR, migration and observed postcondition.

## Key classifications

The YAML matrix owns the exhaustive table list. The important families are:

- authoritative history: sessions, messages and prompts;
- durable/operational evidence: usage, admissions, worker receipts, delegations,
  resumable subagents and generation fences;
- source-specific operational state: gateway routing and mixed `state_meta` namespaces;
- transient mirror-only evidence: locks, leases, heartbeats, queues and expiring grants;
- derived state: FTS projections;
- excluded mechanics: FTS shadow tables, SQLite sequence state and Litestream tables.

`state_meta` is mirrored as a complete table, but canonicalization is deferred until an
executable, versioned key registry exists. Its values are not inspected by the schema
fixture. Because Hermes source writes structured receipts/results into this open namespace,
the whole table is conservatively `local_sensitive` until namespace-level rules exist.

`delivery_obligations` is also `local_sensitive`: alongside transient queue/lease state it
stores outgoing content and error text. Sensitivity does not make either table replayable;
both remain non-canonical operational evidence.

The present manifest covers one observed profile `state.db` shape. Install-level
`shared-state.db` needs its own verified manifest and matrix before hosted-room authority
or coverage can be called complete. Hosted-room tables observed in the profile snapshot
are mirror evidence only and have `canonical: defer`.

## Interface

The current slice exposes two small interfaces:

```text
inspect_schema(snapshot) -> schema manifest
validate_coverage_matrix(matrix, manifest) -> PASS or fail-closed error
compare_schema_manifests(expected, observed) -> deterministic drift findings
```

These interfaces read metadata only and never mutate the snapshot.

The source-row envelope, canonical decoders, tombstones and PostgreSQL persistence are
separate later slices. PR #273's source-independent reconciliation interface remains the
comparison seam for those records.

## Consequences

### Positive

- New Hermes fields are detected when the observed-manifest gate is run.
- Transient source evidence can be retained without polluting portable canonical state.
- A future adapter or harness can target project-owned entities instead of Hermes tables.
- Coverage decisions are reviewable and machine-checkable.
- The metadata comparator detects schema changes without reading row payloads.

### Negative

- Every source-schema change needs an explicit fixture and matrix review.
- Private mirror storage will be larger than the current sessions/messages bundle.
- Sensitive local evidence requires strict logging and export discipline.
- Canonical decoders need versioned, family-specific work instead of one generic row copy.

## Rejected alternatives

### Keep only sessions and messages

Rejected because it loses tool, delegation, execution, usage, receipt and runtime-lineage
evidence needed by the context driver and a possible future harness.

### Copy SQLite into PostgreSQL table-for-table

Rejected because it makes SQLite mechanics and Hermes table names the permanent project
interface and invites accidental replay of live control state.

### Automatically accept unknown columns

Rejected because upstream changes would silently acquire project meaning. The manifest
may observe the change, but promotion waits for an explicit matrix decision.

### Exclude all operational tables

Rejected because durable admissions, receipts, generation fences and delegation ledgers
are historical evidence even though they must never be replayed.

## Verification

The repository gate must prove:

1. all observed schema-33 tables, views and triggers are covered;
2. primary keys match the schema fixture;
3. source kind and supported source version are enforced;
4. unknown or changed table DDL, columns, views, triggers and indexes produce deterministic drift;
5. incompatible axis decisions, replay permission, or missing sensitivity fail closed;
6. metadata inspection does not read row values or change snapshot bytes;
7. the existing source, receipt, reconciliation and PostgreSQL tests remain green.

## Deferred

- source-row extraction and mirror bundles;
- canonical entity decoders and relation tables;
- tombstone and retention policy;
- PostgreSQL mirror persistence and head promotion;
- `shared-state.db` coverage;
- automatic observed-schema validation inside export/promotion;
- query/retrieval interfaces, hooks, MCP and cloud targets;
- a custom model/agent harness or authority migration.
