@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title gui-manager — Stopping

set "RUNDIR=%LOCALAPPDATA%\upstitch-tools\gui-manager"
set "BACKEND_PID=%RUNDIR%\backend.pid"
set "FRONTEND_PID=%RUNDIR%\frontend.pid"

echo ============================================================
echo   gui-manager — Stopping servers
echo ============================================================
echo.

set "FOUND=0"

REM -- Send graceful stop -------------------------------------------------------
if exist "%BACKEND_PID%" (
    set "FOUND=1"
    for /f "usebackq" %%p in ("%BACKEND_PID%") do (
        echo Stopping backend  (PID %%p^)...
        taskkill /T /PID %%p >nul 2>&1
    )
) else (
    echo Backend  — no PID file found (not started with start.bat?)
)

if exist "%FRONTEND_PID%" (
    set "FOUND=1"
    for /f "usebackq" %%p in ("%FRONTEND_PID%") do (
        echo Stopping frontend (PID %%p^)...
        taskkill /T /PID %%p >nul 2>&1
    )
) else (
    echo Frontend — no PID file found (not started with start.bat?)
)

if "%FOUND%"=="0" (
    echo Nothing to stop.
    timeout /t 2 /nobreak >nul
    exit /b 0
)

REM -- Wait for graceful shutdown -----------------------------------------------
echo.
echo Waiting for processes to exit...
timeout /t 5 /nobreak >nul

REM -- Force-kill anything still alive -----------------------------------------
if exist "%BACKEND_PID%" (
    for /f "usebackq" %%p in ("%BACKEND_PID%") do (
        tasklist /FI "PID eq %%p" /NH 2>nul | find "%%p" >nul 2>&1
        if not errorlevel 1 (
            echo Backend still running — force killing...
            taskkill /F /T /PID %%p >nul 2>&1
        )
    )
    del "%BACKEND_PID%" 2>nul
)

if exist "%FRONTEND_PID%" (
    for /f "usebackq" %%p in ("%FRONTEND_PID%") do (
        tasklist /FI "PID eq %%p" /NH 2>nul | find "%%p" >nul 2>&1
        if not errorlevel 1 (
            echo Frontend still running — force killing...
            taskkill /F /T /PID %%p >nul 2>&1
        )
    )
    del "%FRONTEND_PID%" 2>nul
)

echo.
echo Done.
timeout /t 2 /nobreak >nul
exit /b 0
