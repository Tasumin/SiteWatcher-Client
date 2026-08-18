$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    throw "Missing .env file at $envFile"
}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

$dataDir = Join-Path $PSScriptRoot "data"
$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $dataDir,$logDir | Out-Null

if (-not $env:SITEWATCH_DB) { $env:SITEWATCH_DB = Join-Path $dataDir "queue.db" }
if (-not $env:SITEWATCH_LOCK_FILE) { $env:SITEWATCH_LOCK_FILE = Join-Path $dataDir "sitewatch-agent.lock" }
if (-not $env:SITEWATCH_FFMPEG_DIR) {
    $localFfmpeg = Join-Path $PSScriptRoot "bin"
    if (Test-Path (Join-Path $localFfmpeg "ffmpeg.exe")) { $env:SITEWATCH_FFMPEG_DIR = $localFfmpeg }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python environment is missing. Run install-sitewatcher-native.ps1 first." }

$logFile = Join-Path $logDir "agent.log"
"`n===== SiteWatcher Native start $(Get-Date -Format o) =====" | Out-File -FilePath $logFile -Append -Encoding utf8

& $python -u -m sitewatch_agent.main *>> $logFile
exit $LASTEXITCODE
