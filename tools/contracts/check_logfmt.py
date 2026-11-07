#!/usr/bin/env python3
"""Check the repository's logfmt -> Alloy -> alert contract.

`logfmtkv` owns logfmt syntax. This checker owns only project semantics that a
vendor parser cannot know: source fields must be mapped, alert metrics must be
declared, and separator-heavy values must be quoted for our Loki pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
ALLOY = REPO / "infra/monitoring/alloy/config.alloy"
RULES = REPO / "infra/grafana/alertrules"
SEPARATORS = ",:"


def tool_path() -> str | None:
    found = shutil.which("logfmtkv")
    if found:
        return found
    for path in (pathlib.Path.home() / ".local/bin/logfmtkv.exe", pathlib.Path("E:/tools/logfmtkv/logfmtkv.exe")):
        if path.is_file():
            return str(path)
    return None


def block(text: str, start: int) -> tuple[int, int]:
    opening = text.index("{", start)
    depth = 0
    for pos in range(opening, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return opening, pos + 1
    raise ValueError("unbalanced braces")


def examples(text: str) -> list[tuple[int, str]]:
    result = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("//"):
            line = line.lstrip("/").strip()
            if line.startswith("event="):
                result.append((line_no, line))
    return result


def bare_separators(line: str) -> list[str]:
    """Return bare key=value tokens containing separators.

    This is intentionally not a logfmt parser. `logfmtkv` parses syntax; this
    narrow regex enforces our stricter downstream contract on known examples.
    """
    result = []
    for match in re.finditer(r"(?:^|\s)([A-Za-z_][\w.-]*)=(\S+)", line):
        value = match.group(2)
        if not value.startswith('"') and any(char in value for char in SEPARATORS):
            result.append(f"{match.group(1)}={value}")
    return result


def metric_sources(text: str) -> tuple[list[str], set[str]]:
    errors = []
    metrics = set()
    for match in re.finditer(r"stage\.match\s*\{", text):
        opening, closing = block(text, match.start())
        body = text[opening:closing]
        mapping_match = re.search(r"stage\.logfmt\s*\{[^}]*mapping\s*=\s*\{([^}]*)", body, re.S)
        if not mapping_match:
            continue
        mapped = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", mapping_match.group(1)))
        sources = set(re.findall(r'source\s*=\s*"([^"]+)"', body))
        line = text.count("\n", 0, match.start()) + 1
        for source in sorted(sources - mapped):
            errors.append(f"{ALLOY.name}:{line}: source={source!r} is absent from mapping {sorted(mapped)}")

    for match in re.finditer(r"metric\.(?:counter|gauge|histogram)\s*\{", text):
        opening, closing = block(text, match.start())
        body = text[opening:closing]
        name = re.search(r'name\s*=\s*"([^"]+)"', body)
        if name:
            prefix = re.search(r'prefix\s*=\s*"([^"]*)"', body)
            metrics.add((prefix.group(1) if prefix else "loki_process_custom_") + name.group(1))
    return errors, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        cases = [("a=1", []), ('packs="a:b,c:d"', []), ("packs=a:b,c:d", ["packs=a:b,c:d"])]
        failed = sum(bare_separators(line) != expected for line, expected in cases)
        tool = tool_path()
        if tool:
            result = subprocess.run([tool, "check"], input='a="unterminated\n', text=True, capture_output=True)
            failed += result.returncode == 0
        else:
            print("SKIP logfmtkv not installed")
        print("self-test:", "PASS" if not failed else f"FAIL ({failed})")
        return int(bool(failed))

    if not ALLOY.is_file():
        print(f"FAIL missing {ALLOY}", file=sys.stderr)
        return 2
    text = ALLOY.read_text(encoding="utf-8")
    problems = []
    sample_lines = [line for _, line in examples(text)]
    tool = tool_path()
    if not tool:
        message = "logfmtkv not installed"
        if os.environ.get("LOGFMT_STRICT") == "1":
            problems.append(message)
        else:
            print("SKIP", message)
    elif sample_lines:
        result = subprocess.run([tool, "check"], input="\n".join(sample_lines) + "\n", text=True, capture_output=True)
        if result.returncode:
            problems.append(f"logfmtkv: {(result.stderr or result.stdout).strip()}")

    for line_no, line in examples(text):
        for token in bare_separators(line):
            problems.append(f"{ALLOY.name}:{line_no}: bare separator value {token!r}; quote it")

    source_errors, declared = metric_sources(text)
    problems.extend(source_errors)
    for path in sorted(RULES.rglob("*.json")) if RULES.is_dir() else []:
        try:
            rule = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: cannot parse rule: {exc}")
            continue
        for ref, node in (rule.get("spec", {}).get("expressions") or {}).items():
            expression = (node.get("model") or {}).get("expr") or ""
            for metric in set(re.findall(r"\b(honcho_[a-z0-9_]+)\b", expression)):
                if metric not in declared:
                    problems.append(f"{path.name}:{ref}: rule metric {metric!r} is not declared in Alloy")

    if problems:
        for problem in problems:
            print("FAIL", problem, file=sys.stderr)
        return 1
    print("ok  logfmt syntax, separator policy, source mappings, and alert metrics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
