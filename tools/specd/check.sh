#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

run=tools/specd/run.sh

echo "=== SpecD version ==="
"$run" --version

echo "=== SpecD project status ==="
"$run" project status --format json

echo "=== SpecD schema ==="
"$run" schema validate --format json

echo "=== context-driver change ==="
"$run" changes validate context-driver-foundation --all --format json
