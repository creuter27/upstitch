@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title gui-manager — Starting

set "VENV=%LOCALAPPDATA%\upstitch-venvs\gui-manager"
set "MODULES=%LOCALAPPDATA%\upstitch-venvs\gui-manager\node_modules"
set "FRONTEND_DIR=%~dp0frontend"
set "RUNDIR=%LOCALAPPDATA%\upstitch-tools\gui-manager"

REM -- Passed via %env:% to PowerShell to avoid batch-to-PS quote issues
set "PS_BACKEND_EXE=%VENV%\Scripts\uvicorn.exe"
set "PS_BACKEND_WD=%~dp0backend"
set "PS_BACKEND_LOG=%RUNDIR%\backend.log"
set "PS_BACKEND_PID=%RUNDIR%\backend.pid"
set "PS_FRONTEND_WD=%VENV%"
set "PS_FRONTEND_LOG=%RUNDIR%\frontend.log"
set "PS_FRONTEND_PID=%RUNDIR%\frontend.pid"
set "PS_VITE=%MODULES%\.bin\vite.cmd"
set "PS_FRONTEND_SRC=%VENV%\frontend-src"
set "PS_VITE_CFG=%VENV%\vite.config.ts"

echo ============================================================
echo   gui-manager
echo ============================================================
echo.

REM -- Check setup --------------------------------------------------------------
if not exist "%VENV%\Scripts\uvicorn.exe" (
    echo [ERROR] Backend not set up. Run setup.bat first.
    pause
    exit /b 1
)

REM -- Locate npm ---------------------------------------------------------------
where npm >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%i in ('where npm') do (
        set "NPM=%%i"
        goto :npm_found
    )
)
if exist "%ProgramFiles%\nodejs\npm.cmd" (
    set "NPM=%ProgramFiles%\nodejs\npm.cmd" & goto :npm_found
)
if exist "%ProgramFiles(x86)%\nodejs\npm.cmd" (
    set "NPM=%ProgramFiles(x86)%\nodejs\npm.cmd" & goto :npm_found
)
if exist "%APPDATA%\nvm\npm.cmd" (
    set "NPM=%APPDATA%\nvm\npm.cmd" & goto :npm_found
)
echo [ERROR] npm not found. Install Node.js from https://nodejs.org
pause
exit /b 1

:npm_found

REM -- Install frontend dependencies if missing ---------------------------------
if not exist "%MODULES%\.bin\vite.cmd" (
    echo Installing frontend dependencies...
    mkdir "%VENV%" 2>nul
    set "PSRC=%FRONTEND_DIR%\package.json"
    set "PDST=%VENV%\package.json"
    powershell -NoProfile -Command "[System.IO.File]::Copy($env:PSRC,$env:PDST,$true)" >nul 2>&1
    if not exist "%VENV%\package.json" type "%FRONTEND_DIR%\package.json" > "%VENV%\package.json"
    if not exist "%VENV%\package.json" (
        echo [ERROR] Cannot read package.json - check Tresorit sync.
        pause & exit /b 1
    )
    set "PSRC=%FRONTEND_DIR%\package-lock.json"
    set "PDST=%VENV%\package-lock.json"
    powershell -NoProfile -Command "[System.IO.File]::Copy($env:PSRC,$env:PDST,$true)" >nul 2>&1
    if not exist "%VENV%\package-lock.json" type "%FRONTEND_DIR%\package-lock.json" > "%VENV%\package-lock.json" 2>nul
    cd /d "%VENV%"
    "%NPM%" install
    if errorlevel 1 (echo [ERROR] npm install failed. & pause & exit /b 1)
    if exist "%VENV%\package-lock.json" copy "%VENV%\package-lock.json" "%FRONTEND_DIR%\" >nul
    del "%VENV%\package.json" 2>nul
    del "%VENV%\package-lock.json" 2>nul
)

REM -- Symlink frontend-src -> frontend on T: -----------------------------------
rd "%VENV%\frontend-src" 2>nul
mklink /D "%VENV%\frontend-src" "%FRONTEND_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot create directory symlink.
    echo Enable Developer Mode: Settings -^> Privacy ^& Security -^> For developers
    echo Or right-click start.bat and choose "Run as administrator".
    pause & exit /b 1
)

