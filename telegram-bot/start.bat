@echo off
setlocal
chcp 65001 >nul

set "VENV=%LOCALAPPDATA%\upstitch-venvs\telegram-bot"
set "PYTHON=%VENV%\Scripts\python.exe"
set "SCRIPT_DIR=%~dp0"

if not exist "%PYTHON%" (
    echo ERROR: venv not found at %VENV%
    echo Run setup.bat first.
    pause
    exit /b 1
)

echo Starting Upstitch Telegram Bot...
"%PYTHON%" "%SCRIPT_DIR%bot.py"
pause
