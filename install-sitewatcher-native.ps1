param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [string]$ServerUrl = "",
    [string]$AgentToken = "",
    [string]$DiscoveryCidrs = ""
)

$ErrorActionPreference = "Stop"
$RepoZip = "https://github.com/Tasumin/SiteWatcher-Client/archive/refs/heads/main.zip"
$TaskName = "SiteWatcher Agent"

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
        $args = @('-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallPath',('"' + $InstallPath + '"'))
        if ($ServerUrl) { $args += @('-ServerUrl',('"' + $ServerUrl + '"')) }
        if ($AgentToken) { $args += @('-AgentToken',('"' + $AgentToken + '"')) }
        if ($DiscoveryCidrs) { $args += @('-DiscoveryCidrs',('"' + $DiscoveryCidrs + '"')) }
        Start-Process powershell.exe -Verb RunAs -ArgumentList ($args -join ' ')
        exit
    }
}
function Find-Python {
    foreach ($cmd in @('python','py')) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($c) {
            if ($cmd -eq 'py') { return @{ Command=$c.Source; Args=@('-3') } }
            return @{ Command=$c.Source; Args=@() }
        }
    }
    return $null
}

Require-Admin

Write-Step "Checking Python"
$pythonInfo = Find-Python
if (-not $pythonInfo) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw "Python 3 is not installed and winget is unavailable. Install Python 3.11+ and rerun this installer." }
    Write-Host "Python not found. Installing Python 3..." -ForegroundColor Yellow
    & winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    $pythonInfo = Find-Python
    if (-not $pythonInfo) {
        $candidate = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter python.exe -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($candidate) { $pythonInfo = @{ Command=$candidate.FullName; Args=@() } }
    }
    if (-not $pythonInfo) { throw "Python installation completed but python.exe could not be located. Reopen PowerShell and rerun the installer." }
}
Write-Host "Python: $($pythonInfo.Command)"

Write-Step "Refreshing SiteWatcher client files"
$tempRoot = Join-Path $env:TEMP ("sitewatch-native-" + [guid]::NewGuid().ToString('N'))
$zipPath = Join-Path $tempRoot "client.zip"
$extractPath = Join-Path $tempRoot "extract"
New-Item -ItemType Directory -Force -Path $tempRoot,$extractPath,$InstallPath | Out-Null

$existingEnv = Join-Path $InstallPath ".env"
$envBackup = if (Test-Path $existingEnv) { Get-Content $existingEnv -Raw } else { $null }
Invoke-WebRequest -Uri $RepoZip -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
$source = Get-ChildItem $extractPath -Directory | Select-Object -First 1
if (-not $source) { throw "Unable to extract SiteWatcher client package." }
Get-ChildItem $source.FullName -Force | ForEach-Object {
    if ($_.Name -notin @('.git','.venv','data','logs','.env')) {
        $dest = Join-Path $InstallPath $_.Name
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item $_.FullName $dest -Recurse -Force
    }
}
if ($envBackup) { Set-Content -Path $existingEnv -Value $envBackup -Encoding utf8 }
Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Step "Creating Python environment"
$venvPython = Join-Path $InstallPath ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { & $pythonInfo.Command @($pythonInfo.Args) -m venv (Join-Path $InstallPath '.venv') }
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $InstallPath 'requirements.txt')

Write-Step "Checking FFmpeg / FFprobe"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "FFmpeg not found. Installing FFmpeg for RTSP monitoring..." -ForegroundColor Yellow
        & winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements --silent
        $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
        $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
        $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    }
}
if ($ffmpeg -and $ffprobe) {
    $binDir = Join-Path $InstallPath 'bin'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    Copy-Item $ffmpeg.Source (Join-Path $binDir 'ffmpeg.exe') -Force
    Copy-Item $ffprobe.Source (Join-Path $binDir 'ffprobe.exe') -Force
    Write-Host "FFmpeg bundled into $binDir"
} else {
    Write-Warning "FFmpeg/FFprobe were not found. Ping/TCP/HTTP/HTTPS/ONVIF will work, but RTSP checks, snapshots and previews will not work until FFmpeg is installed."
}

Write-Step "Configuring SiteWatcher"
$envFile = Join-Path $InstallPath ".env"
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
if (-not $DiscoveryCidrs) { $DiscoveryCidrs = Read-Host "Discovery CIDR range(s), comma separated (example 192.168.1.0/24,192.168.4.0/24)" }
if (-not $ServerUrl -or -not $AgentToken) { throw "Server URL and agent token are required." }

$discoveryInterval = Get-OrDefault $existing 'SITEWATCH_DISCOVERY_INTERVAL_SECONDS' '900'
$snapshotInterval = Get-OrDefault $existing 'SITEWATCH_SNAPSHOT_INTERVAL_SECONDS' '300'
$config = @(
    "SITEWATCH_SERVER_URL=$($ServerUrl.TrimEnd('/'))",
    "SITEWATCH_AGENT_TOKEN=$AgentToken",
    "SITEWATCH_DISCOVERY_CIDRS=$DiscoveryCidrs",
    "SITEWATCH_DISCOVERY_INTERVAL_SECONDS=$discoveryInterval",
    "SITEWATCH_SNAPSHOT_INTERVAL_SECONDS=$snapshotInterval",
    "SITEWATCH_FFMPEG_DIR=$(Join-Path $InstallPath 'bin')"
)
Set-Content -Path $envFile -Value $config -Encoding utf8
New-Item -ItemType Directory -Force -Path (Join-Path $InstallPath 'data'),(Join-Path $InstallPath 'logs') | Out-Null

Write-Step "Registering automatic startup task"
$runner = Join-Path $InstallPath 'run-sitewatcher-native.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

Write-Step "Starting SiteWatcher Agent"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$task = Get-ScheduledTask -TaskName $TaskName
Write-Host "Task state: $($task.State)" -ForegroundColor Green
Write-Host "Install path: $InstallPath"
Write-Host "Log file: $(Join-Path $InstallPath 'logs\agent.log')"
Write-Host "`nSiteWatcher native Windows agent installed. Docker is not required." -ForegroundColor Green
