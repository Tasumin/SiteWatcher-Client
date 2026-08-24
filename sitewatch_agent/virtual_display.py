from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

from .tightvnc import restart_tightvnc

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AGENT_ROOT = Path(os.getenv("SITEWATCH_AGENT_ROOT", r"C:\SiteWatcher-Agent"))
VDD_TOOLS_DIR = AGENT_ROOT / "tools" / "vdd"
VDD_LOG_DIR = AGENT_ROOT / "logs"
VDD_LOG_PATH = VDD_LOG_DIR / "virtual-display.log"
VDD_MANAGER_PATH = VDD_TOOLS_DIR / "virtual-driver-manager.ps1"
VDD_MANAGER_URL = (
    "https://raw.githubusercontent.com/VirtualDrivers/Virtual-Display-Driver/"
    "master/Community%20Scripts/virtual-driver-manager.ps1"
)
VDD_HARDWARE_ID = r"Root\MttVDD"


def _ensure_dirs() -> None:
    VDD_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    VDD_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _log(message: str) -> None:
    _ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with VDD_LOG_PATH.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"{timestamp} {message.rstrip()}\n")


def _run_ps(script: str, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, errors="replace", timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def get_virtual_display_status() -> dict:
    if os.name != "nt":
        return {
            "installed": False, "enabled": False, "healthy": False,
            "status": "Unsupported", "device": None, "hardwareId": VDD_HARDWARE_ID,
            "instanceId": None, "problemCode": None, "restartRequired": False,
            "message": "Virtual display management is supported only on Windows agents.",
        }

    script = r"""
$devices = @(Get-PnpDevice -PresentOnly:$false -ErrorAction SilentlyContinue | Where-Object {
    ($_.InstanceId -like 'ROOT\MTTVDD*') -or
    ($_.FriendlyName -in @('Virtual Display Driver','IddSampleDriver Device HDR'))
})
$d = $devices | Sort-Object @{Expression={if($_.InstanceId -like 'ROOT\MTTVDD*'){0}else{1}}} | Select-Object -First 1
if (-not $d) {
    [pscustomobject]@{
        installed=$false; enabled=$false; healthy=$false; status='Not installed';
        device=$null; hardwareId='Root\MttVDD'; instanceId=$null; problemCode=$null;
        restartRequired=$false; message='Virtual display device was not found.'
    } | ConvertTo-Json -Compress
    exit 0
}
$problemCode = $null
try {
    $problem = Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_ProblemCode' -ErrorAction Stop
    if ($null -ne $problem.Data) { $problemCode = [int]$problem.Data }
} catch {}
$status = [string]$d.Status
$enabled = ($status -eq 'OK')
$restartRequired = ($problemCode -eq 14)
[pscustomobject]@{
    installed=$true; enabled=$enabled; healthy=($enabled -and -not $restartRequired); status=$status;
    device=[string]$d.FriendlyName; hardwareId='Root\MttVDD'; instanceId=[string]$d.InstanceId;
    problemCode=$problemCode; restartRequired=$restartRequired;
    message=if($restartRequired){'Windows reports that a reboot is required for the virtual display device.'}elseif($enabled){'Virtual display is installed and healthy.'}else{'Virtual display is installed but is not currently healthy/enabled.'}
} | ConvertTo-Json -Compress
"""
    try:
        completed = _run_ps(script, 45)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Virtual display status check timed out after 45 seconds.") from exc
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to query virtual display status.").strip())
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Virtual display status check returned no data.")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse virtual display status: {lines[-1][:500]}") from exc


