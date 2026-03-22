#!/usr/bin/env bash
# Fix Billbee delivery addresses and set package type tags.
# Applies address fixes, package type tags, and state transitions per-order.
# Run fetchDocuments.sh next while Billbee automation assigns shipping profiles.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
cd "$SCRIPT_DIR"
"$VENV"/bin/python main.py "$@"
