#!/bin/bash
# Production mode: build frontend and serve everything from FastAPI
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTERNAL="$HOME/.local/share/upstitch-venvs/gui-manager"
VENV="$EXTERNAL/venv"
MODULES="$EXTERNAL/node_modules"
NM_LINK="$SCRIPT_DIR/frontend/node_modules"

if [ ! -x "$VENV/bin/uvicorn" ]; then
    echo "ERROR: Backend not set up. Run setup.sh first."
    exit 1
fi

# Remove any re-synced real directory (Tresorit may sync through the symlink)
if [ -e "$NM_LINK" ] && [ ! -L "$NM_LINK" ]; then
    echo "Removing re-synced node_modules from Tresorit folder..."
    rm -rf "$NM_LINK"
fi

# Install if vite is missing from external location
if [ ! -f "$MODULES/.bin/vite" ]; then
    echo "Installing frontend dependencies..."
    rm -f "$NM_LINK"
    cd "$SCRIPT_DIR/frontend"
    npm install
    rm -rf "$MODULES"
    mv "$SCRIPT_DIR/frontend/node_modules" "$MODULES"
    echo "node_modules moved to: $MODULES"
fi

# Ensure symlink exists so ESM loader can resolve node_modules
if [ ! -L "$NM_LINK" ]; then
    ln -s "$MODULES" "$NM_LINK"
fi

echo "Building frontend..."
cd "$SCRIPT_DIR/frontend"
npm run build

echo "Starting backend (serving frontend from /dist)..."
cd "$SCRIPT_DIR/backend"
"$VENV"/bin/uvicorn main:app --host 127.0.0.1 --port 8000
