import base64
import os
import subprocess
import uuid
from datetime import datetime, timezone

SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")


def _install_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _update_log_path() -> str:
    log_dir = os.path.join(_install_root(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "update.log")


def _append(message: str) -> None:
    try:
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(_update_log_path(), "a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def launch_self_update() -> None:
    installer_url = SERVER + "/downloads/sitewatcher-agent"
    task_name = "SiteWatcher-Agent-Update-" + uuid.uuid4().hex[:10]
    update_log = _update_log_path()
    _append(f"remote update requested; task={task_name}; installer={installer_url}")

    updater_script = f"""$ErrorActionPreference = 'Stop'
$Log = '{_ps_quote(update_log)}'
function Write-UpdateLog([string]$Message) {{
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff zzz'
  for ($attempt = 0; $attempt -lt 10; $attempt++) {{
    try {{ Add-Content -LiteralPath $Log -Value "[$stamp] $Message" -Encoding UTF8 -ErrorAction Stop; return }}
    catch [System.IO.IOException] {{ Start-Sleep -Milliseconds 250 }}
  }}
}}
Write-UpdateLog 'scheduled updater process started'
Start-Sleep -Seconds 3
$installer = Join-Path $env:TEMP 'install-sitewatcher-agent.ps1'
try {{
  Write-UpdateLog 'downloading installer'
  Invoke-WebRequest -Uri '{_ps_quote(installer_url)}' -OutFile $installer -UseBasicParsing
  Write-UpdateLog "installer downloaded: $installer"
  $installerBuild = (Select-String -Path $installer -Pattern '\\$InstallerBuild\\s*=\\s*[\"'']([^\"'']+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
  if ($installerBuild) {{ Write-UpdateLog ('installer build: ' + $installerBuild) }}
  Write-UpdateLog 'starting installer child process'
  $stdout = Join-Path $env:TEMP ('sitewatcher-update-' + [guid]::NewGuid().ToString('N') + '.out')
  $stderr = Join-Path $env:TEMP ('sitewatcher-update-' + [guid]::NewGuid().ToString('N') + '.err')
  $arguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$installer,'-ServerUrl','{_ps_quote(SERVER)}')
  $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  if (Test-Path $stdout) {{ Get-Content $stdout | ForEach-Object {{ Write-UpdateLog ('installer: ' + $_) }}; Remove-Item $stdout -Force -ErrorAction SilentlyContinue }}
  if (Test-Path $stderr) {{ Get-Content $stderr | ForEach-Object {{ Write-UpdateLog ('installer-error: ' + $_) }}; Remove-Item $stderr -Force -ErrorAction SilentlyContinue }}
  Write-UpdateLog "installer exited with code $($p.ExitCode)"
  $versionFile = 'C:\\SiteWatcher-Agent\\sitewatch_agent\\__init__.py'
  if (Test-Path $versionFile) {{ Write-UpdateLog ('installed version file: ' + ((Get-Content $versionFile -Raw).Trim())) }}
  $svc = Get-Service -Name 'SiteWatcherAgent' -ErrorAction SilentlyContinue
  if ($svc) {{ Write-UpdateLog ('service status after installer: ' + $svc.Status) }}
  if ($p.ExitCode -ne 0) {{ throw "Installer exited with code $($p.ExitCode)" }}
}} catch {{
  Write-UpdateLog ('ERROR: ' + ($_ | Out-String).Trim())
}} finally {{
  Write-UpdateLog 'scheduled updater finished'
  try {{ Unregister-ScheduledTask -TaskName '{_ps_quote(task_name)}' -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
}}
"""
    encoded = base64.b64encode(updater_script.encode("utf-16le")).decode("ascii")
    _append(f"updater command encoded; chars={len(encoded)}")

    register = f"""
$ErrorActionPreference = 'Stop'
$taskName = '{task_name}'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances Parallel -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
$running = $false
$state = ''
for ($i = 0; $i -lt 20; $i++) {{
  Start-Sleep -Milliseconds 250
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($task) {{ $state = [string]$task.State }}
  if ($state -eq 'Running') {{ $running = $true; break }}
}}
if (-not $running) {{
  $fallback = & schtasks.exe /Run /TN $taskName 2>&1
  Start-Sleep -Milliseconds 750
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($task) {{ $state = [string]$task.State }}
  if ($state -ne 'Running') {{
    $info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    $result = if ($info) {{ $info.LastTaskResult }} else {{ 'unknown' }}
    throw "Scheduled update task failed to enter Running state; state=$state; lastResult=$result; fallback=$fallback"
  }}
}}
Write-Output "started state=Running"
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", register],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    detail = ((completed.stdout or "") + " " + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        _append(f"ERROR creating or starting scheduled task: {detail or 'unknown error'}")
        raise RuntimeError(detail or "Unable to create/start update task")
    _append(f"scheduled task {task_name} verified started; {detail or 'state=Running'}")
