#!/usr/bin/env bash
# run.sh — fix addresses then create shipping labels
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/fixBillbeeAdresses"
cd "$SCRIPT_DIR"

if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

echo "=== Fix Addresses + Create Labels ==="
echo

# --- Step 1: Fix addresses ---
echo "[1/2] Fixing delivery addresses..."
echo

"$VENV"/bin/python main.py "$@"
ADDR_EXIT=$?

echo
if [ $ADDR_EXIT -ne 0 ]; then
    echo "Address fixer finished with errors (exit code $ADDR_EXIT)."
    echo "Skipping label creation."
    echo
    exit $ADDR_EXIT
fi
echo "Address fixing done."
echo

# --- Step 2: Create labels ---
echo "[2/2] Creating shipping labels..."
echo

"$VENV"/bin/python run_labels.py
LABEL_EXIT=$?

echo
if [ $LABEL_EXIT -ne 0 ]; then
    echo "Label creation finished with errors (exit code $LABEL_EXIT)."
else
    echo "All done."
fi
echo
