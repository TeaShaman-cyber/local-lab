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
python tools/state_db_backup/verify.py --json
python tools/context_backend/export_snapshot.py \
  E:/theseus-backups/state-db/snapshots/state-YYYYmmdd-HHMMSS.db \
  --validate-only
```

The exporter uses `mode=ro&immutable=1`. It never opens the live database and
never modifies the source. The first decoder supports only the observed Hermes
schema version `33`; an unknown version fails closed before any PostgreSQL write.

The exported bundle contains message content and is private data. Keep it outside
the repository and do not send it to Neon automatically:

```bash
python tools/context_backend/export_snapshot.py SNAPSHOT \
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

## Tests

The source boundary is covered without PostgreSQL or real transcript contents:

```bash
python -m unittest tools/context_backend/test_context_backend.py -v
```

For a real database, use a private snapshot and inspect only row counts and the
returned load summary. Do not print bundle records or DSNs.
