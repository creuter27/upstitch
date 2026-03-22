@echo off
:: Fix Billbee delivery addresses and set package type tags.
:: Applies address fixes, package type tags, and state transitions per-order.
:: Run fetchDocuments.bat next while Billbee automation assigns shipping profiles.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"
if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" main.py %*
