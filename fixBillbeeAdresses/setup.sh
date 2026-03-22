#!/usr/bin/env bash
# setup.sh — initialise the fixBillbeeAdresses environment on macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/fixBillbeeAdresses"
cd "$SCRIPT_DIR"

echo "=== fixBillbeeAdresses Setup ==="
echo

# ---------- locate Python ----------
PYTHON=python3.14
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found."
    echo "Install Python 3.14 from https://python.org or via Homebrew:"
    echo "  brew install python@3.14"
    exit 1
fi
"$PYTHON" --version
echo

# ---------- create venv ----------
if [ -d "$VENV" ]; then
    echo "Virtual environment already exists — skipping creation."
else
    mkdir -p "$(dirname "$VENV")"
    echo "Creating virtual environment at $VENV ..."
    "$PYTHON" -m venv "$VENV"
    echo "Done."
fi
echo

# ---------- install packages ----------
echo "Installing packages from requirements.txt..."
"$VENV"/bin/pip install --quiet -r requirements.txt
echo "Done."
echo

# ---------- install shared libraries ----------
install_lib() {
    local LIB="$1"
    local FOUND=""

    # Look for sibling folder (../billbee-python-client relative to this project)
    if [ -d "$SCRIPT_DIR/../$LIB" ]; then
        FOUND="$SCRIPT_DIR/../$LIB"
    fi

    if [ -z "$FOUND" ]; then
        echo "WARNING: $LIB not found at $SCRIPT_DIR/../$LIB"
        echo "  Make sure $LIB is in the same parent folder as this project."
        echo
        return
    fi

    echo "Installing shared library: $LIB"
    echo "  Path: $FOUND"
    "$VENV"/bin/pip install --quiet -e "$FOUND"
    echo "Done."
    echo
}

install_lib "billbee-python-client"
install_lib "google-client"

# ---------- check .env ----------
if [ ! -f .env ]; then
    echo "WARNING: .env file not found."
    echo "Create .env in this folder with:"
    echo "  OPENCAGE_API_KEY=your_key_here"
    echo "  ANTHROPIC_API_KEY=your_key_here"
    echo
    echo "Also make sure billbee-python-client/.env contains:"
    echo "  BILLBEE_API_KEY=..."
    echo "  BILLBEE_USERNAME=..."
    echo "  BILLBEE_PASSWORD=..."
    echo
else
    echo ".env found."
fi

echo
echo "=== Setup complete ==="
echo "You can now run:"
echo "  ./fixaddresses.sh  — fix delivery addresses"
echo "  ./getlabels.sh     — create shipping labels"
echo "  ./run.sh           — run both in sequence"
echo
