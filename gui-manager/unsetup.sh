#!/usr/bin/env bash
# unsetup.sh — remove everything created by setup.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTERNAL="$HOME/.local/share/upstitch-venvs/gui-manager"
NM_LINK="$SCRIPT_DIR/frontend/node_modules"

echo "=== gui-manager Unsetup ==="
echo "Project: $SCRIPT_DIR"
echo

# Remove node_modules symlink inside Tresorit
if [ -L "$NM_LINK" ]; then
    echo "Removing node_modules symlink at $NM_LINK ..."
    rm "$NM_LINK"
    echo "Done."
elif [ -d "$NM_LINK" ]; then
    echo "WARNING: $NM_LINK is a real directory, not a symlink — skipping."
fi

# Remove the external directory (contains venv/ and node_modules/)
if [ -d "$EXTERNAL" ]; then
    echo "Removing external data at $EXTERNAL ..."
    rm -rf "$EXTERNAL"
    echo "Done."
else
    echo "No external data found at $EXTERNAL — nothing to remove."
fi

echo
echo "=== Unsetup complete ==="
echo
