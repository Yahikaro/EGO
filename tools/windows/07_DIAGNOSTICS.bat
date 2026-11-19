@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "EGO_ROOT=%~dp0..\.."
set "EGO_EXIT=2"
set "EGO_PUSHED=0"
echo EGO Windows Developer Toolkit
echo Native Windows BAT/cmd execution: NOT_TESTED
pushd "%EGO_ROOT%"
if errorlevel 1 goto root_error
set "EGO_PUSHED=1"
set "EGO_PYTHON=%EGO_ROOT%\.venv\Scripts\python.exe"
if not exist "%EGO_PYTHON%" goto venv_error
"%EGO_PYTHON%" -c "import sys,platform;sys.exit(0 if sys.version_info[:2] == (3,12) and sys.maxsize.bit_length() == 63 and platform.machine().lower() in ('amd64','x86_64') else 2)" >nul 2>&1
if errorlevel 1 goto python_error
"%EGO_PYTHON%" "%~dp0runner.py" diagnostics
set "EGO_EXIT=%ERRORLEVEL%"
goto finish
:venv_error
echo FAIL: Project .venv is missing. Run 01_SETUP.bat first.
set "EGO_BOOT_REASON=VENV_MISSING"
goto bootstrap_log
:python_error
echo FAIL: Python 3.12 x64 is required. Run 01_SETUP.bat after installing it.
set "EGO_BOOT_REASON=PYTHON_312_X64_MISSING_OR_INVALID"
goto bootstrap_log
:root_error
echo FAIL: Cannot open the project folder.
set "EGO_BOOT_REASON=PROJECT_ROOT_UNAVAILABLE"
goto bootstrap_log
:bootstrap_log
rem cmd-only local timestamp fallback; Python may not exist yet.
set "EGO_STAMP=%DATE%_%TIME%"
set "EGO_STAMP=%EGO_STAMP:/=-%"
set "EGO_STAMP=%EGO_STAMP:\=-%"
set "EGO_STAMP=%EGO_STAMP::=-%"
set "EGO_STAMP=%EGO_STAMP: =_%"
set "EGO_STAMP=%EGO_STAMP:,=-%"
if not exist "%EGO_ROOT%\logs" mkdir "%EGO_ROOT%\logs" >nul 2>&1
if not exist "%EGO_ROOT%\logs" goto bootstrap_log_error
set "EGO_BOOT_LOG=%EGO_ROOT%\logs\bootstrap-%~n0-%EGO_STAMP%-%RANDOM%.log"
> "%EGO_BOOT_LOG%" echo Status: FAIL
if errorlevel 1 goto bootstrap_log_error
>> "%EGO_BOOT_LOG%" echo Reason: %EGO_BOOT_REASON%
>> "%EGO_BOOT_LOG%" echo Exit code: %EGO_EXIT%
>> "%EGO_BOOT_LOG%" echo Native Windows BAT/cmd execution: NOT_TESTED
echo Bootstrap failure log saved under project logs.
goto finish
:bootstrap_log_error
echo FAIL: Could not write the bootstrap log. The original exit code is preserved.
:finish
if "%EGO_PUSHED%"=="1" popd
:paused_exit
echo Exit code: %EGO_EXIT%
echo Press any key to close this window.
pause >nul
exit /b %EGO_EXIT%
