#!/bin/bash
# ============================================================================
# Neue Artikel Pipeline
#
# Benennung: "Neue Artikel TRX.sh"  ->  Hersteller = TRX
#            "Neue Artikel FRE.sh"  ->  Hersteller = FRE
# Das letzte Wort im Dateinamen (ohne .sh) wird als Herstellercode verwendet.
# ============================================================================

set -euo pipefail

VENV="$HOME/.local/share/upstitch-venvs/Billbee-Artikelmanager"

# Hersteller aus eigenem Dateinamen extrahieren (letztes Wort)
FILENAME=$(basename "$0" .sh)
MFR=$(echo "$FILENAME" | awk '{print $NF}')

if [[ -z "$MFR" ]]; then
    echo "[FEHLER] Herstellercode konnte nicht aus Dateiname ermittelt werden."
    exit 1
fi

echo ""
echo "============================================================"
echo " Neue Artikel Pipeline  |  Hersteller: $MFR"
echo "============================================================"
echo ""

pause() {
    read -rp "Weiter mit Enter (Ctrl+C zum Abbrechen)..." _
    echo ""
}

# ── Schritt 0: Manueller Download aus Billbee ────────────────────────────────
echo "Schritt 0 | Manueller Download"
echo "─────────────────────────────────────────────────────────────"
echo "Bitte alle Artikel aus Billbee exportieren:"
echo "  Artikel > Exportieren > Billbee XLSX"
echo ""
echo "Datei speichern unter:"
echo "  ./backups/Billbee_Artikelexport_*.xlsx"
echo ""
pause

# ── Neueste XLSX in ./backups suchen ─────────────────────────────────────────
XLSX=$(ls -t backups/Billbee_Artikelexport*.xlsx 2>/dev/null | head -1)

if [[ -z "$XLSX" ]]; then
    echo ""
    echo "[FEHLER] Keine Datei \"Billbee_Artikelexport*.xlsx\" im Ordner ./backups gefunden."
    echo "Bitte Datei herunterladen und erneut versuchen."
    exit 1
fi

echo "Gefundene XLSX-Datei: $XLSX"
echo ""

# ── Schritt 1: Neue Artikel importieren ──────────────────────────────────────
echo "============================================================"
echo " Schritt 1/8 | Neue Artikel importieren"
echo "============================================================"
echo "Importiert XLSX, filtert auf wirklich neue Artikel"
echo "($MFR, Id noch nicht im 'ProductList'-Tab), und schreibt"
echo "nur diese in den 'new'-Tab."
echo ""

"$VENV"/bin/python execution/import_new_articles.py \
    --xlsx-file "$XLSX" \
    --manufacturer "$MFR"

# Sheet-URL aus Tempfile lesen
SHEET_URL=$(cat .tmp/sheet_url.txt 2>/dev/null || true)
if [[ -z "$SHEET_URL" ]]; then
    echo "[FEHLER] .tmp/sheet_url.txt nicht gefunden oder leer."
    exit 1
fi

echo ""
echo "Sheet-URL: $SHEET_URL"
echo ""
pause

# ── Schritt 2: SKU-Anreicherung ──────────────────────────────────────────────
echo "============================================================"
echo " Schritt 2/8 | SKU-Anreicherung  (enrich_from_sku)"
echo "============================================================"
echo "Fuellt Produktkategorie, -groesse, -variante, -farbe und"
echo "Hersteller aus dem SKU-Format."
echo ""

"$VENV"/bin/python execution/enrich_from_sku.py \
    --sheet-url "$SHEET_URL" \
    --tab new \
    --manufacturer "$MFR"

echo ""
pause

# ── Schritt 3: BOM-SKUs fuellen ──────────────────────────────────────────────
echo "============================================================"
echo " Schritt 3/8 | BOM-SKUs + Metadaten  (fill_bom_skus)"
echo "============================================================"
echo "Verknuepft Listing-Artikel mit ihren physischen Komponenten,"
echo "setzt IsBom und Stocksync-Felder korrekt."
echo ""

"$VENV"/bin/python execution/fill_bom_skus.py \
    --sheet-url "$SHEET_URL" \
    --tab new \
    --lookup-tab ProductList \
    --yes-all

echo ""
pause

# ── Schritt 4: Produktspezifikationen ────────────────────────────────────────
echo "============================================================"
echo " Schritt 4/8 | Produktspezifikationen  (apply_product_specs)"
echo "============================================================"
echo "Fuellt Gewicht, Masse, Einkaufspreis und Ursprungsland"
echo "aus den Spezifikations-Mappings."
echo ""

"$VENV"/bin/python execution/apply_product_specs.py \
    --sheet-url "$SHEET_URL" \
    --tab new \
    --lookup-tab ProductList

echo ""
pause

# ── Schritt 5: TARIC-Codes ───────────────────────────────────────────────────
echo "============================================================"
echo " Schritt 5/8 | TARIC-Codes  (assign_taric)"
echo "============================================================"
echo "Weist TARIC-Codes und Ursprungslaender zu."
echo ""

"$VENV"/bin/python execution/assign_taric.py \
    --sheet-url "$SHEET_URL" \
    --tab new

echo ""
pause

# ── Schritt 6: Neue Artikel an upload-Tab anhaengen ──────────────────────────
echo "============================================================"
echo " Schritt 6/8 | An upload anhaengen  (append_new_to_upload)"
echo "============================================================"
echo "Haengt die angereicherten neuen Artikel an den 'upload'-Tab"
echo "an, damit kuenftige Laeufe sie als bekannt erkennen."
echo ""

"$VENV"/bin/python execution/append_new_to_upload.py \
    --sheet-url "$SHEET_URL"

echo ""
pause

# ── Schritt 7: Export der neuen Artikel ──────────────────────────────────────
echo "============================================================"
echo " Schritt 7/8 | Export neuer Artikel  (export_new_articles)"
echo "============================================================"
echo "Exportiert nur die neu hinzugefuegten Artikel (aus dem"
echo "'new'-Tab) mit allen Pipeline-Anreicherungen als XLSX."
echo ""

"$VENV"/bin/python execution/export_new_articles.py \
    --sheet-url "$SHEET_URL"

echo ""

# ── Schritt 8: Stocksync aktivieren (nach Billbee-Import) ────────────────────
echo "============================================================"
echo " Bitte jetzt die XLSX-Datei in Billbee importieren:"
echo "   Artikel > Importieren > Billbee XLSX > Datei auswaehlen"
echo " Erst danach Schritt 8 starten!"
echo "============================================================"
echo ""
pause

echo "============================================================"
echo " Schritt 8/8 | Stocksync aktivieren  (activate_stocksync)"
echo "============================================================"
echo "Aktiviert Stocksync fuer alle neuen Artikel via Billbee API."
echo ""

"$VENV"/bin/python execution/activate_stocksync.py \
    --sheet-url "$SHEET_URL"

echo ""

# ── Fertig ────────────────────────────────────────────────────────────────────
echo "============================================================"
echo " Pipeline abgeschlossen!"
echo "============================================================"
echo ""
