param(
    [string]$InstallPath = "C:\NodeVyu-Agent",
    [ValidateSet('Start','Stop','Restart','Status','Upgrade')]
    [string]$Action = 'Status'
)

$ErrorActionPreference = "Stop"
$RepoInstaller = "https://raw.githubusercontent.com/Tasumin/SiteWatcher-Client/main/install-sitewatcher-native.ps1"
$ServiceName = "NodeVyuAgent"
$LegacyServiceName = "SiteWatcherAgent"
$LegacyInstallPath = "C:\SiteWatcher-Agent"
$DefaultServerUrl = "https://nodevyu.com"

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallPath',('"' + $InstallPath + '"'),'-Action',$Action)
        Start-Process powershell.exe -Verb RunAs -ArgumentList ($args -join ' ')
        exit
    }
}

function Get-ConfiguredServerUrl {
    $envFile = Join-Path $InstallPath '.env'
    if (-not (Test-Path $envFile)) {
        $legacyEnv = Join-Path $LegacyInstallPath '.env'
        if (Test-Path $legacyEnv) { $envFile = $legacyEnv } else { return $null }
    }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*SITEWATCH_SERVER_URL\s*=\s*(.+?)\s*$') {
            return $Matches[1].Trim().TrimEnd('/')
        }
    }
    return $null
}

function Invoke-LatestInstaller {
    $installer = Join-Path $env:TEMP ("install-nodevyu-agent-" + [guid]::NewGuid().ToString('N') + ".ps1")
    Invoke-WebRequest -Uri ($RepoInstaller + "?v=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile $installer -UseBasicParsing
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -InstallPath $InstallPath
        if ($LASTEXITCODE -ne 0) { throw "NodeVyu installer failed with exit code $LASTEXITCODE." }
    } finally {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }
}

Require-Admin
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$legacyService = Get-Service -Name $LegacyServiceName -ErrorAction SilentlyContinue

if (-not $service -and $legacyService) {
    Write-Host "Legacy SiteWatcher Agent detected. Migrating it to NodeVyu Agent..." -ForegroundColor Yellow
    Invoke-LatestInstaller
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

if (-not $service) {
    Write-Host "NodeVyu Windows service is not installed. Installing it now..." -ForegroundColor Yellow
    Invoke-LatestInstaller
    exit 0
}

switch ($Action) {
    'Start' {
        if ($service.Status -ne 'Running') { Start-Service -Name $ServiceName }
    }
    'Stop' {
        if ($service.Status -ne 'Stopped') { Stop-Service -Name $ServiceName -Force }
    }
    'Restart' {
        if ($service.Status -ne 'Stopped') { Stop-Service -Name $ServiceName -Force }
        Start-Service -Name $ServiceName
    }
    'Upgrade' {
        Invoke-LatestInstaller
        exit 0
    }
    'Status' { }
}

$service = Get-Service -Name $ServiceName
Write-Host "NodeVyu Agent" -ForegroundColor Cyan
Write-Host "Service name : $ServiceName"
Write-Host "Status       : $($service.Status)" -ForegroundColor $(if ($service.Status -eq 'Running') {'Green'} else {'Yellow'})
Write-Host "Install path : $InstallPath"
Write-Host "Server URL   : $(Get-ConfiguredServerUrl)"
Write-Host "Log          : $(Join-Path $InstallPath 'logs\agent.log')"
Write-Host ""
Write-Host "Commands:"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Start"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Stop"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Restart"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Upgrade"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Status"
