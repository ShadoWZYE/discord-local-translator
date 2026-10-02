$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "The virtual environment is missing. Run .\setup.ps1 first."
}
if (-not (Test-Path -LiteralPath ".env")) {
    throw ".env is missing. Run .\setup.ps1, then add the Discord bot token."
}

# Start the local inference service when Windows has not already started it.
$OllamaReady = $false
try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
    $OllamaReady = $true
} catch {}
if (-not $OllamaReady) {
    $OllamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $OllamaExe) {
        $OllamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    }
    if (-not (Test-Path -LiteralPath $OllamaExe)) {
        throw 'Ollama is missing. Run setup.ps1 first.'
    }
    Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        try {
            Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
            $OllamaReady = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $OllamaReady) { throw 'Ollama did not start within 30 seconds.' }
}

& ".venv\Scripts\python.exe" run.py
exit $LASTEXITCODE
