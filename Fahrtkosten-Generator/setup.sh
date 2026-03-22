#!/usr/bin/env bash
# One-time setup for Fahrtkosten-Generator.
# Re-running is safe — wipes and recreates the external venv.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/Fahrtkosten-Generator"
cd "$SCRIPT_DIR"

echo "=== Fahrtkosten-Generator — Setup ==="
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
    echo "[0/3] Removing existing virtual environment..."
    rm -rf "$VENV"
fi

# Create venv
mkdir -p "$(dirname "$VENV")"
echo "[1/3] Creating virtual environment with python3.14 at $VENV ..."
python3.14 -m venv "$VENV"

# Install dependencies
echo "[2/3] Installing dependencies..."
"$VENV"/bin/pip install -q -r requirements.txt

# Install shared Google client from sibling directory
echo "[3/3] Installing shared Google client..."
if [ ! -d "../google-client" ]; then
    echo "ERROR: ../google-client not found at $SCRIPT_DIR/../google-client"
    exit 1
fi
"$VENV"/bin/pip install -q -e ../google-client

echo
echo "Setup complete!"
echo "Run with: $VENV/bin/python generate_fahrtkosten_pdfs.py"
