param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [string]$ServerUrl = "https://monitoring.talondns.com",
    [string]$AgentToken = "",
    [string]$DiscoveryCidrs = ""
)

$ErrorActionPreference = "Stop"
$InstallerBuild = "0.9.1-latest-package"
$TaskName = "SiteWatcher Agent"
$ServiceName = "SiteWatcherAgent"

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Get-OrDefault($table, $key, $default) {
    if ($table.ContainsKey($key) -and $table[$key]) { return $table[$key] }
    return $default
}
function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object System.Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "Administrator rights are required. Relaunching elevated..." -ForegroundColor Yellow
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallPath',('"' + $InstallPath + '"'))
        if ($ServerUrl) { $args += @('-ServerUrl',('"' + $ServerUrl + '"')) }
        if ($AgentToken) { $args += @('-AgentToken',('"' + $AgentToken + '"')) }
        if ($DiscoveryCidrs) { $args += @('-DiscoveryCidrs',('"' + $DiscoveryCidrs + '"')) }
        Start-Process powershell.exe -Verb RunAs -ArgumentList ($args -join ' ')
        exit
    }
}
function Test-PythonCandidate($command, $args) {
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $command
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $psi.Arguments = (($args + @('--version')) -join ' ')
        $p = New-Object System.Diagnostics.Process
        $p.StartInfo = $psi
        [void]$p.Start()
        if (-not $p.WaitForExit(10000)) { try { $p.Kill() } catch {}; return $false }
        $output = (($p.StandardOutput.ReadToEnd()) + ' ' + ($p.StandardError.ReadToEnd())).Trim()
        return ($p.ExitCode -eq 0 -and $output -match 'Python\s+3\.')
    } catch { return $false }
}
function Find-Python {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py -and (Test-PythonCandidate $py.Source @('-3'))) { return @{ Command=$py.Source; Args=@('-3') } }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and (Test-PythonCandidate $python.Source @())) { return @{ Command=$python.Source; Args=@() } }
    $roots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python313",
        "$env:ProgramFiles\Python312",
        "$env:ProgramFiles\Python311",
        "$env:ProgramFiles\Python310"
    )
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        $items = @()
        if (Test-Path (Join-Path $root 'python.exe')) { $items += Get-Item (Join-Path $root 'python.exe') }
        $items += Get-ChildItem $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue
        foreach ($item in ($items | Sort-Object FullName -Descending -Unique)) {
            if (Test-PythonCandidate $item.FullName @()) { return @{ Command=$item.FullName; Args=@() } }
        }
    }
    return $null
}
function Remove-ExistingService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { return }
    if ($svc.Status -ne 'Stopped') {
        Write-Host "Stopping existing $ServiceName service..." -ForegroundColor Yellow
        try { Stop-Service -Name $ServiceName -Force -ErrorAction Stop } catch { & sc.exe stop $ServiceName | Out-Null }
        Start-Sleep -Seconds 2
    }
    Write-Host "Removing existing $ServiceName service definition..." -ForegroundColor Yellow
    & sc.exe delete $ServiceName | Out-Null
    for ($i=0; $i -lt 20; $i++) {
        if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
}
function Show-ServiceDiagnostics {
    Write-Host "`n--- SiteWatcher service diagnostics ---" -ForegroundColor Yellow
    & sc.exe queryex $ServiceName 2>&1 | ForEach-Object { Write-Host $_ }
    Get-ChildItem (Join-Path $InstallPath 'logs') -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 6 |
        ForEach-Object {
            Write-Host "`n--- $($_.FullName) ---" -ForegroundColor Yellow
            Get-Content $_.FullName -Tail 80 -ErrorAction SilentlyContinue
        }
}

Require-Admin
Write-Host "SiteWatcher native installer build: $InstallerBuild" -ForegroundColor DarkGray
New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null

$oldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($oldTask) {
    Write-Step "Removing legacy scheduled task"
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Remove-ExistingService

Write-Step "Checking Python"
$pythonInfo = Find-Python
if (-not $pythonInfo) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { throw "A working Python 3 installation was not found and winget is unavailable. Install Python 3.11+ and rerun this installer." }
    Write-Host "Working Python not found. Installing Python 3.13..." -ForegroundColor Yellow
    & $winget.Source install --id Python.Python.3.13 -e --scope machine --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        & $winget.Source install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements --silent
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    Start-Sleep -Seconds 2
    $pythonInfo = Find-Python
    if (-not $pythonInfo) { throw "Python installation completed but no working Python 3 interpreter could be found." }
}
Write-Host "Python: $($pythonInfo.Command) $($pythonInfo.Args -join ' ')"

Write-Step "Refreshing SiteWatcher client files"
$tempRoot = Join-Path $env:TEMP ("sitewatch-native-" + [guid]::NewGuid().ToString('N'))
$zipPath = Join-Path $tempRoot 'client.zip'
$extractPath = Join-Path $tempRoot 'extract'
New-Item -ItemType Directory -Force -Path $tempRoot,$extractPath | Out-Null
$envFile = Join-Path $InstallPath '.env'
$envBackup = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { $null }

$packageUrl = $ServerUrl.TrimEnd('/') + "/downloads/sitewatcher-client?v=" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
Write-Host "Downloading latest client package from $($ServerUrl.TrimEnd('/'))..."
$response = Invoke-WebRequest -Uri $packageUrl -OutFile $zipPath -UseBasicParsing -Headers @{ "Cache-Control"="no-cache"; "Pragma"="no-cache" } -PassThru
$clientCommit = $response.Headers['X-SiteWatcher-Client-Commit']
if ($clientCommit) { Write-Host "Client commit: $clientCommit" -ForegroundColor DarkGray }

Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
$source = Get-ChildItem $extractPath -Directory | Select-Object -First 1
if (-not $source) { throw "Unable to extract SiteWatcher client package." }
Get-ChildItem $source.FullName -Force | ForEach-Object {
    if ($_.Name -notin @('.git','.venv','data','logs','.env','bin','SiteWatcherAgent.exe','SiteWatcherAgent.xml')) {
        $dest = Join-Path $InstallPath $_.Name
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item $_.FullName $dest -Recurse -Force
    }
}
if ($envBackup) { Set-Content -Path $envFile -Value $envBackup -Encoding utf8 }
Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Step "Creating/updating Python environment"
$venvPath = Join-Path $InstallPath '.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & $pythonInfo.Command @($pythonInfo.Args) -m venv $venvPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { throw "Unable to create the SiteWatcher Python environment." }
}
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Unable to upgrade pip." }
& $venvPython -m pip install -r (Join-Path $InstallPath 'requirements.txt') --upgrade
if ($LASTEXITCODE -ne 0) { throw "Unable to install SiteWatcher dependencies." }

Write-Step "Checking FFmpeg / FFprobe"
$binDir = Join-Path $InstallPath 'bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$ffmpegLocal = Join-Path $binDir 'ffmpeg.exe'
$ffprobeLocal = Join-Path $binDir 'ffprobe.exe'
if (-not ((Test-Path $ffmpegLocal) -and (Test-Path $ffprobeLocal))) {
    $ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    $ffprobe = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if (-not $ffmpeg -or -not $ffprobe) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "FFmpeg not found. Installing FFmpeg for RTSP monitoring..." -ForegroundColor Yellow
            & $winget.Source install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements --silent
            $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
            $ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
            $ffprobe = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
        }
    }
    if ($ffmpeg -and $ffprobe) {
        Copy-Item $ffmpeg.Source $ffmpegLocal -Force
        Copy-Item $ffprobe.Source $ffprobeLocal -Force
    } else {
        Write-Warning "FFmpeg/FFprobe were not found. RTSP checks and previews will be unavailable."
    }
}

