$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$listener = Get-NetTCPConnection -State Listen -LocalPort 8001 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($process.CommandLine -notmatch 'uvicorn.*deeptutor.api.main:app') { throw 'Unexpected backend process' }
    Stop-Process -Id $process.ProcessId -Force
}
Start-Process -FilePath (Join-Path $projectRoot '.venv/Scripts/python.exe') -ArgumentList '-m','uvicorn','deeptutor.api.main:app','--host','127.0.0.1','--port','8001' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot 'data/user/runtime/backend-local.log') -RedirectStandardError (Join-Path $projectRoot 'data/user/runtime/backend-local-error.log') -PassThru | Select-Object Id
