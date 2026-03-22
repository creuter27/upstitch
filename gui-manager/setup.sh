#!/usr/bin/env bash
# setup.sh — run backend and frontend setup in sequence.
# Re-running is safe — wipes and recreates all generated artifacts.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== gui-manager Setup ==="
echo "Project: $SCRIPT_DIR"
echo

bash "$SCRIPT_DIR/setup_backend.sh"
bash "$SCRIPT_DIR/setup_frontend.sh"

echo "=== Setup complete! ==="
echo "Run ./start.sh to launch both servers in dev mode."
echo "Run ./start_prod.sh to build and serve in production mode."
echo
