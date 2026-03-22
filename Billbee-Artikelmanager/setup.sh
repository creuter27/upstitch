#!/usr/bin/env bash
# setup.sh — create/recreate venv at ~/.local/share/upstitch-venvs/ and install all dependencies
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/Billbee-Artikelmanager"

echo "=== Billbee-Artikelmanager setup ==="
echo "Project: $PROJECT_DIR"

# Remove existing venv
if [ -d "$VENV" ]; then
    echo "Removing existing venv ..."
    rm -rf "$VENV"
fi

# Create fresh venv
mkdir -p "$(dirname "$VENV")"
echo "Creating venv with python3.14 at $VENV ..."
python3.14 -m venv "$VENV"

PIP="$VENV/bin/pip"

# Upgrade pip
"$PIP" install --upgrade pip --quiet

# Install shared libraries as editable packages
echo "Installing shared libraries ..."
"$PIP" install -e "$PROJECT_DIR/../billbee-python-client" --quiet
"$PIP" install -e "$PROJECT_DIR/../google-client" --quiet

# Install project requirements
echo "Installing requirements.txt ..."
"$PIP" install -r "$PROJECT_DIR/requirements.txt" --quiet

# Install Playwright browsers
echo "Installing Playwright browsers ..."
"$VENV/bin/playwright" install chromium

echo ""
echo "Done! Activate with:  source $VENV/bin/activate"
echo "Or run scripts with:  $VENV/bin/python execution/..."
