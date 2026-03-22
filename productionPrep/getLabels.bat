@echo off
:: Create shipping labels for orders in the after_fix state.
:: Polls until Billbee automation has assigned Verpackungstypen, then
:: calls the carrier API and saves label PDFs to the labels\ folder.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"
if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" run_labels.py %*
