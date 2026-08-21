import base64
import concurrent.futures
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone

import requests

from .agent_logs import collect_agent_logs


SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
TOKEN = os.environ["SITEWATCH_AGENT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
UPDATE_COMMAND = "__SITEWATCH_UPDATE_AGENT__"
SCAN_PREFIX = "__SITEWATCH_IP_SCAN__|"
LOG_PREFIX = "__SITEWATCH_GET_LOGS__|"
SCAN_PORTS = (22, 53, 80, 443, 554, 8000, 8080, 9000)

BLOCKED_TOKENS = (";", "&&", "||", "|", ">", "<", "`", "$(", "@(")
ALLOWED_PREFIXES = (
    "ping ", "ping.exe ", "tracert ", "tracert.exe ", "pathping ", "pathping.exe ",
    "nslookup ", "nslookup.exe ", "curl ", "curl.exe ", "arp ", "arp.exe ",
    "ipconfig", "route print", "route.exe print", "netstat ", "netstat.exe ",
    "test-netconnection ", "resolve-dnsname ", "get-netipaddress", "get-netroute",
    "get-netadapter", "get-nettcpconnection", "get-netneighbor", "get-dnsclient",
    "get-dnsclientserveraddress", "invoke-webrequest ", "invoke-restmethod "
)


def _allowed(command: str) -> tuple[bool, str]:
    text = command.strip()
    if not text:
        return False, "Command is empty."
    lower = text.lower()
    if any(token in text for token in BLOCKED_TOKENS):
        return False, "Command chaining, pipelines, redirection, and subexpressions are disabled in Remote Console."
    if "\n" in text or "\r" in text:
        return False, "Only one diagnostic command may be run at a time."
    if not any(lower == prefix.strip() or lower.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return False, "Command is not in the SiteWatcher diagnostic allowlist."
    return True, ""


def _run(command: str, shell: str, timeout_seconds: int = 60):
    ok, reason = _allowed(command)
    if not ok:
        return {"stdout": "", "stderr": reason, "exitCode": None, "rejected": True}
    argv = ["cmd.exe", "/d", "/s", "/c", command] if shell == "cmd" else ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout_seconds, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"stdout": (completed.stdout or "")[:200000], "stderr": (completed.stderr or "")[:200000], "exitCode": completed.returncode}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {"stdout": stdout[:200000], "stderr": (stderr + "\nCommand timed out after 60 seconds.").strip()[:200000], "exitCode": None, "timedOut": True}
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exitCode": 1}


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _install_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _update_log_path() -> str:
    log_dir = os.path.join(_install_root(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "update.log")


def _append_update_log(message: str):
    try:
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(_update_log_path(), "a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _launch_self_update():
    """Launch the updater as a SYSTEM scheduled task and verify that it starts."""
    installer_url = SERVER + "/downloads/sitewatcher-agent"
    task_name = "SiteWatcher-Agent-Update-" + uuid.uuid4().hex[:10]
    update_log = _update_log_path()

    _append_update_log(f"remote update requested; task={task_name}; installer={installer_url}")

    updater_script = f"""$ErrorActionPreference = 'Stop'
$Log = '{_ps_quote(update_log)}'
function Write-UpdateLog([string]$Message) {{
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff zzz'
  for ($attempt = 0; $attempt -lt 10; $attempt++) {{
    try {{
      Add-Content -LiteralPath $Log -Value "[$stamp] $Message" -Encoding UTF8 -ErrorAction Stop
      return
    }} catch [System.IO.IOException] {{
      Start-Sleep -Milliseconds 250
    }}
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
  if ($svc) {{ Write-UpdateLog ('service status after installer: ' + $svc.Status) }} else {{ Write-UpdateLog 'service missing after installer' }}
  if ($p.ExitCode -ne 0) {{ throw "Installer exited with code $($p.ExitCode)" }}
}} catch {{
  Write-UpdateLog ('ERROR: ' + ($_ | Out-String).Trim())
}} finally {{
  Write-UpdateLog 'scheduled updater finished'
  try {{ Unregister-ScheduledTask -TaskName '{_ps_quote(task_name)}' -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
}}
"""
    encoded = base64.b64encode(updater_script.encode("utf-16le")).decode("ascii")
    _append_update_log(f"updater command encoded; chars={len(encoded)}")

    # Run PowerShell directly.  Do not redirect the whole task process to
    # update.log: cmd.exe keeps that file handle open for the lifetime of the
    # updater, which prevents Add-Content inside the updater from writing it.
    register = f"""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Principal $principal -Force | Out-Null
$before = Get-ScheduledTaskInfo -TaskName '{task_name}'
Start-ScheduledTask -TaskName '{task_name}'
$started = $false
$state = ''
$lastRun = $before.LastRunTime
for ($i = 0; $i -lt 20; $i++) {{
  Start-Sleep -Milliseconds 250
  $task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue
  $info = Get-ScheduledTaskInfo -TaskName '{task_name}' -ErrorAction SilentlyContinue
  if ($task) {{ $state = [string]$task.State }}
  if ($task -and ($task.State -eq 'Running' -or ($info -and $info.LastRunTime -gt $lastRun))) {{ $started = $true; break }}
}}
if (-not $started) {{ throw "Scheduled update task did not start; state=$state" }}
Write-Output "started state=$state"
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
        detail = detail or "Unable to create/start update task"
        _append_update_log(f"ERROR creating or starting scheduled task: {detail}")
        raise RuntimeError(detail)
    _append_update_log(f"scheduled task {task_name} verified started; {detail or 'running'}")


def _scan_host(ip: str):
    open_ports = []
    for port in SCAN_PORTS:
        try:
            with socket.create_connection((ip, port), timeout=0.25):
                open_ports.append(port)
        except OSError:
            pass
    alive = bool(open_ports)
    if not alive:
        try:
            ping = subprocess.run(["ping.exe", "-n", "1", "-w", "350", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            alive = ping.returncode == 0
        except Exception:
            pass
    if not alive:
        return None
    hostname = ""
    try:
        hostname = socket.gethostbyaddr(ip)[0]
    except Exception:
        pass
    return {"ip":ip, "hostname":hostname, "ports":open_ports}


def _scan_network(cidr: str):
    network = ipaddress.ip_network(cidr.strip(), strict=False)
    if network.version != 4:
        raise ValueError("Only IPv4 networks are supported.")
    if network.prefixlen < 24:
        raise ValueError("Remote IP Scanner is limited to /24 or smaller networks.")
    hosts = [str(ip) for ip in network.hosts()]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        for item in pool.map(_scan_host, hosts):
            if item:
                results.append(item)
    results.sort(key=lambda row: tuple(int(x) for x in row["ip"].split(".")))
    return results


def _post_result(command_id: str, result: dict):
    result["id"] = command_id
    post = requests.post(SERVER + "/api/agent/commands", headers=HEADERS, json=result, timeout=20)
    post.raise_for_status()


def remote_console_loop():
    time.sleep(3)
    while True:
        try:
            response = requests.get(SERVER + "/api/agent/commands", headers=HEADERS, timeout=20)
            if not response.ok:
                if response.status_code != 404:
                    print(f"[console] server HTTP {response.status_code}: {response.text[:200]}", flush=True)
                time.sleep(3)
                continue
            item = response.json().get("command")
            if not item:
                time.sleep(2)
                continue

            command_id = str(item.get("id") or "")
            command = str(item.get("command") or "")
            shell = str(item.get("shell") or "powershell").lower()

            if command == UPDATE_COMMAND and shell == "system":
                print(f"[update] scheduling SiteWatcher self-update job id={command_id[:8]}", flush=True)
                try:
                    _launch_self_update()
                    _post_result(command_id, {"stdout":"SiteWatcher agent update task started as SYSTEM. See logs/update.log for detailed progress.","stderr":"","exitCode":0})
                    print(f"[update] self-update task started id={command_id[:8]}", flush=True)
                except Exception as exc:
                    print(f"[update] unable to start update: {exc}", flush=True)
                    _post_result(command_id, {"stdout":"","stderr":f"Unable to start SiteWatcher update: {exc}","exitCode":1})
                time.sleep(10)
                continue

            if command.startswith(LOG_PREFIX) and shell == "system":
                raw_lines = command[len(LOG_PREFIX):].strip()
                try:
                    lines = max(50, min(1000, int(raw_lines or "250")))
                except ValueError:
                    lines = 250
                print(f"[logs] collecting recent agent logs lines={lines} job id={command_id[:8]}", flush=True)
                try:
                    output = collect_agent_logs(lines)
                    _post_result(command_id, {"stdout":output,"stderr":"","exitCode":0})
                    print(f"[logs] collection completed id={command_id[:8]}", flush=True)
                except Exception as exc:
                    _post_result(command_id, {"stdout":"","stderr":f"Unable to collect agent logs: {exc}","exitCode":1})
                continue

            if command.startswith(SCAN_PREFIX) and shell == "system":
                cidr = command[len(SCAN_PREFIX):].strip()
                print(f"[scanner] scanning {cidr} job id={command_id[:8]}", flush=True)
                try:
                    results = _scan_network(cidr)
                    _post_result(command_id, {"stdout":json.dumps({"cidr":cidr,"hosts":results}),"stderr":"","exitCode":0})
                    print(f"[scanner] {cidr} completed hosts={len(results)}", flush=True)
                except Exception as exc:
                    _post_result(command_id, {"stdout":"","stderr":str(exc),"exitCode":1})
                continue

            print(f"[console] executing diagnostic command id={command_id[:8]} shell={shell}", flush=True)
            result = _run(command, shell)
            _post_result(command_id, result)
            print(f"[console] command id={command_id[:8]} completed", flush=True)
        except Exception as exc:
            print(f"[console] worker error: {exc}", flush=True)
            time.sleep(5)
