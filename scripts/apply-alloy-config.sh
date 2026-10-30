#!/usr/bin/env bash
# Render the Alloy config template into the service path.
#
# The repository holds only the template with placeholders. Real values live in
# ALLOY_ENV_FILE (default D:/theseus-monitoring/alloy.env), outside git.
#
# Usage:
#   scripts/apply-alloy-config.sh            # dry-run: render + diff, no writes
#   scripts/apply-alloy-config.sh --apply    # backup, write, restart service
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/infra/monitoring/alloy/config.alloy"
BLACKBOX="$REPO_ROOT/infra/monitoring/alloy/blackbox.yml"

# HERMES_HOME-style normalisation: accept Windows paths from the environment.
winpath() {
  if command -v cygpath >/dev/null 2>&1 && [[ "$1" =~ ^[A-Za-z]: ]]; then
    cygpath -u "$1"
  else
    printf '%s' "${1//\\//}"
  fi
}

ENV_FILE="${ALLOY_ENV_FILE:-D:/theseus-monitoring/alloy.env}"
ENV_FILE_U="$(winpath "$ENV_FILE")"
TARGET_DIR="${ALLOY_DIR:-C:/Program Files/GrafanaLabs/Alloy}"
TARGET_DIR_U="$(winpath "$TARGET_DIR")"
TARGET="$TARGET_DIR_U/config.alloy"

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

[[ -f "$TEMPLATE" ]] || { echo "template not found: $TEMPLATE" >&2; exit 1; }
[[ -f "$ENV_FILE_U" ]] || { echo "env file not found: $ENV_FILE (set ALLOY_ENV_FILE)" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE_U"
set +a

for var in GRAFANA_API_TOKEN GRAFANA_INSTANCE_ID PROMETHEUS_USER_ID LOKI_HOST PROMETHEUS_HOST; do
  [[ -n "${!var:-}" ]] || { echo "missing $var in $ENV_FILE" >&2; exit 1; }
done

RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT

sed -e "s|<PASTE_TOKEN_HERE>|${GRAFANA_API_TOKEN}|g" \
    -e "s|<GRAFANA_INSTANCE_ID>|${GRAFANA_INSTANCE_ID}|g" \
    -e "s|<PROMETHEUS_USER_ID>|${PROMETHEUS_USER_ID}|g" \
    -e "s|<LOKI_HOST>|${LOKI_HOST}|g" \
    -e "s|<PROMETHEUS_HOST>|${PROMETHEUS_HOST}|g" \
    "$TEMPLATE" > "$RENDERED"

if grep -q "<PASTE_TOKEN_HERE>\|<GRAFANA_INSTANCE_ID>\|<PROMETHEUS_USER_ID>\|<LOKI_HOST>\|<PROMETHEUS_HOST>" "$RENDERED"; then
  echo "FAIL: placeholders remain after rendering" >&2
  exit 1
fi

echo "template: $TEMPLATE"
echo "env:      $ENV_FILE"
echo "target:   $TARGET"
echo

if [[ -f "$TARGET" ]] && diff -q "$RENDERED" "$TARGET" >/dev/null 2>&1; then
  echo "no change: target already matches the rendered template"
  exit 0
fi

echo "--- diff (target vs rendered), values redacted ---"
if [[ -f "$TARGET" ]]; then
  diff "$TARGET" "$RENDERED" | sed -E 's/(glc_|ghp_|github_pat_)[A-Za-z0-9_=.-]+/\1[REDACTED]/g' | head -40
else
  echo "target does not exist yet"
fi
echo

if [[ "$APPLY" -eq 0 ]]; then
  echo "dry-run: nothing written. Re-run with --apply to write and restart."
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
if [[ -f "$TARGET" ]]; then
  cp -a "$TARGET" "$TARGET.bak-fromlocal-$STAMP"
  echo "backup:  $TARGET.bak-fromlocal-$STAMP"
fi

cp "$RENDERED" "$TARGET"
echo "written: $TARGET"

if [[ -f "$BLACKBOX" ]]; then
  cp "$BLACKBOX" "$TARGET_DIR_U/blackbox.yml"
  echo "written: $TARGET_DIR_U/blackbox.yml"
fi

echo "restarting Alloy service..."
sc stop Alloy >/dev/null 2>&1 || true
sleep 2
sc start Alloy >/dev/null 2>&1 || true
sleep 3
sc query Alloy 2>&1 | tr -d '\r' | grep -i "STATE" || true
