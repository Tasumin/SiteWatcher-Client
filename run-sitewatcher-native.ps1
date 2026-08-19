param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [ValidateSet('Start','Stop','Restart','Status','Upgrade')]
    [string]$Action = 'Status'
)

$ErrorActionPreference = "Stop"
$RepoInstaller = "https://raw.githubusercontent.com/Tasumin/SiteWatcher-Client/main/install-sitewatcher-native.ps1"
$ServiceName = "SiteWatcherAgent"

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallPath',('"' + $InstallPath + '"'),'-Action',$Action)
        Start-Process powershell.exe -Verb RunAs -ArgumentList ($args -join ' ')
        exit
    }
}

function Invoke-LatestInstaller {
    $installer = Join-Path $env:TEMP ("install-sitewatcher-native-" + [guid]::NewGuid().ToString('N') + ".ps1")
    Invoke-WebRequest -Uri ($RepoInstaller + "?v=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile $installer -UseBasicParsing
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -InstallPath $InstallPath
        if ($LASTEXITCODE -ne 0) { throw "SiteWatcher installer failed with exit code $LASTEXITCODE." }
    } finally {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }
}

Require-Admin
New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

if (-not $service) {
    Write-Host "SiteWatcher Windows service is not installed. Installing it now..." -ForegroundColor Yellow
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
Write-Host "SiteWatcher Agent" -ForegroundColor Cyan
Write-Host "Service name : $ServiceName"
Write-Host "Status       : $($service.Status)" -ForegroundColor $(if ($service.Status -eq 'Running') {'Green'} else {'Yellow'})
Write-Host "Install path : $InstallPath"
Write-Host "Log          : $(Join-Path $InstallPath 'logs\agent.log')"
Write-Host ""
Write-Host "Commands:"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Start"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Stop"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Restart"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Upgrade"
Write-Host "  .\run-sitewatcher-native.ps1 -Action Status"
