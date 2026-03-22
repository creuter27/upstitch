#!/usr/bin/env bash
# unsetup.sh — remove everything created by setup.sh
# NOTE: .env is NOT removed — it may contain your API keys.
set -e

VENV="$HOME/.local/share/upstitch-venvs/productionPrep"

echo "=== productionPrep Unsetup ==="
echo

if [ -d "$VENV" ]; then
    echo "Removing venv at $VENV ..."
    rm -rf "$VENV"
    echo "Done."
else
    echo "No venv found at $VENV — nothing to remove."
fi

echo
echo "NOTE: .env was not removed. Delete it manually if needed."
echo
echo "=== Unsetup complete ==="
echo
