#!/usr/bin/env bash
# setup.sh — run setup for every project in one shot.
# Each project creates its venv at ~/.local/share/upstitch-venvs/<ProjectName> (outside Tresorit).
# Safe to re-run — each per-project setup wipes and recreates its venv.
set -e

CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== Full setup — all projects ==="
echo "Code root: $CODE_ROOT"
echo

projects=(
    "Billbee-Artikelmanager"
    "fetchBillbeeDocs"
    "fixBillbeeAdresses"
    "productionPrep"
    "Fahrtkosten-Generator"
    "Geh-Abr-Splitter"
    "gmailAttachmentExtractor"
    "shopifyPlugins/customizer"
    "gui-manager"
)

for proj in "${projects[@]}"; do
    setup="$CODE_ROOT/$proj/setup.sh"
    if [ -f "$setup" ]; then
        echo "──────────────────────────────────────"
        echo " $proj"
        echo "──────────────────────────────────────"
        bash "$setup"
        echo
    else
        echo "SKIP: $proj (no setup.sh found)"
    fi
done

echo "══════════════════════════════════════"
echo " All projects set up."
echo "══════════════════════════════════════"