def _download_manager() -> None:
    _ensure_dirs()
    _log(f"Downloading official manager script from {VDD_MANAGER_URL}")
    request = urllib.request.Request(
        VDD_MANAGER_URL,
        headers={"User-Agent": "SiteWatcher-Agent/VirtualDisplayManager"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except Exception as exc:
        raise RuntimeError(f"Unable to download the official Virtual Display Driver manager script: {exc}") from exc

    if len(body) < 5000 or b"virtual-driver-manager" not in body.lower():
        raise RuntimeError("Downloaded virtual-driver-manager.ps1 failed a basic content validation check.")

    text = body.decode("utf-8-sig", errors="strict")

    asset_needle = '$asset = $releaseInfo.assets | Where-Object { $_.name -match "x64\\.zip$" } | Select-Object -First 1'
    asset_replacement = (
        '$asset = $releaseInfo.assets | Where-Object { $_.name -match "^VirtualDisplayDriver-x86\\.Driver\\.Only\\.zip$" } | Select-Object -First 1\n'
        '                if (-not $asset) {\n'
        '                    $asset = $releaseInfo.assets | Where-Object { $_.name -match "^VirtualDisplayDriver.*Driver\\.Only\\.zip$" -and $_.name -notmatch "ARM64" } | Select-Object -First 1\n'
        '                }\n'
        '                if (-not $asset) { throw "Could not find the standard Windows Virtual Display Driver package in the latest GitHub release." }\n'
        '                Write-Log -Message ("Using official Virtual Display Driver asset: " + $asset.name) -Status \'Warning\''
    )
    if asset_needle not in text:
        raise RuntimeError("Official virtual-driver-manager.ps1 changed its release asset selector; refusing to apply an unverified compatibility patch.")
    text = text.replace(asset_needle, asset_replacement, 1)
    _log("Applied SiteWatcher compatibility selector for standard Windows VirtualDisplayDriver package; ARM64 excluded.")

    inf_needle = '& $devconExe install (Join-Path $tempDir "MttVDD.inf") "Root\\MttVDD"'
    inf_replacement = (
        '$vddInf = Get-ChildItem -Path $tempDir -Filter "MttVDD.inf" -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1\n'
        '            if (-not $vddInf) { throw "Downloaded Virtual Display Driver package did not contain MttVDD.inf." }\n'
        '            Write-Log -Message ("Using Virtual Display Driver INF: " + $vddInf.FullName)\n'
        '            & $devconExe install $vddInf.FullName "Root\\MttVDD"\n'
        '            $devconExit = $LASTEXITCODE\n'
        '            if ($devconExit -ne 0) { throw "DevCon failed to install Root\\MttVDD (exit $devconExit)." }'
    )
    if inf_needle not in text:
        raise RuntimeError("Official virtual-driver-manager.ps1 changed its DevCon install command; refusing to apply an unverified compatibility patch.")
    text = text.replace(inf_needle, inf_replacement, 1)
    _log("Applied SiteWatcher recursive MttVDD.inf lookup and DevCon exit-code validation.")

    VDD_MANAGER_PATH.write_text(text, encoding="utf-8-sig", newline="\r\n")
    _log(f"Manager script saved to {VDD_MANAGER_PATH} ({len(body)} downloaded bytes)")


def _run_manager(action: str, timeout: int = 300) -> tuple[int, str, str]:
    _download_manager()
    argv = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(VDD_MANAGER_PATH),
        "-Action", action, "-Silent", "-Json",
    ]
    _log(f"Running virtual-driver-manager.ps1 -Action {action} -Silent -Json")
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, errors="replace",
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        _log(f"Manager action {action} timed out after {timeout} seconds.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        raise RuntimeError(
            f"Virtual Display Driver {action} timed out after {timeout} seconds. "
            f"See {VDD_LOG_PATH} on the agent for details."
        ) from exc

    stdout, stderr = completed.stdout or "", completed.stderr or ""
    _log(f"Manager action {action} exit={completed.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    return completed.returncode, stdout, stderr


def manage_virtual_display(action: str) -> dict:
    if os.name != "nt":
        raise RuntimeError("Virtual display management is supported only on Windows agents.")
    if action not in {"install", "enable", "disable", "repair"}:
        raise ValueError(f"Unsupported virtual display action: {action}")

    _ensure_dirs()
    manager_action = "disable" if action == "disable" else "enable" if action == "enable" else "install"
    before = get_virtual_display_status()
    _log(f"Requested action={action}; before={json.dumps(before, separators=(',', ':'))}")

    exit_code, stdout, stderr = _run_manager(manager_action)
    combined = f"{stdout}\n{stderr}".lower()
    after = get_virtual_display_status()
    restart_required = bool(after.get("restartRequired")) or any(
        phrase in combined
        for phrase in ("restart required", "reboot required", "restart the computer", "reboot the computer")
    )
    after.update({
        "restartRequired": restart_required,
        "managerExitCode": exit_code,
        "action": action,
        "logPath": str(VDD_LOG_PATH),
    })

    if exit_code != 0:
        detail = (stderr or stdout).strip()
        if restart_required:
            after["message"] = "The virtual display operation requires a Windows reboot before it can finish."
            _log(f"Action {action} requires reboot despite manager exit={exit_code}.")
            return after
        raise RuntimeError(
            f"Virtual Display Driver manager exited with code {exit_code}. "
            f"{detail[:2000] or 'No additional output was returned.'} "
            f"See {VDD_LOG_PATH} on the agent for details."
        )

    if action in {"install", "repair"} and not after.get("installed"):
        if restart_required:
            after["message"] = "Installation completed, but Windows must reboot before Root\\MttVDD becomes available."
            _log(after["message"])
            return after
        raise RuntimeError(
            "Virtual Display Driver manager completed successfully, but Root\\MttVDD was not detected afterward. "
            f"See {VDD_LOG_PATH} on the agent for details."
        )

    if action == "enable" and not after.get("enabled"):
        if restart_required:
            after["message"] = "Enable completed, but Windows must reboot before the virtual display becomes healthy."
            _log(after["message"])
            return after
        raise RuntimeError(
            f"Virtual display enable completed, but device status is {after.get('status') or 'unknown'}. "
            f"See {VDD_LOG_PATH} on the agent for details."
        )

    if action == "disable":
        after["message"] = "Virtual display is disabled." if after.get("installed") else "Virtual display is not installed."
        _log(after["message"])
        return after

    if after.get("enabled") and not restart_required:
        try:
            vnc_status = restart_tightvnc()
            after["tightVncRestarted"] = True
            after["tightVncReady"] = bool(vnc_status.get("ready"))
            _log("TightVNC service restarted after successful virtual display operation.")
        except Exception as exc:
            after["tightVncRestarted"] = False
            after["tightVncWarning"] = str(exc)
            _log(f"Virtual display succeeded, but TightVNC restart failed: {exc}")

    if restart_required:
        after["message"] = "Virtual display operation completed, but Windows must be rebooted before the device is ready."
    elif after.get("healthy"):
        after["message"] = "Virtual display is installed and healthy."
    else:
        after["message"] = f"Virtual display is installed, but device status is {after.get('status') or 'unknown'}."

    _log(f"Completed action={action}; after={json.dumps(after, separators=(',', ':'))}")
    return after