Write-Step "Configuring SiteWatcher"
$existing = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $parts = $line -split '=',2
            if ($parts.Count -eq 2) { $existing[$parts[0].Trim()] = $parts[1].Trim() }
        }
    }
}
if (-not $ServerUrl) { $ServerUrl = $existing['SITEWATCH_SERVER_URL'] }
if (-not $AgentToken) { $AgentToken = $existing['SITEWATCH_AGENT_TOKEN'] }
if (-not $DiscoveryCidrs) { $DiscoveryCidrs = $existing['SITEWATCH_DISCOVERY_CIDRS'] }
if (-not $ServerUrl) { $ServerUrl = Read-Host "SiteWatcher server URL (https://...)" }
if (-not $AgentToken) { $AgentToken = Read-Host "SiteWatcher agent token" }
if (-not $DiscoveryCidrs) { $DiscoveryCidrs = Read-Host "Discovery CIDR range(s), comma separated" }
if (-not $ServerUrl -or -not $AgentToken) { throw "Server URL and agent token are required." }
$config = @(
    "SITEWATCH_SERVER_URL=$($ServerUrl.TrimEnd('/'))",
    "SITEWATCH_AGENT_TOKEN=$AgentToken",
    "SITEWATCH_DISCOVERY_CIDRS=$DiscoveryCidrs",
    "SITEWATCH_DISCOVERY_INTERVAL_SECONDS=$(Get-OrDefault $existing 'SITEWATCH_DISCOVERY_INTERVAL_SECONDS' '900')",
    "SITEWATCH_SNAPSHOT_INTERVAL_SECONDS=$(Get-OrDefault $existing 'SITEWATCH_SNAPSHOT_INTERVAL_SECONDS' '300')",
    "SITEWATCH_FFMPEG_DIR=$binDir"
)
Set-Content -Path $envFile -Value $config -Encoding utf8
New-Item -ItemType Directory -Force -Path (Join-Path $InstallPath 'data'),(Join-Path $InstallPath 'logs') | Out-Null

Write-Step "Installing WinSW service wrapper"
$wrapper = Join-Path $InstallPath 'SiteWatcherAgent.exe'
$wrapperConfig = Join-Path $InstallPath 'SiteWatcherAgent.xml'
$winSwUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET4.exe"
Invoke-WebRequest -Uri $winSwUrl -OutFile $wrapper -UseBasicParsing

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
Set-Content -Path $wrapperConfig -Value $xml -Encoding utf8

& $wrapper install
if ($LASTEXITCODE -ne 0) { throw "WinSW could not install the $ServiceName service." }

Write-Step "Starting SiteWatcher Agent service"
try {
    & $wrapper start
    if ($LASTEXITCODE -ne 0) { throw "WinSW start returned exit code $LASTEXITCODE" }
    $svc = Get-Service -Name $ServiceName -ErrorAction Stop
    $svc.WaitForStatus('Running', (New-TimeSpan -Seconds 30))
} catch {
    Show-ServiceDiagnostics
    throw "SiteWatcherAgent was installed but failed to start: $($_.Exception.Message)"
}

$svc = Get-Service -Name $ServiceName
$installedVersion = "unknown"
$versionFile = Join-Path $InstallPath 'sitewatch_agent\__init__.py'
if (Test-Path $versionFile) {
    $match = Select-String -Path $versionFile -Pattern '__version__\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
    if ($match -and $match.Matches.Count) { $installedVersion = $match.Matches[0].Groups[1].Value }
}
Write-Host "Service: $($svc.DisplayName) ($ServiceName)" -ForegroundColor Green
Write-Host "Status: $($svc.Status)" -ForegroundColor Green
Write-Host "Agent version: $installedVersion" -ForegroundColor Green
Write-Host "Startup: Automatic (Delayed Start)"
Write-Host "Server: $ServerUrl"
Write-Host "Install path: $InstallPath"
Write-Host "Service logs: $(Join-Path $InstallPath 'logs')"
Write-Host "`nSiteWatcher native Windows service installed/upgraded successfully. Docker is not required." -ForegroundColor Green
