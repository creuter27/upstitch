@echo off
setlocal
chcp 65001 >nul
set "VENV=%LOCALAPPDATA%\upstitch-venvs\gui-manager"
set "MODULES=%LOCALAPPDATA%\upstitch-venvs\gui-manager\node_modules"

echo === gui-manager Frontend Setup ===
echo.

echo Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found on PATH.
    echo Install Node.js from https://nodejs.org
    if not defined BATCH_PARENT pause
    exit /b 1
)

REM Ensure external dir exists (created by setup_backend.bat, but mkdir is idempotent)
mkdir "%VENV%" 2>nul

REM Copy files from the Tresorit T: drive.
REM Primary: PowerShell [System.IO.File]::Copy via $env: vars (CopyFile API, no FindFirstFile).
REM Fallback: 'type' redirect — if file still missing or empty, bail with a clear error.
echo Copying package.json from T: drive...
set "PSRC=%~dp0frontend\package.json"
set "PDST=%VENV%\package.json"
powershell -NoProfile -Command "[System.IO.File]::Copy($env:PSRC, $env:PDST, $true)" >nul 2>&1
if not exist "%PDST%" type "%PSRC%" > "%PDST%"
if not exist "%PDST%" (
    echo ERROR: Cannot read package.json from %PSRC% - check Tresorit sync.
    if not defined BATCH_PARENT pause
    exit /b 1
)
for %%A in ("%PDST%") do if %%~zA==0 (
    echo ERROR: Copied package.json is empty - check Tresorit sync.
    if not defined BATCH_PARENT pause
    exit /b 1
)

echo Copying package-lock.json from T: drive...
set "PSRC=%~dp0frontend\package-lock.json"
set "PDST=%VENV%\package-lock.json"
powershell -NoProfile -Command "[System.IO.File]::Copy($env:PSRC, $env:PDST, $true)" >nul 2>&1
if not exist "%PDST%" type "%PSRC%" > "%PDST%" 2>nul

echo Running npm install in %VENV%...
cd /d "%VENV%"
npm install
if errorlevel 1 (
    echo ERROR: npm install failed.
    if not defined BATCH_PARENT pause
    exit /b 1
)

REM Write package-lock.json back to the project (C: -> T: write works fine)
if exist "%VENV%\package-lock.json" copy "%VENV%\package-lock.json" "%~dp0frontend\" >nul
del "%VENV%\package.json" 2>nul
del "%VENV%\package-lock.json" 2>nul

REM Create a symlink frontend\node_modules → external so VS Code / TypeScript can find types.
REM Requires Developer Mode (same as the frontend-src symlink in start.bat).
rd "%~dp0frontend\node_modules" 2>nul
mklink /D "%~dp0frontend\node_modules" "%MODULES%"
if errorlevel 1 (
    echo   [warn] Could not create node_modules symlink ^(enable Developer Mode for symlinks^)
) else (
    echo Symlink: frontend\node_modules -^> %MODULES%
)

echo.
echo Frontend packages installed to: %MODULES%
echo.
echo === Frontend setup complete! ===
echo.
if not defined BATCH_PARENT pause
exit /b 0

:rmdir_fast
if not exist %1 goto :eof
md "%TEMP%\empty_robocopy_src" 2>nul
robocopy "%TEMP%\empty_robocopy_src" %1 /MIR /R:0 /W:0 /NFL /NDL /NJH /NJS /NC /NS /NP >nul
rd /s /q %1 2>nul
exit /b 0
