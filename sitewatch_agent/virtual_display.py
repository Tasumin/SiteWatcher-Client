from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import time
import urllib.request
import zipfile
from ctypes import wintypes
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
VDD_RELEASE_API = "https://api.github.com/repos/VirtualDrivers/Virtual-Display-Driver/releases/latest"
VDD_ASSET_NAME = "VirtualDisplayDriver-x86.Driver.Only.zip"
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
    request = urllib.request.Request(VDD_MANAGER_URL, headers={"User-Agent": "SiteWatcher-Agent/VirtualDisplayManager"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except Exception as exc:
        raise RuntimeError(f"Unable to download the official Virtual Display Driver manager script: {exc}") from exc
    if len(body) < 5000 or b"virtual-driver-manager" not in body.lower():
        raise RuntimeError("Downloaded virtual-driver-manager.ps1 failed a basic content validation check.")
    VDD_MANAGER_PATH.write_bytes(body)
    _log(f"Manager script saved to {VDD_MANAGER_PATH} ({len(body)} bytes)")


def _run_manager(action: str, timeout: int = 180) -> tuple[int, str, str]:
    _download_manager()
    argv = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(VDD_MANAGER_PATH),
        "-Action", action, "-Silent", "-Json",
    ]
    _log(f"Running virtual-driver-manager.ps1 -Action {action} -Silent -Json")
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                   timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        _log(f"Manager action {action} timed out after {timeout} seconds.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        raise RuntimeError(f"Virtual Display Driver {action} timed out after {timeout} seconds. See {VDD_LOG_PATH}.") from exc
    stdout, stderr = completed.stdout or "", completed.stderr or ""
    _log(f"Manager action {action} exit={completed.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    return completed.returncode, stdout, stderr


def _download_official_driver() -> Path:
    _ensure_dirs()
    request = urllib.request.Request(VDD_RELEASE_API, headers={"User-Agent": "SiteWatcher-Agent/VirtualDisplayPnPUtil"})
    _log(f"Resolving latest official VDD release from {VDD_RELEASE_API}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Unable to query the official Virtual Display Driver release: {exc}") from exc

    asset = next((item for item in release.get("assets", []) if item.get("name") == VDD_ASSET_NAME), None)
    if not asset or not str(asset.get("browser_download_url", "")).startswith(
        "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/"
    ):
        raise RuntimeError(f"Official release did not contain expected asset {VDD_ASSET_NAME}.")

    package_dir = VDD_TOOLS_DIR / "package"
    if package_dir.exists():
        shutil.rmtree(package_dir, ignore_errors=True)
    package_dir.mkdir(parents=True, exist_ok=True)
    zip_path = VDD_TOOLS_DIR / VDD_ASSET_NAME
    _log(f"Downloading official VDD asset {VDD_ASSET_NAME}")
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "SiteWatcher-Agent/VirtualDisplayPnPUtil"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, zip_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:
        raise RuntimeError(f"Unable to download official VDD driver package: {exc}") from exc

    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(package_dir)
    except Exception as exc:
        raise RuntimeError(f"Unable to extract official VDD driver package: {exc}") from exc

    infs = list(package_dir.rglob("MttVDD.inf"))
    if len(infs) != 1:
        raise RuntimeError(f"Expected exactly one MttVDD.inf in the official package, found {len(infs)}.")
    _log(f"Official VDD INF extracted to {infs[0]}")
    return infs[0]


def _trust_official_catalog(inf: Path) -> None:
    cat = inf.with_name("mttvdd.cat")
    if not cat.exists():
        matches = list(inf.parent.rglob("mttvdd.cat"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one mttvdd.cat beside the official VDD INF, found {len(matches)}.")
        cat = matches[0]

    cat_literal = str(cat).replace("'", "''")
    script = rf"""
$ErrorActionPreference = 'Stop'
$cat = '{cat_literal}'
$bytes = [System.IO.File]::ReadAllBytes($cat)
$certs = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2Collection
$certs.Import($bytes)
if ($certs.Count -lt 1) {{ throw 'No signer certificates were found in mttvdd.cat.' }}
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('TrustedPublisher','LocalMachine')
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
try {{
    foreach ($cert in $certs) {{
        $existing = $store.Certificates | Where-Object {{ $_.Thumbprint -eq $cert.Thumbprint }} | Select-Object -First 1
        if (-not $existing) {{ $store.Add($cert) }}
        Write-Output ("TrustedPublisher certificate: " + $cert.Subject + " [" + $cert.Thumbprint + "]")
    }}
}} finally {{
    $store.Close()
}}
"""
    _log(f"Importing signer certificate(s) from official VDD catalog {cat} into LocalMachine\\TrustedPublisher")
    try:
        result = _run_ps(script, 60)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while trusting the official Virtual Display Driver catalog signer.") from exc
    _log(f"Certificate trust exit={result.returncode}\nSTDOUT:\n{result.stdout or ''}\nSTDERR:\n{result.stderr or ''}")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Unable to trust the official VDD catalog signer.").strip())


def _run_pnputil(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = ["pnputil.exe", *args]
    _log("Running: " + subprocess.list2cmdline(cmd))
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                   timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"PnPUtil timed out after {timeout} seconds: {' '.join(args)}") from exc
    _log(f"PnPUtil exit={completed.returncode}\nSTDOUT:\n{completed.stdout or ''}\nSTDERR:\n{completed.stderr or ''}")
    return completed


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _GUID), ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


def _display_class_guid() -> _GUID:
    return _GUID(0x4D36E968, 0xE325, 0x11CE, (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18))


def _create_root_device() -> None:
    if os.name != "nt":
        raise RuntimeError("SetupAPI root device creation is available only on Windows.")
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    guid = _display_class_guid()
    devinfo = _SP_DEVINFO_DATA()
    devinfo.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)

    setupapi.SetupDiCreateDeviceInfoList.argtypes = [ctypes.POINTER(_GUID), wintypes.HWND]
    setupapi.SetupDiCreateDeviceInfoList.restype = wintypes.HANDLE
    setupapi.SetupDiCreateDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD, ctypes.POINTER(_SP_DEVINFO_DATA)]
    setupapi.SetupDiCreateDeviceInfoW.restype = wintypes.BOOL
    setupapi.SetupDiSetDeviceRegistryPropertyW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(ctypes.c_ubyte), wintypes.DWORD]
    setupapi.SetupDiSetDeviceRegistryPropertyW.restype = wintypes.BOOL
    setupapi.SetupDiCallClassInstaller.argtypes = [wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA)]
    setupapi.SetupDiCallClassInstaller.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    handle = setupapi.SetupDiCreateDeviceInfoList(ctypes.byref(guid), None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        DICD_GENERATE_ID = 0x00000001
        SPDRP_HARDWAREID = 0x00000001
        DIF_REGISTERDEVICE = 0x00000019
        if not setupapi.SetupDiCreateDeviceInfoW(handle, "Display", ctypes.byref(guid), "SiteWatcher Virtual Display", None, DICD_GENERATE_ID, ctypes.byref(devinfo)):
            raise ctypes.WinError(ctypes.get_last_error())
        hardware_ids = (VDD_HARDWARE_ID + "\0\0").encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(hardware_ids)).from_buffer_copy(hardware_ids)
        if not setupapi.SetupDiSetDeviceRegistryPropertyW(handle, ctypes.byref(devinfo), SPDRP_HARDWAREID, buffer, len(hardware_ids)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not setupapi.SetupDiCallClassInstaller(DIF_REGISTERDEVICE, handle, ctypes.byref(devinfo)):
            raise ctypes.WinError(ctypes.get_last_error())
        _log("Created Root\\MttVDD device node using native Windows SetupAPI.")
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)


