$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $ProjectDir "start.ps1"
$PowerShellExe = Join-Path $PSHOME "pwsh.exe"
$TaskName = "Discord EN-RU Translator Bot"

if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir ".env"))) {
    throw ".env is missing. Run .\setup.ps1 and add the Discord token first."
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Runs the local English/Russian Discord translation bot at sign-in." `
    -Force | Out-Null

Write-Host "Startup task registered: $TaskName" -ForegroundColor Green
