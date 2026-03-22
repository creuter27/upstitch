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
echo Install Node.js from https://nodejs.org (LTS version), then close and
echo reopen this window so the new PATH takes effect.
pause
exit /b 1

:npm_found
echo Using npm: %NPM%

REM -- Install if vite is missing from external location --------------------
REM    npm install runs in %VENV% (C: drive), never in the Tresorit folder.
REM    'type' reads files from T: via direct file read (CreateFile API) which
REM    Tresorit supports. 'copy' and 'if exist <dir>' use FindFirstFile which
REM    Tresorit blocks — those hang on T:.
if not exist "%MODULES%\.bin\vite.cmd" (
    echo Installing frontend dependencies...
    mkdir "%VENV%" 2>nul
    type "%FRONTEND_DIR%\package.json" > "%VENV%\package.json"
    type "%FRONTEND_DIR%\package-lock.json" > "%VENV%\package-lock.json" 2>nul
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


REM -- Create directory symlink %VENV%\frontend-src -> frontend on T: -------
REM    esbuild (Go binary) corrupts T: UNC paths internally; running vite with
REM    a C:-based symlink root makes all paths local and avoids the corruption.
REM    PostCSS CJS require() also walks up from C: and finds %VENV%\node_modules.
REM    Requires Developer Mode or admin: Settings > Privacy & Security > For developers
rd "%VENV%\frontend-src" 2>nul
mklink /D "%VENV%\frontend-src" "%FRONTEND_DIR%"
if errorlevel 1 (
    echo ERROR: Cannot create directory symlink.
    echo Enable Windows Developer Mode so symlink creation works without admin:
    echo   Settings -^> Privacy ^& Security -^> For developers -^> Developer Mode
    echo Or right-click start.bat and choose "Run as administrator".
    pause
    exit /b 1
)

REM -- Copy vite config to %VENV% so the compiled .mjs temp file lands there,
REM    where ESM finds %VENV%\node_modules\vite during config loading.
copy "%FRONTEND_DIR%\vite.config.ts" "%VENV%\" >nul

REM -- Set env vars inherited by child windows ------------------------------
REM NODE_OPTIONS=--preserve-symlinks: keeps the C: symlink path (frontend-src)
REM when Node.js loads files through the symlink, so require/import resolution
REM walks up from C:\...\frontend-src to C:\...\node_modules (not T: where
REM node_modules doesn't exist). Fixes PostCSS/tailwindcss loading.
set "NODE_PATH=%MODULES%"
set "NODE_OPTIONS=--preserve-symlinks"
set "VITE_CACHE_DIR=%VENV%\.vite-cache"
set "VITE_FRONTEND_SRC=./frontend-src"

REM -- Start servers ---------------------------------------------------------
echo Starting gui-manager backend...
start "gui-manager backend" /d "%~dp0backend" cmd /k ""%VENV%\Scripts\uvicorn" main:app --host 127.0.0.1 --port 8000 --reload"

echo Starting gui-manager frontend...
start "gui-manager frontend" /d "%VENV%" cmd /k ""%MODULES%\.bin\vite.cmd" "%VENV%\frontend-src" --config "%VENV%\vite.config.ts""

echo.
echo  Backend:   http://127.0.0.1:8000
echo  Frontend:  http://127.0.0.1:5173
echo.
echo Both servers started in separate windows.
echo Close those windows to stop the servers.
echo.
pause
exit /b 0
