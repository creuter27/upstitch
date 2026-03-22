@echo off
setlocal
chcp 65001 >nul
set "VENV=%LOCALAPPDATA%\upstitch-venvs\gui-manager"

echo === gui-manager Backend Setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and check "Add Python to PATH".
    if not defined BATCH_PARENT pause
    exit /b 1
)

if exist "%VENV%" (
    echo Removing existing venv...
    call :rmdir_fast "%VENV%"
)

echo Creating venv...
python -m venv "%VENV%"
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    if not defined BATCH_PARENT pause
    exit /b 1
)

echo Installing backend dependencies...
"%VENV%\Scripts\pip" install --quiet -r "%~dp0backend\requirements.txt"
if errorlevel 1 (
    echo ERROR: pip install failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)

echo.
echo === Backend setup complete! ===
echo.
if not defined BATCH_PARENT pause
exit /b 0

:rmdir_fast
if not exist %1 goto :eof
md "%TEMP%\empty_robocopy_src" 2>nul
robocopy "%TEMP%\empty_robocopy_src" %1 /MIR /R:0 /W:0 /NFL /NDL /NJH /NJS /NC /NS /NP >nul
rd /s /q %1 2>nul
exit /b 0
