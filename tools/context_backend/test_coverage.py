from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

from tools.context_backend.core import SourceContractError
from tools.context_backend.coverage import (
    CoverageContractError,
    compare_schema_manifests,
    load_coverage_matrix,
    load_schema_manifest,
    validate_coverage_matrix,
)
from tools.context_backend.schema_manifest import inspect_schema

HERE = Path(__file__).resolve().parent
MATRIX_PATH = HERE / "coverage_matrix.yaml"
MANIFEST_PATH = HERE / "fixtures" / "hermes_schema_v33.json"


def test_repository_coverage_matrix_covers_schema_v33() -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)

    validate_coverage_matrix(matrix, manifest)

    assert manifest["source_schema_version"] == 33
    assert len(manifest["tables"]) == 48
    assert len(manifest["views"]) == 2
    assert len(manifest["triggers"]) == 14
    assert len(manifest["indexes"]) == 68


def test_unknown_table_and_column_fail_schema_drift_gate() -> None:
    expected = load_schema_manifest(MANIFEST_PATH)
    observed = copy.deepcopy(expected)
    observed["tables"].append(
        {
            "name": "future_runtime_state",
            "sql_sha256": "0" * 64,
            "columns": [
                {
                    "name": "id",
                    "type": "TEXT",
                    "notnull": False,
                    "default": None,
                    "pk": 1,
                    "hidden": 0,
                }
            ],
            "foreign_keys": [],
        }
    )
    observed["tables"][0]["columns"].append(
        {
            "name": "future_column",
            "type": "TEXT",
            "notnull": False,
            "default": None,
            "pk": 0,
            "hidden": 0,
        }
    )
    messages = next(table for table in observed["tables"] if table["name"] == "messages")
    messages["foreign_keys"] = []

    drift = compare_schema_manifests(expected, observed)

    assert any("unexpected table future_runtime_state" in item for item in drift)
    assert any("unexpected column" in item and "future_column" in item for item in drift)
    assert any("foreign key contract changed messages" in item for item in drift)


def test_missing_matrix_entry_and_primary_key_drift_fail_closed() -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)

    missing = copy.deepcopy(matrix)
    missing["tables"].pop("sessions")
    with pytest.raises(CoverageContractError, match="missing table decisions: sessions"):
        validate_coverage_matrix(missing, manifest)

    wrong_key = copy.deepcopy(matrix)
    wrong_key["tables"]["sessions"]["primary_key"] = ["session_key"]
    with pytest.raises(CoverageContractError, match="primary key mismatch for sessions"):
        validate_coverage_matrix(wrong_key, manifest)


def test_source_kind_and_supported_version_fail_closed() -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)

    wrong_kind = copy.deepcopy(manifest)
    wrong_kind["source_kind"] = "other-runtime"
    with pytest.raises(CoverageContractError, match="unsupported schema source kind"):
        validate_coverage_matrix(matrix, wrong_kind)

    wrong_matrix_kind = copy.deepcopy(matrix)
    wrong_matrix_kind["source_kind"] = "other-runtime"
    with pytest.raises(CoverageContractError, match="unsupported matrix source kind"):
        validate_coverage_matrix(wrong_matrix_kind, manifest)

    unsupported = copy.deepcopy(manifest)
    unsupported["source_schema_version"] = 34
    unsupported_matrix = copy.deepcopy(matrix)
    unsupported_matrix["source_schema_version"] = 34
    with pytest.raises(CoverageContractError, match="unsupported source schema version: 34"):
        validate_coverage_matrix(unsupported_matrix, unsupported)

    missing = copy.deepcopy(manifest)
    missing.pop("source_schema_version")
    with pytest.raises(CoverageContractError, match="unsupported source schema version: None"):
        validate_coverage_matrix(matrix, missing)

    incomplete = copy.deepcopy(manifest)
    incomplete["tables"][0].pop("sql_sha256")
    with pytest.raises(CoverageContractError, match="invalid table DDL hash"):
        validate_coverage_matrix(matrix, incomplete)


