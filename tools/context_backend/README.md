# Context backend — first slice

This directory contains the first backend layer for the future context driver.
It does **not** install a Hermes hook and does not write to `state.db`.

```text
verified Hermes snapshot
  -> source decoder (schema-gated)
  -> private canonical bundle
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

The external research and phase plan are recorded in
`docs/research/context-backend-reconciliation.md`.

For a real database, use a private snapshot and inspect only row counts and the
returned load summary. Do not print bundle records or DSNs.
