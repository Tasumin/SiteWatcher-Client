param(
    [string]$InstallPath = "C:\NodeVyu-Agent",
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"
$ServiceName = "NodeVyuAgent"
$LegacyServiceName = "SiteWatcherAgent"
$LegacyInstallPath = "C:\SiteWatcher-Agent"

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "Administrator rights are required. Relaunching elevated..." -ForegroundColor Yellow
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallPath',('"' + $InstallPath + '"'))
        if ($KeepData) { $args += '-KeepData' }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $args
        exit
    }
}

function Remove-AgentService([string]$Name,[string]$Path,[string]$WrapperName) {
    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Stopped') {
        Write-Host "Stopping $Name..."
        Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        try { $svc.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(20)) } catch {}
    }

    $wrapper = Join-Path $Path $WrapperName
    if (Test-Path $wrapper) {
        try { & $wrapper uninstall *> $null } catch {}
        Start-Sleep 1
    }

    if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
        sc.exe delete $Name | Out-Null
        Start-Sleep 1
    }
}

Require-Admin

Write-Host "`nNodeVyu Agent Uninstaller" -ForegroundColor Cyan
Write-Host "Install path: $InstallPath"

Remove-AgentService $ServiceName $InstallPath 'NodeVyuAgent.exe'
Remove-AgentService $LegacyServiceName $LegacyInstallPath 'SiteWatcherAgent.exe'

if (Test-Path $InstallPath) {
    if ($KeepData) {
        Write-Host "Removing program files while preserving .env, data, and logs..."
        $preserve = @('.env','data','logs')
        Get-ChildItem -LiteralPath $InstallPath -Force -ErrorAction SilentlyContinue |
            Where-Object { $preserve -notcontains $_.Name } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "Removing NodeVyu Agent files..."
        Remove-Item -LiteralPath $InstallPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ((Test-Path $LegacyInstallPath) -and $LegacyInstallPath -ne $InstallPath) {
    Remove-Item -LiteralPath $LegacyInstallPath -Recurse -Force -ErrorAction SilentlyContinue
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw "The NodeVyuAgent Windows service could not be removed. Restart Windows and run the uninstaller again."
}

Write-Host "`nNodeVyu Agent was uninstalled successfully." -ForegroundColor Green
if ($KeepData) { Write-Host "Configuration, data, and logs were preserved at $InstallPath." -ForegroundColor Yellow }
