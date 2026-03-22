#!/usr/bin/env bash
# Recreate the virtual environment for gmailAttachmentExtractor.
# Run this from any location — paths are resolved relative to this script.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/gmailAttachmentExtractor"

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
echo "Done. Activate with:"
echo "  source $VENV/bin/activate"
echo "Or run directly with:"
echo "  $VENV/bin/python execution/extract_attachments.py --help"
