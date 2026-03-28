#!/usr/bin/env bash
# Full production preparation workflow — runs all four steps in sequence:
#
#   0. importDesigns             — import design CSV as new dated tab in Design Sheet
#   1. fixAddressesAndPackaging  — fix addresses, set package types + order state
#   2. fetchDocuments            — fetch invoices + delivery notes
#                                  (runs while Billbee automation assigns shipping profiles)
#   3. getLabels                 — poll for Verpackungstyp tags, create shipping labels
#
# Pass --dry-run to any step by adding it as an argument: ./run.sh --dry-run
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
cd "$SCRIPT_DIR"

echo "======================================================"
echo " productionPrep — Full Workflow"
echo "======================================================"
echo

echo "────────────────────────────────"
echo " Step 0: Import Designs CSV"
echo "────────────────────────────────"
"$VENV"/bin/python import_designs.py

echo
echo "────────────────────────────────"
echo " Step 1: Fix Addresses & Packaging"
echo "────────────────────────────────"
"$VENV"/bin/python main.py "$@"

echo
echo "────────────────────────────────"
echo " Step 2: Fetch Documents"
echo "────────────────────────────────"
"$VENV"/bin/python execution/fetch_documents.py "$@"

echo
echo "────────────────────────────────"
echo " Step 3: Create Labels"
echo "────────────────────────────────"
"$VENV"/bin/python run_labels.py "$@"

echo
echo "======================================================"
echo " All steps complete."
echo "======================================================"
