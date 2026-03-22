@echo off
:: Fetch invoices and delivery notes from Billbee or Google Drive.
:: Run this after fixAddressesAndPackaging.bat while Billbee automation
:: assigns shipping profiles (gives automation time before getLabels.bat polls).
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"
if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" execution\fetch_documents.py %*
