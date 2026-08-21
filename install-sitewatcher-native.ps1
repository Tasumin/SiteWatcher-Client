param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [string]$ServerUrl = "https://monitoring.talondns.com",
    [string]$AgentToken = "",
    [string]$DiscoveryCidrs = ""
)

$ErrorActionPreference = "Stop"
$InstallerBuild = "0.9.11-latest-package"
$TaskName = "SiteWatcher Agent"
$ServiceName = "SiteWatcherAgent"

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Get-OrDefault($table, $key, $default) {
    if ($table.ContainsKey($key) -and $table[$key]) { return $table[$key] }
    return $default
}
function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "Administrator rights are required. Relaunching elevated..." -ForegroundColor Yellow
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallPath',('"' + $InstallPath + '"'))
        if ($ServerUrl) { $args += @('-ServerUrl',('"' + $ServerUrl + '"')) }
        if ($AgentToken) { $args += @('-AgentToken',('"' + $AgentToken + '"')) }
        if ($DiscoveryCidrs) { $args += @('-DiscoveryCidrs',('"' + $DiscoveryCidrs + '"')) }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $args
        exit
    }
}

Require-Admin

$RepoZip = Join-Path $env:TEMP "sitewatcher-client-main.zip"
$ExtractRoot = Join-Path $env:TEMP "sitewatcher-client-main"
$RepoRoot = Join-Path $ExtractRoot "SiteWatcher-Client-main"

Write-Host "SiteWatcher native installer build: $InstallerBuild" -ForegroundColor DarkGray
Write-Step "Downloading latest SiteWatcher client package"
if (Test-Path $RepoZip) { Remove-Item $RepoZip -Force }
if (Test-Path $ExtractRoot) { Remove-Item $ExtractRoot -Recurse -Force }
Invoke-WebRequest -Uri "https://github.com/Tasumin/SiteWatcher-Client/archive/refs/heads/main.zip" -OutFile $RepoZip -UseBasicParsing
Expand-Archive -Path $RepoZip -DestinationPath $ExtractRoot -Force

if (-not (Test-Path $RepoRoot)) { throw "Downloaded package did not contain expected SiteWatcher-Client-main folder." }

Write-Step "Preserving existing configuration"
$ExistingEnv = @{}
$ExistingEnvFile = Join-Path $InstallPath ".env"
if (Test-Path $ExistingEnvFile) {
    foreach ($line in Get-Content $ExistingEnvFile) {
        if ($line -match '^\s*([^#][^=]*)=(.*)$') { $ExistingEnv[$matches[1].Trim()] = $matches[2].Trim() }
    }
}

$ServerUrl = if ($ServerUrl) { $ServerUrl.TrimEnd('/') } else { Get-OrDefault $ExistingEnv 'SITEWATCH_SERVER_URL' 'https://monitoring.talondns.com' }
$AgentToken = if ($AgentToken) { $AgentToken } else { Get-OrDefault $ExistingEnv 'SITEWATCH_AGENT_TOKEN' '' }
$DiscoveryCidrs = if ($DiscoveryCidrs) { $DiscoveryCidrs } else { Get-OrDefault $ExistingEnv 'DISCOVERY_CIDRS' '' }
if (-not $AgentToken) { throw "Agent token is required for a new installation. Existing upgrades preserve the current token." }

Write-Step "Stopping existing SiteWatcherAgent service"
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
}

Write-Step "Installing latest agent files"
New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
$preserve = @('.env','logs')
Get-ChildItem -LiteralPath $InstallPath -Force -ErrorAction SilentlyContinue | Where-Object { $preserve -notcontains $_.Name } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $RepoRoot '*') -Destination $InstallPath -Recurse -Force

$envLines = @(
    "SITEWATCH_SERVER_URL=$ServerUrl",
    "SITEWATCH_AGENT_TOKEN=$AgentToken"
)
if ($DiscoveryCidrs) { $envLines += "DISCOVERY_CIDRS=$DiscoveryCidrs" }
Set-Content -Path (Join-Path $InstallPath '.env') -Value $envLines -Encoding ASCII

Write-Step "Creating Python virtual environment"
$Python = $null
$Candidates = @('py.exe','python.exe')
foreach ($candidate in $Candidates) {
    try {
        if ($candidate -eq 'py.exe') {
            & $candidate -3 --version *> $null
            if ($LASTEXITCODE -eq 0) { $Python = @($candidate,'-3'); break }
        } else {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) { $Python = @($candidate); break }
        }
    } catch {}
}
if (-not $Python) { throw "Python 3 is required but was not found in PATH." }

$Venv = Join-Path $InstallPath '.venv'
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    if ($Python.Count -gt 1) { & $Python[0] $Python[1] -m venv $Venv } else { & $Python[0] -m venv $Venv }
}
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
& $VenvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
& $VenvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $InstallPath 'requirements.txt')

Write-Step "Installing Windows service"
$Wrapper = Join-Path $InstallPath 'SiteWatcherAgent.exe'
$WrapperXml = Join-Path $InstallPath 'SiteWatcherAgent.xml'
if (-not (Test-Path $Wrapper)) { throw "SiteWatcherAgent.exe service wrapper is missing." }
if (-not (Test-Path $WrapperXml)) { throw "SiteWatcherAgent.xml service configuration is missing." }

& $Wrapper uninstall 2>$null | Out-Null
Start-Sleep -Seconds 1
& $Wrapper install
& $Wrapper start
Start-Sleep -Seconds 3

$installedService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $installedService) { throw "SiteWatcherAgent service was not created." }
if ($installedService.Status -ne 'Running') {
    try { Start-Service -Name $ServiceName -ErrorAction Stop } catch {}
    Start-Sleep -Seconds 2
    $installedService = Get-Service -Name $ServiceName
}

$version = 'unknown'
$versionFile = Join-Path $InstallPath 'sitewatch_agent\__init__.py'
if (Test-Path $versionFile) {
    $match = Select-String -Path $versionFile -Pattern '__version__\s*=\s*["'']([^"'']+)' | Select-Object -First 1
    if ($match -and $match.Matches.Count) { $version = $match.Matches[0].Groups[1].Value }
}

Write-Host "`nService: SiteWatcher Agent ($ServiceName)" -ForegroundColor Green
Write-Host "Status: $($installedService.Status)"
Write-Host "Agent version: $version"
Write-Host "Startup: Automatic (Delayed Start)"
Write-Host "Server: $ServerUrl"
Write-Host "Install path: $InstallPath"
Write-Host "Service logs: $InstallPath\logs"
Write-Host "`nSiteWatcher native Windows service installed/upgraded successfully. Docker is not required." -ForegroundColor Green
