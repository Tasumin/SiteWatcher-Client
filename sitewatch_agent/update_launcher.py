import os
import subprocess
import time
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


def _task_started_marker(task_name: str) -> str:
    return f"scheduled updater process started task={task_name}"


def _write_updater_script(task_name: str, installer_url: str, update_log: str) -> str:
    update_root = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "SiteWatcher", "updates")
    os.makedirs(update_root, exist_ok=True)
    updater_path = os.path.join(update_root, f"{task_name}.ps1")

    updater_script = f"""$ErrorActionPreference = 'Stop'
$Log = '{_ps_quote(update_log)}'
$UpdaterScript = '{_ps_quote(updater_path)}'
function Write-UpdateLog([string]$Message) {{
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff zzz'
  for ($attempt = 0; $attempt -lt 10; $attempt++) {{
    try {{ Add-Content -LiteralPath $Log -Value "[$stamp] $Message" -Encoding UTF8 -ErrorAction Stop; return }}
    catch [System.IO.IOException] {{ Start-Sleep -Milliseconds 250 }}
  }}
}}
Write-UpdateLog '{_ps_quote(_task_started_marker(task_name))}'
Start-Sleep -Seconds 2
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
  if ($svc) {{ Write-UpdateLog ('service status after installer: ' + $svc.Status) }} else {{ Write-UpdateLog 'service missing after installer' }}
  if ($p.ExitCode -ne 0) {{ throw "Installer exited with code $($p.ExitCode)" }}
}} catch {{
  Write-UpdateLog ('ERROR: ' + ($_ | Out-String).Trim())
}} finally {{
  Write-UpdateLog 'scheduled updater finished'
  try {{ Unregister-ScheduledTask -TaskName '{_ps_quote(task_name)}' -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
  try {{ Remove-Item -LiteralPath $UpdaterScript -Force -ErrorAction SilentlyContinue }} catch {{}}
}}
"""
    with open(updater_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(updater_script)
    return updater_path


def _read_update_log() -> str:
    try:
        with open(_update_log_path(), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()[-100000:]
    except Exception:
        return ""


def launch_self_update() -> None:
    installer_url = SERVER + "/downloads/sitewatcher-agent"
    task_name = "SiteWatcher-Agent-Update-" + uuid.uuid4().hex[:10]
    update_log = _update_log_path()
    marker = _task_started_marker(task_name)
    _append(f"remote update requested; task={task_name}; installer={installer_url}")

    updater_path = _write_updater_script(task_name, installer_url, update_log)
    _append(f"updater script written: {updater_path}")

    register = f"""
$ErrorActionPreference = 'Stop'
$taskName = '{_ps_quote(task_name)}'
$scriptPath = '{_ps_quote(updater_path)}'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $scriptPath + '"')
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances Parallel -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'registered'
"""
    try:
        registered = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", register],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while registering SiteWatcher update task") from exc

    detail = ((registered.stdout or "") + " " + (registered.stderr or "")).strip()
    if registered.returncode != 0:
        _append(f"ERROR registering scheduled task: {detail or 'unknown error'}")
        raise RuntimeError(detail or "Unable to register update task")
    _append(f"scheduled task registered: {task_name}")

    try:
        started = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", task_name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while requesting SiteWatcher update task start") from exc

    start_detail = ((started.stdout or "") + " " + (started.stderr or "")).strip()
    if started.returncode != 0:
        _append(f"ERROR starting scheduled task: {start_detail or 'unknown error'}")
        raise RuntimeError(start_detail or "Unable to start update task")
    _append(f"scheduled task run requested: {start_detail or task_name}")

    # Verify actual script execution rather than trusting the transient Task Scheduler state.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if marker in _read_update_log():
            _append(f"scheduled updater execution verified: task={task_name}")
            return
        time.sleep(0.25)

    diagnostic = f"""
$task = Get-ScheduledTask -TaskName '{_ps_quote(task_name)}' -ErrorAction SilentlyContinue
$info = Get-ScheduledTaskInfo -TaskName '{_ps_quote(task_name)}' -ErrorAction SilentlyContinue
if ($task) {{ Write-Output ('state=' + [string]$task.State) }} else {{ Write-Output 'state=missing' }}
if ($info) {{ Write-Output ('lastResult=' + [string]$info.LastTaskResult); Write-Output ('lastRun=' + [string]$info.LastRunTime) }}
"""
    try:
        diag = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", diagnostic],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        diag_detail = ((diag.stdout or "") + " " + (diag.stderr or "")).strip()
    except Exception as exc:
        diag_detail = str(exc)

    _append(f"ERROR updater did not execute within verification window: {diag_detail}")
    raise RuntimeError(f"Update task was requested but updater did not execute. {diag_detail}".strip())
