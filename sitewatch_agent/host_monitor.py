from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone

import requests

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps_json(script: str, timeout: int = 45):
    c = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )
    if c.returncode != 0:
        raise RuntimeError((c.stderr or c.stdout or "PowerShell host monitor failed").strip())
    raw = (c.stdout or "").strip()
    if not raw:
        return None
    return json.loads(raw)


def collect_host_status(settings: dict) -> dict:
    if os.name != "nt":
        raise RuntimeError("Host resource monitoring currently supports Windows agents.")

    monitored = [str(x) for x in settings.get("monitoredServices", []) if str(x).strip()]
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
    if cpu is not None and float(cpu) >= cpu_threshold: problems.append(f"CPU {float(cpu):.1f}% >= {cpu_threshold:.1f}%")
    memory = data.get("memoryPercent")
    if memory is not None and float(memory) >= memory_threshold: problems.append(f"Memory {float(memory):.1f}% >= {memory_threshold:.1f}%")
    for disk in data.get("disks") or []:
        used = disk.get("usedPercent")
        if used is not None and float(used) >= disk_threshold: problems.append(f"Disk {disk.get('name')} {float(used):.1f}% >= {disk_threshold:.1f}%")
    for service in data.get("services") or []:
        if str(service.get("status") or "").lower() != "running": problems.append(f"Service {service.get('displayName') or service.get('name')} is {service.get('status') or 'Unknown'}")

    return {
        "observedAt": datetime.now(timezone.utc).isoformat(), "hostname": socket.gethostname(),
        "cpuPercent": data.get("cpuPercent"), "memoryPercent": data.get("memoryPercent"),
        "memoryTotalBytes": data.get("memoryTotalBytes"), "memoryAvailableBytes": data.get("memoryAvailableBytes"),
        "disks": data.get("disks") or [], "services": data.get("services") or [], "serviceInventory": data.get("serviceInventory") or [],
        "overallOk": len(problems) == 0, "problems": problems,
    }


def host_monitor_loop():
    server = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
    token = os.environ["SITEWATCH_AGENT_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    time.sleep(8)
    while True:
        interval = 60
        try:
            r = requests.get(server + "/api/agent/config", headers=headers, timeout=20)
            r.raise_for_status()
            settings = (r.json() or {}).get("hostMonitor") or {}
            interval = max(15, min(3600, int(settings.get("intervalSeconds", 60))))
            if settings.get("enabled", True):
                result = collect_host_status(settings)
                post = requests.post(server + "/api/agent/host-monitor", headers=headers, json=result, timeout=30)
                post.raise_for_status()
                state = "OK" if result.get("overallOk") else "WARN"
                print(f"[host] {state} cpu={result.get('cpuPercent')}% memory={result.get('memoryPercent')}% problems={len(result.get('problems') or [])}", flush=True)
        except Exception as exc:
            print(f"[host] monitor error: {exc}", flush=True)
        time.sleep(interval)
