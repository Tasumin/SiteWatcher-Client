param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [ValidateSet('Start','Stop','Restart','Status','Upgrade')]
    [string]$Action = 'Status'
)

$ErrorActionPreference = "Stop"
$RepoInstaller = "https://raw.githubusercontent.com/Tasumin/SiteWatcher-Client/main/install-sitewatcher-native.ps1"
$ServiceName = "SiteWatcherAgent"
$DefaultServerUrl = "https://monitoring.talondns.com"

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
    if (-not (Test-Path $envFile)) { return $null }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*SITEWATCH_SERVER_URL\s*=\s*(.+?)\s*$') {
            return $Matches[1].Trim().TrimEnd('/')
        }
    }
    return $null
}

function Get-InstallerServerUrl {
    param([bool]$IsNewInstall)

    if ($IsNewInstall) { return $DefaultServerUrl }

    $configured = Get-ConfiguredServerUrl
    if (-not $configured) { return $DefaultServerUrl }

    # Automatically move legacy Vercel-hosted SiteWatcher installs to the
    # production VM. Custom/non-Vercel server URLs are preserved.
    try {
        $uri = [Uri]$configured
        if ($uri.Host -like '*.vercel.app') {
            Write-Host "Migrating SiteWatcher server URL:" -ForegroundColor Yellow
            Write-Host "  $configured"
            Write-Host "  -> $DefaultServerUrl" -ForegroundColor Green
            return $DefaultServerUrl
        }
    } catch { }

    return $null
}

function Invoke-LatestInstaller {
    param([bool]$IsNewInstall = $false)

    $installer = Join-Path $env:TEMP ("install-sitewatcher-native-" + [guid]::NewGuid().ToString('N') + ".ps1")
    Invoke-WebRequest -Uri ($RepoInstaller + "?v=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile $installer -UseBasicParsing
    try {
        $serverUrl = Get-InstallerServerUrl -IsNewInstall $IsNewInstall
        $installerArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$installer,'-InstallPath',$InstallPath)
        if ($serverUrl) { $installerArgs += @('-ServerUrl',$serverUrl) }

        & powershell.exe @installerArgs
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
    Invoke-LatestInstaller -IsNewInstall $true
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
        Invoke-LatestInstaller -IsNewInstall $false
        exit 0
    }
    'Status' { }
}

$service = Get-Service -Name $ServiceName
Write-Host "SiteWatcher Agent" -ForegroundColor Cyan
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
