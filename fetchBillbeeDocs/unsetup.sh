#!/usr/bin/env bash
# unsetup.sh — remove everything created by setup.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/fetchBillbeeDocs"
PLIST="$SCRIPT_DIR/launchd/com.billbee.morning-fetch.plist"

echo "=== fetchBillbeeDocs Unsetup ==="
echo

if [ -d "$VENV" ]; then
    echo "Removing venv at $VENV ..."
    rm -rf "$VENV"
    echo "Done."
else
    echo "No venv found at $VENV — nothing to remove."
fi

if [ -f "$PLIST" ]; then
    echo "Removing generated plist: $PLIST ..."
    rm "$PLIST"
    echo "Done."
fi

echo
echo "=== Unsetup complete ==="
echo
echo "NOTE: If the LaunchAgent was installed, unload and remove it manually:"
echo "  launchctl unload ~/Library/LaunchAgents/com.billbee.morning-fetch.plist"
echo "  rm ~/Library/LaunchAgents/com.billbee.morning-fetch.plist"
echo
