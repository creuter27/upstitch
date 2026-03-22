@echo off
:: setup.bat  -  run setup for every project in one shot.
:: Each project creates its own .venv-win in its own directory.
:: Safe to re-run  -  each per-project setup wipes and recreates its venv.
setlocal
set "BATCH_PARENT=1"
set "CODE_ROOT=%~dp0"

echo === Full setup  -  all projects ===
echo Code root: %CODE_ROOT%
echo.

call :run_setup "Billbee-Artikelmanager"
call :run_setup "fetchBillbeeDocs"
call :run_setup "fixBillbeeAdresses"
call :run_setup "productionPrep"
call :run_setup "Geh-Abr-Splitter"
call :run_setup "gui-manager"

echo ======================================
echo  All projects set up.
echo ======================================
echo.
exit /b 0

:run_setup
set "PROJ=%~1"
set "SETUP=%CODE_ROOT%%PROJ%\setup.bat"
if exist "%SETUP%" (
    echo --------------------------------------
    echo  %PROJ%
    echo --------------------------------------
    call "%SETUP%"
    echo.
) else (
    echo SKIP: %PROJ% ^(no setup.bat found^)
)
goto :eof
