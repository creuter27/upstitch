@echo off
setlocal
set "VENV=%LOCALAPPDATA%\upstitch-venvs\fixBillbeeAdresses"

echo === fixBillbeeAdresses Setup ===
echo.

REM ---------- locate Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.11+ from https://python.org and make sure to check
    echo "Add Python to PATH" during installation.
    if not defined BATCH_PARENT pause
    exit /b 1
)

python --version
echo.

REM ---------- create venv ----------
if exist "%VENV%" (
    echo Virtual environment already exists - skipping creation.
) else (
    echo Creating virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        if not defined BATCH_PARENT pause
        exit /b 1
    )
    echo Done.
)
echo.

REM ---------- install packages ----------
echo Installing packages from requirements.txt...
"%VENV%\Scripts\pip" install --quiet -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)
echo Done.
echo.

REM ---------- install shared libraries ----------
REM Looks for sibling folders (e.g. ..\billbee-python-client relative to this project).

call :install_lib "billbee-python-client"
call :install_lib "google-client"

REM ---------- check .env ----------
if not exist .env (
    echo WARNING: .env file not found.
    echo Create .env in this folder with the following keys:
    echo   OPENCAGE_API_KEY=your_key_here
    echo   ANTHROPIC_API_KEY=your_key_here
    echo.
    echo Also make sure billbee-python-client\.env contains:
    echo   BILLBEE_API_KEY=...
    echo   BILLBEE_USERNAME=...
    echo   BILLBEE_PASSWORD=...
    echo.
) else (
    echo .env found.
)

echo.
echo === Setup complete ===
echo You can now run:
echo   fixaddresses.bat   -  fix delivery addresses
echo   getLabels.bat      -  create shipping labels
echo   run.bat            -  run both in sequence
echo.
if not defined BATCH_PARENT pause
exit /b 0

REM ---------- subroutine: find and install a shared library ----------
:install_lib
set LIB=%~1
set FOUND=

REM Sibling folder: ..\<lib> relative to this project
if exist "%~dp0..\%LIB%" (
    set FOUND=%~dp0..\%LIB%
    goto :do_install
)

echo WARNING: %LIB% not found at %~dp0..\%LIB%
echo   Make sure %LIB% is in the same parent folder as this project.
echo.
goto :eof

:do_install
echo Installing shared library: %LIB%
echo   Path: %FOUND%
"%VENV%\Scripts\pip" install --quiet -e "%FOUND%"
if errorlevel 1 (
    echo ERROR: Failed to install %LIB%.
) else (
    echo Done.
)
echo.
goto :eof
