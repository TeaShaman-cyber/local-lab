#!/usr/bin/env bash
# Install project skills from .agents/skills into the Hermes profile skills dir.
#
# The repository is the source of truth. Hermes only reads $HERMES_HOME/skills,
# so this script projects the repo copy there and then proves the copy matches.
#
# Usage: scripts/sync-skills.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/.agents/skills"

if [ ! -d "$SRC" ]; then
  echo "no skills directory at $SRC" >&2
  exit 1
fi

# Guard before any pruning: an empty or wrong source would make the prune step
# delete every installed skill. "Source exists" is not the same as "source has
# content", and the difference here is the whole projection.
#
# Count with the same rule the copy loop uses (`*/` plus -d), not `find -type d`:
# find does not follow symlinks, so a source of symlinked skills counted as zero
# and refused to sync while the copy loop would have handled it. Both the
# independent reviewer and Codex flagged this asymmetry separately.
skill_count=0
for dir in "$SRC"/*/; do
  # if, not `[ -d ] && count++`: under set -e the AND-OR form makes the loop's
  # exit status depend on whether the last entry was a directory.
  if [ -d "$dir" ]; then
    skill_count=$((skill_count + 1))
  fi
done
if [ "$skill_count" -eq 0 ]; then
  echo "refusing to sync: $SRC contains no skill directories; pruning would empty the projection" >&2
  exit 1
fi

# HERMES_HOME may arrive as a Windows path; normalise it for this shell.
if [ -n "${HERMES_HOME:-}" ] && command -v cygpath >/dev/null 2>&1; then
  HERMES_ROOT="$(cygpath -u "$HERMES_HOME")"
elif [ -n "${HERMES_HOME:-}" ]; then
  HERMES_ROOT="$HERMES_HOME"
else
  HERMES_ROOT="$HOME/AppData/Local/hermes"
fi

DEST="$HERMES_ROOT/skills/local-lab"

# ShellCheck SC2115: with an empty HERMES_ROOT, `rm -rf "$DEST/$name"` would
# expand to `rm -rf "/$name"`. Neither the author, nor the independent reviewer,
# nor Codex caught this — the linter did. That is the argument for linters over
# careful reading.
if [ -z "$HERMES_ROOT" ]; then
  echo "refusing to sync: HERMES_HOME resolved to an empty path" >&2
  exit 1
fi

echo "source:    $SRC"
echo "installed: $DEST"
echo

mkdir -p "$DEST"

for dir in "$SRC"/*/; do
  name="$(basename "$dir")"
  rm -rf "${DEST:?}/$name"
  mkdir -p "$DEST/$name"
  cp -R "$dir." "$DEST/$name/"
  echo "synced  $name"
done

echo
# Remove skills that exist only in the destination. The loop above walks SRC, so
# a skill deleted from the source was never touched, and the readback stayed
# green while an obsolete skill sat in the projection. Found 2026-10-31: a stale
# fourth skill survived every sync.
#
# The list of extras is captured BEFORE pruning. Checking the set only after
# prune would be theatre — prune has already removed those entries, so the
# assertion could never fire. Independent review caught exactly that.
echo "--- prune (installed, but no longer in source) ---"
fail=0
extras="$(mktemp)"
trap 'rm -f "$extras"' EXIT

for dir in "$DEST"/*/; do
  [ -d "$dir" ] || continue
  name="$(basename "$dir")"
  if [ ! -d "$SRC/$name" ]; then
    printf '%s\n' "$name" >> "$extras"
  fi
done

pruned=0
while IFS= read -r name; do
  [ -z "$name" ] && continue
  rm -rf "${DEST:?}/$name"
  echo "REMOVED $name"
  pruned=1
done < "$extras"

# Not `[ "$pruned" -eq 0 ] && echo "none"`: under `set -e` that form makes the
# script's exit status depend on whether anything was pruned.
if [ "$pruned" -eq 0 ]; then
  echo "none"
fi

# The real assertion about prune: what it claimed to remove is actually gone.
while IFS= read -r name; do
  [ -z "$name" ] && continue
  if [ -d "$DEST/$name" ]; then
    echo "EXTRA   $name (prune did not remove it)"
    fail=1
  fi
done < "$extras"

echo
echo "--- readback (content) ---"
for dir in "$SRC"/*/; do
  name="$(basename "$dir")"
  if diff -r "$dir" "$DEST/$name" >/dev/null 2>&1; then
    echo "OK      $name"
  else
    echo "DIFF    $name"
    fail=1
  fi
done

exit "$fail"
