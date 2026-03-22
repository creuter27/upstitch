@echo off
setlocal EnableDelayedExpansion
set "VENV=%~dp0.venv-win"

:: ============================================================================
:: Neue Artikel Pipeline
::
:: Benennung: "Neue Artikel TRX.bat"  ->  Hersteller = TRX
::            "Neue Artikel FRE.bat"  ->  Hersteller = FRE
:: Das letzte Wort im Dateinamen (ohne .bat) wird als Herstellercode verwendet.
:: ============================================================================

:: Hersteller aus eigenem Dateinamen extrahieren (letztes Wort)
set "FILENAME=%~n0"
set "MFR="
for %%W in (!FILENAME!) do set "MFR=%%W"

if not defined MFR (
    echo [FEHLER] Herstellercode konnte nicht aus Dateiname ermittelt werden.
    pause & exit /b 1
)

echo.
echo ============================================================
echo  Neue Artikel Pipeline  ^|  Hersteller: !MFR!
echo ============================================================
echo.

:: -- Schritt 0: Manueller Download aus Billbee --------------------------------
echo Schritt 0 ^| Manueller Download
echo -------------------------------------------------------------
echo Bitte alle Artikel aus Billbee exportieren:
echo   Artikel ^> Exportieren ^> Billbee XLSX
echo.
echo Datei speichern unter:
echo   .\backups\Billbee_Artikelexport_*.xlsx
echo.
pause

:: -- Neueste XLSX in .\backups suchen -----------------------------------------
set "XLSX="
for /f "delims=" %%F in ('dir /b /o-d "backups\Billbee_Artikelexport*.xlsx" 2^>nul') do (
    if not defined XLSX set "XLSX=backups\%%F"
)

if not defined XLSX (
    echo.
    echo [FEHLER] Keine Datei "Billbee_Artikelexport*.xlsx" im Ordner .\backups gefunden.
    echo Bitte Datei herunterladen und erneut versuchen.
    pause & exit /b 1
)

echo.
echo Gefundene XLSX-Datei: !XLSX!
echo.

:: -- Schritt 1: Neue Artikel importieren --------------------------------------
echo ============================================================
echo  Schritt 1/8 ^| Neue Artikel importieren
echo ============================================================
echo Importiert XLSX, filtert auf wirklich neue Artikel
echo (!MFR!, Id noch nicht im 'ProductList'-Tab), und schreibt
echo nur diese in den 'new'-Tab.
echo.

"%VENV%\Scripts\python.exe" execution\import_new_articles.py ^
    --xlsx-file "!XLSX!" ^
    --manufacturer !MFR!

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 1 fehlgeschlagen.
    pause & exit /b 1
)

:: Sheet-URL aus Tempfile lesen
set "SHEET_URL="
set /p SHEET_URL=<.tmp\sheet_url.txt
if not defined SHEET_URL (
    echo [FEHLER] .tmp\sheet_url.txt nicht gefunden oder leer.
    pause & exit /b 1
)

echo.
echo Sheet-URL: !SHEET_URL!
echo.
pause

:: -- Schritt 2: SKU-Anreicherung ----------------------------------------------
echo ============================================================
echo  Schritt 2/8 ^| SKU-Anreicherung  (enrich_from_sku)
echo ============================================================
echo Fuellt Produktkategorie, -groesse, -variante, -farbe und
echo Hersteller aus dem SKU-Format.
echo.

"%VENV%\Scripts\python.exe" execution\enrich_from_sku.py ^
    --sheet-url "!SHEET_URL!" ^
    --tab new ^
    --manufacturer !MFR!

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 2 fehlgeschlagen.
    pause & exit /b 1
)
echo.
pause

:: -- Schritt 3: BOM-SKUs fuellen ----------------------------------------------
echo ============================================================
echo  Schritt 3/8 ^| BOM-SKUs + Metadaten  (fill_bom_skus)
echo ============================================================
echo Verknuepft Listing-Artikel mit ihren physischen Komponenten,
echo setzt IsBom und Stocksync-Felder korrekt.
echo.

"%VENV%\Scripts\python.exe" execution\fill_bom_skus.py ^
    --sheet-url "!SHEET_URL!" ^
    --tab new ^
    --lookup-tab ProductList ^
    --yes-all

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 3 fehlgeschlagen.
    pause & exit /b 1
)
echo.
pause

:: -- Schritt 4: Produktspezifikationen ----------------------------------------
echo ============================================================
echo  Schritt 4/8 ^| Produktspezifikationen  (apply_product_specs)
echo ============================================================
echo Fuellt Gewicht, Masse, Einkaufspreis und Ursprungsland
echo aus den Spezifikations-Mappings.
echo.

"%VENV%\Scripts\python.exe" execution\apply_product_specs.py ^
    --sheet-url "!SHEET_URL!" ^
    --tab new ^
    --lookup-tab ProductList

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 4 fehlgeschlagen.
    pause & exit /b 1
)
echo.
pause

:: -- Schritt 5: TARIC-Codes ---------------------------------------------------
echo ============================================================
echo  Schritt 5/8 ^| TARIC-Codes  (assign_taric)
echo ============================================================
echo Weist TARIC-Codes und Ursprungslaender zu.
echo.

"%VENV%\Scripts\python.exe" execution\assign_taric.py ^
    --sheet-url "!SHEET_URL!" ^
    --tab new

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 5 fehlgeschlagen.
    pause & exit /b 1
)
echo.
pause

:: -- Schritt 6: Neue Artikel an upload-Tab anhaengen --------------------------
echo ============================================================
echo  Schritt 6/8 ^| An upload anhaengen  (append_new_to_upload)
echo ============================================================
echo Haengt die angereicherten neuen Artikel an den 'upload'-Tab
echo an, damit kuenftige Laeufe sie als bekannt erkennen.
echo.

"%VENV%\Scripts\python.exe" execution\append_new_to_upload.py ^
    --sheet-url "!SHEET_URL!"

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 6 fehlgeschlagen.
    pause & exit /b 1
)
echo.
pause

:: -- Schritt 7: Export der neuen Artikel --------------------------------------
echo ============================================================
echo  Schritt 7/8 ^| Export neuer Artikel  (export_new_articles)
echo ============================================================
echo Exportiert nur die neu hinzugefuegten Artikel (aus dem
echo 'new'-Tab) mit allen Pipeline-Anreicherungen als XLSX.
echo.

"%VENV%\Scripts\python.exe" execution\export_new_articles.py ^
    --sheet-url "!SHEET_URL!"

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 7 fehlgeschlagen.
    pause & exit /b 1
)
echo.

:: -- Schritt 8: Stocksync aktivieren (nach Billbee-Import) --------------------
echo ============================================================
echo  Bitte jetzt die XLSX-Datei in Billbee importieren:
echo    Artikel ^> Importieren ^> Billbee XLSX ^> Datei auswaehlen
echo  Erst danach Schritt 8 starten!
echo ============================================================
echo.
pause

echo ============================================================
echo  Schritt 8/8 ^| Stocksync aktivieren  (activate_stocksync)
echo ============================================================
echo Aktiviert Stocksync fuer alle neuen Artikel via Billbee API.
echo.

"%VENV%\Scripts\python.exe" execution\activate_stocksync.py ^
    --sheet-url "!SHEET_URL!"

if errorlevel 1 (
    echo.
    echo [FEHLER] Schritt 8 fehlgeschlagen.
    pause & exit /b 1
)
echo.

:: -- Fertig --------------------------------------------------------------------
echo ============================================================
echo  Pipeline abgeschlossen!
echo ============================================================
echo.
pause
