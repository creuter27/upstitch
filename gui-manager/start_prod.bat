@echo off
setlocal
chcp 65001 >nul
set "VENV=%LOCALAPPDATA%\upstitch-venvs\gui-manager"
set "MODULES=%LOCALAPPDATA%\upstitch-venvs\gui-manager\node_modules"
set "FRONTEND_DIR=%~dp0frontend"

if not exist "%VENV%\Scripts\uvicorn.exe" (
    echo ERROR: Backend not set up. Run setup.bat first.
    pause
    exit /b 1
)

if not exist "%MODULES%\.bin\vite.cmd" (
    echo ERROR: Frontend not set up. Run setup.bat first.
    pause
    exit /b 1
)

REM -- Locate npm ------------------------------------------------------------
where npm >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%i in ('where npm') do (
        set "NPM=%%i"
        goto :npm_found
    )
)

if exist "%ProgramFiles%\nodejs\npm.cmd" (
    set "NPM=%ProgramFiles%\nodejs\npm.cmd"
    goto :npm_found
)
if exist "%ProgramFiles(x86)%\nodejs\npm.cmd" (
    set "NPM=%ProgramFiles(x86)%\nodejs\npm.cmd"
    goto :npm_found
)
if exist "%APPDATA%\nvm\npm.cmd" (
    set "NPM=%APPDATA%\nvm\npm.cmd"
    goto :npm_found
)

echo ERROR: npm not found.
pause
exit /b 1

:npm_found

REM -- Remove any node_modules re-synced into the Tresorit folder -----------
if exist "%FRONTEND_DIR%\node_modules" (
    echo Removing node_modules re-synced from Tresorit...
    rd /s /q "%FRONTEND_DIR%\node_modules" 2>nul
)

REM -- Create directory symlink %VENV%\frontend-src -> frontend on T: -------
rd "%VENV%\frontend-src" 2>nul
mklink /D "%VENV%\frontend-src" "%FRONTEND_DIR%"
if errorlevel 1 (
    echo ERROR: Cannot create directory symlink.
    echo Enable Windows Developer Mode so symlink creation works without admin:
    echo   Settings -^> Privacy ^& Security -^> For developers -^> Developer Mode
    echo Or right-click start_prod.bat and choose "Run as administrator".
    pause
    exit /b 1
)

copy "%FRONTEND_DIR%\vite.config.ts" "%VENV%\" >nul

set "NODE_PATH=%MODULES%"
set "NODE_OPTIONS=--preserve-symlinks"
set "VITE_CACHE_DIR=%VENV%\.vite-cache"
set "VITE_FRONTEND_SRC=./frontend-src"

echo Building frontend...
cd /d "%VENV%"
"%MODULES%\.bin\vite.cmd" "%VENV%\frontend-src" --config "%VENV%\vite.config.ts" build
if errorlevel 1 (
    echo ERROR: Frontend build failed.
    pause
    exit /b 1
)

echo.
echo Starting backend (serving frontend from /dist)...
cd /d "%~dp0backend"
"%VENV%\Scripts\uvicorn" main:app --host 127.0.0.1 --port 8000
