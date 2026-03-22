@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\Billbee-Artikelmanager"

echo === Billbee-Artikelmanager Setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and check "Add Python to PATH".
    if not defined BATCH_PARENT pause
    exit /b 1
)

python --version
echo.

REM -- Clean up any venv accidentally created inside Tresorit -----------------
if exist ".venv" (
    echo Removing .venv from project directory ^(must not live in Tresorit^)...
    call :rmdir_fast ".venv"
)

REM -- Virtual environment ----------------------------------------------------
if exist "%VENV%" (
    echo Virtual environment already exists - skipping creation.
) else (
    echo Creating virtual environment at %VENV%...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        if not defined BATCH_PARENT pause
        exit /b 1
    )
    echo Done.
)
echo.

REM -- Shared libraries -------------------------------------------------------
call :install_lib "billbee-python-client"
call :install_lib "google-client"

REM -- Project requirements ---------------------------------------------------
echo Installing requirements.txt...
"%VENV%\Scripts\pip" install --quiet -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)
echo Done.
echo.

REM -- Playwright browsers ----------------------------------------------------
echo Installing Playwright browsers...
"%VENV%\Scripts\playwright" install chromium
if errorlevel 1 (
    echo ERROR: Playwright browser install failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)
echo Done.
echo.

echo === Setup complete! ===
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

REM -- Subroutine: find and install a shared library --------------------------
:install_lib
set LIB=%~1
if exist "%~dp0..\%LIB%" (
    echo Installing shared library: %LIB%
    "%VENV%\Scripts\pip" install --quiet -e "%~dp0..\%LIB%"
    if errorlevel 1 (
        echo ERROR: Failed to install %LIB%.
    ) else (
        echo Done.
    )
) else (
    echo WARNING: %LIB% not found at %~dp0..\%LIB%
)
echo.
goto :eof
