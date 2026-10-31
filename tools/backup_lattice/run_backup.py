"""One backup run for the Theseus lab: snapshot, push to every repository, verify.

Designed to be called by a scheduled task. It never asks anything, writes a
single machine-readable heartbeat line per run, and exits non-zero on any
failure so the scheduler records a failure instead of a silent no-op.

Repositories are declared, not discovered, because each holds a different tier:
  local  - everything, fastest restore
  s3     - everything, immutable (Object Lock), off-site
  drive  - survival kit only, because the free quota cannot hold the full repo

Retention is grandfather-father-son, applied per repository.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import time
import traceback

# A scheduled task has no console; without this every child process would flash one.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

STAGING = pathlib.Path(r"D:/theseus-backups/staging")
SURVIVAL = pathlib.Path(r"D:/theseus-backups/survival")
RESTORE_CHECK = pathlib.Path(r"D:/theseus-backups/restore-check")
HEARTBEAT = pathlib.Path.home() / "AppData/Local/hermes/logs/backup-lattice.log"
S3_ENV = pathlib.Path(r"D:/theseus-backups/.s3-env")

FULL_REPOS = {
    "local": "D:/theseus-backups/restic",
    "s3": "s3:https://s3.twcstorage.ru/theseus-lab-backups/restic",
}
SMALL_REPOS = {
    "drive": "rclone:gdrive:theseus-backups/restic",
}

KEEP = ["--keep-daily", "14", "--keep-weekly", "8", "--keep-monthly", "6"]

# The uplink is the bottleneck (~270 KiB/s measured). Without a cap the backup
# takes the whole channel for the duration and everything else on the machine —
# probes, model calls, interactive work — degrades. 2 MiB/s leaves headroom and
# costs nothing at night.
UPLOAD_LIMIT = ["--limit-upload", "2048"]


def env_with_s3() -> dict:
    env = dict(os.environ)
    if S3_ENV.exists():
        for line in S3_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def run(cmd: list[str], env: dict, timeout: int = 7200) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout,
                       creationflags=_NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def bin_path(name: str) -> str:
    """Resolve a binary even when the scheduler starts us with a minimal PATH."""
    found = shutil.which(name)
    if found:
        return found
    candidates = {
        "restic": [
            pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links/restic.exe",
            pathlib.Path(r"C:/ProgramData/chocolatey/bin/restic.exe"),
        ],
        "docker": [pathlib.Path(r"C:/Program Files/Docker/Docker/resources/bin/docker.exe")],
    }.get(name, [])
    for c in candidates:
        if c and c.exists():
            return str(c)
    return name


def restic(repo: str, args: list[str], env: dict, timeout: int = 7200) -> tuple[int, str]:
    return run([bin_path("restic"), "-r", repo] + args, env, timeout)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_repo(name: str, repo: str, source: pathlib.Path, env: dict) -> dict:
    started = time.time()
    cmd = ["backup", str(source), "--tag", "lab", "--host", "theseus-laptop"]
    if repo[:2].upper() not in ("C:", "D:"):   # network repositories only
        cmd += UPLOAD_LIMIT
    code, out = restic(repo, cmd, env)
    if code != 0:
        return {"repo": name, "ok": False, "stage": "backup", "detail": out[-300:]}

    code, out = restic(repo, ["forget", "--prune"] + KEEP, env)
    if code != 0:
        return {"repo": name, "ok": False, "stage": "forget", "detail": out[-300:]}

    code, out = restic(repo, ["snapshots", "--json"], env)
    count = out.count('"short_id"') if code == 0 else 0

    return {
        "repo": name,
        "ok": True,
        "stage": "done",
        "snapshots": count,
        "duration_s": round(time.time() - started, 1),
    }


def verify(env: dict) -> dict:
    """Restore the bank dump from the local repository and compare hashes."""
    source = STAGING / "data" / "hindsight.dump"
    if not source.exists():
        return {"ok": False, "detail": "no dump to verify"}

    if RESTORE_CHECK.exists():
        # `rm -rf` was the 31.10 failure: the scheduled task runs with a minimal PATH
        # where rm does not exist, so verify() raised FileNotFoundError and the run
        # died after a successful backup, before any repository result was written.
        shutil.rmtree(RESTORE_CHECK, ignore_errors=True)

    code, out = restic(
        FULL_REPOS["local"],
        ["restore", "latest", "--target", str(RESTORE_CHECK), "--include", "*hindsight.dump"],
        env,
    )
    if code != 0:
        return {"ok": False, "detail": out[-200:]}

    restored = next(RESTORE_CHECK.rglob("hindsight.dump"), None)
    if restored is None:
        return {"ok": False, "detail": "restore produced no file"}

    same = sha256(restored) == sha256(source)
    return {"ok": same, "detail": "sha256 match" if same else "sha256 MISMATCH"}


def _append(line: str) -> None:
    """Every line lands in the file first, stdout second.

    The scheduled task runs under pythonw, which has no stdout at all: anything
    printed only there is lost, and the log then looks like a run that started and
    did nothing. That is exactly how the 31.10 failure was read as a mystery.
    """
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        with HEARTBEAT.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


def log(msg: str) -> None:
    _append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}")


def heartbeat(parts: dict) -> None:
    """Machine-readable event line, with the time it happened.

    The timestamp is not decoration: the previous format made it impossible to tell
    which run a line belonged to, or whether a `BACKUP_RUN ok` was from today or from
    the day before.
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    _append(f"{stamp} " + " ".join(f"{k}={v}" for k, v in parts.items()))


