param(
    [string]$InstallPath = "C:\SiteWatcher-Agent"
)

$ErrorActionPreference = "Stop"
$RepoInstaller = "https://raw.githubusercontent.com/Tasumin/SiteWatcher-Client/main/install-sitewatcher-native.ps1"

function Test-SiteWatcherInstall([string]$Path) {
    return (
        (Test-Path (Join-Path $Path ".env")) -and
        (Test-Path (Join-Path $Path ".venv\Scripts\python.exe")) -and
        (Test-Path (Join-Path $Path "sitewatch_agent\main.py"))
    )
}

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null

if (-not (Test-SiteWatcherInstall $InstallPath)) {
    Write-Host "SiteWatcher is not installed in $InstallPath." -ForegroundColor Yellow
    Write-Host "Downloading the latest native SiteWatcher installer..." -ForegroundColor Cyan

    # Never use a potentially stale installer sitting beside this runner.
    # Always fetch the current installer from the client repository.
    $installer = Join-Path $env:TEMP ("install-sitewatcher-native-" + [guid]::NewGuid().ToString('N') + ".ps1")
    Invoke-WebRequest -Uri ($RepoInstaller + "?v=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile $installer -UseBasicParsing

    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -InstallPath $InstallPath
        if ($LASTEXITCODE -ne 0) {
            throw "SiteWatcher installation failed with exit code $LASTEXITCODE."
        }
    } finally {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-SiteWatcherInstall $InstallPath)) {
        throw "SiteWatcher installation did not complete successfully in $InstallPath."
    }

    # The installer registers and starts the scheduled task itself. Avoid
    # launching a second foreground agent from this bootstrap invocation.
    Write-Host "SiteWatcher installation completed in $InstallPath." -ForegroundColor Green
    Write-Host "The SiteWatcher Agent scheduled task has been started." -ForegroundColor Green
    exit 0
}

Set-Location $InstallPath

$envFile = Join-Path $InstallPath ".env"
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

$dataDir = Join-Path $InstallPath "data"
$logDir = Join-Path $InstallPath "logs"
New-Item -ItemType Directory -Force -Path $dataDir,$logDir | Out-Null

if (-not $env:SITEWATCH_DB) { $env:SITEWATCH_DB = Join-Path $dataDir "queue.db" }
if (-not $env:SITEWATCH_LOCK_FILE) { $env:SITEWATCH_LOCK_FILE = Join-Path $dataDir "sitewatch-agent.lock" }
if (-not $env:SITEWATCH_FFMPEG_DIR) {
    $localFfmpeg = Join-Path $InstallPath "bin"
    if (Test-Path (Join-Path $localFfmpeg "ffmpeg.exe")) { $env:SITEWATCH_FFMPEG_DIR = $localFfmpeg }
}

$python = Join-Path $InstallPath ".venv\Scripts\python.exe"
$logFile = Join-Path $logDir "agent.log"
"`n===== SiteWatcher Native start $(Get-Date -Format o) =====" | Out-File -FilePath $logFile -Append -Encoding utf8

& $python -u -m sitewatch_agent.main *>> $logFile
exit $LASTEXITCODE