def test_decision_pairs_replay_and_sensitivity_fail_closed() -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)

    incompatible = copy.deepcopy(matrix)
    incompatible["tables"]["messages"]["mirror"] = "exclude_mechanic"
    with pytest.raises(CoverageContractError, match="incompatible mirror/canonical"):
        validate_coverage_matrix(incompatible, manifest)

    replayable = copy.deepcopy(matrix)
    replayable["tables"]["messages"]["replay"] = "allowed"
    with pytest.raises(CoverageContractError, match="replay must be forbidden"):
        validate_coverage_matrix(replayable, manifest)

    missing_sensitivity = copy.deepcopy(matrix)
    missing_sensitivity["tables"]["messages"].pop("sensitivity")
    with pytest.raises(CoverageContractError, match="sensitivity must be a string"):
        validate_coverage_matrix(missing_sensitivity, manifest)

    sensitive_without_sensitive_decision = copy.deepcopy(matrix)
    sensitive_without_sensitive_decision["tables"]["session_model_usage"][
        "sensitivity"
    ] = "local_sensitive"
    with pytest.raises(CoverageContractError, match="cannot use canonical include"):
        validate_coverage_matrix(sensitive_without_sensitive_decision, manifest)

    sensitive_decision_without_sensitive_class = copy.deepcopy(matrix)
    sensitive_decision_without_sensitive_class["tables"]["messages"][
        "sensitivity"
    ] = "local_internal"
    with pytest.raises(CoverageContractError, match="requires local_sensitive"):
        validate_coverage_matrix(sensitive_decision_without_sensitive_class, manifest)

    transient_as_canonical_history = copy.deepcopy(matrix)
    transient_as_canonical_history["tables"]["gateway_heartbeats"][
        "canonical"
    ] = "include"
    with pytest.raises(CoverageContractError, match="decisions conflict with authority"):
        validate_coverage_matrix(transient_as_canonical_history, manifest)

    for table_name in ("state_meta", "delivery_obligations"):
        sensitivity_regression = copy.deepcopy(matrix)
        sensitivity_regression["tables"][table_name]["sensitivity"] = "local_internal"
        with pytest.raises(
            CoverageContractError, match="decisions conflict with authority"
        ):
            validate_coverage_matrix(sensitivity_regression, manifest)

    invalid_descriptor_sensitivity = copy.deepcopy(matrix)
    invalid_descriptor_sensitivity["tables"]["schema_version"]["sensitivity"] = "none"
    with pytest.raises(CoverageContractError, match="decisions conflict with authority"):
        validate_coverage_matrix(invalid_descriptor_sensitivity, manifest)

    authoritative_history_deferred = copy.deepcopy(matrix)
    authoritative_history_deferred["tables"]["messages"]["canonical"] = "defer"
    with pytest.raises(CoverageContractError, match="decisions conflict with authority"):
        validate_coverage_matrix(authoritative_history_deferred, manifest)

    unknown_authority = copy.deepcopy(matrix)
    unknown_authority["tables"]["messages"]["authority"] = "bogus_authority"
    with pytest.raises(CoverageContractError, match="unknown authority for messages"):
        validate_coverage_matrix(unknown_authority, manifest)

    for field in ("mirror", "canonical", "sensitivity", "authority"):
        non_string = copy.deepcopy(matrix)
        non_string["tables"]["messages"][field] = ["not-a-string"]
        with pytest.raises(CoverageContractError, match=f"{field} must be a string"):
            validate_coverage_matrix(non_string, manifest)

    assert matrix["tables"]["state_meta"]["canonical"] == "defer"
    assert all(
        decision["canonical"] == "defer"
        for name, decision in matrix["tables"].items()
        if name.startswith("hosted_room")
    )


def _manifest_for_ddl(
    path: Path, ddl: str, extra_statements: tuple[str, ...] = ()
) -> dict[str, object]:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (33)")
    conn.execute(ddl)
    for statement in extra_statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return inspect_schema(path)


@pytest.mark.parametrize(
    "changed_ddl",
    [
        "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT DEFAULT 'x')",
        "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT CHECK(length(payload) > 0))",
        "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT COLLATE NOCASE)",
        "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT) STRICT",
        (
            "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT, "
            "normalized TEXT GENERATED ALWAYS AS (lower(payload)) VIRTUAL)"
        ),
        "CREATE TABLE example (id INTEGER, payload TEXT, UNIQUE(payload, id))",
    ],
    ids=["default", "check", "collate", "strict", "generated", "table-unique-order"],
)
def test_table_ddl_mutations_fail_schema_drift_gate(
    tmp_path: Path, changed_ddl: str
) -> None:
    expected = _manifest_for_ddl(
        tmp_path / "expected.db",
        "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT)",
    )
    observed = _manifest_for_ddl(tmp_path / "observed.db", changed_ddl)

    drift = compare_schema_manifests(expected, observed)

    assert "table DDL changed example" in drift


