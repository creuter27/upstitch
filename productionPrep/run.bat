@echo off
:: Full production preparation workflow  -  runs all four steps in sequence:
::
::   1. fetchDocuments             -  fetch invoices + delivery notes
::   2. importDesigns              -  import design CSV as new dated tab in Design Sheet
::   3. fixAddressesAndPackaging   -  fix addresses, set package types + order state
::   4. getLabels                  -  poll for Verpackungstyp tags, create shipping labels
::
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"

if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

echo ======================================================
echo  productionPrep - Full Workflow
echo ======================================================
echo.

echo ----------------------------------------
echo  Step 1: Fetch Documents
echo ----------------------------------------
"%VENV%\Scripts\python.exe" execution\fetch_documents.py %*
if errorlevel 1 (
    echo.
    echo Step 1 exited with an error. Continue anyway? [Y/N]
    set /p choice=
    if /i not "%choice%"=="Y" exit /b 1
)

echo.
echo ----------------------------------------
echo  Step 2: Import Designs CSV
echo ----------------------------------------
"%VENV%\Scripts\python.exe" import_designs.py
if errorlevel 1 (
    echo.
    echo Step 2 exited with an error. Continue anyway? [Y/N]
    set /p choice=
    if /i not "%choice%"=="Y" exit /b 1
)

echo.
echo ----------------------------------------
echo  Step 3: Fix Addresses ^& Packaging
echo ----------------------------------------
"%VENV%\Scripts\python.exe" main.py %*
if errorlevel 1 (
    echo.
    echo Step 3 exited with an error. Continue to labels? [Y/N]
    set /p choice=
    if /i not "%choice%"=="Y" exit /b 1
)

echo.
echo ----------------------------------------
echo  Step 4: Create Labels
echo ----------------------------------------
"%VENV%\Scripts\python.exe" run_labels.py %*

echo.
echo ======================================================
echo  All steps complete.
echo ======================================================
pause
