@echo off
setlocal
chcp 65001 >nul
set "VENV=%LOCALAPPDATA%\upstitch-venvs\gui-manager"

echo === gui-manager Unsetup ===
echo.

if exist "%VENV%" (
    echo Removing external data at %VENV% ^(venv + node_modules^) ...
    call :rmdir_fast "%VENV%"
    echo Done.
) else (
    echo No external data found at %VENV% -- nothing to remove.
)

echo.
echo === Unsetup complete ===
echo.
if not defined BATCH_PARENT pause
exit /b 0

:rmdir_fast
if not exist %1 goto :eof
md "%TEMP%\empty_robocopy_src" 2>nul
robocopy "%TEMP%\empty_robocopy_src" %1 /MIR /R:0 /W:0 /NFL /NDL /NJH /NJS /NC /NS /NP >nul
rd /s /q %1 2>nul
exit /b 0
