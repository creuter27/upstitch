@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\Geh-Abr-Splitter"

echo === Geh-Abr-Splitter Setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and check "Add Python to PATH".
    if not defined BATCH_PARENT pause
    exit /b 1
)

REM -- Virtual environment ----------------------------------------------------
if exist "%VENV%" (
    echo Removing existing venv...
    call :rmdir_fast "%VENV%"
)

echo Creating virtual environment at %VENV%...
python -m venv "%VENV%"
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    if not defined BATCH_PARENT pause
    exit /b 1
)

REM -- Dependencies -----------------------------------------------------------
echo Installing dependencies...
"%VENV%\Scripts\pip" install --quiet pymupdf
if errorlevel 1 (
    echo ERROR: pip install failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)

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
