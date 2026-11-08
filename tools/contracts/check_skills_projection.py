#!/usr/bin/env python3
"""Projection check: the installed project skills must equal the repository source.

`scripts/sync-skills.sh` copies `.agents/skills/<name>/` to
`$HERMES_HOME/skills/local-lab/<name>/` and reads the copy back. That readback is
only as good as the moment it runs: a skill edited directly in the profile — or a
sync that was never re-run after the source changed — is invisible to the gate
until someone notices by hand. This check makes the divergence a failing gate.

What it asserts, per skill:

    missing in projection      source has a skill the profile does not
    extra in projection        profile has a skill the source does not
    missing in projection      a source file is absent from the copy
    extra in projection        a profile file with no source — this is what a
                               hand edit looks like from here
    differs                    same path, different bytes (CRLF normalised)

A machine without a Hermes profile (CI, a clean checkout) has nothing to compare:
that is SKIP, exit 0, said out loud — not a silent pass and not a failure.

Usage:
    python tools/contracts/check_skills_projection.py
    python tools/contracts/check_skills_projection.py --self-test
    python tools/contracts/check_skills_projection.py --source DIR --projection DIR
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / ".agents" / "skills"


def default_hermes_home() -> pathlib.Path | None:
    env = os.environ.get("HERMES_HOME")
    if env:
        return pathlib.Path(env.replace("\\", "/"))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return None
    return pathlib.Path(local_appdata.replace("\\", "/")) / "hermes"


def default_projection() -> pathlib.Path | None:
    home = default_hermes_home()
    return None if home is None else home / "skills" / "local-lab"


def normalise(data: bytes) -> bytes:
    """Compare content, not line endings: a Windows checkout yields CRLF."""
    return data.replace(b"\r\n", b"\n")


def skill_dirs(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Direct children that carry a SKILL.md — the unit sync-skills.sh copies."""
    found: dict[str, pathlib.Path] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            found[child.name] = child
    return found


def relative_files(root: pathlib.Path) -> dict[str, pathlib.Path]:
    files: dict[str, pathlib.Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path
    return files


def compare_skill(source: pathlib.Path, projection: pathlib.Path) -> list[str]:
    problems: list[str] = []
    src_files = relative_files(source)
    dst_files = relative_files(projection)

    for rel in sorted(set(src_files) - set(dst_files)):
        problems.append(f"missing in projection: {source.name}/{rel}")
    for rel in sorted(set(dst_files) - set(src_files)):
        problems.append(
            f"extra in projection: {projection.name}/{rel} — a file with no source; "
            f"this is what an edit made in the installed profile looks like"
        )
    for rel in sorted(set(src_files) & set(dst_files)):
        if normalise(src_files[rel].read_bytes()) != normalise(dst_files[rel].read_bytes()):
            problems.append(f"differs: {source.name}/{rel}")
    return problems


def compare(source_root: pathlib.Path, projection_root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    src = skill_dirs(source_root)
    dst = skill_dirs(projection_root)

    for name in sorted(set(src) - set(dst)):
        problems.append(f"missing in projection: {name}/ (whole skill)")
    for name in sorted(set(dst) - set(src)):
        problems.append(
            f"extra in projection: {name}/ — no source skill of that name; "
            f"sync would prune it, but until then the profile serves something the repo does not define"
        )
    for name in sorted(set(src) & set(dst)):
        problems.extend(compare_skill(src[name], dst[name]))
    return problems


def run_self_test() -> int:
    """Negative controls: a checker that cannot fail proves nothing."""
    failures = 0

    def make(root: pathlib.Path, files: dict[str, str]) -> None:
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def check(label: str, condition: bool, detail: object = "") -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'} {label}{'' if condition else f' — {detail}'}")
        failures += 0 if condition else 1

    base = {"alpha/SKILL.md": "---\nname: alpha\n---\nbody\n", "alpha/ref.md": "x\n"}

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "src").mkdir()
        (root / "dst").mkdir()

        # 1. identical trees, including a CRLF copy: must pass
        make(root / "src", base)
        make(root / "dst", base)
        (root / "dst" / "alpha" / "ref.md").write_bytes(b"x\r\n")
        check("identical tree passes (CRLF tolerated)", compare(root / "src", root / "dst") == [])

        # 2. changed content
        (root / "dst" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\nedited\n", encoding="utf-8")
        found = compare(root / "src", root / "dst")
        check("changed content is caught", any(p.startswith("differs: alpha/SKILL.md") for p in found), found)
        make(root / "dst", base)

        # 3. extra file in the projection — the hand-edit shape
        (root / "dst" / "alpha" / "scratch.md").write_text("mine\n", encoding="utf-8")
        found = compare(root / "src", root / "dst")
        check("extra projected file is caught", any("extra in projection" in p for p in found), found)
        (root / "dst" / "alpha" / "scratch.md").unlink()

        # 4. missing file in the projection
        (root / "dst" / "alpha" / "ref.md").unlink()
        found = compare(root / "src", root / "dst")
        check("missing projected file is caught", any("missing in projection: alpha/ref.md" in p for p in found), found)
        make(root / "dst", base)

        # 5. whole skills missing / extra
        (root / "src" / "beta").mkdir()
        make(root / "src", {"beta/SKILL.md": "---\nname: beta\n---\n"})
        found = compare(root / "src", root / "dst")
        check("skill absent from projection is caught", any("beta" in p for p in found), found)
        make(root / "dst", {"beta/SKILL.md": "---\nname: beta\n---\n", "gamma/SKILL.md": "---\nname: gamma\n---\n"})
        found = compare(root / "src", root / "dst")
        check("extra skill in projection is caught", any("gamma" in p for p in found), found)

        # 6. a directory without SKILL.md is not a skill (no false positive)
        (root / "dst" / "beta" / "assets").mkdir(parents=True, exist_ok=True)
        (root / "dst" / "beta" / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
        found = compare(root / "src", root / "dst")
        check("extra asset file is still caught", any("logo.svg" in p for p in found), found)

    print(f"self-test: {'PASS' if failures == 0 else f'FAIL ({failures})'}")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projection", type=pathlib.Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    source: pathlib.Path = args.source
    projection = args.projection if args.projection is not None else default_projection()

    if not source.is_dir():
        print(f"FAIL: source not found: {source}", file=sys.stderr)
        return 1
    if projection is None:
        print("SKIP: no Hermes home on this machine (HERMES_HOME / LOCALAPPDATA unset)")
        return 0
    if not projection.is_dir():
        print(f"SKIP: no projection at {projection} — nothing installed, nothing to compare")
        return 0

    problems = compare(source, projection)
    if problems:
        print("FAIL: installed project skills diverge from .agents/skills:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nfix: run `bash scripts/sync-skills.sh`; if the extra file was an edit, move the edit into the repo first.", file=sys.stderr)
        return 1

    skills = len(skill_dirs(source))
    print(f"ok  {skills} project skill(s): projection matches .agents/skills")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
