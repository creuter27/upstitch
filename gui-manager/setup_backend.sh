#!/usr/bin/env bash
# setup_backend.sh — create Python venv and install backend dependencies
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTERNAL="$HOME/.local/share/upstitch-venvs/gui-manager"
VENV="$EXTERNAL/venv"

echo "=== gui-manager Backend Setup ==="
echo "Project: $SCRIPT_DIR"
echo

mkdir -p "$EXTERNAL"

[ -d "$VENV" ] && rm -rf "$VENV"

echo "Creating venv with python3.14..."
python3.14 -m venv "$VENV"

echo "Installing backend dependencies..."
"$VENV"/bin/pip install -q -r "$SCRIPT_DIR/backend/requirements.txt"

echo
echo "=== Backend setup complete! ==="
echo
