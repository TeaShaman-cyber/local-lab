#!/usr/bin/env python3
"""Check that the Alloy config the collector runs matches the versioned template.

The repository file is a template: endpoint hosts, instance ids and credentials
arrive as ``<PLACEHOLDER>`` tokens, so the repository never carries a secret.
The installed file is that template rendered with real values. Comparing them
byte-for-byte is meaningless — every secret would look like drift — so the
comparison is structural: a placeholder matches whatever stands in its place,
and every other character must be identical.

What that buys: a component that exists in the repository but never reached the
collector (or the reverse) is a failure, and it is found by a command instead of
by noticing that an alert stays quiet. That exact case happened: the availability
rule depended on ``up`` while the versioned config, not yet deployed, dropped it.

A machine without the collector has nothing to compare: SKIP, exit 0, said out
loud. CI is such a machine.

Usage:
    python tools/contracts/check_alloy_projection.py
    python tools/contracts/check_alloy_projection.py --self-test
    python tools/contracts/check_alloy_projection.py --live PATH --template PATH
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "infra" / "monitoring" / "alloy" / "config.alloy"
DEFAULT_LIVE = pathlib.Path(r"C:/Program Files/GrafanaLabs/Alloy/config.alloy")

PLACEHOLDER = re.compile(r"<([A-Z][A-Z0-9_]*)>")

# A diagnostic must never carry a live credential. Anything that looks like a
# secret assignment is printed with its value replaced.
SECRET_LINE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api_key|apikey|bearer_token|client_secret)\b"
)
ASSIGNMENT = re.compile(r"^(\s*[\w.]+\s*=\s*).*$")


def redact(line: str) -> str:
    """Return ``line`` safe to print: secret values become ``"<redacted>"``."""
    if not SECRET_LINE.search(line):
        return line
    match = ASSIGNMENT.match(line)
    return f'{match.group(1)}"<redacted>"' if match else "<redacted line>"


def unresolved_placeholders(lines: list[str]) -> list[str]:
    """Lines of the installed file that still hold template placeholders.
    Comments are exempt: the template explains where a token comes from with
    ``// ... <PASTE_TOKEN_HERE>``, and that instruction travels into the rendered
    file unchanged. A placeholder in an assignment is a real defect; the same
    text in a comment is documentation.
    """
    found: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("//", "#")):
            continue
        if PLACEHOLDER.search(line):
            found.append(line)
    return found


def lines_of(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")


def template_to_pattern(template_line: str) -> re.Pattern[str]:
    """Build a regex for one template line: placeholders match any quoted value."""
    out: list[str] = []
    last = 0
    for match in PLACEHOLDER.finditer(template_line):
        out.append(re.escape(template_line[last:match.start()]))
        # Rendered values sit inside quotes; stop at the closing quote so a
        # placeholder cannot swallow the rest of the line.
        out.append(r'[^"]*')
        last = match.end()
    out.append(re.escape(template_line[last:]))
    return re.compile("".join(out) + r"\s*")


def compare(template: list[str], live: list[str]) -> list[str]:
    problems: list[str] = []

    # A template deployed without rendering is not a rendering: every placeholder
    # would otherwise be happily absorbed by the wildcard, and the check would
    # report success on a config that cannot deliver telemetry.
    for line in unresolved_placeholders(live):
        problems.append(f"unresolved placeholder in the installed config: {redact(line).strip()[:120]}")

    if len(template) != len(live):
        problems.append(
            f"line count differs: template {len(template)} vs installed {len(live)} "
            f"— the installed config is not a rendering of this template"
        )
    for index in range(min(len(template), len(live))):
        want = template[index]
        have = live[index]
        if template_to_pattern(want).fullmatch(have):
            continue
        problems.append(
            f"line {index + 1}:\n"
            f"    template:  {redact(want).rstrip()[:120]}\n"
            f"    installed: {redact(have).rstrip()[:120]}"
        )
        if len(problems) >= 8:
            problems.append("... (first 8 differences only)")
            break
    return problems


def run_self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: object = "") -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'} {label}{'' if condition else f' — {detail}'}")
        failures += 0 if condition else 1

    template = [
        'url = "https://<LOKI_HOST>/loki/api/v1/push"',
        "  username = \"<GRAFANA_INSTANCE_ID>\"",
        "  password = \"<LOKI_PASSWORD>\"",
        "}",
        "",
        'prometheus.relabel "honcho_keep" {',
        '  forward_to = [prometheus.remote_write.grafana_cloud.receiver]',
        "}",
    ]
    rendered = [
        'url = "https://logs-prod-012.grafana.net/loki/api/v1/push"',
        '  username = "1849597"',
        '  password = "s3cret"',
        "}",
        "",
        'prometheus.relabel "honcho_keep" {',
        '  forward_to = [prometheus.remote_write.grafana_cloud.receiver]',
        "}",
    ]

    check("rendered template passes", compare(template, rendered) == [], compare(template, rendered))

    # A different secret value is not drift: the placeholder absorbs it.
    other_secret = list(rendered)
    other_secret[2] = '  password = "rotated-secret"'
    check("rotated secret is not drift", compare(template, other_secret) == [])

    # The case that motivated this check: a component exists only in the repo.
    missing_component = rendered[:4] + rendered[5:]
    check(
        "component absent from the collector is caught",
        any("line count differs" in p for p in compare(template, missing_component)),
        compare(template, missing_component),
    )

    # Same shape, different content.
    changed = list(rendered)
    changed[6] = "  forward_to = [prometheus.remote_write.other.receiver]"
    check("changed line is caught", any("line 7" in p for p in compare(template, changed)), compare(template, changed))

    # A placeholder that the installed file does not satisfy structurally.
    broken = list(rendered)
    broken[0] = 'url = "not a url at all"'
    check("placeholder mismatch is caught", any("line 1" in p for p in compare(template, broken)))

    # The template deployed without rendering: placeholders still in the live file.
    unrendered = list(template)
    check(
        "unrendered template in the collector is caught",
        any("unresolved placeholder" in p for p in compare(template, unrendered)),
        compare(template, unrendered),
    )

    # A diagnostic must not carry a live credential, even when the line moved.
    shifted = list(rendered)
    shifted[2] = '    password = "synthetic-sensitive-token"'
    diag = "\n".join(compare(template, shifted))
    check("diagnostic redacts live credential", "synthetic-sensitive-token" not in diag, diag)
    check("diagnostic still names the problem", "line 3" in diag, diag)

    # A hint inside a comment is documentation, not an unrendered value.
    documented = list(rendered)
    documented.insert(0, "// token: <PASTE_TOKEN_HERE>")
    check(
        "placeholder in a comment is not a defect",
        not any("unresolved placeholder" in p for p in compare(["// token: <PASTE_TOKEN_HERE>"] + template, documented)),
    )

    print(f"self-test: {'PASS' if failures == 0 else f'FAIL ({failures})'}")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", type=pathlib.Path, default=TEMPLATE)
    parser.add_argument("--live", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    template_path: pathlib.Path = args.template
    live_path = args.live if args.live is not None else DEFAULT_LIVE

    if not template_path.is_file():
        print(f"FAIL: template missing: {template_path}", file=sys.stderr)
        return 1
    if not live_path.is_file():
        print(f"SKIP Alloy projection: no collector config at {live_path} (expected on CI)")
        return 0

    problems = compare(lines_of(template_path), lines_of(live_path))
    if problems:
        print("FAIL: the running Alloy config is not a rendering of the versioned template:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nfix: render the template with this machine's values and deploy it, or bring the template "
            "in line with what actually runs. A component that exists only in git does not run.",
            file=sys.stderr,
        )
        return 1

    print(f"ok  Alloy config matches the template ({len(lines_of(template_path))} lines, placeholders resolved)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
