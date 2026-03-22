@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "VENV=%LOCALAPPDATA%\upstitch-venvs\fixBillbeeAdresses"

if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

echo === Fix Addresses + Create Labels ===
echo.

REM --- Step 1: Fix addresses ---
echo [1/2] Fixing delivery addresses...
echo.
"%VENV%\Scripts\python" main.py %*
set ADDR_EXIT=%errorlevel%

echo.
if %ADDR_EXIT% neq 0 (
    echo Address fixer finished with errors ^(exit code %ADDR_EXIT%^).
    echo Skipping label creation.
    echo.
    pause
    exit /b %ADDR_EXIT%
)
echo Address fixing done.
echo.

REM --- Step 2: Create labels ---
echo [2/2] Creating shipping labels...
echo.
"%VENV%\Scripts\python" run_labels.py
set LABEL_EXIT=%errorlevel%

echo.
if %LABEL_EXIT% neq 0 (
    echo Label creation finished with errors ^(exit code %LABEL_EXIT%^).
) else (
    echo All done.
)
echo.
pause
