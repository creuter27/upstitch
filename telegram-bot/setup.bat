@echo off
:: One-time setup for telegram-bot on Windows.
:: Re-running is safe — wipes and recreates the venv.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\telegram-bot"

echo === telegram-bot - Windows Setup ===
echo Venv: %VENV%
echo.

:: Wipe existing venv
if exist "%VENV%" (
    echo [0/3] Removing existing virtual environment...
    call :rmdir_fast "%VENV%"
)

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and tick "Add to PATH".
    pause
    exit /b 1
)

:: Create venv
echo [1/3] Creating virtual environment...
python -m venv "%VENV%"
if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    pause
    exit /b 1
)

:: Install dependencies
echo [2/3] Installing dependencies...
"%VENV%\Scripts\pip" install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

:: Copy .env if needed
echo [3/3] Checking .env...
if not exist ".env" (
    copy .env.example .env >nul
    echo   Created .env from .env.example - fill in your API keys.
) else (
    echo   .env already exists.
)

echo.
echo Setup complete!
echo Run start.bat to launch the bot.
echo.
pause
exit /b 0

:rmdir_fast
if not exist %1 goto :eof
md "%TEMP%\empty_robocopy_src" 2>nul
robocopy "%TEMP%\empty_robocopy_src" %1 /MIR /R:0 /W:0 /NFL /NDL /NJH /NJS /NC /NS /NP >nul
rd /s /q %1 2>nul
exit /b 0
