#!/bin/bash
# Start gui-manager (backend + frontend dev server)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTERNAL="$HOME/.local/share/upstitch-venvs/gui-manager"
VENV="$EXTERNAL/venv"
MODULES="$EXTERNAL/node_modules"

if [ ! -x "$VENV/bin/uvicorn" ]; then
    echo "ERROR: Backend not set up. Run setup.sh first."
    exit 1
fi

# Install if vite is missing — run npm install in $EXTERNAL so node_modules
# is never created inside the Tresorit folder (Tresorit would sync it to Windows).
if [ ! -f "$MODULES/.bin/vite" ]; then
    echo "Installing frontend dependencies..."
    mkdir -p "$EXTERNAL"
    cp "$SCRIPT_DIR/frontend/package.json" "$EXTERNAL/"
    cp "$SCRIPT_DIR/frontend/package-lock.json" "$EXTERNAL/" 2>/dev/null || true
    rm -rf "$MODULES"
    cd "$EXTERNAL"
    npm install
    cp "$EXTERNAL/package-lock.json" "$SCRIPT_DIR/frontend/" 2>/dev/null || true
    rm -f "$EXTERNAL/package.json" "$EXTERNAL/package-lock.json"
    echo "node_modules installed to: $MODULES"
fi

# Create a symlink $EXTERNAL/frontend-src -> $SCRIPT_DIR/frontend.
# The symlink lives entirely outside Tresorit so it never gets synced.
# Vite runs from $EXTERNAL with --preserve-symlinks: module resolution
# walks $EXTERNAL/frontend-src/src/ -> $EXTERNAL/frontend-src/ -> $EXTERNAL/
# and finds $EXTERNAL/node_modules naturally — same approach as Windows.
rm -f "$EXTERNAL/frontend-src"
ln -s "$SCRIPT_DIR/frontend" "$EXTERNAL/frontend-src"

# Copy vite config next to node_modules so its imports resolve from there.
cp "$SCRIPT_DIR/frontend/vite.config.ts" "$EXTERNAL/"

echo "Starting gui-manager backend..."
cd "$SCRIPT_DIR/backend"
"$VENV"/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

echo "Starting gui-manager frontend..."
export NODE_OPTIONS="--preserve-symlinks"
export VITE_FRONTEND_SRC="./frontend-src"
export VITE_CACHE_DIR="$EXTERNAL/.vite-cache"
cd "$EXTERNAL"
"$MODULES/.bin/vite" "$EXTERNAL/frontend-src" \
    --config "$EXTERNAL/vite.config.ts" &
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://127.0.0.1:8000"
echo "  Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
