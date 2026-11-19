@echo off
setlocal
cd /d "%~dp0"
set "EGO_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%EGO_PYTHON%" (
  echo [EGO] Project Python is missing. Run START_EGO_SERVER.bat to install dependencies.
  pause
  exit /b 2
)
echo [EGO] Emergency shutdown of this installation. Active generations will be lost.
choice /M "Force close EGO Core, Runtime and all EGO windows"
if errorlevel 2 exit /b 0
"%EGO_PYTHON%" "%~dp0scripts\force_close.py" --root "%~dp0." --all-guis
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
