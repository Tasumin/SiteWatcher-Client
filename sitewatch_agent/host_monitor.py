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


def _linux_cpu_percent(sample_seconds: float = 0.15):
    def read():
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            parts = handle.readline().split()[1:]
        values = [int(x) for x in parts]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle
    total1, idle1 = read()
    time.sleep(sample_seconds)
    total2, idle2 = read()
    delta = total2 - total1
    return None if delta <= 0 else round((1.0 - ((idle2 - idle1) / delta)) * 100.0, 1)


def _linux_memory():
    values = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    percent = round(((total - available) / total) * 100.0, 1) if total else None
    return percent, total, available


def _linux_disks():
    result = subprocess.run(["df", "-P", "-B1", "-x", "tmpfs", "-x", "devtmpfs"], capture_output=True, text=True, errors="replace", timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "df failed").strip())
    disks = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        filesystem, size, _used, available, capacity, mountpoint = parts
        try:
            disks.append({"name": mountpoint, "label": filesystem, "sizeBytes": int(size), "freeBytes": int(available), "usedPercent": float(capacity.rstrip("%"))})
        except ValueError:
            continue
    return disks


def _linux_service(name: str):
    unit = name if name.endswith(".service") else name + ".service"
    result = subprocess.run(["systemctl", "show", unit, "--no-pager", "--property=Id,Description,ActiveState,UnitFileState"], capture_output=True, text=True, errors="replace", timeout=10)
    if result.returncode != 0:
        return {"name": name, "displayName": name, "status": "Missing", "startMode": "Unknown"}
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    active = values.get("ActiveState") or "unknown"
    return {"name": values.get("Id") or unit, "displayName": values.get("Description") or values.get("Id") or unit, "status": "Running" if active == "active" else active.capitalize(), "startMode": values.get("UnitFileState") or "Unknown"}


def _linux_service_inventory():
    result = subprocess.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"], capture_output=True, text=True, errors="replace", timeout=15)
    if result.returncode != 0:
        return []
    inventory = []
    for raw in result.stdout.splitlines():
        line = raw.lstrip("● ").strip()
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, _load, active, _sub = parts[:4]
        description = parts[4] if len(parts) > 4 else unit
        inventory.append({"name": unit, "displayName": description, "status": "Running" if active == "active" else active.capitalize(), "startMode": "Unknown"})
    return inventory


def _linux_process_inventory():
    result = subprocess.run(["ps", "-eo", "comm=,pid=,pcpu=,pmem="], capture_output=True, text=True, errors="replace", timeout=15)
    if result.returncode != 0:
        return []
    grouped = {}
    for raw in result.stdout.splitlines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        name = parts[0]
        try:
            pid = int(parts[1]); cpu = float(parts[2]); memory = float(parts[3])
        except ValueError:
            continue
        row = grouped.setdefault(name, {"name": name, "displayName": name, "status": "Running", "instances": 0, "pids": [], "cpuPercent": 0.0, "memoryPercent": 0.0})
        row["instances"] += 1
        if len(row["pids"]) < 20:
            row["pids"].append(pid)
        row["cpuPercent"] = round(float(row["cpuPercent"]) + cpu, 1)
        row["memoryPercent"] = round(float(row["memoryPercent"]) + memory, 1)
    return sorted(grouped.values(), key=lambda x: x["name"].lower())


def _selected_processes(names, inventory):
    by_name = {str(x.get("name") or "").lower(): x for x in inventory}
    selected = []
    for name in names:
        row = by_name.get(str(name).lower())
        selected.append(row or {"name": name, "displayName": name, "status": "Missing", "instances": 0, "pids": [], "cpuPercent": 0.0, "memoryPercent": 0.0})
    return selected


def _linux_host_data(monitored_services, monitored_processes):
    memory_percent, memory_total, memory_available = _linux_memory()
    process_inventory = _linux_process_inventory()
    return {"cpuPercent": _linux_cpu_percent(), "memoryPercent": memory_percent, "memoryTotalBytes": memory_total, "memoryAvailableBytes": memory_available, "disks": _linux_disks(), "services": [_linux_service(name) for name in monitored_services], "serviceInventory": _linux_service_inventory(), "processes": _selected_processes(monitored_processes, process_inventory), "processInventory": process_inventory}


