#!/usr/bin/env bash
# Import export-current-design.csv as a new date-named tab in Upstitch Design Sheet.
# The new tab is formatted exactly like the "Template" tab.
set -euo pipefail
cd "$(dirname "$0")"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found. Run setup.sh first."
    exit 1
fi
"$VENV/bin/python" import_designs.py "$@"
