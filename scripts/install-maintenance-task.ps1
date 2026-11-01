param([string]$TaskName = 'Discord Translator Maintenance Gate', [switch]$AtBoot, [string]$CodexPath,
      [string]$ReviewerTaskName = 'Discord Translator Maintenance Reviewer')
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$TaskPython = Join-Path $TaskRoot '.venv\Scripts\pythonw.exe'
. (Join-Path $PSScriptRoot 'startup-executables.ps1')
$TaskCodex = Resolve-TranslatorExecutable -Name codex -ExplicitPath $CodexPath
$TaskRunner = Join-Path $PSScriptRoot 'maintenance_hidden.py'
if (-not (Test-Path -LiteralPath $TaskPython)) { throw 'Project Python environment missing.' }
$TaskArguments = '"{0}" --codex "{1}" --reviewer-task "{2}"' -f $TaskRunner, $TaskCodex, $ReviewerTaskName
# pythonw is a GUI-subsystem executable: no console is created, even briefly.
$TaskAction = New-ScheduledTaskAction -Execute $TaskPython -Argument $TaskArguments -WorkingDirectory $TaskRoot
$TaskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
if ($AtBoot) {
    $TaskBootTrigger = New-ScheduledTaskTrigger -AtStartup
    $TaskBootTrigger.Delay = 'PT90S'
    $TaskTrigger = @($TaskTrigger,$TaskBootTrigger)
}
$TaskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType $(if ($AtBoot) { 'S4U' } else { 'Interactive' }) -RunLevel Limited
$TaskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$ReviewerAction = New-ScheduledTaskAction -Execute $TaskPython -Argument ('"{0}" --codex "{1}" --interactive-reviewer' -f $TaskRunner,$TaskCodex) -WorkingDirectory $TaskRoot
$ReviewerPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$ReviewerSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $ReviewerTaskName -Action $ReviewerAction -Principal $ReviewerPrincipal -Settings $ReviewerSettings -Description 'On-demand sandboxed reviewer in the signed-in user session; no timer and no stored password.' -Force -ErrorAction Stop | Select-Object TaskName,State
Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Trigger $TaskTrigger -Principal $TaskPrincipal -Settings $TaskSettings -Description 'Token-free five-minute boot preflight; requests the signed-in bounded reviewer only when work is due.' -Force -ErrorAction Stop | Select-Object TaskName,State
