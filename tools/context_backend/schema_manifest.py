"""Read-only SQLite schema metadata for the context-backend source gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import tempfile
from typing import Any

from tools.context_backend.core import SourceContractError, open_snapshot

MANIFEST_VERSION = "theseus.context.source-schema-manifest.v2"


def _sql_hash(sql: str | None) -> str | None:
    if sql is None:
        return None
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _schema_version(conn: sqlite3.Connection) -> int:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "schema_version" not in tables:
        raise SourceContractError("schema manifest requires schema_version table")
    row = conn.execute("SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise SourceContractError("schema_version is empty")
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise SourceContractError("schema version is not an integer") from exc


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_xinfo({_quote_identifier(table)})").fetchall()
    return [
        {
            "name": str(row[1]),
            "type": str(row[2]),
            "notnull": bool(row[3]),
            "default": None if row[4] is None else str(row[4]),
            "pk": int(row[5]),
            "hidden": int(row[6]),
        }
        for row in rows
    ]


def _foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})").fetchall()
    return sorted(
        [
            {
                "from": str(row[3]),
                "to_table": str(row[2]),
                "to_column": str(row[4]),
                "on_update": str(row[5]),
                "on_delete": str(row[6]),
                "match": str(row[7]),
            }
            for row in rows
        ],
        key=lambda item: (item["from"], item["to_table"], item["to_column"]),
    )


def _objects(conn: sqlite3.Connection, kind: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = ? ORDER BY name",
        (kind,),
    ).fetchall()
    return [
        {
            "name": str(row[0]),
            "table": str(row[1]),
            "sql_sha256": _sql_hash(None if row[2] is None else str(row[2])),
        }
        for row in rows
    ]


def inspect_schema(path: pathlib.Path) -> dict[str, Any]:
    """Return schema metadata only from an immutable snapshot.

    The implementation reads sqlite_master, PRAGMA metadata and the schema version
    marker. It never selects application row values.
    """
    with open_snapshot(path) as conn:
        version = _schema_version(conn)
        table_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        tables = [
            {
                "name": str(row[0]),
                "sql_sha256": _sql_hash(None if row[1] is None else str(row[1])),
                "columns": _columns(conn, str(row[0])),
                "foreign_keys": _foreign_keys(conn, str(row[0])),
            }
            for row in table_rows
        ]
        return {
            "manifest_version": MANIFEST_VERSION,
            "source_kind": "hermes-state-snapshot",
            "source_schema_version": version,
            "tables": tables,
            "views": _objects(conn, "view"),
            "triggers": _objects(conn, "trigger"),
            "indexes": _objects(conn, "index"),
        }


def _write_atomic(path: pathlib.Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temporary = pathlib.Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect SQLite schema metadata only")
    parser.add_argument("snapshot", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    payload = json.dumps(inspect_schema(args.snapshot), indent=2, sort_keys=True) + "\n"
    if args.output:
        _write_atomic(args.output, payload)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
