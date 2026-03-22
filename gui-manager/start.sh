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

# Copy vite.config.ts next to node_modules so Vite can resolve tailwindcss /
# autoprefixer from $EXTERNAL/node_modules without a symlink in the Tresorit folder.
cp "$SCRIPT_DIR/frontend/vite.config.ts" "$EXTERNAL/"

echo "Starting gui-manager backend..."
cd "$SCRIPT_DIR/backend"
"$VENV"/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

echo "Starting gui-manager frontend..."
# Run Vite from $EXTERNAL (where node_modules lives) so no symlink is needed
# inside the Tresorit folder. VITE_FRONTEND_SRC points Tailwind content globs
# and the dev server root at the actual source files.
export VITE_FRONTEND_SRC="$SCRIPT_DIR/frontend"
export VITE_CACHE_DIR="$EXTERNAL/.vite-cache"
"$MODULES/.bin/vite" \
    --root "$SCRIPT_DIR/frontend" \
    --config "$EXTERNAL/vite.config.ts" &
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://127.0.0.1:8000"
echo "  Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
