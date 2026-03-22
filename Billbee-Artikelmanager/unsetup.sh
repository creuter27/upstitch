#!/usr/bin/env bash
# unsetup.sh — remove everything created by setup.sh
set -e

VENV="$HOME/.local/share/upstitch-venvs/Billbee-Artikelmanager"

echo "=== Billbee-Artikelmanager Unsetup ==="
echo

if [ -d "$VENV" ]; then
    echo "Removing venv at $VENV ..."
    rm -rf "$VENV"
    echo "Done."
else
    echo "No venv found at $VENV — nothing to remove."
fi

echo
echo "=== Unsetup complete ==="
echo
