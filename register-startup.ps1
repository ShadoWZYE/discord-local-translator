param([switch]$AtBoot, [string]$PowerShellPath)
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $ProjectDir "start.ps1"
. (Join-Path $ProjectDir 'scripts\startup-executables.ps1')
$PowerShellExe = Resolve-TranslatorExecutable -Name pwsh -ExplicitPath $PowerShellPath
$TaskName = "Discord EN-RU Translator Bot"
$TaskPython = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'
$HiddenLauncher = Join-Path $ProjectDir 'scripts\start_translator_hidden.py'

if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir ".env"))) {
    throw ".env is missing. Run .\setup.ps1 and add the Discord token first."
}

$Action = New-ScheduledTaskAction -Execute $TaskPython `
    -Argument ('"{0}" --powershell "{1}"' -f $HiddenLauncher,$PowerShellExe) `
    -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
if ($AtBoot) {
    $BootTrigger = New-ScheduledTaskTrigger -AtStartup
    $BootTrigger.Delay = 'PT45S'
    $Trigger = @($BootTrigger,$Trigger)
}
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType $(if ($AtBoot) { 'S4U' } else { 'Interactive' }) `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description $(if ($AtBoot) { 'Starts the translator and local Ollama before sign-in, with network readiness and failure recovery.' } else { 'Starts the translator and local Ollama at sign-in, with failure recovery.' }) `
    -Force -ErrorAction Stop | Out-Null

Write-Host "Startup task registered: $TaskName" -ForegroundColor Green
