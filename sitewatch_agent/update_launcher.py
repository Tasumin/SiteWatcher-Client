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


def _started_marker(update_id: str) -> str:
    return f"detached updater process started id={update_id}"


def _write_updater_script(update_id: str, installer_url: str, update_log: str) -> str:
    update_root = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "NodeVyu", "updates")
    os.makedirs(update_root, exist_ok=True)
    updater_path = os.path.join(update_root, f"NodeVyu-Agent-Update-{update_id}.ps1")

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
Write-UpdateLog '{_ps_quote(_started_marker(update_id))}'
Start-Sleep -Seconds 2
$installer = Join-Path $env:TEMP 'install-nodevyu-agent.ps1'
try {{
  Write-UpdateLog 'downloading NodeVyu installer'
  Invoke-WebRequest -Uri '{_ps_quote(installer_url)}' -OutFile $installer -UseBasicParsing
  Write-UpdateLog "installer downloaded: $installer"
  $installerBuild = (Select-String -Path $installer -Pattern '\\$InstallerBuild\\s*=\\s*[\"'']([^\"'']+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
  if ($installerBuild) {{ Write-UpdateLog ('installer build: ' + $installerBuild) }}
  $stdout = Join-Path $env:TEMP ('nodevyu-update-' + [guid]::NewGuid().ToString('N') + '.out')
  $stderr = Join-Path $env:TEMP ('nodevyu-update-' + [guid]::NewGuid().ToString('N') + '.err')
  $arguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$installer,'-ServerUrl','{_ps_quote(SERVER)}')
  $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  if (Test-Path $stdout) {{ Get-Content $stdout | ForEach-Object {{ Write-UpdateLog ('installer: ' + $_) }}; Remove-Item $stdout -Force -ErrorAction SilentlyContinue }}
  if (Test-Path $stderr) {{ Get-Content $stderr | ForEach-Object {{ Write-UpdateLog ('installer-error: ' + $_) }}; Remove-Item $stderr -Force -ErrorAction SilentlyContinue }}
  Write-UpdateLog "installer exited with code $($p.ExitCode)"
  $versionFile = 'C:\\NodeVyu-Agent\\sitewatch_agent\\__init__.py'
  if (Test-Path $versionFile) {{ Write-UpdateLog ('installed version file: ' + ((Get-Content $versionFile -Raw).Trim())) }}
  $svc = Get-Service -Name 'NodeVyuAgent' -ErrorAction SilentlyContinue
  if ($svc) {{ Write-UpdateLog ('service status after installer: ' + $svc.Status) }} else {{ Write-UpdateLog 'NodeVyu service missing after installer' }}
  if ($p.ExitCode -ne 0) {{ throw "Installer exited with code $($p.ExitCode)" }}
}} catch {{
  Write-UpdateLog ('ERROR: ' + ($_ | Out-String).Trim())
}} finally {{
  Write-UpdateLog 'detached updater finished'
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
    """Launch the updater outside the WinSW service process tree via WMI/CIM."""
    # Keep the legacy download route during the rebrand so older servers and
    # existing agents can transition without a coordinated cutover.
    installer_url = SERVER + "/downloads/sitewatcher-agent"
    update_id = uuid.uuid4().hex[:10]
    update_log = _update_log_path()
    marker = _started_marker(update_id)
    _append(f"remote NodeVyu update requested; id={update_id}; installer={installer_url}")

    updater_path = _write_updater_script(update_id, installer_url, update_log)
    _append(f"updater script written: {updater_path}")

    launch = f"""
$ErrorActionPreference = 'Stop'
$scriptPath = '{_ps_quote(updater_path)}'
$commandLine = 'powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{ CommandLine = $commandLine }}
if (-not $result) {{ throw 'Win32_Process.Create returned no result' }}
if ([int]$result.ReturnValue -ne 0) {{ throw ('Win32_Process.Create failed with return value ' + $result.ReturnValue) }}
Write-Output ('created pid=' + $result.ProcessId)
"""
    try:
        created = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", launch],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        _append("ERROR WMI launcher timed out")
        raise RuntimeError("Timed out while creating detached NodeVyu updater process") from exc

    detail = ((created.stdout or "") + " " + (created.stderr or "")).strip()
    if created.returncode != 0:
        _append(f"ERROR creating detached updater process: {detail or 'unknown error'}")
        raise RuntimeError(detail or "Unable to create detached NodeVyu updater process")
    _append(f"detached updater requested: {detail or update_id}")

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if marker in _read_update_log():
            _append(f"detached updater execution verified: id={update_id}")
            return
        time.sleep(0.25)

    _append(f"ERROR detached updater did not execute within verification window: id={update_id}; {detail}")
    raise RuntimeError(f"Detached updater process was created but did not execute within verification window. {detail}".strip())
