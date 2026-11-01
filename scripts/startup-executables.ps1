# Resolve native executables even in elevated sessions with a different PATH.
function Resolve-TranslatorExecutable {
    param([ValidateSet('codex','pwsh')][string]$Name, [string]$ExplicitPath)
    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath -PathType Leaf)) { throw "Executable missing: $ExplicitPath" }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }
    $ResolvedCommand = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($ResolvedCommand -and (Test-Path -LiteralPath $ResolvedCommand.Source -PathType Leaf)) { return $ResolvedCommand.Source }
    if ($Name -eq 'pwsh') {
        $InstalledPowerShell = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
        if (Test-Path -LiteralPath $InstalledPowerShell -PathType Leaf) { return $InstalledPowerShell }
    } else {
        $InstalledGate = Get-ScheduledTask -TaskName 'Discord Translator Maintenance Gate' -ErrorAction SilentlyContinue
        foreach ($GateAction in $InstalledGate.Actions) {
            if ($GateAction.Arguments -match '--codex\s+"([^"]+)"') {
                if (Test-Path -LiteralPath $Matches[1] -PathType Leaf) { return $Matches[1] }
            }
        }
        $CodexBundleRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
        if (Test-Path -LiteralPath $CodexBundleRoot) {
            foreach ($CodexBundle in (Get-ChildItem -LiteralPath $CodexBundleRoot -Directory | Sort-Object LastWriteTime -Descending)) {
                $BundledExecutable = Join-Path $CodexBundle.FullName 'codex.exe'
                if (Test-Path -LiteralPath $BundledExecutable -PathType Leaf) { return $BundledExecutable }
            }
        }
    }
    throw "$Name executable not found. Install it or supply its full path. No PATH change is required."
}
