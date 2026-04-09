#!/usr/bin/env bash
# Setup virtual environment for emailBot.
# Run this from any location — paths are resolved relative to this script.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/emailBot"

echo "Project root: $SCRIPT_DIR"

if [ -d "$VENV" ]; then
    echo "Removing existing venv..."
    rm -rf "$VENV"
fi

mkdir -p "$(dirname "$VENV")"
echo "Creating venv with python3.14 at $VENV ..."
python3.14 -m venv "$VENV"

echo "Installing dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "Done. Run the monitor with:"
echo "  $VENV/bin/python $SCRIPT_DIR/execution/monitor.py --help"
echo ""
echo "To run every 5 minutes as a daemon:"
echo "  $VENV/bin/python $SCRIPT_DIR/execution/monitor.py --daemon 5"
