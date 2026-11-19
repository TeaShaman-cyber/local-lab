# Context backend: research and reconciliation plan

Status: proposed research checkpoint for [#230](https://github.com/TeaShaman-cyber/local-lab/issues/230).

This note answers a narrow question: how should the backend detect additions,
updates, and deletions while preserving the upstream-safe source boundary? [INFERENCE]
It does not propose a hook, MCP server, CLI, retrieval layer, Neon deployment, or
cloud export. [INFERENCE]

## Executive conclusion

The next backend phase should be **snapshot-based reconciliation**, not CDC. [11][12][13]
Hermes currently gives us a verified SQLite snapshot boundary, but no project-owned
logical change feed. The safe first implementation is therefore:

```text
verified immutable snapshot
  -> canonical records + deterministic record fingerprints
  -> compare with the last promoted snapshot for the same source
  -> staged reconciliation result
  -> atomic projection-head promotion
```

A snapshot hash is a valid cursor for an exact source image. [INFERENCE] It is not
a row-level change cursor. [INFERENCE] We should not use `timestamp`,
`last_activity_at`, or SQLite `rowid` as a change feed until Hermes exposes a
supported contract that gives those fields that meaning. [INFERENCE]

## What established implementations teach us

### 1. SQLite backup is the right acquisition primitive, not a logical export

SQLite's Online Backup API produces a consistent destination snapshot and can copy
incrementally, releasing the source read lock between steps. If writes restart a
copy often enough, the backup may fail to finish, so the acquisition layer still
needs bounded retry/timeout and a failed receipt rather than a partial success [1].

`VACUUM INTO` leaves the original unchanged and produces a consistent logical
snapshot, but an interrupted operation can leave the output incomplete or corrupt;
the output must therefore be staged, verified, and published atomically [2]. These
mechanisms copy database state. Neither one supplies row-level insert/update/delete
events or deletion tombstones.

**Takeaway for local-lab:** keep the existing verified snapshot route. Treat the
snapshot SHA-256 plus the verification receipt as the source-image cursor. Do not
call the current `VACUUM INTO` path an upstream logical-export API.

### 2. Litestream is useful evidence for physical replication, but solves a different problem

Litestream continuously copies SQLite WAL pages into LTX files, assigns contiguous
transaction IDs, checks for gaps during restore, and restores by replaying whole
LTX-file ranges [3]. Its restore command explicitly supports dry-run planning,
read-only follow mode, integrity checks, and a crash-resume sidecar [4].

This is a strong model for physical backup/archive properties: contiguous sequence,
explicit restore boundary, no silent gap, and a read-only replica. It does **not**
produce project-owned `session`/`message` records, canonical provenance, or a
semantic deletion model. Introducing it now would also add a long-running process,
WAL retention, and operational storage without removing the need for a decoder and
reconciliation layer. [INFERENCE]

**Takeaway for local-lab:** borrow the ideas of contiguous cursors, explicit gaps,
read-only replicas, dry-run plans, and crash-safe checkpoint publication. Do not
adopt Litestream as the context projection itself. Revisit it only if the acquisition
problem becomes the bottleneck and Hermes permits the required operational surface.

### 3. Cursor-based incremental syncs are safe only when the source owns the cursor semantics

Airbyte's incremental model persists stream state after records are successfully
transmitted and resumes from that state. Its datetime cursor is commonly an
`updated_at` value, and its connectors intentionally use inclusive cursor windows
for an at-least-once guarantee [7]. Airbyte also documents the failure mode: if a
record changes without its cursor field changing, the change is not emitted [8].

This is the important distinction for Hermes. `messages.timestamp` describes a
message, but the current source contract does not establish it as a monotonic
mutation sequence. It cannot safely detect edits, late writes, or deletions. A
cursor based on “newest message timestamp” can be a freshness signal, but not a
complete replication cursor. [11][12][13]

**Takeaway for local-lab:** if a future source-owned cursor appears, checkpoint it
only after the candidate projection is durable, use an inclusive replay window, and
make replay idempotent. Until then, use complete snapshot comparison.

### 4. CDC and incremental snapshots need source cooperation

Debezium's incremental snapshot design records a bounded primary-key endpoint,
processes ordered chunks, and stores table/chunk/last-key state so a restart can
continue. Its watermark/window logic reconciles changes that race with a chunk
read [6]. That is a useful design for resumable work, but it assumes a change log or
transaction-log integration.

Debezium's SQLite connector makes the boundary especially clear: SQLite has no
external replication log, so the connector installs insert/update/delete triggers
and writes a `_debezium_cdc_log` table in the same transactions as the source
writes. It requires read-write access, changes the source database, is incubating,
and does not support incremental snapshots [5]. That is incompatible with our
source rule: Hermes owns `state.db`, and our importer must be read-only.

**Takeaway for local-lab:** borrow chunk checkpoints and “do not advance past a
gap” semantics only for our own staged processing. Do not install triggers, CDC
tables, or a writer into Hermes `state.db`.

### 5. CR-SQLite demonstrates explicit logical change state, but requires an instrumented schema

CR-SQLite exposes a logical changes table and tracks a peer's last-seen database
version so a sync can request only changes after that cursor [9]. The model requires
both databases to share CRR-enabled schema and the tracked-peer state is part of the
sync protocol [10].

**Takeaway for local-lab:** explicit source identity, peer/cursor ownership, and
out-of-order/gap handling are valuable. CR-SQLite itself is not applicable to an
upstream-owned Hermes database that we cannot instrument.

## Proposed phase 3: snapshot reconciliation

### Invariants

1. **Source authority:** the verified snapshot and its receipt are the only source
   evidence; the importer opens them read-only and never writes `state.db`. [INFERENCE]
2. **Exact cursor:** `(source_id, snapshot_sha256)` identifies an exact source image.
   `source_id` is a stable configured source identity, not a transient output path. [INFERENCE]
3. **Canonical identity:** a record key is `(source_id, record_type,
   source_record_id)`. Hermes table names remain confined to the decoder. [INFERENCE]
4. **Deterministic equality:** a record fingerprint is computed from normalized
   canonical data, excluding snapshot-specific provenance (`snapshot_sha256`,
   receipt hash, export time). The same logical record in two snapshots must hash
   identically even if JSON key order differs. [INFERENCE]
5. **Complete diff:** reconciliation reports `added`, `changed`, `deleted`, and
   `unchanged`. A missing key in the candidate snapshot is a deletion from the
   current projection; it must never be silently treated as “not read yet”. [INFERENCE]
6. **Atomic visibility:** a failed or interrupted run cannot move
   `context_snapshot_heads`. The previous head remains queryable until the new
   snapshot and its reconciliation result are complete. [INFERENCE]
7. **Rebuildability:** the projection can be dropped and rebuilt from a verified
   snapshot. No cursor state stored only in PostgreSQL may be required to recover. [INFERENCE]
8. **At-least-once processing:** a candidate snapshot may be replayed. Replaying
   the same `(source_id, snapshot_sha256)` is idempotent and cannot duplicate
   current records or change counts. [INFERENCE]

### Implementation slices

#### A. Define source cursor and canonical fingerprint

- Document the lifecycle of `source_id` and reject accidental changes to it. [INFERENCE]
- Add a versioned canonical fingerprint function for sessions and messages. [INFERENCE]
- Add fixtures proving that provenance changes do not change a record fingerprint,
  while a content/metadata change does. [INFERENCE]
- Keep `snapshot_sha256` and receipt hash in provenance, not in logical equality. [INFERENCE]

#### B. Add a staging reconciliation run

- Create a run record containing source ID, candidate snapshot hash, base head hash,
  receipt hash, format/schema versions, start/completion times, and status. [INFERENCE]
- Validate receipt, bundle, record keys, and record counts before opening the target. [INFERENCE]
- Compare candidate keys/fingerprints with the last promoted head. The initial run
  compares against an empty base; an identical hash is a no-op; a different hash is
  a new candidate even if its counts are unchanged. [INFERENCE]
- Materialize a diff summary and, for auditability, per-record change rows with
  `added`, `changed`, or `deleted` plus old/new fingerprints. Keep prior snapshots
  immutable; do not physically delete source evidence from the projection store. [INFERENCE]

#### C. Promote one new head transactionally

- Write the candidate snapshot rows and reconciliation metadata in one transaction. [INFERENCE]
- Promote `context_snapshot_heads` only after all records, counts, fingerprints, and
  diff totals pass validation. [INFERENCE]
- On any failure, roll back the candidate transaction and leave the old head and its
  queryable rows untouched. [INFERENCE]
- Make a second invocation with the same snapshot hash return the existing result
  or a verified no-op, not a second logical import. [INFERENCE]

#### D. Add explicit deletion tests before any optimization

The deterministic test matrix must cover:

- first snapshot: all records are `added`; [INFERENCE]
- second snapshot with one new row: exactly one `added`; [INFERENCE]
- same key with changed canonical content: exactly one `changed`; [INFERENCE]
- a row absent from the candidate: exactly one `deleted`, and it is absent from the
  promoted head view while remaining present in the previous immutable snapshot; [INFERENCE]
- same bytes replayed: no new changes and no duplicate current rows; [INFERENCE]
- changed bytes with unchanged counts: candidate is not treated as a no-op; [INFERENCE]
- malformed/stale/mismatched receipt: no target mutation; [INFERENCE]
- crash/failure before promotion: old head remains unchanged; [INFERENCE]
- rebuild from empty: current head and diff results match the original run; [INFERENCE]
- stable output under reordered JSON input and repeated runs. [INFERENCE]

A property-based test would be useful for set-difference invariants, but a small
executable fixture matrix is the required first gate. [INFERENCE]

## Acceptance trace for #230

This phase is complete only when the repository can demonstrate all of the following
without a real transcript export:

- a machine-readable reconciliation receipt identifies source, base head, candidate
  snapshot, counts, diff totals, and verifier/format versions; [INFERENCE]
- additions, updates, and deletions are detected by deterministic fixtures; [INFERENCE]
- a failed candidate leaves the previous promoted head intact; [INFERENCE]
- replay of the same verified snapshot is idempotent; [INFERENCE]
- the target can be dropped and rebuilt from the same snapshot; [INFERENCE]
- source immutability is asserted by a before/after hash and read-only open; [INFERENCE]
- `tools/dev/check.sh` runs the new tests; [INFERENCE]
- no hook, MCP, Honcho/Hindsight, Neon, cloud publication, retrieval, or query
  contract is changed. [INFERENCE]

The issue/PR must identify the phase as `replication/reconciliation` and include a
review check against the original source-authority, atomic-promotion, and rebuild
invariants. [INFERENCE] Passing tests alone are not sufficient if the acceptance
contract has changed. [INFERENCE]

## Explicitly deferred

- SQLite trigger-based CDC or any write to Hermes `state.db`. [INFERENCE]
- Litestream or another physical WAL replicator as a new runtime dependency. [INFERENCE]
- Debezium, Airbyte, or CR-SQLite as imported infrastructure. [INFERENCE]
- Incremental timestamp/rowid cursors without an upstream guarantee. [INFERENCE]
- Query service, retrieval/ranking, embeddings, MCP, hooks, and CLI adapters. [INFERENCE]
- Neon/cloud export, credentials, federation, sharding, and external publication. [INFERENCE]

These may become later adapters or acquisition options, but none is implied by this
phase. [INFERENCE]

## Sources

[1] https://sqlite.org/backup.html
[2] https://www.sqlite.org/lang_vacuum.html
[3] https://litestream.io/how-it-works
[4] https://litestream.io/reference/restore
[5] https://debezium.io/documentation/reference/connectors/sqlite.html
[6] https://debezium.io/blog/2021/10/07/incremental-snapshots
[7] https://docs.airbyte.com/platform/connector-development/config-based/understanding-the-yaml-file/incremental-syncs
[8] https://docs.airbyte.com/platform/using-airbyte/core-concepts/sync-modes/incremental-append
[9] https://vlcn.io/docs/cr-sqlite/networking/whole-crr-sync
[10] https://vlcn.io/docs/cr-sqlite/networking/background
[11] https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/backup-and-transfer.md
[12] https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/backup_sqlite.py
[13] https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/backup.py
