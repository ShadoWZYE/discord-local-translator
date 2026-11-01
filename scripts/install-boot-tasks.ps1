param([string]$CodexPath, [string]$PowerShellPath)
$ErrorActionPreference = 'Stop'
$BootRoot = Split-Path -Parent $PSScriptRoot
$BootIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($BootIdentity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Boot-task registration requires PowerShell opened as Administrator under the translator account. No tasks were changed.'
}
foreach ($BootFile in @('.env','.venv\Scripts\pythonw.exe','scripts\start_translator_hidden.py','scripts\maintenance_hidden.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $BootRoot $BootFile))) { throw "Required startup file missing: $BootFile" }
}
. (Join-Path $PSScriptRoot 'startup-executables.ps1')
$BootCodex = Resolve-TranslatorExecutable -Name codex -ExplicitPath $CodexPath
$BootPowerShell = Resolve-TranslatorExecutable -Name pwsh -ExplicitPath $PowerShellPath
$BootTaskNames = @('Discord EN-RU Translator Bot','Discord Translator Maintenance Gate')
$BootBackups = @{}
foreach ($BootTaskName in ($BootTaskNames + 'Discord Translator Maintenance Reviewer')) {
    if (Get-ScheduledTask -TaskName $BootTaskName -ErrorAction SilentlyContinue) {
        $BootBackups[$BootTaskName] = Export-ScheduledTask -TaskName $BootTaskName
    }
}
try {
    & (Join-Path $BootRoot 'register-startup.ps1') -AtBoot -PowerShellPath $BootPowerShell
    & (Join-Path $PSScriptRoot 'install-maintenance-task.ps1') -AtBoot -CodexPath $BootCodex
} catch {
    foreach ($BootTaskName in $BootBackups.Keys) {
        Register-ScheduledTask -TaskName $BootTaskName -Xml $BootBackups[$BootTaskName] -Force -ErrorAction Stop | Out-Null
    }
    throw
}
foreach ($BootTaskName in $BootTaskNames) {
    $BootTask = Get-ScheduledTask -TaskName $BootTaskName
    if ($BootTask.Principal.LogonType -ne 'S4U' -or -not ($BootTask.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' })) {
        throw "Boot task verification failed: $BootTaskName"
    }
    [pscustomobject]@{ TaskName=$BootTaskName; State=$BootTask.State; LogonType=$BootTask.Principal.LogonType; Startup=$true }
}
Write-Host 'Boot tasks installed without storing a Windows password. The translator starts Ollama if needed. No running bot was restarted.'
