param(
    [string]$InstallPath = "C:\NodeVyu-Agent",
    [string]$ServerUrl = "https://nodevyu.com",
    [string]$AgentToken = "",
    [string]$EnrollmentKey = "__SITEWATCH_ENROLLMENT_KEY__",
    [string]$DiscoveryCidrs = ""
)

$ErrorActionPreference = "Stop"
$InstallerBuild = "1.0.0-nodevyu-rebrand"
$ServiceName = "NodeVyuAgent"
$ServiceDisplayName = "NodeVyu Agent"
$LegacyServiceName = "SiteWatcherAgent"
$LegacyInstallPath = "C:\SiteWatcher-Agent"
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
        if ($EnrollmentKey -and $EnrollmentKey -ne '__SITEWATCH_ENROLLMENT_KEY__') { $args += @('-EnrollmentKey',('"' + $EnrollmentKey + '"')) }
        if ($DiscoveryCidrs) { $args += @('-DiscoveryCidrs',('"' + $DiscoveryCidrs + '"')) }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $args
        exit
    }
}
function Ensure-ServiceRuntime {
    $Wrapper = Join-Path $InstallPath 'NodeVyuAgent.exe'
    $WrapperXml = Join-Path $InstallPath 'NodeVyuAgent.xml'
    if (-not (Test-Path $Wrapper)) { Invoke-WebRequest -Uri $WinSwUrl -OutFile $Wrapper -UseBasicParsing }
    $xml = @"
<service>
  <id>$ServiceName</id><name>$ServiceDisplayName</name><description>NodeVyu native Windows monitoring agent</description>
  <executable>%BASE%\.venv\Scripts\python.exe</executable><arguments>-u -m sitewatch_agent.service_entry</arguments>
  <workingdirectory>%BASE%</workingdirectory><startmode>Automatic</startmode><delayedAutoStart>true</delayedAutoStart>
  <hidewindow>true</hidewindow><stoptimeout>20 sec</stoptimeout><onfailure action="restart" delay="60 sec" />
  <resetfailure>1 hour</resetfailure><logpath>%BASE%\logs</logpath><log mode="roll-by-size"><sizeThreshold>10485760</sizeThreshold><keepFiles>5</keepFiles></log>
</service>
"@
    Set-Content -Path $WrapperXml -Value $xml -Encoding UTF8
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
        $TempFfmpegRoot = Join-Path $env:TEMP ("nodevyu-ffmpeg-" + [guid]::NewGuid().ToString('N')); $TempFfmpegZip = "$TempFfmpegRoot.zip"
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
function Stop-And-RemoveLegacyService {
    $legacy = Get-Service -Name $LegacyServiceName -ErrorAction SilentlyContinue
    if (-not $legacy) { return }
    Write-Step "Migrating legacy SiteWatcher Windows service"
    if ($legacy.Status -ne 'Stopped') {
        Stop-Service -Name $LegacyServiceName -Force -ErrorAction SilentlyContinue
        try { $legacy.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(20)) } catch {}
    }
    $legacyWrapper = Join-Path $LegacyInstallPath 'SiteWatcherAgent.exe'
    if (Test-Path $legacyWrapper) { try { & $legacyWrapper uninstall *> $null } catch {} }
    Start-Sleep 1
    if (Get-Service -Name $LegacyServiceName -ErrorAction SilentlyContinue) {
        sc.exe delete $LegacyServiceName | Out-Null
        Start-Sleep 1
    }
}
function Try-RecoverService {
    try {
        $runtime=Ensure-ServiceRuntime
        $svc=Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if(-not $svc){& $runtime.Wrapper install *> $null; Start-Sleep 1}
        Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    } catch {}
}

Require-Admin
$RepoZip=Join-Path $env:TEMP "nodevyu-agent-main.zip"; $ExtractRoot=Join-Path $env:TEMP "nodevyu-agent-main"; $RepoRoot=Join-Path $ExtractRoot "SiteWatcher-Client-main"
$ExistingEnvFile=Join-Path $InstallPath '.env'
$LegacyEnvFile=Join-Path $LegacyInstallPath '.env'
$IsNodeVyuUpgrade=Test-Path $ExistingEnvFile
$IsLegacyMigration=(-not $IsNodeVyuUpgrade) -and (Test-Path $LegacyEnvFile)
$IsUpgrade=$IsNodeVyuUpgrade -or $IsLegacyMigration
$SourceEnvFile=if($IsNodeVyuUpgrade){$ExistingEnvFile}elseif($IsLegacyMigration){$LegacyEnvFile}else{$null}
$EnvBackup=$null

