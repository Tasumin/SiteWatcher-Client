from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG_PATH = Path(os.getenv("SITEWATCH_HOST_MONITOR_LOG", str(Path.cwd() / "logs" / "host-monitor.log")))


def _log(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " " + datetime.now().astimezone().strftime("%z")
    line = f"[{stamp}] {message}"
    print(f"[host] {message}", flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception as exc:
        print(f"[host] unable to write {LOG_PATH}: {exc}", flush=True)


def _ps_json(script: str, timeout: int = 45):
    started = time.monotonic()
    _log("starting Windows performance/service collection via PowerShell")
    c = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )
    elapsed = time.monotonic() - started
    if c.returncode != 0:
        error = (c.stderr or c.stdout or "PowerShell host monitor failed").strip()
        _log(f"PowerShell collection failed exitCode={c.returncode} elapsed={elapsed:.2f}s error={error}")
        raise RuntimeError(error)
    raw = (c.stdout or "").strip()
    _log(f"PowerShell collection completed exitCode=0 elapsed={elapsed:.2f}s bytes={len(raw)}")
    if not raw:
        raise RuntimeError("PowerShell returned no host-monitor data")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _log(f"PowerShell returned invalid JSON: {raw[:1000]}")
        raise RuntimeError(f"Unable to parse host-monitor PowerShell output: {exc}") from exc


def collect_host_status(settings: dict) -> dict:
    if os.name != "nt":
        raise RuntimeError("Host resource monitoring currently supports Windows agents.")

    monitored = [str(x) for x in settings.get("monitoredServices", []) if str(x).strip()]
    _log(
        "collecting host status "
        f"cpuThreshold={settings.get('cpuThresholdPercent', 90)}% "
        f"memoryThreshold={settings.get('memoryThresholdPercent', 90)}% "
        f"diskThreshold={settings.get('diskThresholdPercent', 90)}% "
        f"services={len(monitored)}"
    )
    if monitored:
        _log("monitored services: " + ", ".join(monitored))

    service_json = json.dumps(monitored).replace("'", "''")
    script = rf"""
$ErrorActionPreference='SilentlyContinue'
$cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os=Get-CimInstance Win32_OperatingSystem
$total=[int64]$os.TotalVisibleMemorySize*1024
$free=[int64]$os.FreePhysicalMemory*1024
$mem=if($total -gt 0){{[math]::Round((($total-$free)/$total)*100,1)}}else{{$null}}
$disks=@(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {{
  $used=if($_.Size -gt 0){{[math]::Round((($_.Size-$_.FreeSpace)/$_.Size)*100,1)}}else{{$null}}
  [pscustomobject]@{{name=$_.DeviceID;label=$_.VolumeName;sizeBytes=[int64]$_.Size;freeBytes=[int64]$_.FreeSpace;usedPercent=$used}}
}})
$wanted=ConvertFrom-Json '{service_json}'
$all=@(Get-CimInstance Win32_Service | Sort-Object DisplayName | ForEach-Object {{[pscustomobject]@{{name=$_.Name;displayName=$_.DisplayName;status=$_.State;startMode=$_.StartMode}}}})
$selected=@()
foreach($name in @($wanted)){{
  $svc=$all | Where-Object {{$_.name -eq $name}} | Select-Object -First 1
  if($svc){{$selected += $svc}}else{{$selected += [pscustomobject]@{{name=$name;displayName=$name;status='Missing';startMode='Unknown'}}}}
}}
[pscustomobject]@{{cpuPercent=if($null-ne$cpu){{[math]::Round([double]$cpu,1)}}else{{$null}};memoryPercent=$mem;memoryTotalBytes=$total;memoryAvailableBytes=$free;disks=$disks;services=$selected;serviceInventory=$all}} | ConvertTo-Json -Depth 6 -Compress
"""
    data = _ps_json(script, 60) or {}

    cpu_threshold = float(settings.get("cpuThresholdPercent", 90))
    memory_threshold = float(settings.get("memoryThresholdPercent", 90))
    disk_threshold = float(settings.get("diskThresholdPercent", 90))
    problems = []
    cpu = data.get("cpuPercent")
    memory = data.get("memoryPercent")
    disks = data.get("disks") or []
    services = data.get("services") or []

    if cpu is not None and float(cpu) >= cpu_threshold:
        problems.append(f"CPU {float(cpu):.1f}% >= {cpu_threshold:.1f}%")
    if memory is not None and float(memory) >= memory_threshold:
        problems.append(f"Memory {float(memory):.1f}% >= {memory_threshold:.1f}%")
    for disk in disks:
        used = disk.get("usedPercent")
        if used is not None and float(used) >= disk_threshold:
            problems.append(f"Disk {disk.get('name')} {float(used):.1f}% >= {disk_threshold:.1f}%")
    for service in services:
        if str(service.get("status") or "").lower() != "running":
            problems.append(f"Service {service.get('displayName') or service.get('name')} is {service.get('status') or 'Unknown'}")

    disk_summary = ", ".join(f"{d.get('name')}={d.get('usedPercent')}%" for d in disks) or "none"
    service_summary = ", ".join(f"{s.get('name')}={s.get('status')}" for s in services) or "none selected"
    _log(f"values cpu={cpu}% memory={memory}% disks=[{disk_summary}] services=[{service_summary}]")
    if problems:
        _log("threshold/service problems: " + " | ".join(problems))
    else:
        _log("threshold/service evaluation: OK")

    return {
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "cpuPercent": cpu,
        "memoryPercent": memory,
        "memoryTotalBytes": data.get("memoryTotalBytes"),
        "memoryAvailableBytes": data.get("memoryAvailableBytes"),
        "disks": disks,
        "services": services,
        "serviceInventory": data.get("serviceInventory") or [],
        "overallOk": len(problems) == 0,
        "problems": problems,
    }


def host_monitor_loop():
    server = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
    token = os.environ["SITEWATCH_AGENT_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    _log(f"host monitor worker started server={server} log={LOG_PATH}")
    time.sleep(8)
    cycle = 0

    while True:
        cycle += 1
        interval = 60
        try:
            _log(f"cycle={cycle} requesting host-monitor configuration")
            r = requests.get(server + "/api/agent/config", headers=headers, timeout=20)
            _log(f"cycle={cycle} config HTTP {r.status_code}")
            r.raise_for_status()
            config = r.json() or {}
            settings = config.get("hostMonitor") or {}
            interval = max(15, min(3600, int(settings.get("intervalSeconds", 60))))
            enabled = settings.get("enabled", True)
            _log(
                f"cycle={cycle} config enabled={enabled} interval={interval}s "
                f"cpu={settings.get('cpuThresholdPercent', 90)}% "
                f"memory={settings.get('memoryThresholdPercent', 90)}% "
                f"disk={settings.get('diskThresholdPercent', 90)}% "
                f"services={len(settings.get('monitoredServices') or [])}"
            )

            if not enabled:
                _log(f"cycle={cycle} monitoring disabled; skipping collection")
            else:
                result = collect_host_status(settings)
                payload_bytes = len(json.dumps(result, separators=(",", ":")))
                _log(f"cycle={cycle} posting host status bytes={payload_bytes} overallOk={result.get('overallOk')}")
                post = requests.post(server + "/api/agent/host-monitor", headers=headers, json=result, timeout=30)
                _log(f"cycle={cycle} upload HTTP {post.status_code} response={post.text[:500]!r}")
                post.raise_for_status()
                _log(f"cycle={cycle} report accepted by server")
        except Exception as exc:
            _log(f"cycle={cycle} monitor error type={type(exc).__name__}: {exc}")

        _log(f"cycle={cycle} sleeping {interval}s")
        time.sleep(interval)
