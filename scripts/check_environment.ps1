$ErrorActionPreference = 'Stop'
$PythonExe = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
if (!(Test-Path $PythonExe)) { throw 'Create the Python 3.12 venv first.' }
& $PythonExe (Join-Path $PSScriptRoot 'check_environment.py')
exit $LASTEXITCODE
