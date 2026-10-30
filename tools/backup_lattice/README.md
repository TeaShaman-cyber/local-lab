# Backup Lattice

Portable backup tooling for the local Theseus lab.

Principles:

- GitHub stores safe configuration and source history.
- Local archives store recoverable data snapshots.
- A local cloud-sync folder, such as Google Drive for desktop, can mirror the
  archives without adding another paid service or cloud API.
- Secrets are excluded. Raw `.env`, `.env.local`, tokens, keys, and certificates
  must never be packed.
- Worker agents should be able to inspect manifests without seeing secrets.

## One-Shot Backup

Run from the Docker-backed agent shell:

```powershell
.\scripts\agent-shell.ps1 -Command "python3 tools/backup_lattice/backup_lattice.py once"
```

This writes an archive under `backups/data/`.

To mirror into a local Google Drive sync folder, set `BACKUP_MIRROR_DIR` in
`.env.local` or pass `--mirror-dir`.

## Background Service

```powershell
cd backup-lattice
docker compose up -d --build backup-runner
docker compose logs --tail=80 backup-runner
docker compose down
```

Default interval: 6 hours.

The background service writes local archives even when no mirror is configured.
If the mirror path is unavailable, the archive remains local and the manifest
records the failure.

## Restore Sketch

1. Restore the repository from GitHub.
2. Copy the latest `theseus-data-*.tar.gz` from local backup or cloud mirror.
3. Inspect `BACKUP_MANIFEST.md` inside the archive.
4. Restore `observatory/state.db` into `stateful-agent-net/data/state.db`.
5. Restore AutoMem using the `automem/automem-api-backup.tar.gz` payload and
   AutoMem's restore tooling.
