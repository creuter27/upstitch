#!/usr/bin/env bash
# start.sh — start the Telegram bot
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/telegram-bot"
PYTHON="$VENV/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "ERROR: venv not found at $VENV"
    echo "Run setup.sh first."
    exit 1
fi

echo "Starting Upstitch Telegram Bot..."
exec "$PYTHON" "$SCRIPT_DIR/bot.py"
