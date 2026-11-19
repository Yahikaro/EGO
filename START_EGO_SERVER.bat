@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)" >nul 2>&1
    if not errorlevel 1 goto project_python
)
py -3.12-64 -c "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)" >nul 2>&1
if not errorlevel 1 goto launcher_python
python -c "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)" >nul 2>&1
if not errorlevel 1 goto system_python
if not exist "%~dp0logs" mkdir "%~dp0logs"
>>"%~dp0logs\gui-bootstrap.log" echo {"status":"FAIL","code":"PYTHON_312_X64_REQUIRED"}
echo [EGO] FAIL PYTHON_312_X64_REQUIRED. Install Python 3.12 x64 for this user.
echo [EGO] No administrator or PowerShell is required.
pause
exit /b 10
:project_python
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\gui_bootstrap.py"
goto result
:launcher_python
py -3.12-64 "%~dp0scripts\gui_bootstrap.py"
goto result
:system_python
python "%~dp0scripts\gui_bootstrap.py"
:result
if errorlevel 1 goto failed
exit /b 0
:failed
set "EGO_EXIT_CODE=%errorlevel%"
echo [EGO] Control Center could not start. See logs\gui-bootstrap.log.
pause
exit /b %EGO_EXIT_CODE%
