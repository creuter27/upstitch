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

REM Use 'type' to read files from the Tresorit T: drive.
REM 'type' uses direct file read (CreateFile API) and works on Tresorit virtual drives.
REM 'copy', 'robocopy', and 'if exist <dir>' all use directory enumeration (FindFirstFile)
REM which Tresorit blocks -- those commands hang or fail silently on T:.
echo Copying package.json from T: drive...
type "%~dp0frontend\package.json" > "%VENV%\package.json"
if not exist "%VENV%\package.json" (
    echo ERROR: Failed to copy package.json to %VENV%
    if not defined BATCH_PARENT pause
    exit /b 1
)

echo Copying package-lock.json from T: drive...
type "%~dp0frontend\package-lock.json" > "%VENV%\package-lock.json" 2>nul

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
