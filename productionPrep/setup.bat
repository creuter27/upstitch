@echo off
:: One-time setup for productionPrep on Windows.
:: Re-running is safe  -  wipes and recreates the shared .venv-win.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\productionPrep"

echo === productionPrep - Windows Setup ===
echo.

:: Wipe existing venv
if exist "%VENV%" (
    echo [0/5] Removing existing virtual environment...
    call :rmdir_fast "%VENV%"
)

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and tick "Add to PATH".
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Create venv
echo [1/5] Creating virtual environment...
python -m venv "%VENV%"
if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Install dependencies
echo [2/5] Installing dependencies...
"%VENV%\Scripts\pip" install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install -r requirements.txt failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Install shared Billbee client
echo [3/5] Installing shared Billbee client...
"%VENV%\Scripts\pip" install -q -e "%~dp0..\billbee-python-client"
if errorlevel 1 (
    echo ERROR: Could not install billbee-python-client.
    echo Expected at: %~dp0..\billbee-python-client
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Install shared Google client
echo [4/5] Installing shared Google client...
"%VENV%\Scripts\pip" install -q -e "%~dp0..\google-client"
if errorlevel 1 (
    echo ERROR: Could not install google-client.
    echo Expected at: %~dp0..\google-client
    if not defined BATCH_PARENT pause
    exit /b 1
)

:: Copy .env if needed
echo [5/5] Checking .env...
if not exist ".env" (
    copy .env.example .env >nul
    echo   Created .env from .env.example - fill in your API keys.
) else (
    echo   .env already exists.
)

echo.
echo Setup complete!
echo.
echo Normal workflow:
echo   fixAddressesAndPackaging.bat   fix addresses + set package types
echo   fetchDocuments.bat             fetch invoices + delivery notes
echo   getLabels.bat                  create shipping labels
echo   run.bat                        all three in sequence
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
