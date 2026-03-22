#!/usr/bin/env bash
# setup_frontend.sh — install npm dependencies directly outside Tresorit
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTERNAL="$HOME/.local/share/upstitch-venvs/gui-manager"
MODULES="$EXTERNAL/node_modules"

echo "=== gui-manager Frontend Setup ==="
echo "Project: $SCRIPT_DIR"
echo

mkdir -p "$EXTERNAL"

# Copy package files to the external dir and npm install there.
# We NEVER run npm install inside the Tresorit folder — Tresorit would
# see the freshly created node_modules and sync thousands of files to Windows.
cp "$SCRIPT_DIR/frontend/package.json" "$EXTERNAL/"
cp "$SCRIPT_DIR/frontend/package-lock.json" "$EXTERNAL/" 2>/dev/null || true

echo "Installing npm dependencies into $EXTERNAL ..."
rm -rf "$MODULES"
cd "$EXTERNAL"
npm install

# Write back the lock file so it stays in sync
cp "$EXTERNAL/package-lock.json" "$SCRIPT_DIR/frontend/" 2>/dev/null || true
rm -f "$EXTERNAL/package.json" "$EXTERNAL/package-lock.json"

echo
echo "node_modules installed to: $MODULES"
echo
echo "=== Frontend setup complete! ==="
echo
