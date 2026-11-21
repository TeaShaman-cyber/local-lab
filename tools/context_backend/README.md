# Context backend

This directory contains the local, read-only substrate for the future context driver.
It does **not** install a Hermes hook and does not write to `state.db`.

```text
verified Hermes snapshot
  -> schema manifest and coverage gate
  -> private source-specific mirror (deferred)
  -> runtime-neutral canonical model
  -> transactional PostgreSQL projection
```

## Source boundary

Use a verified offline snapshot produced by the existing local snapshot path:

```bash
python tools/state_db_backup/verify.py --json \
  --receipt E:/theseus-backups/state-db/receipts/state-YYYYmmdd-HHMMSS.receipt.json
python tools/context_backend/export_snapshot.py \
  E:/theseus-backups/state-db/snapshots/state-YYYYmmdd-HHMMSS.db \
  --receipt E:/theseus-backups/state-db/receipts/state-YYYYmmdd-HHMMSS.receipt.json \
  --validate-only
```

The exporter uses `mode=ro&immutable=1`. It never opens the live database and
never modifies the source. It requires the independent verifier receipt and matches
the receipt's snapshot name, byte size, SHA-256, integrity verdict, freshness verdict,
and session/message counts. The first decoder supports only the observed Hermes
schema version `33`; an unknown version or receipt fails closed before any bundle or
PostgreSQL write.

The exported bundle contains message content and is private data. Keep it outside
the repository and do not send it to Neon automatically:

```bash
python tools/context_backend/export_snapshot.py SNAPSHOT \
  --receipt SNAPSHOT.receipt.json \
  --output-dir C:/Users/s.poklad/AppData/Local/hermes/cache/scratch/context-bundle
```

The output directory is created atomically and must not already exist.

## High-fidelity coverage contract

The current exporter remains the accepted `sessions`/`messages` substrate. It is not the
complete subject model. ADR-0002 defines two later layers: a private source-specific
full-row mirror and a project-owned runtime-neutral canonical model.

`coverage_matrix.yaml` records both decisions independently for every table in the
inspected v0.23.3 profile snapshot. This is an observed profile shape, not a claim that
all lazy migration or install-level shared-state objects exist in every schema-33
database. The checked-in manifest contains schema metadata only; it contains no
application rows, prompts, messages, tool arguments, credentials or DSNs.

Every table declares `replay: forbidden` and a sensitivity class. The validator rejects
missing replay/sensitivity declarations and incompatible mirror/canonical decision pairs.
Operational ledgers, `state_meta`, and hosted-room tables remain available as private
mirror evidence while canonicalization is deferred until family-specific decoder,
namespace, and shared-state contracts exist.

The matrix intentionally forbids YAML merge keys and duplicate mapping keys so every
decision remains explicit at its point of use. The JSON manifest likewise rejects
duplicate object keys.

Validate the repository contract:

```bash
uv run --no-project --with pyyaml \
  python -m tools.context_backend.coverage \
  --matrix tools/context_backend/coverage_matrix.yaml \
  --manifest tools/context_backend/fixtures/hermes_schema_v33.json
```

To compare a verified private snapshot with the checked-in schema, generate an observed
metadata manifest outside the repository, then pass it as `--observed`:

```bash
python -m tools.context_backend.schema_manifest SNAPSHOT --output OBSERVED.json
uv run --no-project --with pyyaml \
  python -m tools.context_backend.coverage \
  --matrix tools/context_backend/coverage_matrix.yaml \
  --manifest tools/context_backend/fixtures/hermes_schema_v33.json \
  --observed OBSERVED.json
```

The comparator detects unknown or changed tables, columns, defaults, generated-column
metadata, full table DDL, foreign keys, views, triggers and indexes. The command exits
nonzero on drift. The repository gate validates the checked-in fixture and matrix; live
`--observed` comparison is a required manual gate in this slice and is not yet invoked by
`export_snapshot.py`. Wiring the comparison into export/promotion is deferred, so this
document does not claim automatic promotion blocking yet.

## Local PostgreSQL

The compose file is a development target only. It binds to loopback and requires a
password from the environment; do not put the password in git:

```bash
cd infra/compose/context-backend
export CONTEXT_POSTGRES_PASSWORD='use-a-local-secret'
docker compose up -d
export CONTEXT_POSTGRES_DSN='postgresql://context_backend:use-a-local-secret@127.0.0.1:55432/context_backend'
```

The loader uses `psycopg` only when invoked. Keep it outside the Hermes environment:

```bash
uv run --no-project --with 'psycopg[binary]>=3.2,<4' \
  python tools/context_backend/load_postgres.py BUNDLE
```

The loader validates the bundle before connecting, creates the schema and imports
both record types in one transaction, then promotes `context_snapshot_heads` only
after every row succeeds. Replaying the same bundle is idempotent. A failed import
leaves the previous head unchanged.

## Canonical reconciliation (phase 3A)

`reconcile.py` contains the first source-independent reconciliation seam. It compares
two canonical record iterables by `(source_id, record_type, source_record_id)` and a
versioned deterministic fingerprint. The `source_id` is a stable configured identity
for one Hermes profile/source authority, not a snapshot path; changing it starts a
new source lineage and requires an explicit empty-base rebuild. Identity fields and
the documented snapshot/receipt provenance fields are excluded from the fingerprint,
while logical content changes produce a `changed` record.

The pure function reports `added`, `changed`, `deleted`, and `unchanged` counts and
returns deterministic per-record changes. It never writes to `state.db` or
PostgreSQL. The PostgreSQL loader does not yet persist reconciliation runs or diff
rows; that is the next integration slice, so this module must not be described as a
live CDC implementation.

## Tests

The source boundary and phase-3A reconciliation seam are covered without PostgreSQL
or real transcript contents:

```bash
uv run --no-project --with pytest python -m pytest tools/context_backend -q
```

The canonical repository gate runs the same directory together with the backup and
mutation tests. This local command may report `SKIP` for unavailable external
linters; hosted CI installs the pinned tools and runs the same gate with `STRICT=1`:

```bash
uv run --no-project --with pytest,ruff,pyyaml bash tools/dev/check.sh
```

Architecture and research are recorded in:

- `docs/adr/0001-context-backend-source-boundary.md`;
- `docs/adr/0002-high-fidelity-runtime-mirror.md`;
- `docs/research/context-backend-reconciliation.md`;
- `docs/research/context-backend-coverage-schema33.md`.

For a real database, use a private snapshot and inspect only row counts and the
returned load summary. Do not print bundle records or DSNs.
