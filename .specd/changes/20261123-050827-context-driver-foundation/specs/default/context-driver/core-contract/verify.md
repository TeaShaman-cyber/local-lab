# Verification: Context Driver Core Contract

## Requirements

### Requirement: Source authority

#### Scenario: Projection cannot authorize a source mutation

- **GIVEN** a record exists in a private mirror or PostgreSQL projection but no upstream-supported Hermes write contract exists
- **WHEN** a component requests mutation or replay into Hermes from that record
- **THEN** the request is rejected before any source write is attempted
- **AND** the projection remains classified as derived evidence

### Requirement: Acquisition boundary

#### Scenario: Mismatched verification receipt is rejected

- **GIVEN** an immutable snapshot and a verification receipt bound to a different artifact identity
- **WHEN** ingestion validates the acquisition artifact
- **THEN** decoding does not start
- **AND** the last accepted projection head remains unchanged

#### Scenario: Live writable source is not canonical input

- **GIVEN** only a writable live `state.db` connection is available
- **WHEN** canonical ingestion is requested
- **THEN** ingestion fails closed rather than opening writable reconcile or migration paths

### Requirement: Source lineage

#### Scenario: Source identity change requires empty base

- **GIVEN** a projection already contains lineage `source-a`
- **WHEN** an import declares `source-b` for the same target without an explicit empty-base rebuild
- **THEN** the import is rejected
- **AND** no record is relabelled into the new lineage

### Requirement: Private mirror fidelity

#### Scenario: Historical control evidence is non-replayable

- **GIVEN** a snapshot contains admissions, worker receipts and coordination rows
- **WHEN** the private mirror retains those rows
- **THEN** each retained family has an explicit sensitivity and replay decision
- **AND** no active lease, worker or admission action is recreated

### Requirement: Canonical decoding

#### Scenario: Unknown payload remains private

- **GIVEN** a supported source row contains an unclassified namespace or provider-specific field
- **WHEN** a versioned decoder emits canonical records
- **THEN** the field is retained only as permitted private evidence or excluded
- **AND** no inferred canonical field is emitted for it

### Requirement: Derived and transient state

#### Scenario: FTS and lock mechanics are excluded

- **GIVEN** a snapshot contains FTS shadow tables, SQLite sequence data, locks, leases and heartbeats
- **WHEN** canonicalization runs
- **THEN** none of those records becomes canonical state solely due to presence
- **AND** any durable rebuild rule is handled by an explicit versioned policy

### Requirement: Projection semantics

#### Scenario: Failed promotion preserves accepted head

- **GIVEN** projection head `A` is accepted
- **AND** loading source image `B` fails before atomic promotion
- **WHEN** a consumer reads the projection head
- **THEN** head `A` and its provenance remain observable and unchanged
- **AND** partial records from `B` are not exposed as accepted

#### Scenario: Reimport is idempotent

- **GIVEN** the same lineage, source artifact identity, decoder version and verified input
- **WHEN** ingestion is repeated
- **THEN** the resulting accepted projection state is equivalent
- **AND** stable identities are not duplicated

### Requirement: Replay prohibition

#### Scenario: Fidelity does not grant permission

- **GIVEN** a private mirror can reconstruct every supported source row
- **WHEN** replay into Hermes is requested without a later approved authority specification
- **THEN** replay is refused

### Requirement: Privacy and sensitivity

#### Scenario: Failure output contains no payload secrets

- **GIVEN** decoding fails on a row containing credentials, a DSN or sensitive message content
- **WHEN** logs and machine-readable receipts are emitted
- **THEN** they identify the structural failure without containing the protected value

#### Scenario: External export requires explicit policy

- **GIVEN** canonical or private records are proposed for export beyond the local private boundary
- **WHEN** no field-level sensitivity policy and independent redaction gate exists
- **THEN** export is rejected

### Requirement: Schema drift

#### Scenario: Same version with changed shape fails closed

- **GIVEN** the source reports a known `schema_version`
- **AND** an object definition, generated column, index, trigger or foreign key differs from the accepted manifest
- **WHEN** source coverage validation runs
- **THEN** the source is rejected as unsupported drift
- **AND** the version number alone does not override the mismatch

#### Scenario: One-profile absence is not universal absence

- **GIVEN** a lazy or shared-state table is absent from one observed profile snapshot
- **WHEN** the coverage report is generated
- **THEN** the table is reported as unobserved or known-absent for that profile
- **AND** it is not declared nonexistent in the migration universe

### Requirement: Failure semantics

#### Scenario: Stage-specific failure is not partial success

- **GIVEN** acquisition succeeds and decoding fails
- **WHEN** the operation reports its result
- **THEN** the result names decoding as the failed stage
- **AND** it does not report projection acceptance or successful promotion

#### Scenario: Retry uses immutable input

- **GIVEN** a failed load of an accepted immutable artifact
- **WHEN** the operation is retried
- **THEN** retry uses the same artifact identity or starts a separately verified import
- **AND** retry does not mutate the source artifact

### Requirement: Verification evidence

#### Scenario: Agent report is insufficient when a postcondition is available

- **GIVEN** an agent reports success and an independent database, filesystem or CLI postcondition can be observed
- **WHEN** the implementation slice is evaluated for acceptance
- **THEN** acceptance remains incomplete until the independent postcondition is recorded with exact artifact and tool identity

### Requirement: SpecD lifecycle

#### Scenario: Missing required artifact blocks acceptance

- **GIVEN** a context-driver change lacks a required proposal, spec, verify, design or tasks artifact
- **WHEN** official schema validation runs
- **THEN** validation fails closed
- **AND** repository QA does not report success

#### Scenario: Agent assets cannot overwrite project contract silently

- **GIVEN** a SpecD agent plugin would generate or modify `AGENTS.md` or `.agents/skills`
- **WHEN** no separate compatibility decision has accepted the generated changes
- **THEN** plugin installation is not performed by the foundation workflow
