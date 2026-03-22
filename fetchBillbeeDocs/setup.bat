@echo off
:: One-time setup for fetchBillbeeDocs on Windows.
:: Run this once before using run.bat.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\fetchBillbeeDocs"

echo === fetchBillbeeDocs  -  Windows Setup ===
echo.

:: Wipe existing venv so we start fresh
if exist "%VENV%" (
    echo [0/4] Removing existing virtual environment...
    call :rmdir_fast "%VENV%"
)

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and make sure to tick "Add to PATH".
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Create venv
echo [1/4] Creating virtual environment...
python -m venv "%VENV%"
if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Install dependencies
echo [2/4] Installing dependencies...
"%VENV%\Scripts\pip" install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install -r requirements.txt failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Install shared Billbee client from sibling directory
echo [3/4] Installing shared Billbee client...
"%VENV%\Scripts\pip" install -q -e "%~dp0..\billbee-python-client"
if errorlevel 1 (
    echo ERROR: Could not install billbee-python-client.
    echo Expected at: %~dp0..\billbee-python-client
    echo Make sure that folder exists and contains pyproject.toml.
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Install shared Google client from sibling directory
echo [4/4] Installing shared Google client...
"%VENV%\Scripts\pip" install -q -e "%~dp0..\google-client"
if errorlevel 1 (
    echo ERROR: Could not install google-client.
    echo Expected at: %~dp0..\google-client
    echo Make sure that folder exists and contains pyproject.toml.
    if not defined BATCH_PARENT pause
    exit /b 1
)

echo.
echo Setup complete!
echo Test with:  run.bat --dry-run
echo.
if not defined BATCH_PARENT pause
exit /b 0

REM -- Subroutine: fast-delete a directory via robocopy -----------------------
:rmdir_fast
if not exist %1 goto :eof
md "%TEMP%\empty_robocopy_src" 2>nul
robocopy "%TEMP%\empty_robocopy_src" %1 /MIR /R:0 /W:0 /NFL /NDL /NJH /NJS /NC /NS /NP >nul
rd /s /q %1 2>nul
exit /b 0
