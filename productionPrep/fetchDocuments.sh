#!/usr/bin/env bash
# Fetch invoices and delivery notes from Billbee or Google Drive.
# Run this after fixAddressesAndPackaging.sh while Billbee automation
# assigns shipping profiles (gives automation time before getLabels.sh polls).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
cd "$SCRIPT_DIR"
"$VENV"/bin/python execution/fetch_documents.py "$@"
