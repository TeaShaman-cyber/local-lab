"""Deterministic reconciliation for canonical context records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping

FINGERPRINT_VERSION = "theseus.context.record-fingerprint.v1"
_PROVENANCE_FIELDS = frozenset(
    {
        "snapshot_sha256",
        "source_schema_version",
        "verification_receipt_sha256",
        "verification_receipt_version",
        "verified_at",
    }
)
_IDENTITY_FIELDS = frozenset({"record_type", "source_id", "source_record_id"})


class ReconciliationContractError(ValueError):
    """Canonical records do not satisfy the reconciliation contract."""


@dataclass(frozen=True)
class ReconciliationChange:
    """One non-unchanged record transition between two source images."""

    kind: Literal["added", "changed", "deleted"]
    record_type: str
    source_record_id: str
    before_fingerprint: str | None
    after_fingerprint: str | None


@dataclass(frozen=True)
class ReconciliationResult:
    """Deterministic diff and summary for one candidate source image."""

    source_id: str
    base_snapshot_sha256: str | None
    candidate_snapshot_sha256: str
    counts: Mapping[str, int]
    changes: tuple[ReconciliationChange, ...]


def _required_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ReconciliationContractError(f"record {field} must be a non-empty string")
    return value


def _record_key(record: Mapping[str, Any], source_id: str) -> tuple[str, str, str]:
    actual_source_id = record.get("source_id")
    if actual_source_id != source_id:
        raise ReconciliationContractError(
            f"source_id mismatch: expected {source_id!r}, got {actual_source_id!r}"
        )
    return (
        actual_source_id,
        _required_text(record, "record_type"),
        _required_text(record, "source_record_id"),
    )


def record_fingerprint(record: Mapping[str, Any]) -> str:
    """Return a stable hash of logical record data, excluding identity/provenance."""
    if not isinstance(record, Mapping):
        raise ReconciliationContractError("record must be a mapping")
    payload = {
        key: value
        for key, value in record.items()
        if key not in _IDENTITY_FIELDS and key not in _PROVENANCE_FIELDS
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReconciliationContractError("record contains non-canonical JSON data") from exc
    digest = hashlib.sha256()
    digest.update(FINGERPRINT_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(encoded)
    return digest.hexdigest()


def _index_records(
    records: Iterable[Mapping[str, Any]], source_id: str
) -> dict[tuple[str, str, str], tuple[str, Mapping[str, Any]]]:
    indexed: dict[tuple[str, str, str], tuple[str, Mapping[str, Any]]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ReconciliationContractError("record must be a mapping")
        key = _record_key(record, source_id)
        if key in indexed:
            raise ReconciliationContractError(
                "duplicate record key: "
                f"{key[1]}:{key[2]} for source_id {source_id!r}"
            )
        indexed[key] = (record_fingerprint(record), record)
    return indexed


def _snapshot_cursor(value: str | None, field: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise ReconciliationContractError(f"{field} must be a non-empty string")
    return value


def reconcile_records(
    base_records: Iterable[Mapping[str, Any]],
    candidate_records: Iterable[Mapping[str, Any]],
    *,
    source_id: str,
    base_snapshot_sha256: str | None,
    candidate_snapshot_sha256: str,
) -> ReconciliationResult:
    """Compare two canonical record sets without mutating either input."""
    if not isinstance(source_id, str) or not source_id:
        raise ReconciliationContractError("source_id must be a non-empty string")
    base_cursor = _snapshot_cursor(base_snapshot_sha256, "base_snapshot_sha256", required=False)
    candidate_cursor = _snapshot_cursor(
        candidate_snapshot_sha256, "candidate_snapshot_sha256", required=True
    )
    base = _index_records(base_records, source_id)
    if base and base_cursor is None:
        raise ReconciliationContractError(
            "base_snapshot_sha256 is required when base_records are present"
        )
    candidate = _index_records(candidate_records, source_id)
    if (
        base_cursor is not None
        and base_cursor == candidate_cursor
        and {key: value[0] for key, value in base.items()}
        != {key: value[0] for key, value in candidate.items()}
    ):
        raise ReconciliationContractError(
            "snapshot cursor reused for different canonical records"
        )

    counts = {"added": 0, "changed": 0, "deleted": 0, "unchanged": 0}
    changes: list[ReconciliationChange] = []
    for key in sorted(base.keys() | candidate.keys()):
        before = base.get(key)
        after = candidate.get(key)
        if before is None:
            kind: Literal["added", "changed", "deleted"] = "added"
            before_fingerprint = None
            after_fingerprint = after[0]
        elif after is None:
            kind = "deleted"
            before_fingerprint = before[0]
            after_fingerprint = None
        elif before[0] == after[0]:
            counts["unchanged"] += 1
            continue
        else:
            kind = "changed"
            before_fingerprint = before[0]
            after_fingerprint = after[0]
        counts[kind] += 1
        changes.append(
            ReconciliationChange(
                kind=kind,
                record_type=key[1],
                source_record_id=key[2],
                before_fingerprint=before_fingerprint,
                after_fingerprint=after_fingerprint,
            )
        )

    return ReconciliationResult(
        source_id=source_id,
        base_snapshot_sha256=base_cursor,
        candidate_snapshot_sha256=candidate_cursor,
        counts=MappingProxyType(counts),
        changes=tuple(changes),
    )
