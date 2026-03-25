#!/usr/bin/env bash
# One-time setup: applies conditional formatting rules to the "Template" tab
# in "Upstitch Design Sheet". Re-run whenever the rules change.
set -euo pipefail
cd "$(dirname "$0")"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: Virtual environment not found. Run setup.sh first."
    exit 1
fi
"$VENV/bin/python" setup_design_template.py "$@"
