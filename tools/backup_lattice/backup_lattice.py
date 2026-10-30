#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AUTOMEM_API_URL = "http://127.0.0.1:8001"
DEFAULT_OUTPUT_DIR = Path("backups/data")
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60
LOCAL_HOSTS = {"127.0.0.1", "localhost", "host.docker.internal"}

SECRET_FILENAMES = {".env", ".env.local"}
SECRET_PATTERNS = ["*.key", "*.pem", "*.pfx", "*.p12", "*.crt", "*.cer"]
SAFE_CONFIG_FILES = [
    ".env.example",
    ".gitignore",
    "RESTORE.md",
    "codewhale/README.md",
    "stateful-agent-net/README.md",
    "tools/memory_context/README.md",
    "tools/backup_lattice/README.md",
]


@dataclass
class BackupStatus:
    name: str
    status: str
    detail: str = ""
    files: list[str] = field(default_factory=list)


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    name, value = stripped.split("=", 1)
    return name.strip(), value.strip().strip('"').strip("'")


def load_env_files(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in (".env", ".env.local"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_env_line(line)
            if parsed:
                key, value = parsed
                values[key] = value
    values.update({key: value for key, value in os.environ.items()})
    return values


def is_local_api(api_url: str) -> bool:
    parsed = urllib.parse.urlparse(api_url)
    return (parsed.hostname or "") in LOCAL_HOSTS


def resolve_admin_token(api_url: str, env: dict[str, str]) -> str | None:
    token = env.get("AUTOMEM_ADMIN_TOKEN") or env.get("ADMIN_API_TOKEN")
    if token:
        return token
    if is_local_api(api_url):
        return "test-admin-token"
    return None


def safe_relative(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    return resolved.relative_to(root.resolve())


def should_exclude_file(path: Path) -> bool:
    name = path.name
    if name in SECRET_FILENAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def request_binary(url: str, headers: dict[str, str], timeout: float = 120.0) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def add_safe_config(root: Path, staging: Path) -> BackupStatus:
    status = BackupStatus(name="safe-config", status="included")
    for relative in SAFE_CONFIG_FILES:
        source = root / relative
        if not source.exists() or should_exclude_file(source):
            continue
        target = staging / "safe-config" / relative
        copy_file(source, target)
        status.files.append(str(target.relative_to(staging)))
    if not status.files:
        status.status = "skipped"
        status.detail = "No safe config files found."
    return status


def backup_automem(root: Path, staging: Path, api_url: str, admin_token: str | None) -> BackupStatus:
    status = BackupStatus(name="automem-api", status="skipped")
    if not admin_token:
        status.detail = "No AUTOMEM_ADMIN_TOKEN or local default admin token available."
        return status
    target = staging / "automem" / "automem-api-backup.tar.gz"
    try:
        data = request_binary(
            f"{api_url.rstrip('/')}/backup",
            headers={"X-Admin-Token": admin_token},
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        status.status = "included"
        status.detail = "AutoMem API backup included."
        status.files.append(str(target.relative_to(staging)))
    except urllib.error.HTTPError as exc:
        status.status = "failed"
        status.detail = f"HTTP {exc.code} from AutoMem backup endpoint."
    except (OSError, urllib.error.URLError) as exc:
        status.status = "failed"
        status.detail = f"{type(exc).__name__}: {exc}"
    return status


def backup_sqlite(source: Path, target: Path) -> BackupStatus:
    status = BackupStatus(name="observatory-sqlite", status="skipped")
    if not source.exists():
        status.detail = f"{source} not found."
        return status
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        status.status = "included"
        status.detail = "SQLite online backup completed."
        status.files.append(str(target))
    except sqlite3.Error as exc:
        status.status = "failed"
        status.detail = f"sqlite3 error: {exc}"
    return status


def add_observatory(root: Path, staging: Path) -> BackupStatus:
    db_source = root / "stateful-agent-net" / "data" / "state.db"
    db_target = staging / "observatory" / "state.db"
    db_status = backup_sqlite(db_source, db_target)
    status = BackupStatus(
        name="observatory",
        status=db_status.status,
        detail=db_status.detail,
        files=[],
    )
    if db_status.status == "included":
        status.files.append("observatory/state.db")

    packets_dir = root / "outputs" / "state_packets"
    if packets_dir.exists():
        count = 0
        for path in sorted(packets_dir.glob("*.md")):
            if should_exclude_file(path):
                continue
            target = staging / "observatory" / "state_packets" / path.name
            copy_file(path, target)
            status.files.append(str(target.relative_to(staging)))
            count += 1
        if count:
            status.status = "included" if status.status != "failed" else "partial"
            detail = f"{status.detail} State packets included: {count}."
            status.detail = detail.strip()
    return status


def git_commit(root: Path) -> str:
    head = root / ".git" / "HEAD"
    if not head.exists():
        return "unknown"
    text = head.read_text(encoding="utf-8", errors="replace").strip()
    if text.startswith("ref: "):
        ref = root / ".git" / text[5:]
        if ref.exists():
            return ref.read_text(encoding="utf-8", errors="replace").strip()
    return text


def write_manifest(
    *,
    staging: Path,
    root: Path,
    archive_name: str,
    api_url: str,
    statuses: list[BackupStatus],
    mirror_dir: str | None,
) -> None:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "created_at": created,
        "archive": archive_name,
        "project_root": str(root),
        "git_commit": git_commit(root),
        "automem_api_url": api_url,
        "mirror_dir_configured": bool(mirror_dir),
        "statuses": [status.__dict__ for status in statuses],
        "exclusions": {
            "filenames": sorted(SECRET_FILENAMES),
            "patterns": SECRET_PATTERNS,
            "note": "Raw env files, API keys, tokens, and certificates are excluded.",
        },
    }
    write_text(staging / "BACKUP_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    lines = [
        "# Theseus Data Backup Manifest",
        "",
        f"- Created: `{created}`",
        f"- Archive: `{archive_name}`",
        f"- Git commit: `{manifest['git_commit']}`",
        f"- AutoMem API: `{api_url}`",
        f"- Mirror configured: `{bool(mirror_dir)}`",
        "",
        "## Status",
        "",
    ]
    for status in statuses:
        lines.append(f"- `{status.name}`: {status.status} - {status.detail}")
    lines.extend(
        [
            "",
            "## Exclusions",
            "",
            "- `.env`, `.env.local`, API tokens, private keys, and certificates.",
            "- Repository internals and generated caches.",
            "",
        ]
    )
    write_text(staging / "BACKUP_MANIFEST.md", "\n".join(lines))


def create_archive(staging: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(staging))


def mirror_archive(archive_path: Path, mirror_dir: str | None) -> BackupStatus:
    status = BackupStatus(name="mirror", status="skipped")
    if not mirror_dir:
        status.detail = "No mirror directory configured."
        return status
    target_dir = Path(mirror_dir).expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / archive_path.name
        shutil.copy2(archive_path, target)
        status.status = "included"
        status.detail = f"Archive mirrored to {target_dir}."
        status.files.append(str(target))
    except OSError as exc:
        status.status = "failed"
        status.detail = f"{type(exc).__name__}: {exc}"
    return status


def run_once(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    env = load_env_files(root)
    api_url = args.automem_api_url or env.get("AUTOMEM_API_URL") or DEFAULT_AUTOMEM_API_URL
    admin_token = args.automem_admin_token or resolve_admin_token(api_url, env)
    mirror_dir = args.mirror_dir or env.get("BACKUP_MIRROR_DIR") or None
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive_name = f"theseus-data-{timestamp}.tar.gz"
    archive_path = output_dir / archive_name

    with tempfile.TemporaryDirectory(prefix="theseus-backup-") as tmp:
        staging = Path(tmp) / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        statuses = [
            add_safe_config(root, staging),
            backup_automem(root, staging, api_url, admin_token),
            add_observatory(root, staging),
        ]
        write_manifest(
            staging=staging,
            root=root,
            archive_name=archive_name,
            api_url=api_url,
            statuses=statuses,
            mirror_dir=mirror_dir,
        )
        create_archive(staging, archive_path)

    mirror_status = mirror_archive(archive_path, mirror_dir)
    print(f"archive {archive_path}", flush=True)
    print(f"mirror {mirror_status.status}", flush=True)
    if mirror_status.detail:
        print(mirror_status.detail, flush=True)
    return 0


def run_loop(args: argparse.Namespace) -> int:
    interval = args.interval_seconds
    print(f"backup_lattice loop started: interval={interval}s", flush=True)
    while True:
        started = time.time()
        try:
            run_once(args)
        except Exception as exc:  # noqa: BLE001 - keep background loop alive
            print(f"backup_lattice error: {type(exc).__name__}: {exc}", flush=True)
        elapsed = int(time.time() - started)
        time.sleep(max(60, interval - elapsed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create data backups for the local Theseus lab.")
    parser.add_argument("command", choices=["once", "loop"])
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--mirror-dir", default=None)
    parser.add_argument("--automem-api-url", default=None)
    parser.add_argument("--automem-admin-token", default=None)
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.environ.get("BACKUP_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "once":
        return run_once(args)
    if args.command == "loop":
        return run_loop(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