copy "%FRONTEND_DIR%\vite.config.ts" "%VENV%\" >nul

REM -- Env vars (inherited by child processes via PowerShell Start-Process) -----
set "NODE_PATH=%MODULES%"
set "NODE_OPTIONS=--preserve-symlinks"
set "VITE_CACHE_DIR=%VENV%\.vite-cache"
set "VITE_FRONTEND_SRC=./frontend-src"

REM -- Prepare run directory ----------------------------------------------------
if not exist "%RUNDIR%" mkdir "%RUNDIR%"

REM -- Kill any stale processes from a previous run -----------------------------
if exist "%RUNDIR%\backend.pid" (
    for /f "usebackq" %%p in ("%RUNDIR%\backend.pid") do taskkill /F /T /PID %%p >nul 2>&1
    del "%RUNDIR%\backend.pid" 2>nul
)
if exist "%RUNDIR%\frontend.pid" (
    for /f "usebackq" %%p in ("%RUNDIR%\frontend.pid") do taskkill /F /T /PID %%p >nul 2>&1
    del "%RUNDIR%\frontend.pid" 2>nul
)

REM -- Start backend in background, log to file ---------------------------------
echo Starting backend...
powershell -NoProfile -Command ^
  "$p = Start-Process -NoNewWindow cmd" ^
  "    -ArgumentList ('/c \"' + $env:PS_BACKEND_EXE + '\" main:app --host 127.0.0.1 --port 8000 --reload >> \"' + $env:PS_BACKEND_LOG + '\" 2>&1')" ^
  "    -WorkingDirectory $env:PS_BACKEND_WD -PassThru;" ^
  "[IO.File]::WriteAllText($env:PS_BACKEND_PID, $p.Id.ToString())"
if errorlevel 1 (
    echo [ERROR] Failed to launch backend.
    pause & exit /b 1
)

powershell -NoProfile -Command ^
  "$ok=$false; for($i=0;$i -lt 30;$i++){" ^
  "  try{$c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8000); $c.Close(); $ok=$true; break}" ^
  "  catch{}; Start-Sleep 1" ^
  "}; if(-not $ok){exit 1}"
if errorlevel 1 (
    echo [ERROR] Backend did not become ready. Check log:
    echo   %RUNDIR%\backend.log
    pause & exit /b 1
)
echo   Backend ready.

REM -- Start frontend in background, log to file --------------------------------
echo Starting frontend...
powershell -NoProfile -Command ^
  "$p = Start-Process -NoNewWindow cmd" ^
  "    -ArgumentList ('/c \"' + $env:PS_VITE + '\" \"' + $env:PS_FRONTEND_SRC + '\" --config \"' + $env:PS_VITE_CFG + '\" >> \"' + $env:PS_FRONTEND_LOG + '\" 2>&1')" ^
  "    -WorkingDirectory $env:PS_FRONTEND_WD -PassThru;" ^
  "[IO.File]::WriteAllText($env:PS_FRONTEND_PID, $p.Id.ToString())"
if errorlevel 1 (
    echo [ERROR] Failed to launch frontend.
    pause & exit /b 1
)

powershell -NoProfile -Command ^
  "$ok=$false; for($i=0;$i -lt 30;$i++){" ^
  "  try{$c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',5173); $c.Close(); $ok=$true; break}" ^
  "  catch{}; Start-Sleep 1" ^
  "}; if(-not $ok){exit 1}"
if errorlevel 1 (
    echo [ERROR] Frontend did not become ready. Check log:
    echo   %RUNDIR%\frontend.log
    pause & exit /b 1
)
echo   Frontend ready.

REM -- Open browser and close this window ---------------------------------------
start http://127.0.0.1:5173

echo.
echo   Both servers running in the background.
echo.
echo   Frontend : http://127.0.0.1:5173
echo   Backend  : http://127.0.0.1:8000
echo   Logs     : %RUNDIR%
echo   Stop     : stop.bat
echo.
timeout /t 4 /nobreak >nul
exit /b 0
