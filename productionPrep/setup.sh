#!/usr/bin/env bash
# One-time setup for productionPrep on macOS.
# Re-running is safe — wipes and recreates the external venv.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/productionPrep"
cd "$SCRIPT_DIR"

echo "=== productionPrep — macOS Setup ==="
echo "Project root: $SCRIPT_DIR"
echo

# Check python3.14 is available
if ! command -v python3.14 &>/dev/null; then
    echo "ERROR: python3.14 not found on PATH."
    echo "Install via Homebrew: brew install python@3.14"
    exit 1
fi

# Wipe existing venv
if [ -d "$VENV" ]; then
    echo "[0/5] Removing existing virtual environment..."
    rm -rf "$VENV"
fi

# Create venv
mkdir -p "$(dirname "$VENV")"
echo "[1/5] Creating virtual environment with python3.14 at $VENV ..."
python3.14 -m venv "$VENV"

# Install dependencies
echo "[2/5] Installing dependencies..."
"$VENV"/bin/pip install -q -r requirements.txt

# Install shared Billbee client from sibling directory
echo "[3/5] Installing shared Billbee client..."
if [ ! -d "../billbee-python-client" ]; then
    echo "ERROR: ../billbee-python-client not found."
    echo "Expected at: $SCRIPT_DIR/../billbee-python-client"
    exit 1
fi
"$VENV"/bin/pip install -q -e ../billbee-python-client

# Install shared Google client from sibling directory
echo "[4/5] Installing shared Google client..."
if [ ! -d "../google-client" ]; then
    echo "ERROR: ../google-client not found."
    echo "Expected at: $SCRIPT_DIR/../google-client"
    exit 1
fi
"$VENV"/bin/pip install -q -e ../google-client

# Copy .env if it doesn't exist yet
echo "[5/5] Checking .env..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  Created .env from .env.example — fill in your API keys."
else
    echo "  .env already exists."
fi

echo
echo "Setup complete!"
echo
echo "Test address fixer (dry run):  $VENV/bin/python main.py --dry-run"
echo "Test document fetch (dry run): $VENV/bin/python execution/fetch_documents.py --dry-run"
echo "Test labels (dry run):         $VENV/bin/python run_labels.py --dry-run"
echo
echo "Normal workflow:"
echo "  ./fixAddressesAndPackaging.sh   # fix addresses + set package types"
echo "  ./fetchDocuments.sh             # fetch invoices + delivery notes"
echo "  ./getLabels.sh                  # create shipping labels"
echo "  ./run.sh                        # all three in sequence"
echo
