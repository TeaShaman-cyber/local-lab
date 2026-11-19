"""Source boundary and canonical bundle format for the context backend.

The source is a verified SQLite snapshot, never the live Hermes database. This module
is deliberately independent of PostgreSQL so the source contract can be tested and
replayed without a running service.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Iterator

from tools.context_backend import (
    FORMAT_VERSION,
    SUPPORTED_SOURCE_SCHEMA_VERSIONS,
    VERIFICATION_RECEIPT_VERSION,
)


class SourceContractError(RuntimeError):
    """The snapshot is not a supported Hermes source artifact."""


class BundleContractError(RuntimeError):
    """The canonical bundle is malformed or unsupported."""


REQUIRED_COLUMNS = {
    "schema_version": {"version"},
    "sessions": {
        "id", "source", "user_id", "session_key", "chat_id", "chat_type", "thread_id",
        "display_name", "parent_session_id", "started_at", "ended_at", "end_reason",
        "title", "profile_name", "archived", "hidden", "last_activity_at",
    },
    "messages": {
        "id", "session_id", "role", "content", "tool_call_id", "tool_name",
        "effect_disposition", "timestamp", "token_count", "finish_reason", "active",
        "compacted", "display_kind",
    },
}


@dataclass(frozen=True)
class SnapshotDescriptor:
    path: pathlib.Path
    source_id: str
    source_schema_version: int
    snapshot_sha256: str
    sessions: int
    messages: int
    newest_message_timestamp: float | None
    verification_receipt_sha256: str | None = None
    verification_receipt_version: str | None = None
    verified_at: str | None = None

    def manifest(self) -> dict[str, Any]:
        if not self.verification_receipt_sha256 or not self.verification_receipt_version or not self.verified_at:
            raise SourceContractError("a verification receipt is required for a canonical manifest")
        return {
            "format_version": FORMAT_VERSION,
            "source": {
                "kind": "hermes-state-snapshot",
                "source_id": self.source_id,
                "source_schema_version": self.source_schema_version,
                "snapshot_name": self.path.name,
                "snapshot_sha256": self.snapshot_sha256,
            },
            "counts": {"sessions": self.sessions, "messages": self.messages},
            "newest_message_timestamp": self.newest_message_timestamp,
            "created_at": datetime.now(UTC).isoformat(),
            "verification": {
                "receipt_version": self.verification_receipt_version,
                "receipt_sha256": self.verification_receipt_sha256,
                "verified_at": self.verified_at,
            },
            "files": {"sessions": "sessions.jsonl", "messages": "messages.jsonl"},
        }


def _sqlite_uri(path: pathlib.Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"


@contextmanager
def open_snapshot(path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """Open a snapshot read-only and immutable; never create or modify a file."""
    if not path.is_file():
        raise SourceContractError(f"snapshot not found: {path}")
    try:
        conn = sqlite3.connect(_sqlite_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise SourceContractError(f"cannot open snapshot: {type(exc).__name__}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except sqlite3.Error as exc:
        raise SourceContractError(f"snapshot read failed: {type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]) for row in rows}


def _check_source_contract(conn: sqlite3.Connection) -> int:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing_tables = set(REQUIRED_COLUMNS) - tables
    if missing_tables:
        raise SourceContractError(
            "unsupported snapshot: missing tables " + ", ".join(sorted(missing_tables))
        )

    missing_columns: dict[str, list[str]] = {}
    for table, required in REQUIRED_COLUMNS.items():
        missing = required - _table_columns(conn, table)
        if missing:
            missing_columns[table] = sorted(missing)
    if missing_columns:
        raise SourceContractError(
            "unsupported snapshot: missing columns "
            + json.dumps(missing_columns, ensure_ascii=False, sort_keys=True)
        )

    version_row = conn.execute(
        "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if version_row is None:
        raise SourceContractError("unsupported snapshot: schema_version is empty")
    try:
        version = int(version_row[0])
    except (TypeError, ValueError) as exc:
        raise SourceContractError("unsupported snapshot: schema version is not an integer") from exc
    if version not in SUPPORTED_SOURCE_SCHEMA_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_SOURCE_SCHEMA_VERSIONS))
        raise SourceContractError(
            f"unsupported source schema version {version}; supported versions: {supported}"
        )
    return version


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_receipt(receipt_path: pathlib.Path) -> tuple[dict[str, Any], str]:
    if not receipt_path.is_file():
        raise SourceContractError(f"receipt not found: {receipt_path}")
    try:
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceContractError(f"cannot read verification receipt: {receipt_path}") from exc
    if not isinstance(receipt, dict):
        raise SourceContractError("verification receipt must be a JSON object")
    if receipt.get("receipt_version") != VERIFICATION_RECEIPT_VERSION:
        raise SourceContractError("unsupported verification receipt version")
    return receipt, hashlib.sha256(raw).hexdigest()


def _attach_verification_receipt(
    descriptor: SnapshotDescriptor, receipt_path: pathlib.Path
) -> SnapshotDescriptor:
    receipt, receipt_sha256 = _read_receipt(receipt_path)
    source = receipt.get("source")
    verdict = receipt.get("verification")
    if not isinstance(source, dict) or not isinstance(verdict, dict):
        raise SourceContractError("verification receipt is missing source or verification")
    if source.get("snapshot_sha256") != descriptor.snapshot_sha256:
        raise SourceContractError("snapshot_sha256 mismatch between snapshot and receipt")
    if source.get("snapshot_name") != descriptor.path.name:
        raise SourceContractError("snapshot_name mismatch between snapshot and receipt")
    try:
        receipt_size = int(source["size_bytes"])
        receipt_sessions = int(verdict["sessions"])
        receipt_messages = int(verdict["messages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceContractError("verification receipt has invalid size or counts") from exc
    if receipt_size != descriptor.path.stat().st_size:
        raise SourceContractError("size_bytes mismatch between snapshot and receipt")
    if verdict.get("ok") is not True or verdict.get("integrity") != "ok":
        raise SourceContractError("verification failed: receipt verdict is not usable")
    if receipt_sessions != descriptor.sessions:
        raise SourceContractError("session count mismatch between snapshot and receipt")
    if receipt_messages != descriptor.messages:
        raise SourceContractError("message count mismatch between snapshot and receipt")
    verified_at = verdict.get("checked")
    if not isinstance(verified_at, str):
        raise SourceContractError("verification receipt is missing checked timestamp")
    try:
        datetime.fromisoformat(verified_at)
    except ValueError as exc:
        raise SourceContractError("verification receipt checked timestamp is invalid") from exc
    return replace(
        descriptor,
        verification_receipt_sha256=receipt_sha256,
        verification_receipt_version=receipt["receipt_version"],
        verified_at=verified_at,
    )


def describe_snapshot(
    path: pathlib.Path, source_id: str, receipt_path: pathlib.Path | None = None
) -> SnapshotDescriptor:
    """Validate the supported source contract and return metadata only."""
    if not source_id.strip():
        raise SourceContractError("source_id must not be empty")
    with open_snapshot(path) as conn:
        schema_version = _check_source_contract(conn)
        sessions = int(conn.execute("SELECT count(*) FROM sessions").fetchone()[0])
        messages = int(conn.execute("SELECT count(*) FROM messages").fetchone()[0])
        newest = conn.execute("SELECT max(timestamp) FROM messages").fetchone()[0]
    descriptor = SnapshotDescriptor(
        path=path,
        source_id=source_id,
        source_schema_version=schema_version,
        snapshot_sha256=_sha256_file(path),
        sessions=sessions,
        messages=messages,
        newest_message_timestamp=float(newest) if newest is not None else None,
    )
    return _attach_verification_receipt(descriptor, receipt_path) if receipt_path else descriptor


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _integer(value: Any) -> int | None:
    return None if value is None else int(value)


def _flag(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _source_metadata(row: sqlite3.Row, *names: str) -> dict[str, Any]:
    return {name: row[name] for name in names if row[name] is not None}


def iter_sessions(
    conn: sqlite3.Connection, descriptor: SnapshotDescriptor
) -> Iterator[dict[str, Any]]:
    query = """
        SELECT id, source, user_id, session_key, chat_id, chat_type, thread_id,
               display_name, parent_session_id, started_at, ended_at, end_reason,
               title, profile_name, archived, hidden, last_activity_at
        FROM sessions
        ORDER BY rowid
    """
    for row in conn.execute(query):
        source_record_id = str(row["id"])
        yield {
            "record_type": "session",
            "source_id": descriptor.source_id,
            "source_record_id": source_record_id,
            "snapshot_sha256": descriptor.snapshot_sha256,
            "source_schema_version": descriptor.source_schema_version,
            "session_id": source_record_id,
            "parent_session_id": _text(row["parent_session_id"]),
            "source": _text(row["source"]),
            "user_id": _text(row["user_id"]),
            "session_key": _text(row["session_key"]),
            "chat_id": _text(row["chat_id"]),
            "chat_type": _text(row["chat_type"]),
            "thread_id": _text(row["thread_id"]),
            "display_name": _text(row["display_name"]),
            "started_at": _number(row["started_at"]),
            "ended_at": _number(row["ended_at"]),
            "end_reason": _text(row["end_reason"]),
            "title": _text(row["title"]),
            "profile_name": _text(row["profile_name"]),
            "archived": _flag(row["archived"]),
            "hidden": _flag(row["hidden"]),
            "last_activity_at": _number(row["last_activity_at"]),
            "metadata": _source_metadata(row, "source", "chat_type", "thread_id", "profile_name"),
        }


def iter_messages(
    conn: sqlite3.Connection, descriptor: SnapshotDescriptor
) -> Iterator[dict[str, Any]]:
    query = """
        SELECT id, session_id, role, content, tool_call_id, tool_name,
               effect_disposition, timestamp, token_count, finish_reason,
               active, compacted, display_kind
        FROM messages
        ORDER BY id
    """
    for row in conn.execute(query):
        yield {
            "record_type": "message",
            "source_id": descriptor.source_id,
            "source_record_id": str(row["id"]),
            "snapshot_sha256": descriptor.snapshot_sha256,
            "source_schema_version": descriptor.source_schema_version,
            "session_id": _text(row["session_id"]),
            "role": _text(row["role"]),
            "content": _text(row["content"]),
            "tool_call_id": _text(row["tool_call_id"]),
            "tool_name": _text(row["tool_name"]),
            "effect_disposition": _text(row["effect_disposition"]),
            "timestamp": _number(row["timestamp"]),
            "token_count": _integer(row["token_count"]),
            "finish_reason": _text(row["finish_reason"]),
            "active": _flag(row["active"]),
            "compacted": _flag(row["compacted"]),
            "display_kind": _text(row["display_kind"]),
            "metadata": {},
        }


def _write_jsonl(path: pathlib.Path, rows: Iterator[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def write_bundle(
    snapshot_path: pathlib.Path,
    output_dir: pathlib.Path,
    source_id: str,
    receipt_path: pathlib.Path,
) -> dict[str, Any]:
    """Write a complete canonical bundle atomically; refuse an existing target."""
    descriptor = describe_snapshot(snapshot_path, source_id, receipt_path)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise BundleContractError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        with open_snapshot(snapshot_path) as conn:
            sessions = _write_jsonl(staging / "sessions.jsonl", iter_sessions(conn, descriptor))
            messages = _write_jsonl(staging / "messages.jsonl", iter_messages(conn, descriptor))
        if sessions != descriptor.sessions or messages != descriptor.messages:
            raise BundleContractError(
                f"source changed while exporting: expected sessions={descriptor.sessions}, "
                f"messages={descriptor.messages}; read sessions={sessions}, messages={messages}"
            )
        if _sha256_file(snapshot_path) != descriptor.snapshot_sha256:
            raise BundleContractError("source changed while exporting: snapshot hash mismatch")
        manifest = descriptor.manifest()
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def read_manifest(bundle_dir: pathlib.Path) -> dict[str, Any]:
    path = bundle_dir / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleContractError(f"cannot read manifest: {path}") from exc
    if not isinstance(manifest, dict):
        raise BundleContractError("manifest must be a JSON object")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BundleContractError(
            f"unsupported bundle format {manifest.get('format_version')!r}; expected {FORMAT_VERSION!r}"
        )
    source = manifest.get("source")
    counts = manifest.get("counts")
    files = manifest.get("files")
    verification = manifest.get("verification")
    if (
        not isinstance(source, dict)
        or not isinstance(counts, dict)
        or not isinstance(files, dict)
        or not isinstance(verification, dict)
    ):
        raise BundleContractError("manifest is missing source, counts, verification, or files")
    if not source.get("source_id") or not source.get("snapshot_sha256") or not source.get("snapshot_name"):
        raise BundleContractError("manifest source is missing source_id, snapshot_name, or snapshot_sha256")
    if not isinstance(manifest.get("created_at"), str):
        raise BundleContractError("manifest is missing created_at")
    if not verification.get("receipt_sha256") or verification.get("receipt_version") != VERIFICATION_RECEIPT_VERSION:
        raise BundleContractError("manifest has invalid verification receipt provenance")
    try:
        datetime.fromisoformat(manifest["created_at"])
    except ValueError as exc:
        raise BundleContractError("manifest created_at is not an ISO timestamp") from exc
    try:
        schema_version = int(source["source_schema_version"])
        expected_sessions = int(counts["sessions"])
        expected_messages = int(counts["messages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleContractError("manifest has invalid source schema version or counts") from exc
    if schema_version not in SUPPORTED_SOURCE_SCHEMA_VERSIONS:
        raise BundleContractError(f"unsupported source schema version {schema_version}")
    for kind in ("sessions", "messages"):
        filename = files.get(kind)
        if not isinstance(filename, str) or pathlib.PurePath(filename).name != filename:
            raise BundleContractError(f"invalid {kind} bundle filename")
        if not (bundle_dir / filename).is_file():
            raise BundleContractError(f"missing {kind} bundle file: {filename}")
    if expected_sessions < 0 or expected_messages < 0:
        raise BundleContractError("manifest counts must not be negative")
    return manifest


def iter_bundle_records(bundle_dir: pathlib.Path, kind: str) -> Iterator[dict[str, Any]]:
    manifest = read_manifest(bundle_dir)
    filename = manifest["files"][kind]
    path = bundle_dir / filename
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise BundleContractError(f"invalid {kind} JSONL at line {line_number}") from exc
                if not isinstance(record, dict) or record.get("record_type") != kind[:-1]:
                    raise BundleContractError(f"invalid {kind} record at line {line_number}")
                if record.get("source_id") != manifest["source"]["source_id"]:
                    raise BundleContractError(f"source_id mismatch in {kind} line {line_number}")
                if record.get("snapshot_sha256") != manifest["source"]["snapshot_sha256"]:
                    raise BundleContractError(f"snapshot provenance mismatch in {kind} line {line_number}")
                yield record
    except OSError as exc:
        raise BundleContractError(f"cannot read {kind} bundle: {path}") from exc


def validate_bundle(bundle_dir: pathlib.Path) -> dict[str, Any]:
    """Parse and count every record before a target connection is opened."""
    manifest = read_manifest(bundle_dir)
    expected = manifest["counts"]
    actual = {
        "sessions": sum(1 for _ in iter_bundle_records(bundle_dir, "sessions")),
        "messages": sum(1 for _ in iter_bundle_records(bundle_dir, "messages")),
    }
    if actual != {"sessions": int(expected["sessions"]), "messages": int(expected["messages"])}:
        raise BundleContractError(
            f"bundle counts do not match manifest: expected {expected}, actual {actual}"
        )
    return manifest
