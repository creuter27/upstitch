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

echo === Create Shipping Labels ===
echo.
"%VENV%\Scripts\python" run_labels.py %*
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% neq 0 (
    echo Finished with errors ^(exit code %EXIT_CODE%^).
) else (
    echo Finished successfully.
)
echo.
pause
