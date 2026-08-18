$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

docker compose --project-directory $PSScriptRoot down --remove-orphans

$stale = docker ps -a --filter "name=sitewatch-agent" --format "{{.ID}}"
if ($stale) { docker rm -f $stale | Out-Null }

docker compose --project-directory $PSScriptRoot build --no-cache
docker compose --project-directory $PSScriptRoot up -d --force-recreate --remove-orphans

docker ps --filter "name=sitewatch-agent" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"
