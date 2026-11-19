#!/usr/bin/env python3
"""Load a canonical context bundle into PostgreSQL transactionally.

The bundle is validated before connecting. All schema creation, rows, and snapshot
head promotion happen in one transaction. A failure therefore leaves the previous
head intact.

Requires psycopg 3 at runtime. Keep the dependency outside the Hermes environment,
for example with:

    uv run --no-project --with 'psycopg[binary]>=3.2,<4' \
      python tools/context_backend/load_postgres.py BUNDLE
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.context_backend.core import (
    BundleContractError,
    iter_bundle_records,
    validate_bundle,
)

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


def _jsonb(value: dict[str, Any]):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _session_params(snapshot_id: int, record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        snapshot_id,
        record["source_id"],
        record["source_record_id"],
        record.get("parent_session_id"),
        record.get("source"),
        record.get("user_id"),
        record.get("session_key"),
        record.get("chat_id"),
        record.get("chat_type"),
        record.get("thread_id"),
        record.get("display_name"),
        _timestamp(record.get("started_at")),
        _timestamp(record.get("ended_at")),
        record.get("end_reason"),
        record.get("title"),
        record.get("profile_name"),
        record.get("archived"),
        record.get("hidden"),
        _timestamp(record.get("last_activity_at")),
        _jsonb(record.get("metadata", {})),
    )


def _message_params(snapshot_id: int, record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        snapshot_id,
        record["source_id"],
        record["source_record_id"],
        record.get("session_id"),
        record.get("role"),
        record.get("content"),
        record.get("tool_call_id"),
        record.get("tool_name"),
        record.get("effect_disposition"),
        _timestamp(record.get("timestamp")),
        record.get("token_count"),
        record.get("finish_reason"),
        record.get("active"),
        record.get("compacted"),
        record.get("display_kind"),
        _jsonb(record.get("metadata", {})),
    )


def _batches(rows: Iterable[dict[str, Any]], size: int = 1000):
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_bundle(bundle_dir: pathlib.Path, dsn: str) -> dict[str, int | str]:
    """Load a validated bundle and promote its head only after all rows succeed."""
    manifest = validate_bundle(bundle_dir)
    source = manifest["source"]
    # Re-open validated JSONL streams for the transactional load.
    session_rows = iter_bundle_records(bundle_dir, "sessions")
    message_rows = iter_bundle_records(bundle_dir, "messages")

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is not installed; run with uv --with 'psycopg[binary]>=3.2,<4>'"
        ) from exc

    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            snapshot = conn.execute(
                """
                INSERT INTO context_snapshots (
                    source_id, snapshot_sha256, verification_receipt_sha256,
                    source_schema_version, format_version, snapshot_name, captured_at,
                    session_count, message_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id, snapshot_sha256) DO UPDATE SET imported_at = now()
                RETURNING snapshot_id
                """,
                (
                    source["source_id"],
                    source["snapshot_sha256"],
                    manifest["verification"]["receipt_sha256"],
                    int(source["source_schema_version"]),
                    manifest["format_version"],
                    source["snapshot_name"],
                    datetime.fromisoformat(manifest["created_at"]),
                    int(manifest["counts"]["sessions"]),
                    int(manifest["counts"]["messages"]),
                ),
            ).fetchone()
            snapshot_id = int(snapshot[0])

            session_count = 0
            session_sql = """
                INSERT INTO context_sessions (
                    snapshot_id, source_id, source_session_id, parent_session_id,
                    source, user_id, session_key, chat_id, chat_type, thread_id,
                    display_name, started_at, ended_at, end_reason, title,
                    profile_name, archived, hidden, last_activity_at, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_id, source_id, source_session_id) DO UPDATE SET
                    parent_session_id = EXCLUDED.parent_session_id,
                    source = EXCLUDED.source,
                    user_id = EXCLUDED.user_id,
                    session_key = EXCLUDED.session_key,
                    chat_id = EXCLUDED.chat_id,
                    chat_type = EXCLUDED.chat_type,
                    thread_id = EXCLUDED.thread_id,
                    display_name = EXCLUDED.display_name,
                    started_at = EXCLUDED.started_at,
                    ended_at = EXCLUDED.ended_at,
                    end_reason = EXCLUDED.end_reason,
                    title = EXCLUDED.title,
                    profile_name = EXCLUDED.profile_name,
                    archived = EXCLUDED.archived,
                    hidden = EXCLUDED.hidden,
                    last_activity_at = EXCLUDED.last_activity_at,
                    metadata = EXCLUDED.metadata
            """
            for batch in _batches(session_rows):
                with conn.cursor() as cursor:
                    cursor.executemany(session_sql, [_session_params(snapshot_id, row) for row in batch])
                session_count += len(batch)

            message_count = 0
            message_sql = """
                INSERT INTO context_messages (
                    snapshot_id, source_id, source_message_id, source_session_id,
                    role, content, tool_call_id, tool_name, effect_disposition,
                    message_timestamp, token_count, finish_reason, active,
                    compacted, display_kind, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s)
                ON CONFLICT (snapshot_id, source_id, source_message_id) DO UPDATE SET
                    source_session_id = EXCLUDED.source_session_id,
                    role = EXCLUDED.role,
                    content = EXCLUDED.content,
                    tool_call_id = EXCLUDED.tool_call_id,
                    tool_name = EXCLUDED.tool_name,
                    effect_disposition = EXCLUDED.effect_disposition,
                    message_timestamp = EXCLUDED.message_timestamp,
                    token_count = EXCLUDED.token_count,
                    finish_reason = EXCLUDED.finish_reason,
                    active = EXCLUDED.active,
                    compacted = EXCLUDED.compacted,
                    display_kind = EXCLUDED.display_kind,
                    metadata = EXCLUDED.metadata
            """
            for batch in _batches(message_rows):
                with conn.cursor() as cursor:
                    cursor.executemany(message_sql, [_message_params(snapshot_id, row) for row in batch])
                message_count += len(batch)

            expected_sessions = int(manifest["counts"]["sessions"])
            expected_messages = int(manifest["counts"]["messages"])
            if session_count != expected_sessions or message_count != expected_messages:
                raise BundleContractError(
                    f"bundle counts changed while loading: expected sessions={expected_sessions}, "
                    f"messages={expected_messages}; read sessions={session_count}, messages={message_count}"
                )

            conn.execute(
                """
                INSERT INTO context_snapshot_heads (source_id, snapshot_id)
                VALUES (%s, %s)
                ON CONFLICT (source_id) DO UPDATE SET
                    snapshot_id = EXCLUDED.snapshot_id,
                    promoted_at = now()
                """,
                (source["source_id"], snapshot_id),
            )
    return {
        "source_id": source["source_id"],
        "snapshot_id": snapshot_id,
        "sessions": session_count,
        "messages": message_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=pathlib.Path)
    parser.add_argument(
        "--dsn-env", default="CONTEXT_POSTGRES_DSN",
        help="environment variable containing the PostgreSQL DSN (default: CONTEXT_POSTGRES_DSN)",
    )
    args = parser.parse_args()
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        print(f"BLOCKED: {args.dsn_env} is not set", file=sys.stderr)
        return 2
    try:
        result = load_bundle(args.bundle, dsn)
    except (BundleContractError, RuntimeError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
