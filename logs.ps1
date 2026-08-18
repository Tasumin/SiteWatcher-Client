Set-Location -LiteralPath $PSScriptRoot
docker compose --project-directory $PSScriptRoot logs -f --tail=100 sitewatch-agent
