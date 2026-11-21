# Hermes schema-33 coverage research

- Date: 2026-11-21
- Issue: [local-lab#230](https://github.com/TeaShaman-cyber/local-lab/issues/230)
- Hermes source: `v0.23.3`, commit `caafe5389ae5`
- Decision: [ADR-0002](../adr/0002-high-fidelity-runtime-mirror.md)
- Machine contract: `tools/context_backend/coverage_matrix.yaml`

## Method

A post-update snapshot was accepted only after the existing independent verifier reported
`integrity=ok` and source schema version `33`. Research read:

- SQLite schema metadata, foreign keys and object definitions;
- table row counts;
- normalized `state_meta` key namespaces, never values;
- Hermes persistence DDL, migrations, interfaces and tests.

It did not read or print messages, prompts, reasoning, tool arguments, receipt values,
credentials, DSNs or other application payloads.

The generated fixture stores metadata only and has no local snapshot path, hash or row
values.

## Observed shape

```text
tables:   48
views:     2
triggers: 14
indexes:  68
schema_version: 33
fts_storage_version: 3
```

Source DDL and the snapshot agree for the objects present in this profile database.
Physical column order is not treated as semantic because Hermes evolves existing stores
with `ALTER TABLE ADD COLUMN`. This observed profile shape is not a claim that every lazy
or shared-state object in the schema-33 migration universe is present.

Point-in-time row counts used only to understand scale:

```text
messages                 196316
sessions                    496
session_model_usage          818
session_admissions          3063
state_meta                  3658
async_delegations              9
resumable_subagents           13
worker_executions              2
worker_receipts               88
```

## Source ownership

| Family | Primary source files | Meaning |
|---|---|---|
| schema/migrations | `persistence/sqlite/_schema_ddl.py`, `_schema.py`, `admin/_schema_history.py`, `admin/_reconcile.py` | Declared shape, one-shot migrations and heal paths |
| sessions/lineage | `conversation/sessions.py`, `_store/sessions.py`, `conversation/lineage.py`, `_store/compression.py` | Session lifecycle, branches, reset/compression/delegate lineage |
| messages | `conversation/messages.py`, `_store/messages.py`, `_store/identity.py`, `_store/compaction_display.py` | Transcript, tool/reasoning payloads, compaction and logical identity |
| usage | `conversation/usage.py`, `_store/usage.py`, `agent/turn_usage.py` | Stored token/cost attribution and aggregates |
| admissions/authority | `authority/admissions.py`, `authority/_admission_store.py`, `authority/runtime.py`, `authority/_mutation.py` | Admission ledger, mutation receipts and revision fences |
| workers/delegation | `authority/_runtime.py`, async delegation and resumable-subagent stores | Worker sequencing, durable receipts and delegation state |
| routing/coordination | `authority/_local.py`, routing and coordination stores | Routes, leases, heartbeats and delivery queues |
| search | `conversation/search.py`, `_store/search.py`, admin maintenance/recovery | Derived FTS projections and rebuild markers |
| hosted rooms | hosted-room persistence modules and shared-state placement | Room event log, grants, remote runs and derived policy checkpoints |

Paths are relative to the installed Hermes source root.

## Stable identities that must survive

- `sessions.id`; `parent_session_id` forms lineage but its edge kind also depends on
  `model_config` markers and `end_reason`.
- `messages.id` is a stable SQLite row anchor; `message_uid`, `tool_call_uid`,
  `tool_call_uids`, `absorbed_message_uids` and `display_identity` carry logical identity
  across compaction and provider shapes.
- `session_model_usage` has a six-column natural key including `task`.
- admissions use `admission_id` plus a unique principal/target/request tuple.
- worker receipts use `(execution_id, sequence)` and a payload digest.
- `conversation_generations` is deliberately never garbage-collected; removing it can
  reissue an old generation (ABA).
- hosted room events use `(room_id, seq)`; retired room ids are permanent tombstones.

## Derived and mechanical state

`messages_fts` and `messages_fts_trigram` are derived indexes over projection views. Their
filters and truncation rules matter for rebuilding search, but their virtual/shadow table
bytes are not logical evidence.

The following are engine or external-tool mechanics and stay outside the private mirror:

- FTS shadow tables;
- `sqlite_sequence`;
- `_litestream_lock` and `_litestream_seq`.

Views, triggers, indexes and foreign keys remain in the schema manifest because a change
in their contract can alter identity, delete ordering, display projection or search
semantics even when table columns do not change.

## Operational state is evidence, not authority

Admissions, workers, delegation and receipts are retained as private historical evidence
but are never replayed from the mirror. Their canonicalization is deferred until decoders
can exclude live control fields. Locks, leases, PIDs, heartbeats, retries and pending
queues may be retained only as private point-in-time evidence and are excluded from the
runtime-neutral canonical layer.

`delivery_obligations` also contains outgoing `content` and `last_error`; it is therefore
classified `local_sensitive` even though its canonical decision is `exclude_transient`.

This distinction is why the coverage matrix has separate `mirror` and `canonical`
decisions.

## `state_meta`

`state_meta` is an open namespace store, so table-level exclusion would lose durable
receipts and fences while table-level canonical inclusion would mix in maintenance noise.
It is therefore mirrored fully, while canonicalization is deferred until an executable,
versioned namespace registry exists.

Observed normalized namespace counts:

```text
gateway.admission.result.v1.*  3025
gateway.mutation.v1.*           530
gateway.local_policy.*            82
gateway.local_legacy.v1:*           4
gateway.local_policy.v1:*           4
gateway.viewer_close.*              2
other singleton markers             11
```

The singleton set includes schema/search/backfill/repair/maintenance markers and one
retired-session fence. Values were not inspected. Hermes source writes structured
receipts/results into some of these namespaces, so `local_sensitive` is the conservative
classification [INFERENCE]. Namespace decoders remain deferred.

## Evolution risks

1. `schema_version` does not encode the complete shape; versions 31/32 added some objects
   without a bump. The manifest, not the number alone, is the gate.
2. FTS layout version is independent from source schema version.
3. Writable-open reconcile/heal paths mutate schema and data. The mirror path stays
   immutable and never invokes them.
4. Some tables are lazy or split between profile `state.db` and install
   `shared-state.db`; absence means unobserved here, not unsupported. Known absent examples
   are recorded in the matrix, including driver execution/result, replica, attachment,
   router-claim, gateway-handoff, legacy-import and Telegram DM topic-root tables.
5. Data migrations are one-shot and do not preserve pre-migration state.
6. Foreign keys and triggers enforce retirement ordering. A table-only copy loses those
   semantics.
7. Usage writes are asynchronously flushed; one snapshot can capture a consistent but
   not yet fully attributed point in time.
8. Source JSON sidecars carry identities and lineage that cannot be reconstructed from
   selected columns.

## Open work

- build the private full-row envelope without emitting payloads to logs;
- classify `state_meta` namespaces with source-backed decoders;
- define canonical message/tool/reasoning shapes without flattening provider evidence;
- define tombstone and retention policy;
- inventory and gate `shared-state.db` separately;
- add PostgreSQL tables only after the envelope and decoder contracts are accepted.

A custom harness, query service, MCP, hooks, embeddings and cloud export remain outside
this phase.
