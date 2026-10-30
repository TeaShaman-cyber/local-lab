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

# HERMES_HOME may arrive as a Windows path; normalise it for this shell.
if [ -n "${HERMES_HOME:-}" ] && command -v cygpath >/dev/null 2>&1; then
  HERMES_ROOT="$(cygpath -u "$HERMES_HOME")"
elif [ -n "${HERMES_HOME:-}" ]; then
  HERMES_ROOT="$HERMES_HOME"
else
  HERMES_ROOT="$HOME/AppData/Local/hermes"
fi

DEST="$HERMES_ROOT/skills/local-lab"

echo "source:    $SRC"
echo "installed: $DEST"
echo

mkdir -p "$DEST"

for dir in "$SRC"/*/; do
  name="$(basename "$dir")"
  rm -rf "$DEST/$name"
  mkdir -p "$DEST/$name"
  cp -R "$dir." "$DEST/$name/"
  echo "synced  $name"
done

echo
echo "--- readback (source vs installed) ---"
fail=0
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
