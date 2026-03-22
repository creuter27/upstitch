@echo off
:: Full production preparation workflow  -  runs all three steps in sequence:
::
::   1. fixAddressesAndPackaging   -  fix addresses, set package types + order state
::   2. fetchDocuments             -  fetch invoices + delivery notes
::                                  (runs while Billbee automation assigns shipping profiles)
::   3. getLabels                  -  poll for Verpackungstyp tags, create shipping labels
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
echo  Step 1: Fix Addresses ^& Packaging
echo ----------------------------------------
"%VENV%\Scripts\python.exe" main.py %*
if errorlevel 1 (
    echo.
    echo Step 1 exited with an error. Continue anyway? [Y/N]
    set /p choice=
    if /i not "%choice%"=="Y" exit /b 1
)

echo.
echo ----------------------------------------
echo  Step 2: Fetch Documents
echo ----------------------------------------
"%VENV%\Scripts\python.exe" execution\fetch_documents.py %*
if errorlevel 1 (
    echo.
    echo Step 2 exited with an error. Continue to labels? [Y/N]
    set /p choice=
    if /i not "%choice%"=="Y" exit /b 1
)

echo.
echo ----------------------------------------
echo  Step 3: Create Labels
echo ----------------------------------------
"%VENV%\Scripts\python.exe" run_labels.py %*

echo.
echo ======================================================
echo  All steps complete.
echo ======================================================
pause
