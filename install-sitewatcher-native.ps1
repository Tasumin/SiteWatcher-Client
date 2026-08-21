param(
    [string]$InstallPath = "C:\SiteWatcher-Agent",
    [string]$ServerUrl = "https://monitoring.talondns.com",
    [string]$AgentToken = "",
    [string]$DiscoveryCidrs = ""
)

$ErrorActionPreference = "Stop"
$InstallerBuild = "0.9.16-latest-package"
$TaskName = "SiteWatcher Agent"
$ServiceName = "SiteWatcherAgent"
$WinSwUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET4.exe"
$FfmpegZipUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

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
    if (-not (Test-Path $Wrapper)) { Invoke-WebRequest -Uri $WinSwUrl -OutFile $Wrapper -UseBasicParsing }
    if (-not (Test-Path $WrapperXml)) {
        $xml = @"
<service>
  <id>$ServiceName</id><name>SiteWatcher Agent</name><description>SiteWatcher native Windows monitoring agent</description>
  <executable>%BASE%\.venv\Scripts\python.exe</executable><arguments>-u -m sitewatch_agent.service_entry</arguments>
  <workingdirectory>%BASE%</workingdirectory><startmode>Automatic</startmode><delayedAutoStart>true</delayedAutoStart>
  <hidewindow>true</hidewindow><stoptimeout>20 sec</stoptimeout><onfailure action="restart" delay="60 sec" />
  <resetfailure>1 hour</resetfailure><logpath>%BASE%\logs</logpath><log mode="roll-by-size"><sizeThreshold>10485760</sizeThreshold><keepFiles>5</keepFiles></log>
</service>
"@
        Set-Content -Path $WrapperXml -Value $xml -Encoding UTF8
    }
    return @{ Wrapper=$Wrapper; Xml=$WrapperXml }
}
function Ensure-Ffmpeg {
    $BinDir = Join-Path $InstallPath 'bin'; $Ffmpeg = Join-Path $BinDir 'ffmpeg.exe'; $Ffprobe = Join-Path $BinDir 'ffprobe.exe'
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    if ((Test-Path $Ffmpeg) -and (Test-Path $Ffprobe)) { return $BinDir }
    Write-Host "FFmpeg/FFprobe missing; repairing RTSP runtime..." -ForegroundColor Yellow
    $SystemFfmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue; $SystemFfprobe = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if ($SystemFfmpeg -and $SystemFfprobe) { Copy-Item $SystemFfmpeg.Source $Ffmpeg -Force; Copy-Item $SystemFfprobe.Source $Ffprobe -Force }
    else {
        $TempFfmpegRoot = Join-Path $env:TEMP ("sitewatcher-ffmpeg-" + [guid]::NewGuid().ToString('N')); $TempFfmpegZip = "$TempFfmpegRoot.zip"
        try {
            New-Item -ItemType Directory -Force -Path $TempFfmpegRoot | Out-Null
            Invoke-WebRequest -Uri $FfmpegZipUrl -OutFile $TempFfmpegZip -UseBasicParsing; Expand-Archive -Path $TempFfmpegZip -DestinationPath $TempFfmpegRoot -Force
            $DownloadedFfmpeg = Get-ChildItem $TempFfmpegRoot -Filter ffmpeg.exe -Recurse | Select-Object -First 1; $DownloadedFfprobe = Get-ChildItem $TempFfmpegRoot -Filter ffprobe.exe -Recurse | Select-Object -First 1
            if (-not $DownloadedFfmpeg -or -not $DownloadedFfprobe) { throw "Downloaded FFmpeg package did not contain ffmpeg.exe and ffprobe.exe." }
            Copy-Item $DownloadedFfmpeg.FullName $Ffmpeg -Force; Copy-Item $DownloadedFfprobe.FullName $Ffprobe -Force
        } finally { Remove-Item $TempFfmpegZip -Force -ErrorAction SilentlyContinue; Remove-Item $TempFfmpegRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (-not (Test-Path $Ffmpeg) -or -not (Test-Path $Ffprobe)) { throw "Unable to restore FFmpeg/FFprobe required for RTSP monitoring." }
    return $BinDir
}
function Try-RecoverService { try { $runtime=Ensure-ServiceRuntime; $svc=Get-Service -Name $ServiceName -ErrorAction SilentlyContinue; if(-not $svc){& $runtime.Wrapper install *> $null; Start-Sleep 1}; Start-Service -Name $ServiceName -ErrorAction SilentlyContinue } catch {} }

Require-Admin
$RepoZip=Join-Path $env:TEMP "sitewatcher-client-main.zip"; $ExtractRoot=Join-Path $env:TEMP "sitewatcher-client-main"; $RepoRoot=Join-Path $ExtractRoot "SiteWatcher-Client-main"
$ExistingEnvFile=Join-Path $InstallPath '.env'; $IsUpgrade=Test-Path $ExistingEnvFile; $EnvBackup=$null

try {
    if ($IsUpgrade) {
        $EnvBackup=Join-Path $env:TEMP ("sitewatcher-env-"+[guid]::NewGuid().ToString('N')+".bak")
        Copy-Item -LiteralPath $ExistingEnvFile -Destination $EnvBackup -Force
        Write-Host "Existing .env backed up and will be preserved unchanged." -ForegroundColor DarkGray
    }

    Write-Host "SiteWatcher native installer build: $InstallerBuild" -ForegroundColor DarkGray
    if(Test-Path $RepoZip){Remove-Item $RepoZip -Force}; if(Test-Path $ExtractRoot){Remove-Item $ExtractRoot -Recurse -Force}
    Invoke-WebRequest -Uri "https://github.com/Tasumin/SiteWatcher-Client/archive/refs/heads/main.zip" -OutFile $RepoZip -UseBasicParsing; Expand-Archive $RepoZip $ExtractRoot -Force
    if(-not(Test-Path $RepoRoot)){throw "Downloaded package did not contain expected SiteWatcher-Client-main folder."}

    $ExistingEnv=@{}
    if($IsUpgrade){ foreach($line in Get-Content $ExistingEnvFile){if($line -match '^\s*([^#][^=]*)=(.*)$'){$ExistingEnv[$matches[1].Trim()]=$matches[2].Trim()}} }
    if($IsUpgrade){
        $ServerUrl=Get-OrDefault $ExistingEnv 'SITEWATCH_SERVER_URL' $ServerUrl
        $AgentToken=Get-OrDefault $ExistingEnv 'SITEWATCH_AGENT_TOKEN' $AgentToken
        $DiscoveryCidrs=Get-OrDefault $ExistingEnv 'SITEWATCH_DISCOVERY_CIDRS' (Get-OrDefault $ExistingEnv 'DISCOVERY_CIDRS' $DiscoveryCidrs)
    }
    if(-not $AgentToken){throw "Agent token is required for a new installation. Existing upgrades preserve the current token."}

    $service=Get-Service -Name $ServiceName -ErrorAction SilentlyContinue; if($service -and $service.Status -ne 'Stopped'){Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue; try{$service.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(20))}catch{}}
    New-Item -ItemType Directory -Force -Path $InstallPath|Out-Null
    $preserve=@('.env','logs','data','.venv','bin','SiteWatcherAgent.exe','SiteWatcherAgent.xml')
    Get-ChildItem $InstallPath -Force -ErrorAction SilentlyContinue|Where-Object{$preserve -notcontains $_.Name}|Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $RepoRoot -Force|ForEach-Object{if($preserve -contains $_.Name){return};$dest=Join-Path $InstallPath $_.Name;if(Test-Path $dest){Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue};Copy-Item $_.FullName $dest -Recurse -Force}

    if($IsUpgrade -and $EnvBackup){Copy-Item -LiteralPath $EnvBackup -Destination $ExistingEnvFile -Force}

    $Python=$null; foreach($candidate in @('py.exe','python.exe')){try{if($candidate -eq 'py.exe'){& $candidate -3 --version *> $null;if($LASTEXITCODE -eq 0){$Python=@($candidate,'-3');break}}else{& $candidate --version *> $null;if($LASTEXITCODE -eq 0){$Python=@($candidate);break}}}catch{}}
    if(-not $Python){$machinePython=Get-ChildItem "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue|Sort-Object FullName -Descending|Select-Object -First 1;if($machinePython){$Python=@($machinePython.FullName)}};if(-not $Python){throw "Python 3 is required but was not found."}
    $Venv=Join-Path $InstallPath '.venv';$VenvPython=Join-Path $Venv 'Scripts\python.exe';if(-not(Test-Path $VenvPython)){if($Python.Count -gt 1){& $Python[0] $Python[1] -m venv $Venv}else{& $Python[0] -m venv $Venv}}
    & $VenvPython -m pip install --disable-pip-version-check --quiet --upgrade pip; & $VenvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $InstallPath 'requirements.txt') --upgrade
    $BinDir=Ensure-Ffmpeg

    if(-not $IsUpgrade){
        $envLines=@("SITEWATCH_SERVER_URL=$($ServerUrl.TrimEnd('/'))","SITEWATCH_AGENT_TOKEN=$AgentToken");if($DiscoveryCidrs){$envLines+="SITEWATCH_DISCOVERY_CIDRS=$DiscoveryCidrs"};$envLines+="SITEWATCH_FFMPEG_DIR=$BinDir"
        Set-Content -LiteralPath $ExistingEnvFile -Value $envLines -Encoding ASCII
    } else {
        Copy-Item -LiteralPath $EnvBackup -Destination $ExistingEnvFile -Force
    }

    $runtime=Ensure-ServiceRuntime;$Wrapper=$runtime.Wrapper;$existingService=Get-Service $ServiceName -ErrorAction SilentlyContinue;if($existingService){& $Wrapper uninstall 2>$null|Out-Null;Start-Sleep 1}; & $Wrapper install;if($LASTEXITCODE -ne 0){throw "WinSW could not install the $ServiceName service."}; & $Wrapper start;Start-Sleep 3
    $installedService=Get-Service $ServiceName -ErrorAction SilentlyContinue;if(-not $installedService){throw "SiteWatcherAgent service was not created."};if($installedService.Status -ne 'Running'){Start-Service $ServiceName -ErrorAction Stop;Start-Sleep 2;$installedService=Get-Service $ServiceName}
    $version='unknown';$versionFile=Join-Path $InstallPath 'sitewatch_agent\__init__.py';if(Test-Path $versionFile){$match=Select-String $versionFile -Pattern '__version__\s*=\s*["'']([^"'']+)'|Select-Object -First 1;if($match){$version=$match.Matches[0].Groups[1].Value}}
    Write-Host "`nService: SiteWatcher Agent ($ServiceName)" -ForegroundColor Green;Write-Host "Status: $($installedService.Status)";Write-Host "Agent version: $version";Write-Host "Existing configuration preserved: $IsUpgrade";Write-Host "SiteWatcher native Windows service installed/upgraded successfully." -ForegroundColor Green
}
catch {
    if($IsUpgrade -and $EnvBackup -and (Test-Path $EnvBackup)){Copy-Item -LiteralPath $EnvBackup -Destination $ExistingEnvFile -Force -ErrorAction SilentlyContinue}
    Try-RecoverService; throw
}
finally {
    if($IsUpgrade -and $EnvBackup -and (Test-Path $EnvBackup)){Copy-Item -LiteralPath $EnvBackup -Destination $ExistingEnvFile -Force -ErrorAction SilentlyContinue}
    Remove-Item $EnvBackup -Force -ErrorAction SilentlyContinue;Remove-Item $RepoZip -Force -ErrorAction SilentlyContinue;Remove-Item $ExtractRoot -Recurse -Force -ErrorAction SilentlyContinue
}
