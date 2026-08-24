from __future__ import annotations

import json
import subprocess
import time

from . import virtual_display as _base
from .virtual_display_compat import get_virtual_display_status, manage_virtual_display as _manage_virtual_display

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_ps(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def _find_vdd_instance_ids() -> list[str]:
    script = r"""
$matches = @()
$enumRoot = 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Enum\ROOT'
foreach ($key in @(Get-ChildItem -Path $enumRoot -Recurse -ErrorAction SilentlyContinue)) {
    $props = $null
    try { $props = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop } catch { continue }
    $ids = @($props.HardwareID) + @($props.CompatibleIDs)
    if (-not (($ids | ForEach-Object { [string]$_ }) -match '(?i)MttVDD')) { continue }
    $marker = '\Enum\'
    $idx = $key.Name.IndexOf($marker, [System.StringComparison]::OrdinalIgnoreCase)
    if ($idx -lt 0) { continue }
    $instanceId = $key.Name.Substring($idx + $marker.Length)
    if ($instanceId -match '(?i)^ROOT\\DISPLAY\\\d+$') { $matches += $instanceId }
}
@($matches | Sort-Object -Unique) | ConvertTo-Json -Compress
"""
    result = _run_ps(script, 60)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Unable to enumerate VDD instances.").strip())
    text = (result.stdout or "").strip()
    if not text:
        return []
    parsed = json.loads(text)
    if isinstance(parsed, str):
        return [parsed]
    return [str(item) for item in parsed if item]


def _remove_duplicate_vdd_instances() -> tuple[str | None, list[str]]:
    instances = _find_vdd_instance_ids()
    if len(instances) <= 1:
        if instances:
            _base._log(f"VDD duplicate cleanup: one adapter present ({instances[0]}), nothing to remove.")
            return instances[0], []
        _base._log("VDD duplicate cleanup: no existing Root\\MttVDD adapter nodes found.")
        return None, []

    keep = sorted(instances)[0]
    duplicates = [item for item in sorted(instances) if item != keep]
    _base._log(f"VDD duplicate cleanup: keeping {keep}; removing {', '.join(duplicates)}")

    removed: list[str] = []
    for instance_id in duplicates:
        cmd = ["pnputil.exe", "/remove-device", instance_id, "/subtree"]
        _base._log("Running duplicate cleanup: " + subprocess.list2cmdline(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=60,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Timed out removing duplicate VDD adapter {instance_id}.") from exc
        _base._log(
            f"Duplicate VDD removal {instance_id} exit={result.returncode}\n"
            f"STDOUT:\n{result.stdout or ''}\nSTDERR:\n{result.stderr or ''}"
        )
        if result.returncode not in (0, 3010):
            raise RuntimeError(
                f"Unable to remove duplicate Virtual Display Driver adapter {instance_id} "
                f"(PnPUtil exit {result.returncode}). {(result.stderr or result.stdout).strip()}"
            )
        removed.append(instance_id)

    scan = subprocess.run(
        ["pnputil.exe", "/scan-devices"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    _base._log(
        f"PnP rescan after duplicate cleanup exit={scan.returncode}\n"
        f"STDOUT:\n{scan.stdout or ''}\nSTDERR:\n{scan.stderr or ''}"
    )
    time.sleep(2)
    remaining = _find_vdd_instance_ids()
    if len(remaining) > 1:
        raise RuntimeError(
            "Duplicate Virtual Display Driver adapters remain after cleanup: " + ", ".join(remaining)
        )
    _base._log(
        "VDD duplicate cleanup completed; remaining adapter: "
        + (remaining[0] if remaining else "none")
    )
    return (remaining[0] if remaining else keep), removed


def manage_virtual_display(action: str) -> dict:
    removed: list[str] = []
    if action == "repair":
        _, removed = _remove_duplicate_vdd_instances()

    result = _manage_virtual_display(action)
    if removed:
        result["duplicatesRemoved"] = removed
        result["duplicateCountRemoved"] = len(removed)
        result["message"] = (
            f"Removed {len(removed)} duplicate virtual display adapter(s). "
            + str(result.get("message") or "Virtual display repair completed.")
        )
    return result
