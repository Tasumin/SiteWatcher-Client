$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "SiteWatch Agent - Windows Docker" -ForegroundColor Cyan
Write-Host "Folder: $PSScriptRoot" -ForegroundColor DarkGray
Write-Host ""

$envExample = Join-Path $PSScriptRoot ".env.example"
$envFile = Join-Path $PSScriptRoot ".env"

if (-not (Test-Path -LiteralPath $envFile)) {
    if (-not (Test-Path -LiteralPath $envExample)) {
        Write-Host "ERROR: .env.example was not found in:" -ForegroundColor Red
        Write-Host $PSScriptRoot -ForegroundColor Yellow
        exit 1
    }
    Copy-Item -LiteralPath $envExample -Destination $envFile
    Write-Host "Created .env." -ForegroundColor Green
    Write-Host "Set the SiteWatch server URL and agent token, then run again." -ForegroundColor Yellow
    Start-Process notepad.exe -ArgumentList $envFile
    exit 0
}

try { docker version | Out-Null } catch {
    Write-Host "Docker Desktop does not appear to be installed or running." -ForegroundColor Red
    exit 1
}

Write-Host "Stopping old SiteWatch compose stack..." -ForegroundColor Cyan
docker compose --project-directory $PSScriptRoot down --remove-orphans

$stale = docker ps -a --filter "name=sitewatch-agent" --format "{{.ID}} {{.Names}}"
if ($stale) {
    Write-Host "Removing stale SiteWatch containers:" -ForegroundColor Yellow
    $stale | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkYellow }
    $staleIds = $stale | ForEach-Object { ($_ -split ' ')[0] } | Where-Object { $_ }
    if ($staleIds) { docker rm -f $staleIds | Out-Null }
}

Write-Host "Building and starting SiteWatch..." -ForegroundColor Cyan
docker compose --project-directory $PSScriptRoot up -d --build --force-recreate --remove-orphans
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Running SiteWatch containers:" -ForegroundColor Green
docker ps --filter "name=sitewatch-agent" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"
Write-Host ""
Write-Host "There should be exactly ONE sitewatch-agent container." -ForegroundColor Yellow
