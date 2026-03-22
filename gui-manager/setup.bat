@echo off
setlocal
chcp 65001 >nul

REM Preserve caller's BATCH_PARENT so we pause at the end when run standalone,
REM then set it so the sub-scripts don't pause between steps.
set "_PARENT=%BATCH_PARENT%"
set "BATCH_PARENT=1"

echo === gui-manager Setup ===
echo.

call "%~dp0setup_backend.bat"
if errorlevel 1 (
    echo ERROR: Backend setup failed.
    if not defined _PARENT pause
    exit /b 1
)

call "%~dp0setup_frontend.bat"
if errorlevel 1 (
    echo ERROR: Frontend setup failed.
    if not defined _PARENT pause
    exit /b 1
)

echo === Setup complete! ===
echo Run start.bat to launch both servers in dev mode.
echo Run start_prod.bat to build and serve in production mode.
echo.
if not defined _PARENT pause
exit /b 0
