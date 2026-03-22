#!/usr/bin/env bash
# One-time setup for fetchBillbeeDocs on macOS.
# Run once before using the script or the LaunchAgent.
# Re-running this script is safe — it wipes and recreates the external venv.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/fetchBillbeeDocs"
cd "$SCRIPT_DIR"

echo "=== fetchBillbeeDocs — macOS Setup ==="
echo "Project root: $SCRIPT_DIR"
echo

# Check python3.14 is available
if ! command -v python3.14 &>/dev/null; then
    echo "ERROR: python3.14 not found on PATH."
    echo "Install via Homebrew: brew install python@3.14"
    exit 1
fi

# Wipe existing venv so we start fresh
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

# Generate the LaunchAgent plist from the template, substituting the real project path
echo "[5/5] Generating LaunchAgent plist from template..."
TEMPLATE="launchd/com.billbee.morning-fetch.plist.template"
PLIST="launchd/com.billbee.morning-fetch.plist"
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Template not found: $TEMPLATE"
    exit 1
fi
sed -e "s|{{SCRIPT_DIR}}|$SCRIPT_DIR|g" -e "s|{{VENV}}|$VENV|g" "$TEMPLATE" > "$PLIST"
echo "  Generated: $PLIST"

echo
echo "Setup complete!"
echo
echo "Test with:  $VENV/bin/python execution/fetch_morning_documents.py --dry-run"
echo
echo "To install the LaunchAgent (runs Mon-Fri at 06:30):"
echo "  1. cp $PLIST ~/Library/LaunchAgents/"
echo "  2. launchctl load ~/Library/LaunchAgents/com.billbee.morning-fetch.plist"
echo
