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
    } catch {
        return $false
    }
}
function Find-Python {
    # Prefer the real Python launcher because the Windows Store python.exe alias
    # can exist even when Python itself is not installed.
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py -and (Test-PythonCandidate $py.Source @('-3'))) {
        return @{ Command=$py.Source; Args=@('-3') }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and (Test-PythonCandidate $python.Source @())) {
        return @{ Command=$python.Source; Args=@() }
    }

    $searchRoots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python313",
        "$env:ProgramFiles\Python312",
        "$env:ProgramFiles\Python311",
        "$env:ProgramFiles\Python310"
    )
    foreach ($root in $searchRoots) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        $candidates = @()
        if (Test-Path (Join-Path $root 'python.exe')) { $candidates += Get-Item (Join-Path $root 'python.exe') }
        $candidates += Get-ChildItem $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue
        foreach ($candidate in ($candidates | Sort-Object FullName -Descending -Unique)) {
            if (Test-PythonCandidate $candidate.FullName @()) {
                return @{ Command=$candidate.FullName; Args=@() }
            }
        }
    }
    return $null
}

Require-Admin

Write-Step "Checking Python"
$pythonInfo = Find-Python
if (-not $pythonInfo) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { throw "A working Python 3 installation was not found and winget is unavailable. Install Python 3.11+ from python.org and rerun this installer." }
    Write-Host "Working Python not found. Installing Python 3.13..." -ForegroundColor Yellow
    & $winget.Source install --id Python.Python.3.13 -e --scope machine --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Machine-wide Python install returned exit code $LASTEXITCODE. Trying user scope."
        & $winget.Source install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements --silent
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    Start-Sleep -Seconds 2
    $pythonInfo = Find-Python
    if (-not $pythonInfo) {
        throw "Python installation was requested, but a working Python 3 interpreter still could not be found. Disable the Microsoft Store python App Execution Alias or install Python 3.11+ from python.org, then rerun the installer."
    }
}
Write-Host "Python: $($pythonInfo.Command) $($pythonInfo.Args -join ' ')"

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
$venvPath = Join-Path $InstallPath '.venv'
$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $pythonInfo.Command @($pythonInfo.Args) -m venv $venvPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        throw "Python was found, but creation of the SiteWatcher virtual environment failed. Interpreter: $($pythonInfo.Command)"
    }
}
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Unable to upgrade pip in the SiteWatcher Python environment." }
& $venvPython -m pip install -r (Join-Path $InstallPath 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "Unable to install SiteWatcher Python dependencies." }

Write-Step "Checking FFmpeg / FFprobe"
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
