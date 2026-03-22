#!/usr/bin/env bash
# unsetup.sh — remove all external venvs for every project in one shot.
# Safe to re-run — skips projects that are already unset.
set -e

CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== Full unsetup — all projects ==="
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
    unsetup="$CODE_ROOT/$proj/unsetup.sh"
    if [ -f "$unsetup" ]; then
        echo "══════════════════════════════════════"
        echo " $proj"
        echo "══════════════════════════════════════"
        bash "$unsetup"
        echo
    else
        echo "SKIP: $proj (no unsetup.sh found)"
    fi
done

echo "══════════════════════════════════════"
echo " All projects unset."
echo "══════════════════════════════════════"
