#!/bin/bash
# Run the lsfMonitor test suite without requiring an installed tree.
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
export LSFMONITOR_INSTALL_PATH="$ROOT"
export PYTHONUNBUFFERED=1

cd "$ROOT"

status=0
"${PYTHON:-python3}" -m unittest discover -s tests -v || status=1
"${PYTHON:-python3}" -m unittest discover -s jobMine/tests -v || status=1

exit "$status"
