@echo off
:: One-time setup: applies conditional formatting rules to the "Template" tab
:: in "Upstitch Design Sheet". Re-run whenever the rules change.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"
if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" setup_design_template.py %*
