#!/usr/bin/env bash
# Create shipping labels for orders in the after_fix state.
# Polls until Billbee automation has assigned Verpackungstypen, then
# calls the carrier API and saves label PDFs to the labels/ folder.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
cd "$SCRIPT_DIR"
"$VENV"/bin/python run_labels.py "$@"
