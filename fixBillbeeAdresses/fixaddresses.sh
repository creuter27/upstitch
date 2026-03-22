#!/usr/bin/env bash
# fixaddresses.sh — fix delivery addresses in Billbee
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/fixBillbeeAdresses"
cd "$SCRIPT_DIR"

if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

echo "=== Fix Billbee Addresses ==="
echo

"$VENV"/bin/python main.py "$@"
EXIT_CODE=$?

echo
if [ $EXIT_CODE -ne 0 ]; then
    echo "Finished with errors (exit code $EXIT_CODE)."
else
    echo "Finished successfully."
fi
echo
