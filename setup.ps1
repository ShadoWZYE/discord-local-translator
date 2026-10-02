$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

$Python313 = py -3.13 -c "import sys; print(sys.executable)"
if (-not $Python313) {
    throw "Python 3.13 is required and was not found."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $Python313 -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install --requirement requirements.txt

$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path -LiteralPath $OllamaExe)) {
    Write-Host "Installing the local Ollama model runner..." -ForegroundColor Cyan
    winget install --exact --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
}
if (-not (Test-Path -LiteralPath $OllamaExe)) {
    throw "Ollama installation was not found at $OllamaExe"
}

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null
} catch {
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

Write-Host "Installing/verifying the quality-first local translation model..." -ForegroundColor Cyan
& $OllamaExe pull translategemma:12b

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env. Add DISCORD_TOKEN before starting the bot." -ForegroundColor Yellow
}

Write-Host "Setup complete." -ForegroundColor Green
