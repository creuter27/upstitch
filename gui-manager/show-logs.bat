@echo off
setlocal
chcp 65001 >nul
title gui-manager — Logs

set "RUNDIR=%LOCALAPPDATA%\upstitch-tools\gui-manager"
set "BACKEND_LOG=%RUNDIR%\backend.log"
set "FRONTEND_LOG=%RUNDIR%\frontend.log"

REM -- Find Notepad++ -----------------------------------------------------------
set "NPP="
for %%d in (
    "%ProgramFiles%\Notepad++"
    "%ProgramFiles(x86)%\Notepad++"
    "%LOCALAPPDATA%\Programs\Notepad++"
) do (
    if exist "%%~d\notepad++.exe" (
        set "NPP=%%~d\notepad++.exe"
        goto :npp_found
    )
)

echo Notepad++ not found in standard locations.
echo Falling back to regular Notepad.
set "NPP=notepad.exe"
REM notepad can only open one file at a time
if exist "%BACKEND_LOG%"  start "" notepad.exe "%BACKEND_LOG%"
if exist "%FRONTEND_LOG%" start "" notepad.exe "%FRONTEND_LOG%"
goto :done

:npp_found
REM Open both log files in a single Notepad++ instance (tabs)
set "ARGS="
if exist "%BACKEND_LOG%"  set "ARGS=%ARGS% "%BACKEND_LOG%""
if exist "%FRONTEND_LOG%" set "ARGS=%ARGS% "%FRONTEND_LOG%""

if "%ARGS%"=="" (
    echo No log files found in %RUNDIR%
    echo Start the servers with start.bat first.
    pause
    exit /b 1
)

start "" "%NPP%" %ARGS%

:done
exit /b 0
