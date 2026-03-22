#!/bin/bash
# ============================================================
# setup.sh — Virtuelle Umgebung einrichten
# ============================================================
# Erstellt die venv unter ~/.local/share/upstitch-venvs/ und installiert
# alle Abhängigkeiten. Danach: ./start_mac.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/Geh-Abr-Splitter"
cd "$SCRIPT_DIR"

echo "Projektverzeichnis: $SCRIPT_DIR"

# Alte venv entfernen falls vorhanden
if [ -d "$VENV" ]; then
    echo "Entferne bestehende venv ..."
    rm -rf "$VENV"
fi

mkdir -p "$(dirname "$VENV")"
echo "Erstelle venv mit python3.14 unter $VENV ..."
python3.14 -m venv "$VENV"

echo "Installiere Abhängigkeiten ..."
"$VENV"/bin/pip install --upgrade pip --quiet
"$VENV"/bin/pip install pymupdf --quiet

echo ""
echo "Setup abgeschlossen."
echo "Starten mit: ./start_mac.sh"
