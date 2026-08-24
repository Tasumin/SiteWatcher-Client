from __future__ import annotations

import json
import os
import subprocess

from . import virtual_display as _impl

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VDD_HARDWARE_ID = r"Root\MttVDD"


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


def get_virtual_display_status() -> dict:
    if os.name != "nt":
        return {
            "installed": False,
            "enabled": False,
            "healthy": False,
            "status": "Unsupported",
            "device": None,
            "hardwareId": VDD_HARDWARE_ID,
            "instanceId": None,
            "problemCode": None,
            "restartRequired": False,
            "message": "Virtual display management is supported only on Windows agents.",
        }

    script = r"""
$ErrorActionPreference = 'SilentlyContinue'

function Test-VddIds([object[]]$Ids) {
    foreach ($id in @($Ids)) {
        if ([string]$id -match '(?i)MttVDD') { return $true }
    }
    return $false
}

$selected = $null
$selectedHardwareIds = @()

# Primary path: enumerate all PnP devices and inspect both hardware and compatible IDs.
foreach ($dev in @(Get-PnpDevice -PresentOnly:$false)) {
    $hardwareIds = @()
    $compatibleIds = @()
    try { $hardwareIds = @((Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction Stop).Data) } catch {}
    try { $compatibleIds = @((Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName 'DEVPKEY_Device_CompatibleIds' -ErrorAction Stop).Data) } catch {}
    $idMatch = (Test-VddIds ($hardwareIds + $compatibleIds))
    $nameMatch = ([string]$dev.FriendlyName -match '(?i)Virtual Display Driver|IddSampleDriver|MttVDD')
    if ($idMatch -or $nameMatch) {
        $selected = $dev
        $selectedHardwareIds = $hardwareIds
        if ($idMatch -and [string]$dev.Status -eq 'OK') { break }
    }
}

# Strong fallback: inspect the PnP registry directly. This works even when
# Get-PnpDeviceProperty does not expose HardwareIds for the root-enumerated adapter.
if (-not $selected) {
    $enumRoot = 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Enum\ROOT'
    foreach ($key in @(Get-ChildItem -Path $enumRoot -Recurse -ErrorAction SilentlyContinue)) {
        $props = $null
        try { $props = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop } catch { continue }
        $ids = @($props.HardwareID) + @($props.CompatibleIDs)
        if (-not (Test-VddIds $ids)) { continue }

        $marker = '\Enum\'
        $idx = $key.Name.IndexOf($marker, [System.StringComparison]::OrdinalIgnoreCase)
        if ($idx -lt 0) { continue }
        $instanceId = $key.Name.Substring($idx + $marker.Length)
        $dev = Get-PnpDevice -InstanceId $instanceId -PresentOnly:$false -ErrorAction SilentlyContinue
        if ($dev) {
            $selected = $dev
            $selectedHardwareIds = @($props.HardwareID)
            break
        }

        # If the cmdlet cannot materialize it, retain the registry device as a
        # synthetic candidate so SiteWatcher reports Installed/Needs repair.
        $cim = Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -eq $instanceId } | Select-Object -First 1
        $code = if ($null -ne $cim) { [int]$cim.ConfigManagerErrorCode } else { $null }
        $friendly = if ($props.FriendlyName) { [string]$props.FriendlyName } elseif ($cim.Name) { [string]$cim.Name } else { 'Virtual Display Driver' }
        $status = if ($code -eq 0) { 'OK' } else { 'Detected' }
        [pscustomobject]@{
            installed=$true; enabled=($code -eq 0); healthy=($code -eq 0); status=$status;
            device=$friendly; hardwareId=([string](@($props.HardwareID)[0])); instanceId=$instanceId;
            problemCode=$code; restartRequired=($code -eq 14);
            message=if($code -eq 0){'Virtual display is installed and healthy.'}else{'Virtual display device was found in Windows PnP registry and may need repair/restart.'}
        } | ConvertTo-Json -Compress
        exit 0
    }
}

# Final live-device fallback through CIM for drivers whose friendly name is exposed
# there but not by Get-PnpDevice.
if (-not $selected) {
    $cim = Get-CimInstance Win32_PnPEntity | Where-Object {
        ([string]$_.Name -match '(?i)Virtual Display Driver|IddSampleDriver|MttVDD') -or
        ([string]$_.DeviceID -match '(?i)MttVDD')
    } | Select-Object -First 1
    if ($cim) {
        $dev = Get-PnpDevice -InstanceId $cim.DeviceID -PresentOnly:$false -ErrorAction SilentlyContinue
        if ($dev) { $selected = $dev }
        else {
            $code = [int]$cim.ConfigManagerErrorCode
            [pscustomobject]@{
                installed=$true; enabled=($code -eq 0); healthy=($code -eq 0); status=if($code -eq 0){'OK'}else{'Detected'};
                device=[string]$cim.Name; hardwareId='Root\MttVDD'; instanceId=[string]$cim.DeviceID;
                problemCode=$code; restartRequired=($code -eq 14);
                message=if($code -eq 0){'Virtual display is installed and healthy.'}else{'Virtual display device was found and may need repair/restart.'}
            } | ConvertTo-Json -Compress
            exit 0
        }
    }
}

if (-not $selected) {
    [pscustomobject]@{
        installed=$false; enabled=$false; healthy=$false; status='Not installed'; device=$null;
        hardwareId='Root\MttVDD'; instanceId=$null; problemCode=$null; restartRequired=$false;
        message='Virtual display device was not found.'
    } | ConvertTo-Json -Compress
    exit 0
}

$problemCode = $null
try {
    $problemCode = [int](Get-PnpDeviceProperty -InstanceId $selected.InstanceId -KeyName 'DEVPKEY_Device_ProblemCode' -ErrorAction Stop).Data
} catch {}
$matchedHardwareId = $selectedHardwareIds | Where-Object { [string]$_ -match '(?i)MttVDD' } | Select-Object -First 1
if (-not $matchedHardwareId) { $matchedHardwareId = 'Root\MttVDD' }
$status = [string]$selected.Status
$enabled = ($status -eq 'OK' -and ($null -eq $problemCode -or $problemCode -eq 0))
$restartRequired = ($problemCode -eq 14)
[pscustomobject]@{
    installed=$true; enabled=$enabled; healthy=($enabled -and -not $restartRequired); status=$status;
    device=if($selected.FriendlyName){[string]$selected.FriendlyName}else{'Virtual Display Driver'};
    hardwareId=[string]$matchedHardwareId; instanceId=[string]$selected.InstanceId;
    problemCode=$problemCode; restartRequired=$restartRequired;
    message=if($restartRequired){'Windows reports that a reboot is required for the virtual display device.'}elseif($enabled){'Virtual display is installed and healthy.'}else{'Virtual display is installed but is not currently healthy/enabled.'}
} | ConvertTo-Json -Compress
"""

    try:
        completed = _run_ps(script, 60)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Virtual display status check timed out after 60 seconds.") from exc
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to query virtual display status.").strip())
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Virtual display status check returned no data.")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse virtual display status: {lines[-1][:500]}") from exc


def manage_virtual_display(action: str) -> dict:
    # The existing implementation performs the install/configuration work. Temporarily
    # replace its status function so every internal install/repair check uses the same
    # registry-aware detector and cannot create a duplicate device after a false negative.
    original = _impl.get_virtual_display_status
    _impl.get_virtual_display_status = get_virtual_display_status
    try:
        return _impl.manage_virtual_display(action)
    finally:
        _impl.get_virtual_display_status = original
