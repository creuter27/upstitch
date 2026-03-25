#!/usr/bin/env bash
# setup.sh — create venv at ~/.local/share/upstitch-venvs/telegram-bot (outside Tresorit)
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/telegram-bot"

echo "=== telegram-bot setup ==="
echo "Project: $PROJECT_DIR"
echo "Venv:    $VENV"
echo

if ! command -v python3.14 &>/dev/null; then
    echo "ERROR: python3.14 not found. Install via: brew install python@3.14"
    exit 1
fi

if [ -d "$VENV" ]; then
    echo "Removing existing venv..."
    rm -rf "$VENV"
fi

mkdir -p "$(dirname "$VENV")"
echo "Creating venv..."
python3.14 -m venv "$VENV"

echo "Installing dependencies..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "Created .env from .env.example — fill in your API keys."
else
    echo ".env already exists."
fi

echo
echo "Done!"
echo "Run with:  $VENV/bin/python $PROJECT_DIR/bot.py"
