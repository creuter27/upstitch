@echo off
:: unsetup.bat — remove all external venvs for every project in one shot.
:: Safe to re-run — skips projects that are already unset.
setlocal
set "BATCH_PARENT=1"
set "CODE_ROOT=%~dp0"

echo === Full unsetup  -  all projects ===
echo Code root: %CODE_ROOT%
echo.

call :run_unsetup "Billbee-Artikelmanager"
call :run_unsetup "fetchBillbeeDocs"
call :run_unsetup "fixBillbeeAdresses"
call :run_unsetup "productionPrep"
call :run_unsetup "Geh-Abr-Splitter"
call :run_unsetup "gui-manager"

echo ======================================
echo  All projects unset.
echo ======================================
echo.
pause
exit /b 0

:run_unsetup
set "PROJ=%~1"
set "UNSETUP=%CODE_ROOT%%PROJ%\unsetup.bat"
if exist "%UNSETUP%" (
    echo --------------------------------------
    echo  %PROJ%
    echo --------------------------------------
    call "%UNSETUP%"
    echo.
) else (
    echo SKIP: %PROJ% ^(no unsetup.bat found^)
)
goto :eof
