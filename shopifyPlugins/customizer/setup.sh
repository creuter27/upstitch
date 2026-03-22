#!/usr/bin/env bash
# Setup script for shopifyPlugins/customizer.
# Safe to re-run — wipes and recreates the external venv each time.
# All paths are relative to this script's directory so the folder can be
# moved or synced anywhere without changes.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/shopify-customizer"
cd "$SCRIPT_DIR"

echo "=== Shopify Customizer — Setup ==="
echo "Project root: $SCRIPT_DIR"
echo

# ── Python 3.14 check ─────────────────────────────────────────────────────────
if ! command -v python3.14 &>/dev/null; then
    echo "ERROR: python3.14 not found on PATH."
    echo "Install via Homebrew:  brew install python@3.14"
    exit 1
fi

# ── Wipe and recreate venv ───────────────────────────────────────────────────
if [ -d "$VENV" ]; then
    echo "[1/3] Removing existing virtual environment..."
    rm -rf "$VENV"
fi

mkdir -p "$(dirname "$VENV")"
echo "[2/3] Creating virtual environment with python3.14 at $VENV ..."
python3.14 -m venv "$VENV"

# ── Install dependencies ──────────────────────────────────────────────────────
echo "[3/3] Installing Python dependencies..."
"$VENV"/bin/pip install -q --upgrade pip
if [ -f "requirements.txt" ]; then
    "$VENV"/bin/pip install -q -r requirements.txt
else
    echo "  (no requirements.txt found — skipping)"
fi

echo
echo "Setup complete!"
echo
echo "Plugin files:"
echo "  snippets/customizer.liquid  → copy to your Shopify theme snippets/"
echo "  assets/customizer.js        → copy to your Shopify theme assets/"
echo "  assets/customizer.css       → copy to your Shopify theme assets/"
echo
echo "Admin tool:"
echo "  open admin/index.html       → open in browser to configure"
echo
echo "See README.md for full setup instructions."
echo
