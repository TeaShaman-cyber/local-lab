from __future__ import annotations

import pytest

from tools.context_backend.reconcile import (
    ReconciliationContractError,
    reconcile_records,
    record_fingerprint,
)


def _session(*, title: str = "First", snapshot: str = "sha-a") -> dict[str, object]:
    return {
        "record_type": "session",
        "source_id": "hermes:test",
        "source_record_id": "session-1",
        "snapshot_sha256": snapshot,
        "source_schema_version": 33,
        "title": title,
        "metadata": {"chat_type": "direct"},
    }


def test_record_fingerprint_ignores_snapshot_provenance() -> None:
    first = _session(snapshot="sha-a")
    second = _session(snapshot="sha-b")

    assert record_fingerprint(first) == record_fingerprint(second)


def test_record_fingerprint_is_stable_for_nested_key_order() -> None:
    first = {**_session(), "metadata": {"b": 2, "a": {"d": 4, "c": 3}}}
    second = {**_session(), "metadata": {"a": {"c": 3, "d": 4}, "b": 2}}

    assert record_fingerprint(first) == record_fingerprint(second)


def test_record_fingerprint_changes_for_logical_content() -> None:
    first = _session(title="before")
    second = _session(title="after")

    assert record_fingerprint(first) != record_fingerprint(second)


def test_reconcile_reports_add_change_delete_and_unchanged() -> None:
    base = [
        _session(title="unchanged", snapshot="sha-a"),
        {
            **_session(title="changed", snapshot="sha-a"),
            "source_record_id": "session-2",
        },
        {
            **_session(title="deleted", snapshot="sha-a"),
            "source_record_id": "session-3",
        },
    ]
    candidate = [
        _session(title="unchanged", snapshot="sha-b"),
        {
            **_session(title="changed now", snapshot="sha-b"),
            "source_record_id": "session-2",
        },
        {
            **_session(title="new", snapshot="sha-b"),
            "source_record_id": "session-4",
        },
    ]

    result = reconcile_records(
        base,
        candidate,
        source_id="hermes:test",
        base_snapshot_sha256="sha-a",
        candidate_snapshot_sha256="sha-b",
    )

    assert result.counts == {
        "added": 1,
        "changed": 1,
        "deleted": 1,
        "unchanged": 1,
    }
    assert [(change.kind, change.source_record_id) for change in result.changes] == [
        ("changed", "session-2"),
        ("deleted", "session-3"),
        ("added", "session-4"),
    ]


def test_reconcile_is_deterministic_and_replay_is_a_noop() -> None:
    candidate = [
        {
            **_session(snapshot="sha-b"),
            "source_record_id": "session-2",
        },
        _session(snapshot="sha-b"),
    ]

    first = reconcile_records(
        [],
        candidate,
        source_id="hermes:test",
        base_snapshot_sha256=None,
        candidate_snapshot_sha256="sha-b",
    )
    replay = reconcile_records(
        candidate,
        candidate,
        source_id="hermes:test",
        base_snapshot_sha256="sha-b",
        candidate_snapshot_sha256="sha-b",
    )

    assert first.counts == {"added": 2, "changed": 0, "deleted": 0, "unchanged": 0}
    assert replay.counts == {"added": 0, "changed": 0, "deleted": 0, "unchanged": 2}
    assert replay.changes == ()


def test_reconcile_rejects_reusing_cursor_for_different_content() -> None:
    with pytest.raises(ReconciliationContractError, match="cursor reused"):
        reconcile_records(
            [_session(title="before", snapshot="sha-a")],
            [_session(title="after", snapshot="sha-a")],
            source_id="hermes:test",
            base_snapshot_sha256="sha-a",
            candidate_snapshot_sha256="sha-a",
        )


def test_reconciliation_counts_are_immutable() -> None:
    result = reconcile_records(
        [],
        [_session()],
        source_id="hermes:test",
        base_snapshot_sha256=None,
        candidate_snapshot_sha256="sha-a",
    )

    with pytest.raises(TypeError):
        result.counts["added"] = 0  # type: ignore[index]


def test_reconcile_rejects_duplicate_keys() -> None:
    with pytest.raises(ReconciliationContractError, match="duplicate record key"):
        reconcile_records(
            [],
            [_session(), _session()],
            source_id="hermes:test",
            base_snapshot_sha256=None,
            candidate_snapshot_sha256="sha-b",
        )


def test_reconcile_rejects_records_from_another_source() -> None:
    foreign = {**_session(), "source_id": "hermes:other"}

    with pytest.raises(ReconciliationContractError, match="source_id mismatch"):
        reconcile_records(
            [],
            [foreign],
            source_id="hermes:test",
            base_snapshot_sha256=None,
            candidate_snapshot_sha256="sha-b",
        )
