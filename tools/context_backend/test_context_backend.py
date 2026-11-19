from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.context_backend.core import (
    BundleContractError,
    SourceContractError,
    describe_snapshot,
    iter_bundle_records,
    open_snapshot,
    read_manifest,
    validate_bundle,
    write_bundle,
)

SESSION_COLUMNS = [
    "id", "source", "user_id", "session_key", "chat_id", "chat_type", "thread_id",
    "display_name", "parent_session_id", "started_at", "ended_at", "end_reason",
    "title", "profile_name", "archived", "hidden", "last_activity_at",
]
MESSAGE_COLUMNS = [
    "id", "session_id", "role", "content", "tool_call_id", "tool_name",
    "effect_disposition", "timestamp", "token_count", "finish_reason", "active",
    "compacted", "display_kind",
]


def make_snapshot(path: Path, schema_version: int = 33) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES (?)", (schema_version,))
    conn.execute(
        "CREATE TABLE sessions (" + ", ".join(f"{name} TEXT" for name in SESSION_COLUMNS) + ")"
    )
    conn.execute(
        "CREATE TABLE messages (" + ", ".join(f"{name} TEXT" for name in MESSAGE_COLUMNS) + ")"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "session-1", "local", "user", "key", "chat", "private", "thread",
            "A test session", None, 1000, 1100, "done", "Title", "default", 0, 0, 1100,
        ),
    )
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "session-1", "user", "architectural question", None, None, None, 1001, 4, None, 1, 0, "text"),
    )
    conn.commit()
    conn.close()


class ContextBackendSourceTests(unittest.TestCase):
    def test_snapshot_is_read_only_and_schema_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            make_snapshot(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            descriptor = describe_snapshot(path, "test-source")
            self.assertEqual(descriptor.source_schema_version, 33)
            self.assertEqual(descriptor.sessions, 1)
            self.assertEqual(descriptor.messages, 1)
            with open_snapshot(path) as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("CREATE TABLE must_not_write (id INTEGER)")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_unknown_source_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            make_snapshot(path, schema_version=34)
            with self.assertRaisesRegex(SourceContractError, "unsupported source schema version"):
                describe_snapshot(path, "test-source")

    def test_bundle_has_provenance_and_excludes_internal_reasoning_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "state.db"
            make_snapshot(snapshot)
            bundle = root / "bundle"
            manifest = write_bundle(snapshot, bundle, "test-source")
            self.assertEqual(manifest["counts"], {"sessions": 1, "messages": 1})
            self.assertEqual(read_manifest(bundle)["format_version"], "theseus.context.export.v1")
            session = next(iter_bundle_records(bundle, "sessions"))
            message = next(iter_bundle_records(bundle, "messages"))
            self.assertEqual(session["snapshot_sha256"], manifest["source"]["snapshot_sha256"])
            self.assertEqual(message["source_record_id"], "1")
            self.assertNotIn("reasoning", message)
            self.assertNotIn("system_prompt", session)

    def test_bundle_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "state.db"
            make_snapshot(snapshot)
            bundle = root / "bundle"
            bundle.mkdir()
            with self.assertRaisesRegex(BundleContractError, "already exists"):
                write_bundle(snapshot, bundle, "test-source")

    def test_bundle_is_fully_validated_before_target_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "state.db"
            make_snapshot(snapshot)
            bundle = root / "bundle"
            write_bundle(snapshot, bundle, "test-source")
            (bundle / "messages.jsonl").write_text("{not-json}\n", encoding="utf-8")
            with self.assertRaisesRegex(BundleContractError, "invalid messages JSONL"):
                validate_bundle(bundle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
