#!/usr/bin/env python3
"""Does a Hermes plugin package actually load?

A desktop plugin fails quietly in three ways that no linter sees: a JSX tag in a
file that is never compiled, an import of a package the runtime refuses, and a
`plugin_api.py` without the `router` the mount looks for. Each one produces a
toast at best and a plugin that simply never appears at worst — after the user
already believes it is installed.

Checks, by reading files only:
1. `plugin.yaml` name equals the folder name (the namespace is the folder).
2. `dashboard/manifest.json`: `name` matches, `api` points at a real file.
3. That file defines `router` (the dashboard mount imports it) and compiles.
4. `desktop/plugin.js` (or a root `plugin.js`): imports only the allowed
   specifiers, contains no JSX syntax, and default-exports the matching id.
5. No obvious credential literals in the package.

Exit codes: 0 = loads, 1 = defect, 2 = cannot run.

Usage:
    python tools/contracts/check_hermes_plugin.py
    python tools/contracts/check_hermes_plugin.py --self-test
"""

from __future__ import annotations

import argparse
import json
import pathlib
import py_compile
import re
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_ROOTS = [REPO / "infra" / "hermes" / "plugins"]
ALLOWED_SPECIFIERS = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}
SECRET_PATTERNS = [
    re.compile(r"glsa_[A-Za-z0-9]{10,}"),
    re.compile(r"glc_[A-Za-z0-9]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
]


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def imports_of(source: str) -> list[str]:
    specs = re.findall(r"""^\s*import\s[^'"]*?from\s*['"]([^'"]+)['"]""", source, re.M)
    specs += re.findall(r"""^\s*import\s*['"]([^'"]+)['"]""", source, re.M)
    return specs


def jsx_violation(source: str) -> str | None:
    """A JSX tag outside comments and strings. Runtime plugins are not compiled."""
    body = _strip_js_comments(source)
    body = re.sub(r"`(?:[^`\\]|\\.)*`", "``", body, flags=re.S)
    body = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", body)
    body = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', body)
    match = re.search(r"</?[A-Za-z][A-Za-z0-9.]*(?:\s[^<>]*)?/?>", body)
    return match.group(0) if match else None


def check_package(package: pathlib.Path) -> list[str]:
    problems: list[str] = []
    name = package.name

    entry = package / "__init__.py"
    if not entry.is_file():
        problems.append(
            f"{name}: no __init__.py — the loader registers nothing, the plugin never appears "
            f"as an agent row, and a desktop half paired to it never reaches Capabilities"
        )
    else:
        text = entry.read_text(encoding="utf-8")
        if not re.search(r"(?m)^def\s+register\s*\(", text):
            problems.append(f"{name}/__init__.py: no `def register(ctx)` entry point")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                py_compile.compile(str(entry), cfile=str(pathlib.Path(tmp) / "b.pyc"), doraise=True)
        except py_compile.PyCompileError as exc:
            problems.append(f"{name}/__init__.py: does not compile: {exc.msg}")

    yaml_path = package / "plugin.yaml"
    if not yaml_path.is_file():
        problems.append(f"{name}: plugin.yaml missing")
    else:
        head = yaml_path.read_text(encoding="utf-8")
        declared = re.search(r"(?m)^name:\s*(\S+)", head)
        if not declared:
            problems.append(f"{name}/plugin.yaml: no name field")
        elif declared.group(1).strip().strip('"\'') != name:
            problems.append(f"{name}/plugin.yaml: name {declared.group(1)!r} does not match folder {name!r}")

    dashboard = package / "dashboard"
    manifest_path = dashboard / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{name}/dashboard/manifest.json: invalid JSON: {exc}")
            manifest = {}
        declared = manifest.get("name")
        if declared and declared != name:
            problems.append(f"{name}/dashboard/manifest.json: name {declared!r} does not match folder {name!r}")
        api = manifest.get("api")
        if api:
            api_path = dashboard / api
            if not api_path.is_file():
                problems.append(f"{name}/dashboard/manifest.json: api {api!r} does not exist")
            else:
                text = api_path.read_text(encoding="utf-8")
                if not re.search(r"(?m)^router\s*=\s*APIRouter\(", text):
                    problems.append(f"{name}/dashboard/{api}: no `router = APIRouter()` — routes never mount")
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        py_compile.compile(str(api_path), cfile=str(pathlib.Path(tmp) / "a.pyc"), doraise=True)
                except py_compile.PyCompileError as exc:
                    problems.append(f"{name}/dashboard/{api}: does not compile: {exc.msg}")

    desktop = package / "desktop" / "plugin.js"
    if not desktop.is_file():
        desktop = package / "plugin.js"
    if desktop.is_file():
        source = desktop.read_text(encoding="utf-8")
        for specifier in imports_of(source):
            if specifier not in ALLOWED_SPECIFIERS:
                problems.append(
                    f"{name}/{desktop.relative_to(package)}: import {specifier!r} is refused by the runtime; "
                    f"allowed: {sorted(ALLOWED_SPECIFIERS)}"
                )
        tag = jsx_violation(source)
        if tag:
            problems.append(f"{name}/{desktop.relative_to(package)}: JSX syntax {tag!r} — the file is loaded uncompiled, use jsx()")
        declared = re.search(r"""id\s*:\s*['"]([^'"]+)['"]""", source)
        if not declared:
            problems.append(f"{name}/{desktop.relative_to(package)}: no id in the default export")
        elif declared.group(1) != name:
            problems.append(f"{name}/{desktop.relative_to(package)}: id {declared.group(1)!r} does not match folder {name!r}")

    for path in package.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".json", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{name}/{path.relative_to(package)}: looks like a credential literal")
                break
    return problems


def self_test() -> int:
    failures = 0

    def check(label, condition, detail=""):
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'} {label}{'' if condition else f' — {detail}'}")
        failures += 0 if condition else 1

    check("bare import specifier caught", imports_of("import x from 'lodash'") == ["lodash"])
    check("sdk import accepted", set(imports_of("import { jsx } from 'react/jsx-runtime'")) <= ALLOWED_SPECIFIERS)
    check("JSX tag detected", jsx_violation("const a = <div>hi</div>") is not None)
    check("self-closing JSX detected", jsx_violation("const a = <Badge />") is not None)
    check("jsx() call is fine", jsx_violation("jsx('div', { children: 'a < b' })") is None)
    check("comparison is not JSX", jsx_violation("const ok = a < b && c > d") is None)
    check("credential literal detected", any(p.search("tok = 'glsa_ABCDEFGHIJKLMNOP'") for p in SECRET_PATTERNS))
    check("plain token name is not a credential", not any(p.search("GRAFANA_ALERTS_TOKEN") for p in SECRET_PATTERNS))

    # Package-level control: the failure that actually happened — a package
    # with a valid manifest and desktop half but no entry point.
    with tempfile.TemporaryDirectory() as tmp:
        pkg = pathlib.Path(tmp) / "bare"
        (pkg / "dashboard").mkdir(parents=True)
        (pkg / "plugin.yaml").write_text("name: bare\n", encoding="utf-8")
        (pkg / "dashboard" / "manifest.json").write_text('{"name": "bare"}', encoding="utf-8")
        found = check_package(pkg)
        check("missing __init__.py is caught", any("__init__.py" in p for p in found), found)

        (pkg / "__init__.py").write_text("# no entry point\n", encoding="utf-8")
        found = check_package(pkg)
        check("missing register() is caught", any("register" in p for p in found), found)

        (pkg / "__init__.py").write_text("def register(ctx):\n    return None\n", encoding="utf-8")
        found = check_package(pkg)
        check("valid entry point passes", not any("__init__.py" in p for p in found), found)

    print(f"self-test: {'PASS' if not failures else f'FAIL ({failures})'}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    problems: list[str] = []
    checked = 0
    for root in PLUGIN_ROOTS:
        if not root.is_dir():
            continue
        for package in sorted(p for p in root.iterdir() if p.is_dir()):
            checked += 1
            problems.extend(check_package(package))

    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print(f"hermes plugin contract: {len(problems)} violation(s)", file=sys.stderr)
        return 1
    print(f"ok  {checked} plugin package(s) loadable: names, imports, no JSX, router present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
