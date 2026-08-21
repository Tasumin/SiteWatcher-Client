param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [string]$ServerUrl = "https://monitoring.talondns.com",
    [string]$AgentToken = "",
    [string]$DiscoveryCidrs = ""
)

$ErrorActionPreference = "Stop"
$InstallerBuild = "0.9.12-latest-package"
$TaskName = "SiteWatcher Agent"
$ServiceName = "SiteWatcherAgent"
$WinSwUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET4.exe"

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
function Ensure-ServiceRuntime {
    $Wrapper = Join-Path $InstallPath 'SiteWatcherAgent.exe'
    $WrapperXml = Join-Path $InstallPath 'SiteWatcherAgent.xml'

    if (-not (Test-Path $Wrapper)) {
        Write-Host "WinSW wrapper missing; downloading replacement..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $WinSwUrl -OutFile $Wrapper -UseBasicParsing
    }

    if (-not (Test-Path $WrapperXml)) {
        Write-Host "WinSW configuration missing; recreating it..." -ForegroundColor Yellow
        $xml = @"
<service>
  <id>$ServiceName</id>
  <name>SiteWatcher Agent</name>
  <description>SiteWatcher native Windows monitoring agent</description>
  <executable>%BASE%\.venv\Scripts\python.exe</executable>
  <arguments>-u -m sitewatch_agent.service_entry</arguments>
  <workingdirectory>%BASE%</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <hidewindow>true</hidewindow>
  <stoptimeout>20 sec</stoptimeout>
  <onfailure action="restart" delay="60 sec" />
  <resetfailure>1 hour</resetfailure>
  <logpath>%BASE%\logs</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10485760</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
</service>
"@
        Set-Content -Path $WrapperXml -Value $xml -Encoding UTF8
    }

    return @{ Wrapper=$Wrapper; Xml=$WrapperXml }
}
function Try-RecoverService {
    try {
        $runtime = Ensure-ServiceRuntime
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc) {
            & $runtime.Wrapper install *> $null
            Start-Sleep -Seconds 1
        }
        Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    } catch {}
}

Require-Admin

$RepoZip = Join-Path $env:TEMP "sitewatcher-client-main.zip"
$ExtractRoot = Join-Path $env:TEMP "sitewatcher-client-main"
$RepoRoot = Join-Path $ExtractRoot "SiteWatcher-Client-main"

try {
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
    if (-not $DiscoveryCidrs) { $DiscoveryCidrs = Get-OrDefault $ExistingEnv 'SITEWATCH_DISCOVERY_CIDRS' '' }
    if (-not $AgentToken) { throw "Agent token is required for a new installation. Existing upgrades preserve the current token." }

    Write-Step "Stopping existing SiteWatcherAgent service"
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        try { $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20)) } catch {}
    }

    Write-Step "Installing latest agent files"
    New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null

    $preserve = @('.env','logs','data','.venv','bin','SiteWatcherAgent.exe','SiteWatcherAgent.xml')
    Get-ChildItem -LiteralPath $InstallPath -Force -ErrorAction SilentlyContinue |
        Where-Object { $preserve -notcontains $_.Name } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    Get-ChildItem -LiteralPath $RepoRoot -Force | ForEach-Object {
        if ($preserve -contains $_.Name) { return }
        $dest = Join-Path $InstallPath $_.Name
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue }
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force
    }

    $envLines = @(
        "SITEWATCH_SERVER_URL=$ServerUrl",
        "SITEWATCH_AGENT_TOKEN=$AgentToken"
    )
    if ($DiscoveryCidrs) { $envLines += "SITEWATCH_DISCOVERY_CIDRS=$DiscoveryCidrs" }
    foreach ($key in @('SITEWATCH_DISCOVERY_INTERVAL_SECONDS','SITEWATCH_SNAPSHOT_INTERVAL_SECONDS','SITEWATCH_FFMPEG_DIR')) {
        if ($ExistingEnv.ContainsKey($key) -and $ExistingEnv[$key]) { $envLines += "$key=$($ExistingEnv[$key])" }
    }
    Set-Content -Path (Join-Path $InstallPath '.env') -Value $envLines -Encoding ASCII

    Write-Step "Creating/updating Python virtual environment"
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
    if (-not $Python) {
        $machinePython = Get-ChildItem "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($machinePython) { $Python = @($machinePython.FullName) }
    }
    if (-not $Python) { throw "Python 3 is required but was not found." }

    $Venv = Join-Path $InstallPath '.venv'
    $VenvPython = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $VenvPython)) {
        if ($Python.Count -gt 1) { & $Python[0] $Python[1] -m venv $Venv } else { & $Python[0] -m venv $Venv }
    }
    if (-not (Test-Path $VenvPython)) { throw "Unable to create or locate SiteWatcher virtual environment." }

    & $VenvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Unable to update pip." }
    & $VenvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $InstallPath 'requirements.txt') --upgrade
    if ($LASTEXITCODE -ne 0) { throw "Unable to install SiteWatcher dependencies." }

    Write-Step "Installing Windows service"
    $runtime = Ensure-ServiceRuntime
    $Wrapper = $runtime.Wrapper

    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        & $Wrapper uninstall 2>$null | Out-Null
        Start-Sleep -Seconds 1
    }

    & $Wrapper install
    if ($LASTEXITCODE -ne 0) { throw "WinSW could not install the $ServiceName service." }
    & $Wrapper start
    Start-Sleep -Seconds 3

    $installedService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $installedService) { throw "SiteWatcherAgent service was not created." }
    if ($installedService.Status -ne 'Running') {
        Start-Service -Name $ServiceName -ErrorAction Stop
        Start-Sleep -Seconds 2
        $installedService = Get-Service -Name $ServiceName
    }
    if ($installedService.Status -ne 'Running') { throw "SiteWatcherAgent service did not reach Running state." }

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
}
catch {
    Write-Host "`nSiteWatcher install/upgrade failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Attempting to recover the SiteWatcherAgent service..." -ForegroundColor Yellow
    Try-RecoverService
    throw
}
finally {
    Remove-Item $RepoZip -Force -ErrorAction SilentlyContinue
    Remove-Item $ExtractRoot -Recurse -Force -ErrorAction SilentlyContinue
}
