@echo off
:: Billbee Morning Document Fetch  -  manual run helper
:: Run from anywhere; always executes relative to the batch file's own directory.
::
:: Usage:
::   run.bat                   -  normal run
::   run.bat --dry-run         -  list what would be downloaded
::   run.bat --state 4         -  override order state
::   run.bat --since 2026-01-01T06:00:00   -  override lookback start
::
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\upstitch-venvs\fetchBillbeeDocs"

if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: Virtual environment not found.
    echo Run setup.bat first to create it.
    pause
    exit /b 1
)

"%VENV%\Scripts\python.exe" execution\fetch_morning_documents.py %*
