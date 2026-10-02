$ErrorActionPreference = "Stop"
$TaskName = "Discord EN-RU Translator Bot"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Startup task removed: $TaskName" -ForegroundColor Green
} else {
    Write-Host "Startup task is not registered."
}
