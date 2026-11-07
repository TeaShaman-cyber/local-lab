# FROZEN — Hindsight legacy work is frozen (2026-11-07)

**Do not resume active development on Hindsight patches without explicit user decision.**

## Why frozen

- 2026-11-07 user decision: freeze Hindsight work, memory provider switched to **Honcho** (local, Docker on laptop, issue #177).
- Hindsight backend is heavy and the anchor-normalize patch (this dir) was implicated in the console-window issue #67 — `subprocess.run(["git", ...])` without `CREATE_NO_WINDOW` flashed a window each retain. The fix lives **only in the installed live plugin** (`%LOCALAPPDATA%/hermes/plugins/hindsight/__init__.py`); the canonical patch in this directory does **not** carry it and is not applyable as-is (corrupt hunk counts). See #67 and PR #181.

## What this directory is

Patches and tooling for the *Hindsight* Hermes memory plugin, used before the switch to Honcho:

- `apply_hindsight_patch.py` — apply recall-dates + dedupe (string replace, idempotent)
- `check_hindsight_patch.py` — verify 4 patches present + live in running process
- `hindsight-anchor-normalize.patch` — GitHub anchor normalization (needs CREATE_NO_WINDOW fix)
- `hindsight-anchor-fallback.patch`, `hindsight-recall-dates.patch`, `hindsight-reflect-budget.patch`, `hindsight-retain-noise-filter.patch`

## If someone wants to resume

1. Decision must be explicit (user says "resume Hindsight").
2. Before touching anything, run `python check_hindsight_patch.py --runtime` to see what state the installed plugin is in.
3. The anchor-normalize patch in this dir is **not a clean apply** — it was maintained in place; regenerate from diff against upstream `hindsight-integrations/hermes/__init__.py` before using, and keep the `_CREATE_NO_WINDOW` constant (see #67).
4. Do not silently switch memory.provider back from honcho to hindsight — that is a user decision.
