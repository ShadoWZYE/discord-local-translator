param([Parameter(Mandatory=$true)][string]$CodexPath)
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
$TaskData = Join-Path $TaskRoot 'data\maintenance'
New-Item -ItemType Directory -Path $TaskData -Force | Out-Null
$TaskOutput = Join-Path $TaskData 'gate.log'
& (Join-Path $TaskRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'run_maintenance.py') --codex $CodexPath >> $TaskOutput 2>&1
exit $LASTEXITCODE
