#!/usr/bin/env bash
# getlabels.sh — create shipping labels for recent orders
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/fixBillbeeAdresses"
cd "$SCRIPT_DIR"

if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

echo "=== Create Shipping Labels ==="
echo

"$VENV"/bin/python run_labels.py "$@"
EXIT_CODE=$?

echo
if [ $EXIT_CODE -ne 0 ]; then
    echo "Finished with errors (exit code $EXIT_CODE)."
else
    echo "Finished successfully."
fi
echo