def test_generated_expression_literal_whitespace_is_not_collapsed(tmp_path: Path) -> None:
    expected = _manifest_for_ddl(
        tmp_path / "expected-generated.db",
        (
            "CREATE TABLE example (payload TEXT, normalized TEXT GENERATED ALWAYS AS "
            "(replace(payload, 'a  b', 'x')) VIRTUAL)"
        ),
    )
    observed = _manifest_for_ddl(
        tmp_path / "observed-generated.db",
        (
            "CREATE TABLE example (payload TEXT, normalized TEXT GENERATED ALWAYS AS "
            "(replace(payload, 'a b', 'x')) VIRTUAL)"
        ),
    )

    assert "table DDL changed example" in compare_schema_manifests(expected, observed)


@pytest.mark.parametrize(
    ("label", "expected_statement", "observed_statement"),
    [
        (
            "view",
            "CREATE VIEW literal_view AS SELECT 'a  b' AS value",
            "CREATE VIEW literal_view AS SELECT 'a b' AS value",
        ),
        (
            "trigger",
            (
                "CREATE TRIGGER literal_trigger AFTER INSERT ON example "
                "BEGIN SELECT 'a  b'; END"
            ),
            (
                "CREATE TRIGGER literal_trigger AFTER INSERT ON example "
                "BEGIN SELECT 'a b'; END"
            ),
        ),
        (
            "index",
            "CREATE INDEX literal_index ON example(payload) WHERE payload = 'a  b'",
            "CREATE INDEX literal_index ON example(payload) WHERE payload = 'a b'",
        ),
    ],
)
def test_schema_object_literal_whitespace_is_not_collapsed(
    tmp_path: Path,
    label: str,
    expected_statement: str,
    observed_statement: str,
) -> None:
    table_ddl = "CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT)"
    expected = _manifest_for_ddl(
        tmp_path / f"expected-{label}.db", table_ddl, (expected_statement,)
    )
    observed = _manifest_for_ddl(
        tmp_path / f"observed-{label}.db", table_ddl, (observed_statement,)
    )

    assert f"changed {label} literal_{label}" in compare_schema_manifests(
        expected, observed
    )


def test_column_and_schema_object_contract_mutations_fail_closed() -> None:
    expected = load_schema_manifest(MANIFEST_PATH)
    observed = copy.deepcopy(expected)
    messages = next(table for table in observed["tables"] if table["name"] == "messages")
    messages["columns"][0]["type"] = "BLOB"
    messages["columns"][0]["notnull"] = not messages["columns"][0]["notnull"]
    messages["columns"][0]["pk"] = int(messages["columns"][0]["pk"]) + 1
    observed["views"][0]["sql_sha256"] = "0" * 64
    observed["triggers"][0]["sql_sha256"] = "1" * 64
    observed["indexes"][0]["sql_sha256"] = "2" * 64

    drift = compare_schema_manifests(expected, observed)

    assert any("column contract changed messages." in item for item in drift)
    assert any(item.startswith("changed view ") for item in drift)
    assert any(item.startswith("changed trigger ") for item in drift)
    assert any(item.startswith("changed index ") for item in drift)
    assert not any("indexe" in item for item in drift)


def test_malformed_schema_object_fails_closed() -> None:
    expected = load_schema_manifest(MANIFEST_PATH)
    observed = copy.deepcopy(expected)
    observed["indexes"].append({"sql_sha256": "missing-name"})

    with pytest.raises(CoverageContractError, match="indexes contains an invalid object"):
        compare_schema_manifests(expected, observed)


@pytest.mark.parametrize("object_key", ["views", "triggers", "indexes"])
def test_malformed_schema_object_hash_fails_matrix_validation(object_key: str) -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)
    manifest[object_key][0]["sql_sha256"] = "zz"

    with pytest.raises(CoverageContractError, match="invalid .* DDL hash"):
        validate_coverage_matrix(matrix, manifest)


@pytest.mark.parametrize("object_key", ["views", "triggers"])
def test_missing_view_or_trigger_hash_fails_matrix_validation(object_key: str) -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)
    manifest[object_key][0]["sql_sha256"] = None

    with pytest.raises(CoverageContractError, match="missing .* DDL hash"):
        validate_coverage_matrix(matrix, manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pk", "abc", "invalid primary-key contract"),
        ("hidden", "xyz", "invalid hidden-column contract"),
    ],
)
def test_malformed_numeric_column_contract_fails_closed(
    field: str, value: str, message: str
) -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)
    manifest["tables"][0]["columns"][0][field] = value

    with pytest.raises(CoverageContractError, match=message):
        validate_coverage_matrix(matrix, manifest)


