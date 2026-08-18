#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Repository = "https://github.com/Tasumin/SiteWatcher-Client.git",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host "`nERROR: $Message" -ForegroundColor Red
    exit 1
}

# The install/update destination is intentionally the directory from which
# the user launched the script, not C:\Windows\System32 and not the script's
# own download/cache directory.
$TargetDir = (Get-Location).Path
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sitewatcher-client-" + [guid]::NewGuid().ToString("N"))
$CloneDir = Join-Path $TempRoot "repo"
$EnvBackup = $null

Write-Host "SiteWatcher Client Installer / Updater" -ForegroundColor Green
Write-Host "Target folder: $TargetDir"
Write-Host "Repository:    $Repository"

try {
    Write-Step "Checking Docker CLI"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "The 'docker' command was not found in PATH. Install/configure Docker, then rerun this script."
    }

    Write-Step "Checking Docker engine"
    $dockerInfo = & docker info --format '{{.ServerVersion}}' 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker is installed, but the Docker engine is not responding. Make sure Docker is running, then rerun this script.`n$($dockerInfo -join "`n")"
    }
    Write-Host "Docker engine is working. Server version: $dockerInfo" -ForegroundColor Green

    Write-Step "Checking Docker Compose"
    $composeVersion = & docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose is not available through 'docker compose'."
    }
    Write-Host "$composeVersion" -ForegroundColor Green

    Write-Step "Checking Git"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail "Git was not found in PATH. Install Git, then rerun this script."
    }
    $gitVersion = & git --version
    Write-Host "$gitVersion" -ForegroundColor Green

    # Preserve the site-specific server URL/token across a clean refresh.
    $EnvPath = Join-Path $TargetDir ".env"
    if (Test-Path $EnvPath) {
        $EnvBackup = Join-Path $TempRoot ".env"
        New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
        Copy-Item $EnvPath $EnvBackup -Force
        Write-Host "Existing .env will be preserved." -ForegroundColor Yellow
    }

    # Stop the currently installed stack before replacing compose/client files.
    $ExistingCompose = Join-Path $TargetDir "docker-compose.yml"
    if (Test-Path $ExistingCompose) {
        Write-Step "Stopping existing SiteWatcher container"
        Push-Location $TargetDir
        try {
            & docker compose down --remove-orphans
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Existing compose stack could not be stopped cleanly; continuing with refresh." -ForegroundColor Yellow
            }
        }
        finally {
            Pop-Location
        }
    }

    Write-Step "Downloading latest SiteWatcher client from GitHub"
    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    & git clone --depth 1 $Repository $CloneDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $CloneDir)) {
        Fail "Unable to clone $Repository"
    }

    Write-Step "Refreshing client files in current folder"

    # Copy the freshly cloned working tree into the current directory. Do not
    # copy Git metadata and do not overwrite the site's .env configuration.
    Get-ChildItem -LiteralPath $CloneDir -Force | ForEach-Object {
        if ($_.Name -eq ".git" -or $_.Name -eq ".env") { return }

        $destination = Join-Path $TargetDir $_.Name
        if (Test-Path $destination) {
            Remove-Item -LiteralPath $destination -Recurse -Force
        }
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Recurse -Force
    }

    if ($EnvBackup -and (Test-Path $EnvBackup)) {
        Copy-Item $EnvBackup $EnvPath -Force
    }
    elseif (-not (Test-Path $EnvPath)) {
        $example = Join-Path $TargetDir ".env.example"
        if (Test-Path $example) {
            Copy-Item $example $EnvPath
            Write-Host "Created .env from .env.example. Configure SITEWATCH_SERVER_URL and SITEWATCH_AGENT_TOKEN before production use." -ForegroundColor Yellow
        }
    }

    if ($NoStart) {
        Write-Host "`nClient refreshed successfully. Container start skipped because -NoStart was supplied." -ForegroundColor Green
        exit 0
    }

    Write-Step "Building latest SiteWatcher image"
    Push-Location $TargetDir
    try {
        & docker compose build --pull
        if ($LASTEXITCODE -ne 0) {
            Fail "Docker image build failed."
        }

        Write-Step "Starting SiteWatcher"
        & docker compose up -d --force-recreate --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Fail "Docker Compose could not start SiteWatcher."
        }

        Write-Step "Verifying container"
        & docker compose ps
        if ($LASTEXITCODE -ne 0) {
            Fail "Unable to query the SiteWatcher container after startup."
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "`nSiteWatcher client refreshed and started successfully." -ForegroundColor Green
    Write-Host "Installed in: $TargetDir"
    Write-Host "View logs with: docker compose logs -f sitewatch-agent"
}
catch {
    Fail $_.Exception.Message
}
finally {
    if (Test-Path $TempRoot) {
        Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