def _native_install() -> tuple[int, str, str]:
    inf = _download_official_driver()
    output: list[str] = []

    _trust_official_catalog(inf)

    stage = _run_pnputil(["/add-driver", str(inf), "/install"])
    output.append(stage.stdout or "")
    if stage.returncode not in (0, 3010):
        return stage.returncode, "\n".join(output), stage.stderr or ""

    status = get_virtual_display_status()
    if not status.get("installed"):
        _create_root_device()
        time.sleep(2)

    scan = _run_pnputil(["/scan-devices"])
    output.append(scan.stdout or "")
    bind = _run_pnputil(["/add-driver", str(inf), "/install"])
    output.append(bind.stdout or "")
    reboot = stage.returncode == 3010 or bind.returncode == 3010
    rc = 3010 if reboot else bind.returncode
    return rc, "\n".join(output), "\n".join(filter(None, [stage.stderr, scan.stderr, bind.stderr]))


def manage_virtual_display(action: str) -> dict:
    if os.name != "nt":
        raise RuntimeError("Virtual display management is supported only on Windows agents.")
    if action not in {"install", "enable", "disable", "repair"}:
        raise ValueError(f"Unsupported virtual display action: {action}")

    _ensure_dirs()
    before = get_virtual_display_status()
    _log(f"Requested action={action}; before={json.dumps(before, separators=(',', ':'))}")

    if action in {"install", "repair"}:
        _log("Using headless native SetupAPI + PnPUtil installation path; DevCon is not used.")
        exit_code, stdout, stderr = _native_install()
    else:
        exit_code, stdout, stderr = _run_manager(action)

    combined = f"{stdout}\n{stderr}".lower()
    time.sleep(2)
    after = get_virtual_display_status()
    restart_required = bool(after.get("restartRequired")) or exit_code == 3010 or any(
        phrase in combined for phrase in ("restart required", "reboot required", "restart the computer", "reboot the computer")
    )
    after.update({"restartRequired": restart_required, "managerExitCode": exit_code, "action": action, "logPath": str(VDD_LOG_PATH)})

    if exit_code not in (0, 3010):
        detail = (stderr or stdout).strip()
        raise RuntimeError(
            f"Virtual Display Driver {action} failed with code {exit_code}. "
            f"{detail[:2000] or 'No additional output was returned.'} See {VDD_LOG_PATH} on the agent for details."
        )

    if action in {"install", "repair"} and not after.get("installed"):
        if restart_required:
            after["message"] = "Driver installation completed, but Windows must reboot before Root\\MttVDD becomes available."
            _log(after["message"])
            return after
        raise RuntimeError(
            "PnPUtil completed, but Root\\MttVDD was not detected afterward. "
            f"See {VDD_LOG_PATH} on the agent for details."
        )

    if action == "enable" and not after.get("enabled"):
        if restart_required:
            after["message"] = "Enable completed, but Windows must reboot before the virtual display becomes healthy."
            _log(after["message"])
            return after
        raise RuntimeError(
            f"Virtual display enable completed, but device status is {after.get('status') or 'unknown'}. See {VDD_LOG_PATH}."
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
