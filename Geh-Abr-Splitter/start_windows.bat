@echo off
REM ============================================================
REM Gehaltsabrechnungen aufteilen  -  Windows
REM ============================================================
REM Pfad zum Verzeichnis, das die Monats-Unterordner enthaelt:
set ABRECHNUNGEN_DIR=C:\Pfad\zu\Gehaltsabrechnungen

REM PDF-Passwort (optional  -  leer lassen wenn kein Passwort oder PW.txt verwenden):
set PASSWORD=

REM ============================================================
REM Ab hier nichts aendern
set SCRIPT_DIR=%~dp0
set "VENV=%LOCALAPPDATA%\upstitch-venvs\Geh-Abr-Splitter"
set PYTHON=%VENV%\Scripts\python.exe
set SCRIPT=%SCRIPT_DIR%execution\split_payroll.py

if not exist "%ABRECHNUNGEN_DIR%" (
    echo Verzeichnis nicht gefunden: %ABRECHNUNGEN_DIR%
    pause
    exit /b 1
)

cd /d "%ABRECHNUNGEN_DIR%"
if defined PASSWORD (
    "%PYTHON%" "%SCRIPT%" --password "%PASSWORD%" %*
) else (
    "%PYTHON%" "%SCRIPT%" %*
)
pause