def test_duplicate_matrix_schema_object_fails_closed() -> None:
    manifest = load_schema_manifest(MANIFEST_PATH)
    matrix = load_coverage_matrix(MATRIX_PATH)
    matrix["schema_objects"]["views"].append(matrix["schema_objects"]["views"][0])

    with pytest.raises(CoverageContractError, match="schema_objects.views contains duplicates"):
        validate_coverage_matrix(matrix, manifest)


def test_duplicate_yaml_mapping_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        "matrix_version: first\nmatrix_version: second\n",
        encoding="utf-8",
    )

    with pytest.raises(CoverageContractError, match="duplicate key: matrix_version"):
        load_coverage_matrix(path)


def test_yaml_merge_key_is_explicitly_rejected(tmp_path: Path) -> None:
    path = tmp_path / "merge.yaml"
    path.write_text(
        "base: &base\n  family: runtime\ntables:\n  sessions:\n    <<: *base\n",
        encoding="utf-8",
    )

    with pytest.raises(CoverageContractError, match="YAML merge keys are not permitted"):
        load_coverage_matrix(path)


def test_duplicate_json_mapping_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"source_kind":"first","source_kind":"second"}',
        encoding="utf-8",
    )

    with pytest.raises(CoverageContractError, match="duplicate key: source_kind"):
        load_schema_manifest(path)


def test_observed_cli_fails_closed_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.context_backend import coverage

    observed = load_schema_manifest(MANIFEST_PATH)
    observed["source_kind"] = "other-runtime"
    observed_path = tmp_path / "observed.json"
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "coverage.py",
            "--matrix",
            str(MATRIX_PATH),
            "--manifest",
            str(MANIFEST_PATH),
            "--observed",
            str(observed_path),
        ],
    )

    with pytest.raises(CoverageContractError, match="schema drift: source kind changed"):
        coverage.main()


def test_schema_inspector_reads_metadata_only_and_keeps_snapshot_immutable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (33)")
        conn.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("INSERT INTO example VALUES (1, 'must-never-enter-the-manifest')")
        conn.commit()
        conn.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()

        manifest = inspect_schema(path)

        encoded = json.dumps(manifest, sort_keys=True)
        assert "must-never-enter-the-manifest" not in encoded
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert manifest["source_schema_version"] == 33
        assert [table["name"] for table in manifest["tables"]] == ["example", "schema_version"]
        example = manifest["tables"][0]
        assert isinstance(example["sql_sha256"], str)
        assert example["columns"][1]["default"] is None
        assert example["columns"][1]["hidden"] == 0


@pytest.mark.parametrize("schema_version_ddl", [None, "CREATE TABLE schema_version (version INTEGER)"])
def test_schema_inspector_rejects_missing_or_empty_version(
    tmp_path: Path, schema_version_ddl: str | None
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    if schema_version_ddl is not None:
        conn.execute(schema_version_ddl)
    conn.commit()
    conn.close()

    with pytest.raises(SourceContractError, match="schema_version"):
        inspect_schema(path)


def test_schema_inspector_quotes_legal_table_identifiers(tmp_path: Path) -> None:
    manifest = _manifest_for_ddl(
        tmp_path / "state.db",
        'CREATE TABLE "odd""name" (id INTEGER PRIMARY KEY)',
    )

    assert any(table["name"] == 'odd"name' for table in manifest["tables"])


@pytest.mark.parametrize("alias_kind", ["same-path", "hardlink"])
def test_schema_manifest_cli_rejects_source_output_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias_kind: str
) -> None:
    from tools.context_backend import schema_manifest

    snapshot = tmp_path / "state.db"
    _manifest_for_ddl(snapshot, "CREATE TABLE example (id INTEGER PRIMARY KEY)")
    output = snapshot
    if alias_kind == "hardlink":
        output = tmp_path / "manifest.json"
        output.hardlink_to(snapshot)
    before = snapshot.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        ["schema_manifest.py", str(snapshot), "--output", str(output)],
    )

    with pytest.raises(SourceContractError, match="must not alias the source snapshot"):
        schema_manifest.main()

    assert snapshot.read_bytes() == before
    assert snapshot.read_bytes().startswith(b"SQLite format 3")
