# Context Driver Core Contract

## Purpose

The context driver exists to make Hermes runtime history queryable without turning an upstream implementation detail or a derived projection into a new source of authority. This specification defines the authority, evidence, privacy, replay, projection and failure invariants that every future context-driver slice MUST preserve.

## Requirements

### Requirement: Source authority

Hermes-owned runtime state SHALL remain authoritative for facts produced by Hermes. The context driver MUST NOT treat a snapshot, private mirror, canonical record, PostgreSQL projection, search index, cache or receipt as permission to mutate or replay state into Hermes.

### Requirement: Acquisition boundary

The canonical ingestion route SHALL consume an immutable, independently verified snapshot or an upstream-supported logical export. It MUST NOT read live Hermes tables as its canonical route or invoke writable-open reconcile, migration or healing paths.

An accepted acquisition artifact MUST bind source lineage, source schema evidence, artifact identity, integrity result and verification freshness. Missing, failed, stale or mismatched evidence MUST be rejected before decoding.

### Requirement: Source lineage

A configured `source_id` SHALL identify one source-authority lineage and MUST NOT be derived from a temporary filename or snapshot path. Changing `source_id` MUST start a new lineage and MUST require an explicit empty-base rebuild.

Each accepted source image SHALL have an immutable artifact identity. Provenance metadata MUST NOT silently change the lineage or artifact identity.

### Requirement: Private mirror fidelity

The private mirror SHALL preserve supported source rows and source relationships with enough fidelity to re-run versioned decoders and audit an emitted canonical record. Source-specific table names, payload shapes and schema versions MUST remain confined to the acquisition/decoder/private-mirror boundary.

The private mirror MUST NOT reproduce active Hermes control behaviour. Admissions, worker state, delegation state, receipts, routes and coordination records MAY be retained as historical evidence only when their sensitivity and replay policy are explicit.

### Requirement: Canonical decoding

A runtime-neutral canonical record SHALL be emitted only by a versioned decoder with explicit source-schema support. Canonical records MUST NOT depend on Hermes table names and MUST preserve stable identity, lineage and provenance required by their domain semantics.

Unclassified namespaces, provider-specific payloads and ambiguous control fields MUST remain private evidence or be excluded. They MUST NOT be flattened into canonical fields by inference.

### Requirement: Derived and transient state

Derived search indexes, FTS shadow tables, SQLite engine mechanics, temporary locks, leases, PIDs, heartbeats, retry counters and pending queues MUST NOT become canonical state solely because they are present in a snapshot.

Rebuild rules or durable evidence represented by views, triggers, indexes or metadata namespaces MUST be handled by explicit versioned policy rather than copied implicitly.

### Requirement: Projection semantics

Consumer projections SHALL be read-only from the perspective of context-driver consumers, disposable and rebuildable from accepted evidence plus versioned decoders. Projection writes MUST be transactional, idempotent for the same source image and isolated by source lineage.

A failed import MUST leave the last accepted projection head observable and unchanged. A successful promotion MUST atomically bind the new head to its source artifact, decoder version and verification evidence.

### Requirement: Replay prohibition

No mirror, canonical or projection record SHALL be replayed into Hermes runtime unless a future specification explicitly changes authority and defines an upstream-supported write contract. Mere fidelity, transport access or historical completeness MUST NOT be interpreted as replay permission.

### Requirement: Privacy and sensitivity

Secrets, credentials, tokens, DSNs and sensitive application payloads MUST NOT be emitted to logs, fixtures, public receipts or public test artifacts. Schema fixtures and structural receipts SHALL contain only the minimum metadata required for deterministic verification.

Any export beyond the local private boundary MUST have an explicit field-level sensitivity policy and an independently verifiable redaction or exclusion gate.

### Requirement: Schema drift

Source support SHALL be gated by a structural manifest, not by `schema_version` alone. Unknown tables, columns, views, triggers, indexes, foreign keys, generated columns or source object definitions MUST cause a closed failure until classified by a versioned coverage decision.

The gate MUST distinguish observed profile shape from the complete migration universe and MUST NOT claim unsupported absence from one snapshot.

### Requirement: Failure semantics

Acquisition, decoding, loading and promotion SHALL expose distinct failure states. A failure MUST identify the stage and MUST NOT be reported as a successful or partially accepted projection.

Retry MUST be idempotent for immutable input. Recovery MUST NOT depend on replaying transient runtime state or mutating the source artifact.

### Requirement: Verification evidence

Every implementation slice SHALL define deterministic verification scenarios before it changes runtime behaviour. Acceptance evidence MUST include the exact source artifact or fixture identity, decoder/tool version and observed postcondition relevant to the slice.

A tool exit code, agent report or green unit test alone MUST NOT establish semantic acceptance when an independent domain postcondition is available.

### Requirement: SpecD lifecycle

New context-driver behaviour SHALL be introduced through a SpecD change that preserves exploration context and separates proposal, specification, verification, design and tasks. Required artifacts MUST pass official schema validation before implementation is accepted.

The project MUST use a pinned, reproducible SpecD runtime. Generated agent assets MUST NOT overwrite project-owned `AGENTS.md` or `.agents/skills` without a separate compatibility decision.

## Constraints

- This specification does not authorize a custom agent harness, live Hermes hooks, cloud export, embeddings, MCP endpoints or mutation of upstream runtime state.
- Language selection is not fixed by this contract; any implementation language MUST satisfy the same observable requirements and verification scenarios.
- SpecD implementation coverage and Hermes source-schema coverage are separate concerns and MUST remain separately named and verified.

## Spec Dependencies

_none — this is a global constraint spec_
