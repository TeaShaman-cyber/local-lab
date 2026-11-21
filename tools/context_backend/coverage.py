"""Machine-checkable coverage contract for the high-fidelity source mirror."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

import yaml

from tools.context_backend import SUPPORTED_SOURCE_SCHEMA_VERSIONS
from tools.context_backend.schema_manifest import MANIFEST_VERSION

MATRIX_VERSION = "theseus.context.coverage-matrix.v1"
SOURCE_KIND = "hermes-state-snapshot"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MIRROR_DECISIONS = {"include_full_row", "derive", "descriptor", "exclude_mechanic"}
CANONICAL_DECISIONS = {
    "include",
    "include_sensitive",
    "derive",
    "defer",
    "exclude_transient",
    "exclude_source_specific",
    "exclude_mechanic",
}
SENSITIVITY_CLASSES = {"local_sensitive", "local_internal", "derived", "none"}
OBJECT_LABELS = {"views": "view", "triggers": "trigger", "indexes": "index"}
ALLOWED_DECISION_PAIRS = {
    ("include_full_row", "include"),
    ("include_full_row", "include_sensitive"),
    ("include_full_row", "derive"),
    ("include_full_row", "defer"),
    ("include_full_row", "exclude_transient"),
    ("include_full_row", "exclude_source_specific"),
    ("derive", "derive"),
    ("descriptor", "derive"),
    ("exclude_mechanic", "exclude_mechanic"),
}
AUTHORITY_CONTRACTS = {
    "aggregate_ledger": {("include_full_row", "include", "local_internal")},
    "authoritative_history": {
        ("include_full_row", "include_sensitive", "local_sensitive")
    },
    "derived_index": {("derive", "derive", "derived")},
    "durable_delegation_ledger": {
        ("include_full_row", "defer", "local_sensitive")
    },
    "durable_receipt": {("include_full_row", "defer", "local_sensitive")},
    "external_replication_mechanic": {
        ("exclude_mechanic", "exclude_mechanic", "none")
    },
    "mixed_namespace_ledger": {
        ("include_full_row", "defer", "local_sensitive")
    },
    "operational_identity_ledger": {
        ("include_full_row", "include", "local_internal")
    },
    "operational_ledger": {("include_full_row", "defer", "local_internal")},
    "sensitive_operational_ledger": {
        ("include_full_row", "defer", "local_sensitive")
    },
    "placement_dependent_observed_state": {
        ("include_full_row", "defer", "local_sensitive")
    },
    "source_specific_operational_state": {
        ("include_full_row", "exclude_source_specific", "local_internal")
    },
    "sqlite_engine_mechanic": {
        ("exclude_mechanic", "exclude_mechanic", "none")
    },
    "sqlite_fts_shadow": {("exclude_mechanic", "exclude_mechanic", "none")},
    "transient_operational_state": {
        ("include_full_row", "exclude_transient", "local_internal")
    },
    "sensitive_transient_operational_state": {
        ("include_full_row", "exclude_transient", "local_sensitive")
    },
    "version_marker": {("descriptor", "derive", "local_internal")},
}


class CoverageContractError(RuntimeError):
    """The coverage matrix is incomplete or disagrees with the source schema."""


def _construct_unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CoverageContractError(f"schema manifest contains duplicate key: {key}")
        result[key] = value
    return result


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise CoverageContractError("coverage matrix YAML merge keys are not permitted")
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise CoverageContractError(
                "coverage matrix contains an invalid mapping key"
            ) from exc
        if duplicate:
            raise CoverageContractError(f"coverage matrix contains duplicate key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_schema_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_construct_unique_json_object,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageContractError(f"cannot load schema manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise CoverageContractError("schema manifest must be an object")
    return payload


def load_coverage_matrix(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise CoverageContractError(f"cannot load coverage matrix: {path}") from exc
    if not isinstance(payload, dict):
        raise CoverageContractError("coverage matrix must be an object")
    return payload


def _table_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tables = manifest.get("tables")
    if not isinstance(tables, list):
        raise CoverageContractError("schema manifest tables must be a list")
    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise CoverageContractError("schema manifest contains an invalid table")
        name = table["name"]
        if name in result:
            raise CoverageContractError(f"duplicate schema table: {name}")
        sql_sha256 = table.get("sql_sha256")
        if not isinstance(sql_sha256, str) or SHA256_PATTERN.fullmatch(sql_sha256) is None:
            raise CoverageContractError(f"invalid table DDL hash for {name}")
        _column_map(table)
        _foreign_key_contracts(table)
        result[name] = table
    return result


def _primary_key(table: dict[str, Any]) -> list[str]:
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise CoverageContractError(f"columns must be a list for {table.get('name')}")
    keyed = sorted(
        (
            (int(column.get("pk", 0)), str(column.get("name")))
            for column in columns
            if isinstance(column, dict) and int(column.get("pk", 0)) > 0
        ),
        key=lambda item: item[0],
    )
    return [name for _, name in keyed]


def _object_names(manifest: dict[str, Any], key: str) -> set[str]:
    objects = manifest.get(key)
    if not isinstance(objects, list):
        raise CoverageContractError(f"schema manifest {key} must be a list")
    names: set[str] = set()
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise CoverageContractError(f"schema manifest {key} contains an invalid object")
        name = item["name"]
        if name in names:
            raise CoverageContractError(f"duplicate schema {OBJECT_LABELS[key]}: {name}")
        names.add(name)
    return names


def validate_coverage_matrix(matrix: dict[str, Any], manifest: dict[str, Any]) -> None:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CoverageContractError("unsupported schema manifest version")
    if matrix.get("matrix_version") != MATRIX_VERSION:
        raise CoverageContractError("unsupported coverage matrix version")
    if manifest.get("source_kind") != SOURCE_KIND:
        raise CoverageContractError("unsupported schema source kind")
    if matrix.get("source_kind") != SOURCE_KIND:
        raise CoverageContractError("unsupported matrix source kind")
    if matrix.get("source_kind") != manifest.get("source_kind"):
        raise CoverageContractError("source kind mismatch")
    source_schema_version = manifest.get("source_schema_version")
    if source_schema_version not in SUPPORTED_SOURCE_SCHEMA_VERSIONS:
        raise CoverageContractError(
            f"unsupported source schema version: {source_schema_version}"
        )
    if matrix.get("source_schema_version") != manifest.get("source_schema_version"):
        raise CoverageContractError("source schema version mismatch")

    observed = _table_map(manifest)
    decisions = matrix.get("tables")
    if not isinstance(decisions, dict):
        raise CoverageContractError("coverage matrix tables must be a mapping")
    missing = sorted(set(observed) - set(decisions))
    extra = sorted(set(decisions) - set(observed))
    if missing:
        raise CoverageContractError("missing table decisions: " + ", ".join(missing))
    if extra:
        raise CoverageContractError("unknown table decisions: " + ", ".join(extra))

    for name, table in observed.items():
        decision = decisions[name]
        if not isinstance(decision, dict):
            raise CoverageContractError(f"table decision must be a mapping: {name}")
        mirror = decision.get("mirror")
        canonical = decision.get("canonical")
        sensitivity = decision.get("sensitivity")
        authority = decision.get("authority")
        for field, value in (
            ("mirror", mirror),
            ("canonical", canonical),
            ("sensitivity", sensitivity),
            ("authority", authority),
        ):
            if not isinstance(value, str):
                raise CoverageContractError(f"{field} must be a string for {name}")
        if mirror not in MIRROR_DECISIONS:
            raise CoverageContractError(f"invalid mirror decision for {name}: {mirror}")
        if canonical not in CANONICAL_DECISIONS:
            raise CoverageContractError(f"invalid canonical decision for {name}: {canonical}")
        if (mirror, canonical) not in ALLOWED_DECISION_PAIRS:
            raise CoverageContractError(
                f"incompatible mirror/canonical decisions for {name}: "
                f"mirror={mirror}, canonical={canonical}"
            )
        if decision.get("replay") != "forbidden":
            raise CoverageContractError(f"replay must be forbidden for {name}")
        if sensitivity not in SENSITIVITY_CLASSES:
            raise CoverageContractError(f"invalid sensitivity for {name}")
        if canonical == "include_sensitive" and sensitivity != "local_sensitive":
            raise CoverageContractError(
                f"include_sensitive requires local_sensitive for {name}"
            )
        if canonical == "include" and sensitivity == "local_sensitive":
            raise CoverageContractError(
                f"local_sensitive cannot use canonical include for {name}"
            )
        authority_contract = AUTHORITY_CONTRACTS.get(authority)
        if authority_contract is None:
            raise CoverageContractError(f"unknown authority for {name}: {authority}")
        if (mirror, canonical, sensitivity) not in authority_contract:
            raise CoverageContractError(
                f"decisions conflict with authority for {name}: authority={authority}, "
                f"mirror={mirror}, canonical={canonical}, sensitivity={sensitivity}"
            )
        for required in ("family", "authority", "rationale"):
            if not isinstance(decision.get(required), str) or not decision[required].strip():
                raise CoverageContractError(f"missing {required} for {name}")
        declared_key = decision.get("primary_key")
        if not isinstance(declared_key, list) or not all(
            isinstance(item, str) for item in declared_key
        ):
            raise CoverageContractError(f"primary_key must be a string list for {name}")
        actual_key = _primary_key(table)
        if declared_key != actual_key:
            raise CoverageContractError(
                f"primary key mismatch for {name}: matrix={declared_key}, schema={actual_key}"
            )
        if mirror == "include_full_row" and not actual_key:
            raise CoverageContractError(f"included table has no stable primary key: {name}")

    schema_objects = matrix.get("schema_objects")
    if not isinstance(schema_objects, dict):
        raise CoverageContractError("schema_objects must be a mapping")
    for matrix_key, manifest_key in (("views", "views"), ("triggers", "triggers")):
        declared = schema_objects.get(matrix_key)
        if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
            raise CoverageContractError(f"schema_objects.{matrix_key} must be a string list")
        if len(declared) != len(set(declared)):
            raise CoverageContractError(f"schema_objects.{matrix_key} contains duplicates")
        observed_names = set(_object_contract_map(manifest, manifest_key))
        if set(declared) != observed_names:
            missing_names = sorted(observed_names - set(declared))
            extra_names = sorted(set(declared) - observed_names)
            raise CoverageContractError(
                f"{matrix_key} mismatch: missing={missing_names}, extra={extra_names}"
            )
    if schema_objects.get("indexes") != "derive_physical_and_constraints":
        raise CoverageContractError("schema_objects.indexes policy must be explicit")
    _object_contract_map(manifest, "indexes")


def _column_map(table: dict[str, Any]) -> dict[str, tuple[str, bool, str | None, int, int]]:
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise CoverageContractError(f"columns must be a list for {table.get('name')}")
    result: dict[str, tuple[str, bool, str | None, int, int]] = {}
    for column in columns:
        if not isinstance(column, dict) or not isinstance(column.get("name"), str):
            raise CoverageContractError(
                f"invalid column contract for {table.get('name')}"
            )
        name = column["name"]
        if name in result:
            raise CoverageContractError(
                f"duplicate column contract for {table.get('name')}.{name}"
            )
        column_type = column.get("type")
        notnull = column.get("notnull")
        default = column.get("default")
        primary_key = column.get("pk")
        hidden = column.get("hidden")
        if not isinstance(column_type, str):
            raise CoverageContractError(
                f"invalid type contract for {table.get('name')}.{name}"
            )
        if not isinstance(notnull, bool):
            raise CoverageContractError(
                f"invalid notnull contract for {table.get('name')}.{name}"
            )
        if default is not None and not isinstance(default, str):
            raise CoverageContractError(
                f"invalid default contract for {table.get('name')}.{name}"
            )
        if type(primary_key) is not int or primary_key < 0:
            raise CoverageContractError(
                f"invalid primary-key contract for {table.get('name')}.{name}"
            )
        if type(hidden) is not int or hidden not in {0, 1, 2, 3}:
            raise CoverageContractError(
                f"invalid hidden-column contract for {table.get('name')}.{name}"
            )
        result[name] = (column_type, notnull, default, primary_key, hidden)
    return result


def _foreign_key_contracts(table: dict[str, Any]) -> tuple[tuple[str, str, str, str, str, str], ...]:
    contracts = []
    foreign_keys = table.get("foreign_keys")
    if not isinstance(foreign_keys, list):
        raise CoverageContractError(
            f"foreign_keys must be a list for {table.get('name')}"
        )
    for foreign_key in foreign_keys:
        if not isinstance(foreign_key, dict):
            raise CoverageContractError(
                f"invalid foreign key contract for {table.get('name')}"
            )
        contracts.append(
            (
                str(foreign_key.get("from", "")),
                str(foreign_key.get("to_table", "")),
                str(foreign_key.get("to_column", "")),
                str(foreign_key.get("on_update", "")),
                str(foreign_key.get("on_delete", "")),
                str(foreign_key.get("match", "")),
            )
        )
    return tuple(sorted(contracts))


def _object_contract_map(manifest: dict[str, Any], key: str) -> dict[str, str | None]:
    names = _object_names(manifest, key)
    objects = manifest[key]
    result: dict[str, str | None] = {}
    for item in objects:
        sql_sha256 = item.get("sql_sha256")
        if sql_sha256 is None and key != "indexes":
            raise CoverageContractError(
                f"missing {OBJECT_LABELS[key]} DDL hash for {item['name']}"
            )
        if sql_sha256 is not None and (
            not isinstance(sql_sha256, str)
            or SHA256_PATTERN.fullmatch(sql_sha256) is None
        ):
            raise CoverageContractError(
                f"invalid {OBJECT_LABELS[key]} DDL hash for {item['name']}"
            )
        result[str(item["name"])] = sql_sha256
    if set(result) != names:
        raise CoverageContractError(f"invalid schema {key} contract")
    return result


def compare_schema_manifests(
    expected: dict[str, Any], observed: dict[str, Any]
) -> tuple[str, ...]:
    """Return deterministic drift findings; an empty tuple means exact contract match."""
    findings: list[str] = []
    if expected.get("manifest_version") != observed.get("manifest_version"):
        findings.append("manifest version changed")
    if expected.get("source_kind") != observed.get("source_kind"):
        findings.append("source kind changed")
    if expected.get("source_schema_version") != observed.get("source_schema_version"):
        findings.append("source schema version changed")

    expected_tables = _table_map(expected)
    observed_tables = _table_map(observed)
    for name in sorted(set(observed_tables) - set(expected_tables)):
        findings.append(f"unexpected table {name}")
    for name in sorted(set(expected_tables) - set(observed_tables)):
        findings.append(f"missing table {name}")
    for name in sorted(set(expected_tables) & set(observed_tables)):
        if expected_tables[name].get("sql_sha256") != observed_tables[name].get(
            "sql_sha256"
        ):
            findings.append(f"table DDL changed {name}")
        expected_columns = _column_map(expected_tables[name])
        observed_columns = _column_map(observed_tables[name])
        for column in sorted(set(observed_columns) - set(expected_columns)):
            findings.append(f"unexpected column {name}.{column}")
        for column in sorted(set(expected_columns) - set(observed_columns)):
            findings.append(f"missing column {name}.{column}")
        for column in sorted(set(expected_columns) & set(observed_columns)):
            if expected_columns[column] != observed_columns[column]:
                findings.append(
                    f"column contract changed {name}.{column}: "
                    f"expected={expected_columns[column]}, observed={observed_columns[column]}"
                )
        if _foreign_key_contracts(expected_tables[name]) != _foreign_key_contracts(
            observed_tables[name]
        ):
            findings.append(f"foreign key contract changed {name}")

    for key in ("views", "triggers", "indexes"):
        label = OBJECT_LABELS[key]
        expected_objects = _object_contract_map(expected, key)
        observed_objects = _object_contract_map(observed, key)
        for name in sorted(set(observed_objects) - set(expected_objects)):
            findings.append(f"unexpected {label} {name}")
        for name in sorted(set(expected_objects) - set(observed_objects)):
            findings.append(f"missing {label} {name}")
        for name in sorted(set(expected_objects) & set(observed_objects)):
            if expected_objects[name] != observed_objects[name]:
                findings.append(f"changed {label} {name}")
    return tuple(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the context coverage contract")
    parser.add_argument("--matrix", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--observed", type=pathlib.Path)
    args = parser.parse_args()
    matrix = load_coverage_matrix(args.matrix)
    expected = load_schema_manifest(args.manifest)
    validate_coverage_matrix(matrix, expected)
    if args.observed:
        observed = load_schema_manifest(args.observed)
        drift = compare_schema_manifests(expected, observed)
        if drift:
            raise CoverageContractError("schema drift: " + "; ".join(drift))
    print("COVERAGE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
