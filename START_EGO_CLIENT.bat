@echo off
setlocal DisableDelayedExpansion
rem Opens the EGO client in the default browser, connected to the local Core.
rem Starts Core first if needed. Prefers the project .venv (current sources, as
rem START_EGO_SERVER.bat); falls back to the packaged EGO-Core.exe without .venv.
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m server.ego_core.control open-client --root "%~dp0."
    goto result
)
if exist "%~dp0EGO-Core.exe" (
    "%~dp0EGO-Core.exe" open-client --root "%~dp0."
    goto result
)
echo [EGO] Neither .venv nor EGO-Core.exe found. Run START_EGO_SERVER.bat first.
pause
exit /b 10
:result
if errorlevel 1 (
    echo [EGO] Client could not be opened. See logs\latest-core-report.json.
    pause
    exit /b 2
)
exit /b 0