def main() -> int:
    started = time.time()
    env = env_with_s3()
    env.setdefault("RESTIC_PASSWORD_FILE", r"D:/theseus-backups/.restic-password")

    # Emitted before any work: without it a run that hangs shows nothing at all,
    # because every other line is written only after its step completes.
    heartbeat({"event": "BACKUP_START", "pid": os.getpid()})

    try:
        return _run_body(env, started)
    except Exception as exc:  # noqa: BLE001 - the point is to log, not to classify
        # Under pythonw a traceback has nowhere to go: stderr does not exist. Without
        # this the run dies silently and leaves only BACKUP_START behind, which is
        # what made 31.10 look like a hang instead of a crash.
        _append(traceback.format_exc())
        heartbeat({"event": "BACKUP_CRASH", "stage": "unhandled",
                   "detail": f"{type(exc).__name__}_{str(exc)[:160]}".replace(" ", "_")})
        return 1


def _run_body(env: dict, started: float) -> int:
    log("preparing snapshot")
    code, out = run([sys.executable, str(pathlib.Path(__file__).with_name("prepare_snapshot.py"))], env, 3600)
    if code != 0:
        heartbeat({"event": "BACKUP_RUN", "status": "fail", "stage": "prepare",
                   "detail": out[-200:].replace(" ", "_")})
        return 1

    results = []
    for group, repos, source in (("full", FULL_REPOS, STAGING),
                                 ("survival", SMALL_REPOS, SURVIVAL)):
        for name, repo in repos.items():
            log(f"backup -> {name}")
            r = backup_repo(name, repo, source, env)
            results.append(r)
            # Written as soon as the repository finishes, not at the end: a later
            # crash used to erase the fact that three repositories had already
            # succeeded.
            if r["ok"]:
                heartbeat({"event": "BACKUP_RUN", "status": "ok", "repo": r["repo"],
                           "snapshots": r["snapshots"], "duration_s": r["duration_s"]})
            else:
                heartbeat({"event": "BACKUP_RUN", "status": "fail", "repo": r["repo"],
                           "stage": r["stage"], "detail": r["detail"].replace(" ", "_")[:200]})

    failed = [r for r in results if not r["ok"]]

    log("verifying by restore")
    v = verify(env)

    heartbeat({"event": "BACKUP_DRILL", "status": "ok" if v["ok"] else "fail",
               "detail": v["detail"].replace(" ", "_"),
               "total_s": round(time.time() - started, 1)})

    # A single line summarising the run, so the alert can read one value instead of
    # reconstructing state from a sequence of events.
    heartbeat({"event": "BACKUP_END", "ok": int(not failed and v["ok"]),
               "repos_ok": len([r for r in results if r["ok"]]), "repos_total": len(results),
               "total_s": round(time.time() - started, 1)})

    return 0 if (not failed and v["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