try {
    if ($IsUpgrade -and $SourceEnvFile) {
        $EnvBackup=Join-Path $env:TEMP ("nodevyu-env-"+[guid]::NewGuid().ToString('N')+".bak")
        Copy-Item -LiteralPath $SourceEnvFile -Destination $EnvBackup -Force
        Write-Host "Existing agent identity/configuration found and will be preserved." -ForegroundColor DarkGray
    }

    Write-Host "NodeVyu native installer build: $InstallerBuild" -ForegroundColor DarkGray
    if ($IsLegacyMigration) { Write-Host "Legacy SiteWatcher installation detected at $LegacyInstallPath." -ForegroundColor Yellow }
    if(Test-Path $RepoZip){Remove-Item $RepoZip -Force}; if(Test-Path $ExtractRoot){Remove-Item $ExtractRoot -Recurse -Force}
    Invoke-WebRequest -Uri "https://github.com/Tasumin/SiteWatcher-Client/archive/refs/heads/main.zip" -OutFile $RepoZip -UseBasicParsing; Expand-Archive $RepoZip $ExtractRoot -Force
    if(-not(Test-Path $RepoRoot)){throw "Downloaded package did not contain expected repository folder."}

    $ExistingEnv=@{}
    if($IsUpgrade -and $SourceEnvFile){ foreach($line in Get-Content $SourceEnvFile){if($line -match '^\s*([^#][^=]*)=(.*)$'){$ExistingEnv[$matches[1].Trim()]=$matches[2].Trim()}} }
    if($IsUpgrade){
        $ServerUrl=Get-OrDefault $ExistingEnv 'SITEWATCH_SERVER_URL' $ServerUrl
        $AgentToken=Get-OrDefault $ExistingEnv 'SITEWATCH_AGENT_TOKEN' $AgentToken
        $DiscoveryCidrs=Get-OrDefault $ExistingEnv 'SITEWATCH_DISCOVERY_CIDRS' (Get-OrDefault $ExistingEnv 'DISCOVERY_CIDRS' $DiscoveryCidrs)
    }
    if(-not $AgentToken -and -not $IsUpgrade){
        if(-not $EnrollmentKey -or $EnrollmentKey -eq '__SITEWATCH_ENROLLMENT_KEY__'){throw "No agent token was provided and this installer does not contain a NodeVyu enrollment key. Download a fresh installer from $ServerUrl/downloads or supply -AgentToken."}
        Write-Step "Enrolling this computer with NodeVyu"
        $machineGuid=''
        try{$machineGuid=[string](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid -ErrorAction Stop).MachineGuid}catch{}
        $enrollBody=@{enrollmentKey=$EnrollmentKey;hostname=$env:COMPUTERNAME;machineId=$machineGuid}|ConvertTo-Json -Compress
        try{$enrollment=Invoke-RestMethod -Method Post -Uri ($ServerUrl.TrimEnd('/')+'/api/agent/enroll') -ContentType 'application/json' -Body $enrollBody -TimeoutSec 30}catch{throw "Automatic NodeVyu enrollment failed: $($_.Exception.Message)"}
        $AgentToken=[string]$enrollment.token
        if(-not $AgentToken){throw "NodeVyu enrollment did not return an agent token."}
        if($enrollment.reused){Write-Host "Existing agent record recognized for this Windows machine; a duplicate was not created." -ForegroundColor Green}
        Write-Host "Enrolled as $($enrollment.agent.name) in $($enrollment.holding.tenant) / $($enrollment.holding.location)." -ForegroundColor Green
        Write-Host "A NodeVyu administrator can now assign this agent to its final client/location remotely." -ForegroundColor Yellow
    }
    if(-not $AgentToken){throw "Agent token is required for a new installation. Existing upgrades preserve the current token."}

    $nodeService=Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if($nodeService -and $nodeService.Status -ne 'Stopped'){Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue;try{$nodeService.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(20))}catch{}}
    if($IsLegacyMigration){Stop-And-RemoveLegacyService}

    New-Item -ItemType Directory -Force -Path $InstallPath|Out-Null
    if($IsLegacyMigration){
        foreach($legacyItem in @('data','logs','bin')){
            $src=Join-Path $LegacyInstallPath $legacyItem; $dst=Join-Path $InstallPath $legacyItem
            if((Test-Path $src) -and -not(Test-Path $dst)){Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force}
        }
    }
    $preserve=@('.env','logs','data','.venv','bin','NodeVyuAgent.exe','NodeVyuAgent.xml')
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
    $installedService=Get-Service $ServiceName -ErrorAction SilentlyContinue;if(-not $installedService){throw "NodeVyuAgent service was not created."};if($installedService.Status -ne 'Running'){Start-Service $ServiceName -ErrorAction Stop;Start-Sleep 2;$installedService=Get-Service $ServiceName}
    $version='unknown';$versionFile=Join-Path $InstallPath 'sitewatch_agent\__init__.py';if(Test-Path $versionFile){$match=Select-String $versionFile -Pattern '__version__\s*=\s*["'']([^"'']+)'|Select-Object -First 1;if($match){$version=$match.Matches[0].Groups[1].Value}}
    Write-Host "`nService: $ServiceDisplayName ($ServiceName)" -ForegroundColor Green;Write-Host "Status: $($installedService.Status)";Write-Host "Agent version: $version";Write-Host "Install path: $InstallPath";Write-Host "Existing configuration preserved: $IsUpgrade";Write-Host "NodeVyu native Windows service installed/upgraded successfully." -ForegroundColor Green
    if($IsLegacyMigration){Write-Host "Legacy SiteWatcher service removed. The old $LegacyInstallPath folder was left in place for rollback/log history." -ForegroundColor DarkGray}
}
catch {
    if($IsUpgrade -and $EnvBackup -and (Test-Path $EnvBackup)){New-Item -ItemType Directory -Force -Path $InstallPath|Out-Null;Copy-Item -LiteralPath $EnvBackup -Destination $ExistingEnvFile -Force -ErrorAction SilentlyContinue}
    Try-RecoverService; throw
}
finally {
    Remove-Item $EnvBackup -Force -ErrorAction SilentlyContinue;Remove-Item $RepoZip -Force -ErrorAction SilentlyContinue;Remove-Item $ExtractRoot -Recurse -Force -ErrorAction SilentlyContinue
}