def collect_host_status(settings: dict) -> dict:
    raw_targets = [str(x).strip() for x in settings.get("monitoredTargets", []) if str(x).strip()]
    if not raw_targets:
        raw_targets = [f"service:{str(x).strip()}" for x in settings.get("monitoredServices", []) if str(x).strip()]
    monitored_services = [x.split(":", 1)[1] for x in raw_targets if x.startswith("service:") and ":" in x]
    monitored_processes = [x.split(":", 1)[1] for x in raw_targets if x.startswith("process:") and ":" in x]
    _log(
        "collecting host status "
        f"cpuThreshold={settings.get('cpuThresholdPercent', 90)}% "
        f"memoryThreshold={settings.get('memoryThresholdPercent', 90)}% "
        f"diskThreshold={settings.get('diskThresholdPercent', 90)}% "
        f"services={len(monitored_services)} processes={len(monitored_processes)}"
    )
    if monitored_services:
        _log("monitored services: " + ", ".join(monitored_services))
    if monitored_processes:
        _log("monitored processes: " + ", ".join(monitored_processes))

    if os.name == "nt":
        service_json = json.dumps(monitored_services).replace("'", "''")
        process_json = json.dumps(monitored_processes).replace("'", "''")
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
    $wantedProcesses=ConvertFrom-Json '{process_json}'
    $all=@(Get-CimInstance Win32_Service | Sort-Object DisplayName | ForEach-Object {{[pscustomobject]@{{name=$_.Name;displayName=$_.DisplayName;status=$_.State;startMode=$_.StartMode}}}})
    $selected=@()
    foreach($name in @($wanted)){{
      $svc=$all | Where-Object {{$_.name -eq $name}} | Select-Object -First 1
      if($svc){{$selected += $svc}}else{{$selected += [pscustomobject]@{{name=$name;displayName=$name;status='Missing';startMode='Unknown'}}}}
    }}
    $procAll=@(Get-Process | Group-Object ProcessName | Sort-Object Name | ForEach-Object {{
      $group=$_.Group
      [pscustomobject]@{{name=$_.Name;displayName=$_.Name;status='Running';instances=$group.Count;pids=@($group.Id | Select-Object -First 20)}}
    }})
    $procSelected=@()
    foreach($name in @($wantedProcesses)){{
      $proc=$procAll | Where-Object {{$_.name -ieq $name}} | Select-Object -First 1
      if($proc){{$procSelected += $proc}}else{{$procSelected += [pscustomobject]@{{name=$name;displayName=$name;status='Missing';instances=0;pids=@()}}}}
    }}
    [pscustomobject]@{{cpuPercent=if($null-ne$cpu){{[math]::Round([double]$cpu,1)}}else{{$null}};memoryPercent=$mem;memoryTotalBytes=$total;memoryAvailableBytes=$free;disks=$disks;services=$selected;serviceInventory=$all;processes=$procSelected;processInventory=$procAll}} | ConvertTo-Json -Depth 6 -Compress
    """
        data = _ps_json(script, 60) or {}
    else:
        data = _linux_host_data(monitored_services, monitored_processes)

    cpu_threshold = float(settings.get("cpuThresholdPercent", 90))
    memory_threshold = float(settings.get("memoryThresholdPercent", 90))
    disk_threshold = float(settings.get("diskThresholdPercent", 90))
    problems = []
    cpu = data.get("cpuPercent")
    memory = data.get("memoryPercent")
    disks = data.get("disks") or []
    services = data.get("services") or []
    processes = data.get("processes") or []

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
    for process in processes:
        if str(process.get("status") or "").lower() != "running":
            problems.append(f"Process {process.get('displayName') or process.get('name')} is {process.get('status') or 'Unknown'}")

    disk_summary = ", ".join(f"{d.get('name')}={d.get('usedPercent')}%" for d in disks) or "none"
    service_summary = ", ".join(f"{s.get('name')}={s.get('status')}" for s in services) or "none selected"
    process_summary = ", ".join(f"{p.get('name')}={p.get('status')}" for p in processes) or "none selected"
    _log(f"values cpu={cpu}% memory={memory}% disks=[{disk_summary}] services=[{service_summary}] processes=[{process_summary}]")
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
        "processes": processes,
        "processInventory": data.get("processInventory") or [],
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
                f"targets={len(settings.get('monitoredTargets') or settings.get('monitoredServices') or [])}"
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
