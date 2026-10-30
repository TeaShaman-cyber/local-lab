"""Prepare a consistent staging copy of lab data for restic.

Why a staging step at all: a live SQLite file must not be copied while it is
being written, and the Hindsight bank lives inside a container. restic backs up
files, so the data has to be materialised into a consistent snapshot first.

Writes to <staging>/data and <staging>/config, then exits. The restic call is
separate so a failure here is distinguishable from a failure there.
"""
from __future__ import annotations

import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time

# A scheduled task has no console; without this every child process would flash one.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

STAGING = pathlib.Path(r"D:/theseus-backups/staging")
HERMES = pathlib.Path.home() / "AppData/Local/hermes"
CODEX = pathlib.Path.home() / ".codex"

DATA = STAGING / "data"
CONFIG = STAGING / "config"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sqlite_snapshot(src: pathlib.Path, dst: pathlib.Path) -> str:
    """Consistent copy of a live SQLite database via the backup API."""
    if not src.exists():
        return f"skip {src.name}: missing"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
        dst_conn.execute("PRAGMA journal_mode=DELETE")  # single self-contained file
        ok = dst_conn.execute("PRAGMA integrity_check").fetchone()[0]
        rows = dst_conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        dst_conn.close()
        src_conn.close()
    return f"ok {dst.name}: integrity={ok} tables={rows} size={dst.stat().st_size}"


def docker_bin() -> str:
    """Resolve docker even when the scheduler starts us with a minimal PATH."""
    found = shutil.which("docker")
    if found:
        return found
    fallback = pathlib.Path(r"C:/Program Files/Docker/Docker/resources/bin/docker.exe")
    return str(fallback) if fallback.exists() else "docker"


def hindsight_dump(dst: pathlib.Path) -> str:
    """pg_dump of the Hindsight bank, executed inside its container."""
    container = "hindsight-api"
    inner = "/tmp/theseus-backup.dump"
    script = (
        'D=/home/hindsight/.pg0/instances/hindsight; '
        'PG=/home/hindsight/.pg0/installation/18.1.0/bin; '
        'export PGPASSWORD=$(python3 -c "import json;print(json.load(open(\'$D/instance.json\'))[\'password\'])") ; '
        f'$PG/pg_dump -h 127.0.0.1 -p 5432 -U hindsight -d hindsight '
        f'-Fc --compress=6 -f {inner}'
    )
    r = subprocess.run(
        [docker_bin(), "exec", container, "sh", "-lc", script],
        capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW,
    )
    if r.returncode != 0:
        return f"FAIL pg_dump: {(r.stderr or r.stdout).strip()[:200]}"

    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(
        [docker_bin(), "cp", f"{container}:{inner}", str(dst)],
        capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW,
    )
    subprocess.run([docker_bin(), "exec", container, "rm", "-f", inner],
                   capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    if cp.returncode != 0:
        return f"FAIL docker cp: {cp.stderr.strip()[:200]}"
    return f"ok hindsight.dump: size={dst.stat().st_size}"


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG.mkdir(parents=True, exist_ok=True)

    log(hindsight_dump(DATA / "hindsight.dump"))

    log(sqlite_snapshot(HERMES / "state.db", DATA / "hermes-state.db"))
    log(sqlite_snapshot(CODEX / "thread_history_1.sqlite", DATA / "codex-thread-history.sqlite"))

    # Codex sessions are plain JSONL trees: cheap to archive as-is.
    sessions = STAGING / "codex-sessions"
    if sessions.exists():
        shutil.rmtree(sessions)
    sessions.mkdir(parents=True)
    for name in ("sessions", "archived_sessions"):
        src = CODEX / name
        if src.exists():
            shutil.copytree(src, sessions / name, dirs_exist_ok=True)
            n = sum(1 for _ in (sessions / name).rglob("*.jsonl"))
            log(f"ok codex {name}: {n} jsonl files")

    # Configuration: small, text, changes rarely.
    cfg = CONFIG / "hermes"
    cfg.mkdir(parents=True, exist_ok=True)
    for rel in ("config.yaml", "hindsight/config.json", "cron/jobs.json"):
        src = HERMES / rel
        if src.exists():
            dst = cfg / rel.replace("/", "__")
            shutil.copy2(src, dst)
            log(f"ok config {rel}")

    cfg_codex = CONFIG / "codex"
    cfg_codex.mkdir(parents=True, exist_ok=True)
    for rel in ("config.toml", "AGENTS.md"):
        src = CODEX / rel
        if src.exists():
            shutil.copy2(src, cfg_codex / rel)
            log(f"ok config codex {rel}")

    total = sum(f.stat().st_size for f in STAGING.rglob("*") if f.is_file())
    log(f"staging total: {total / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
