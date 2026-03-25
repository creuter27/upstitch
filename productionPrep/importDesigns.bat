@echo off
:: Import export-current-design.csv as a new date-named tab in Upstitch Design Sheet.
:: The new tab is formatted exactly like the "Template" tab.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"
if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" import_designs.py %*
