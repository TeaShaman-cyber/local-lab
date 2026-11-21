# ADR-0001: Upstream-safe source boundary for the context backend

- Status: Accepted
- Date: 2026-11-19
- Issue: [local-lab#230](https://github.com/TeaShaman-cyber/local-lab/issues/230)

## Context

The context driver needs a queryable projection of Hermes agent-runtime history.
Hermes owns `state.db`, and its internal SQLite schema can change with an upstream
release. Reading the live tables directly would make the context backend depend on
an unsupported implementation detail and could also compete with the gateway's
writer.

ADR-0002 deepens this boundary into a private source-specific mirror plus a
runtime-neutral canonical model. The first `sessions`/`messages` bundle below remains
an accepted substrate, not the complete subject model.

Hermes has a maintained backup path, but a whole-home backup is not automatically a
logical export contract: it can contain configuration, credentials, and other files
that must not enter a context projection or a future cloud target.

The repository already has a verified local snapshot path in
`tools/state_db_backup`: SQLite reads a live database into a consistent snapshot and
an independent verifier checks the artifact.

## Decision

The source boundary is an upstream-safe snapshot or export artifact, not a live
`state.db` connection:

```text
upstream-supported acquisition
  -> versioned source decoder and schema manifest
  -> private source-specific mirror
  -> project-owned runtime-neutral canonical records
  -> local PostgreSQL projection
```

The first local implementation consumes a verified SQLite state snapshot. The
existing `VACUUM INTO` snapshot path is the acquisition mechanism for now. A future
adapter may consume an upstream logical export if Hermes provides one, but it must
prove the artifact's versioning, consistency, and secret boundary first.

Verification is a separate source boundary: `tools/state_db_backup/verify.py` emits a
versioned machine-readable receipt binding the snapshot name, size, SHA-256, counts,
integrity result, freshness verdict, and verification time. The context exporter must
reject a missing, failed, stale, or mismatched receipt before producing a canonical
bundle.

The decoder is the only component allowed to know Hermes source table names. The
canonical record model, PostgreSQL schema, query service, and future Neon adapter
do not depend on those table names.

`source_id` is a stable configured identity for one Hermes profile/source authority;
it is not derived from a temporary snapshot path or filename. Changing `source_id`
starts a new source lineage and requires an explicit empty-base rebuild. A snapshot
hash identifies one exact source image within that lineage, and receipt provenance
cannot silently change the lineage identity.

A direct live-schema reader may exist later as a diagnostic or compatibility
fallback. It is not the canonical route.

## Consequences

### Positive

- Hermes updates fail closed at the source decoder instead of silently corrupting a
  projection.
- A verified snapshot can be replayed, compared, and used to rebuild an empty target.
- PostgreSQL and a future Neon deployment consume the same canonical records.
- A failed import can leave the last successful snapshot head untouched.
- Raw Hermes backup archives and credentials are not automatically exported.

### Negative

- A new Hermes schema version requires a decoder update and fixtures.
- Snapshot-based incremental replication may need to diff full snapshots until Hermes
  exposes a change/export stream.
- The first implementation needs an explicit canonical model instead of reusing the
  convenient SQLite schema.

## First implementation slice

1. Validate a read-only SQLite snapshot, its source schema version, and an independent
   verification receipt.
2. Export supported `sessions` and `messages` into a private canonical bundle with
   source IDs and provenance, including receipt provenance.
3. Load the bundle transactionally into local PostgreSQL with idempotent keys and an
   atomic snapshot head.
4. Prove replay, source immutability, receipt mismatch rejection, failure rollback,
   and rebuild from empty.

This slice does not change Hermes hooks, Honcho, Hindsight, Neon, or cloud sharing.
