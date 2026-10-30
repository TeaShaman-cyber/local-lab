#!/usr/bin/env python3
"""Structural checker for the local-lab infrastructure registry and rules.

Checks (no network, CI-safe):
  1. infra/registry.yaml parses and every service has the required fields.
  2. Service ids are unique and match ^INFRA-\\d{3}$.
  3. Rule ids are unique and match ^RULE-\\d{3}$.
  4. Every rule has a non-trivial verification.
  5. Cross-reference: rules in infrastructure.rules.yaml and in infrastructure.md
     name exactly the same set of RULE-NNN ids.
  6. No secrets: known token prefixes, or api_key/password/token fields holding a
     literal value instead of a reference.

What it does NOT check: whether a service is alive, whether the registry matches
reality, or whether rules contradict each other semantically. A PASS here is a
structural fact, not proof of correctness.

Usage:
    python tools/contracts/check_infra.py            # check repo files
    python tools/contracts/check_infra.py --list     # list registry entries
    python tools/contracts/check_infra.py --self-test
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "infra" / "registry.yaml"
RULES = ROOT / "docs" / "reference" / "infrastructure.rules.yaml"
DOC = ROOT / "docs" / "reference" / "infrastructure.md"

SERVICE_ID = re.compile(r"^INFRA-\d{3}$")
STAND_ID = re.compile(r"^LAB-\d{3}$")
RULE_ID = re.compile(r"^RULE-\d{3}$")
RULE_REF = re.compile(r"\bRULE-\d{3}\b")
SERVICE_FIELDS = ("id", "name", "role", "status", "kind", "location", "monitoring", "check", "criticality")
STAND_FIELDS = ("id", "name", "role", "host", "capacity", "constraints", "priority", "monitoring", "check", "criticality")
CRITICALITY = {"P1", "P2", "P3", "T"}

SECRET_PREFIXES = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[bp]-[A-Za-z0-9-]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
SECRET_FIELDS = ("api_key", "apikey", "password", "passwd", "token", "secret", "client_secret")
SECRET_REFERENCE_HINTS = ("$", "env", "external", "vault", "keyring", "none", "not-in-registry")


def check_registry(data: dict) -> list[str]:
    errors: list[str] = []
    services = data.get("services")
    if not isinstance(services, list) or not services:
        return ["registry: 'services' missing or empty"]

    seen: set[str] = set()
    for i, svc in enumerate(services):
        label = f"registry.services[{i}]"
        if not isinstance(svc, dict):
            errors.append(f"{label}: not a mapping")
            continue
        sid = svc.get("id", "")
        label = f"registry service {sid or i}"
        if not SERVICE_ID.match(str(sid)):
            errors.append(f"{label}: id must match ^INFRA-\\d{{3}}$")
        if sid in seen:
            errors.append(f"{label}: duplicate id")
        seen.add(sid)
        for field in SERVICE_FIELDS:
            if field not in svc or svc[field] in (None, "", {}, []):
                errors.append(f"{label}: missing or empty field '{field}'")
        monitoring = svc.get("monitoring")
        if isinstance(monitoring, str) and monitoring.strip().lower() in ("", "none", "tbd", "todo"):
            errors.append(f"{label}: monitoring is a placeholder")
        crit = svc.get("criticality")
        if crit is not None and crit not in CRITICALITY:
            errors.append(f"{label}: criticality must be one of {sorted(CRITICALITY)}, got {crit!r}")
    return errors


def check_stands(data: dict) -> list[str]:
    """Test stands: stable ids, required fields, unique priorities."""
    errors: list[str] = []
    stands = data.get("test_stands")
    if not isinstance(stands, list) or not stands:
        return ["test_stands: missing or empty"]

    seen: set[str] = set()
    priorities: dict[int, str] = {}
    for i, stand in enumerate(stands):
        if not isinstance(stand, dict):
            errors.append(f"test_stands[{i}]: not a mapping")
            continue
        sid = str(stand.get("id", ""))
        label = f"stand {sid or i}"
        if not STAND_ID.match(sid):
            errors.append(f"{label}: id must match ^LAB-\\d{{3}}$")
        if sid in seen:
            errors.append(f"{label}: duplicate id")
        seen.add(sid)
        for field in STAND_FIELDS:
            if field not in stand or stand[field] in (None, "", [], {}):
                errors.append(f"{label}: missing or empty field '{field}'")
        priority = stand.get("priority")
        if not isinstance(priority, int) or priority < 1:
            errors.append(f"{label}: priority must be a positive integer")
        elif priority in priorities:
            errors.append(f"{label}: priority {priority} already used by {priorities[priority]}")
        else:
            priorities[priority] = sid
        crit = stand.get("criticality")
        if crit is not None and crit not in CRITICALITY:
            errors.append(f"{label}: criticality must be one of {sorted(CRITICALITY)}, got {crit!r}")
    return errors


def check_rules(data: dict) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        return ["rules: 'rules' missing or empty"], set()

    seen: set[str] = set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"rules[{i}]: not a mapping")
            continue
        rid = str(rule.get("id", ""))
        label = f"rule {rid or i}"
        if not RULE_ID.match(rid):
            errors.append(f"{label}: id must match ^RULE-\\d{{3}}$")
        if rid in seen:
            errors.append(f"{label}: duplicate id")
        seen.add(rid)
        obligation = rule.get("obligation")
        if obligation not in ("MUST", "MUST_NOT"):
            errors.append(f"{label}: obligation must be MUST or MUST_NOT")
        statement = rule.get("statement")
        if not isinstance(statement, str) or len(statement.strip()) < 20:
            errors.append(f"{label}: statement too short")
        verification = rule.get("verification")
        if not isinstance(verification, str) or len(verification.strip()) < 20:
            errors.append(f"{label}: verification too short or missing")
    return errors, seen


def check_cross_ref(rules: set[str], doc_text: str) -> list[str]:
    doc_refs = set(RULE_REF.findall(doc_text))
    errors: list[str] = []
    for rid in sorted(rules - doc_refs):
        errors.append(f"cross-ref: {rid} is in rules.yaml but absent from infrastructure.md")
    for rid in sorted(doc_refs - rules):
        errors.append(f"cross-ref: {rid} is in infrastructure.md but absent from rules.yaml")
    return errors


def _walk(node, path="") -> list[tuple[str, str, object]]:
    """Yield (dotted_path, key, value) for every mapping entry."""
    out: list[tuple[str, str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(value, (dict, list)):
                out.extend(_walk(value, here))
            else:
                out.append((here, str(key).lower(), value))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            out.extend(_walk(value, f"{path}[{idx}]"))
    return out


def check_secrets(data: dict) -> list[str]:
    errors: list[str] = []
    for here, key, value in _walk(data):
        if not isinstance(value, str):
            continue
        for pattern in SECRET_PREFIXES:
            if pattern.search(value):
                errors.append(f"secret: {here} matches token prefix {pattern.pattern}")
        if key in SECRET_FIELDS:
            lowered = value.strip().lower()
            if lowered and not any(hint in lowered for hint in SECRET_REFERENCE_HINTS):
                errors.append(f"secret: {here} holds a literal value; use a reference")
    return errors


def run_checks(registry: dict, rules: dict, doc_text: str) -> list[str]:
    errors = check_registry(registry)
    errors.extend(check_stands(registry))
    rule_errors, rule_ids = check_rules(rules)
    errors.extend(rule_errors)
    if rule_ids:
        errors.extend(check_cross_ref(rule_ids, doc_text))
    errors.extend(check_secrets(registry))
    return errors


def load_repo() -> tuple[dict, dict, str]:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    doc_text = DOC.read_text(encoding="utf-8")
    return registry, rules, doc_text


def list_services() -> None:
    registry, _, _ = load_repo()
    rows = [("id", "name", "status", "where", "monitoring")]
    for svc in registry.get("services", []):
        loc = svc.get("location") or {}
        where = (loc.get("compose") or loc.get("install_path") or loc.get("endpoint")
                 or loc.get("profile") or loc.get("config") or loc.get("image")
                 or loc.get("iac_project") or "-")
        rows.append((
            str(svc.get("id", "")),
            str(svc.get("name", "")),
            str(svc.get("status", "")),
            str(where),
            str(svc.get("monitoring", ""))[:48],
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    retired = registry.get("retired") or []
    if retired:
        print(f"\nretired: {len(retired)}")


def self_test() -> int:
    """Negative control: broken inputs must fail, valid input must pass."""
    registry, rules, doc_text = load_repo()
    base_errors = run_checks(registry, rules, doc_text)
    if base_errors:
        print("self-test: repository files already fail the checker:")
        for err in base_errors:
            print("  -", err)
        return 1

    import copy

    cases: list[tuple[str, dict, dict, str]] = []

    dup = copy.deepcopy(registry)
    dup["services"].append(copy.deepcopy(dup["services"][0]))
    cases.append(("duplicate service id", dup, rules, doc_text))

    no_monitor = copy.deepcopy(registry)
    no_monitor["services"][0].pop("monitoring", None)
    cases.append(("missing monitoring", no_monitor, rules, doc_text))

    leaky = copy.deepcopy(registry)
    leaky["services"][0]["location"]["debug"] = "ghp_" + "a" * 24
    cases.append(("token prefix in registry", leaky, rules, doc_text))

    missing_rule = copy.deepcopy(rules)
    missing_rule["rules"][0]["id"] = "RULE-999"
    cases.append(("rule missing from doc", registry, missing_rule, doc_text))

    dup_stand = copy.deepcopy(registry)
    dup_stand["test_stands"][1]["priority"] = dup_stand["test_stands"][0]["priority"]
    cases.append(("duplicate stand priority", dup_stand, rules, doc_text))

    bad_class = copy.deepcopy(registry)
    bad_class["services"][0]["criticality"] = "P9"
    cases.append(("invalid criticality", bad_class, rules, doc_text))

    failures = 0
    for name, reg, rul, doc in cases:
        if not run_checks(reg, rul, doc):
            print(f"self-test FAIL: '{name}' was not caught")
            failures += 1
        else:
            print(f"self-test ok:   '{name}' caught")

    if failures:
        print(f"self-test: {failures} broken input(s) passed")
        return 1
    print("self-test: all broken inputs caught; valid input passes")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if "--list" in argv:
        list_services()
        return 0

    registry, rules, doc_text = load_repo()
    errors = run_checks(registry, rules, doc_text)
    if errors:
        print(f"FAIL: {len(errors)} problem(s)")
        for err in errors:
            print("  -", err)
        return 1
    print(f"OK: {len(registry.get('services', []))} services, "
          f"{len(registry.get('test_stands', []))} stands, "
          f"{len(rules.get('rules', []))} rules, "
          f"{len(registry.get('retired', []))} retired")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
