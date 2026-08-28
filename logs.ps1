Set-Location -LiteralPath $PSScriptRoot
$logDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $logDir)) {
    Write-Host "No log directory found at $logDir"
    exit 1
}

Write-Host "NodeVyu Agent logs:" -ForegroundColor Cyan
Get-ChildItem $logDir -Filter *.log | Sort-Object Name | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize

Write-Host "`nTailing active logs. Press Ctrl+C to stop.`n" -ForegroundColor DarkGray
Get-Content (Join-Path $logDir '*.log') -Tail 40 -Wait
