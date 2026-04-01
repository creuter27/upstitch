@echo off
setlocal
chcp 65001 >nul
title gui-manager — Verbose Start
set "VENV=%LOCALAPPDATA%\upstitch-venvs\gui-manager"
set "MODULES=%LOCALAPPDATA%\upstitch-venvs\gui-manager\node_modules"
set "FRONTEND_DIR=%~dp0frontend"

echo ============================================================
echo   gui-manager — Verbose / Troubleshooting Mode
echo   Backend and frontend run in separate visible windows.
echo   Close those windows to stop the servers.
echo ============================================================
echo.

if not exist "%VENV%\Scripts\uvicorn.exe" (
    echo ERROR: Backend not set up. Run setup.bat first.
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
echo ERROR: npm not found.
echo Install Node.js from https://nodejs.org (LTS version), then close and
echo reopen this window so the new PATH takes effect.
pause
exit /b 1

:npm_found
echo Using npm: %NPM%

REM -- Install frontend dependencies if missing ---------------------------------
if not exist "%MODULES%\.bin\vite.cmd" (
    echo Installing frontend dependencies...
    mkdir "%VENV%" 2>nul
    set "PSRC=%FRONTEND_DIR%\package.json"
    set "PDST=%VENV%\package.json"
    powershell -NoProfile -Command "[System.IO.File]::Copy($env:PSRC, $env:PDST, $true)" >nul 2>&1
    if not exist "%VENV%\package.json" type "%FRONTEND_DIR%\package.json" > "%VENV%\package.json"
    if not exist "%VENV%\package.json" (
        echo ERROR: Cannot read package.json - check Tresorit sync.
        pause
        exit /b 1
    )
    set "PSRC=%FRONTEND_DIR%\package-lock.json"
    set "PDST=%VENV%\package-lock.json"
    powershell -NoProfile -Command "[System.IO.File]::Copy($env:PSRC, $env:PDST, $true)" >nul 2>&1
    if not exist "%VENV%\package-lock.json" type "%FRONTEND_DIR%\package-lock.json" > "%VENV%\package-lock.json" 2>nul
    cd /d "%VENV%"
    "%NPM%" install
    if errorlevel 1 (
        echo ERROR: npm install failed.
        pause
        exit /b 1
    )
    if exist "%VENV%\package-lock.json" copy "%VENV%\package-lock.json" "%FRONTEND_DIR%\" >nul
    del "%VENV%\package.json" 2>nul
    del "%VENV%\package-lock.json" 2>nul
    echo node_modules installed to: %MODULES%
)

REM -- Symlink ------------------------------------------------------------------
rd "%VENV%\frontend-src" 2>nul
mklink /D "%VENV%\frontend-src" "%FRONTEND_DIR%"
if errorlevel 1 (
    echo ERROR: Cannot create directory symlink.
    echo Enable Windows Developer Mode so symlink creation works without admin:
    echo   Settings -^> Privacy ^& Security -^> For developers -^> Developer Mode
    echo Or right-click start-verbose.bat and choose "Run as administrator".
    pause
    exit /b 1
)

copy "%FRONTEND_DIR%\vite.config.ts" "%VENV%\" >nul

set "NODE_PATH=%MODULES%"
set "NODE_OPTIONS=--preserve-symlinks"
set "VITE_CACHE_DIR=%VENV%\.vite-cache"
set "VITE_FRONTEND_SRC=./frontend-src"

REM -- Start servers in separate visible windows --------------------------------
echo.
echo Starting gui-manager backend  (new window)...
start "gui-manager BACKEND" /d "%~dp0backend" cmd /k ""%VENV%\Scripts\uvicorn" main:app --host 127.0.0.1 --port 8000 --reload"

echo Starting gui-manager frontend (new window)...
start "gui-manager FRONTEND" /d "%VENV%" cmd /k ""%MODULES%\.bin\vite.cmd" "%VENV%\frontend-src" --config "%VENV%\vite.config.ts""

echo.
echo ============================================================
echo   Backend window  : "gui-manager BACKEND"
echo   Frontend window : "gui-manager FRONTEND"
echo.
echo   Backend  : http://127.0.0.1:8000
echo   Frontend : http://127.0.0.1:5173
echo.
echo   Logs are visible in the respective windows.
echo   Close both server windows to stop the servers.
echo ============================================================
echo.
pause
exit /b 0
