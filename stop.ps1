Set-Location -LiteralPath $PSScriptRoot
docker compose --project-directory $PSScriptRoot down --remove-orphans
