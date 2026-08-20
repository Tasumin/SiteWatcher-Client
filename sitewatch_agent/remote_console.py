import concurrent.futures
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid

import requests


SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
TOKEN = os.environ["SITEWATCH_AGENT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
UPDATE_COMMAND = "__SITEWATCH_UPDATE_AGENT__"
SCAN_PREFIX = "__SITEWATCH_IP_SCAN__|"
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


def _launch_self_update():
    """Launch an updater outside the WinSW service process tree.

    A child process of the service can be terminated when WinSW stops the
    service during reinstall. A one-shot SYSTEM scheduled task survives that
    stop/replacement and can finish the upgrade safely.
    """
    installer_url = SERVER + "/downloads/sitewatcher-agent"
    task_name = "SiteWatcher-Agent-Update-" + uuid.uuid4().hex[:10]
    updater_path = os.path.join(tempfile.gettempdir(), task_name + ".ps1")

    updater_script = f"""$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 5
$installer = Join-Path $env:TEMP 'install-sitewatcher-agent.ps1'
try {{
  Invoke-WebRequest -Uri '{_ps_quote(installer_url)}' -OutFile $installer -UseBasicParsing
  & $installer -ServerUrl '{_ps_quote(SERVER)}'
}} finally {{
  try {{ Unregister-ScheduledTask -TaskName '{_ps_quote(task_name)}' -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
  try {{ Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }} catch {{}}
}}
"""
    with open(updater_path, "w", encoding="utf-8-sig") as handle:
        handle.write(updater_script)

    register = f"""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{updater_path}"'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName '{task_name}'
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", register],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to create update task").strip())


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
                    _post_result(command_id, {"stdout":"SiteWatcher agent update scheduled as SYSTEM. The service will restart automatically.","stderr":"","exitCode":0})
                    print(f"[update] self-update task scheduled id={command_id[:8]}", flush=True)
                except Exception as exc:
                    print(f"[update] unable to schedule update: {exc}", flush=True)
                    _post_result(command_id, {"stdout":"","stderr":f"Unable to schedule SiteWatcher update: {exc}","exitCode":1})
                time.sleep(10)
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
