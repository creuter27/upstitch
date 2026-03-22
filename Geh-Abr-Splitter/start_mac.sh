#!/bin/bash
# ============================================================
# Gehaltsabrechnungen aufteilen — Mac/Linux
# ============================================================
# Pfad zum Verzeichnis, das die Monats-Unterordner enthält:
ABRECHNUNGEN_DIR="/Users/cr/Tresorit/eCommerce/brandXpand/Personal/Gehaltsabrechnungen"

# PDF-Passwort (optional — leer lassen wenn kein Passwort oder PW.txt verwenden):
PASSWORD=""

# ============================================================
# Ab hier nichts ändern
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.local/share/upstitch-venvs/Geh-Abr-Splitter"
PYTHON="$VENV/bin/python"
SCRIPT="$SCRIPT_DIR/execution/split_payroll.py"

cd "$ABRECHNUNGEN_DIR" || { echo "Verzeichnis nicht gefunden: $ABRECHNUNGEN_DIR"; exit 1; }

if [ -n "$PASSWORD" ]; then
    "$PYTHON" "$SCRIPT" --password "$PASSWORD" "$@"
else
    "$PYTHON" "$SCRIPT" "$@"
fi
