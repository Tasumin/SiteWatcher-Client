from __future__ import annotations

import json
import socket
import subprocess
import time


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TERMINAL_SERVER_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server"
RDP_TCP_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
RDP_EVENT_LOG = "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational"


def _powershell(script: str, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def _run_json(script: str, error_message: str, timeout: int = 45) -> dict:
    completed = _powershell(script, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or error_message).strip())
    raw = (completed.stdout or "{}").strip() or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{error_message}: unable to parse PowerShell response: {raw!r}") from exc


def _socket_reachable(host: str = "127.0.0.1", port: int = 3389) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def get_rdp_diagnostics() -> dict:
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$ts = Get-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}'
$rdp = Get-ItemProperty -LiteralPath '{RDP_TCP_KEY}'
$term = Get-CimInstance Win32_Service -Filter "Name='TermService'"
$session = Get-CimInstance Win32_Service -Filter "Name='SessionEnv'"
$um = Get-CimInstance Win32_Service -Filter "Name='UmRdpService'"
$fw = @(Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue)
$port = if ($rdp.PortNumber) {{ [int]$rdp.PortNumber }} else {{ 3389 }}
$listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{
  [pscustomobject]@{{ address=[string]$_.LocalAddress; port=[int]$_.LocalPort; pid=[int]$_.OwningProcess }}
}})
$qwinsta = (& qwinsta.exe 2>&1 | Out-String).Trim()
$events = @(Get-WinEvent -LogName '{RDP_EVENT_LOG}' -MaxEvents 25 -ErrorAction SilentlyContinue | ForEach-Object {{
  [pscustomobject]@{{ time=$_.TimeCreated.ToString('o'); id=$_.Id; level=$_.LevelDisplayName; message=($_.Message -replace '\r?\n',' ') }}
}})
$dlls = @('termsrv.dll','rdpcorets.dll') | ForEach-Object {{
  $p = Join-Path $env:SystemRoot ('System32\\' + $_)
  if (Test-Path $p) {{
    $f = Get-Item $p
    [pscustomobject]@{{ name=$_.ToString(); exists=$true; version=$f.VersionInfo.FileVersion; length=$f.Length }}
  }} else {{ [pscustomobject]@{{ name=$_.ToString(); exists=$false; version=''; length=0 }} }}
}}
$edition = Get-ComputerInfo -Property WindowsProductName,WindowsEditionId,OsName,OsVersion -ErrorAction SilentlyContinue
[pscustomobject]@{{
  enabled = ($ts.fDenyTSConnections -eq 0)
  fDenyTSConnections = $ts.fDenyTSConnections
  fEnableWinStation = $rdp.fEnableWinStation
  nla = ($rdp.UserAuthentication -eq 1)
  userAuthentication = $rdp.UserAuthentication
  securityLayer = $rdp.SecurityLayer
  minEncryptionLevel = $rdp.MinEncryptionLevel
  lanAdapter = $rdp.LanAdapter
  port = $port
  serviceStatus = if ($term) {{ $term.State }} else {{ 'Missing' }}
  termServiceStartMode = if ($term) {{ $term.StartMode }} else {{ 'Missing' }}
  sessionEnvStatus = if ($session) {{ $session.State }} else {{ 'Missing' }}
  umRdpServiceStatus = if ($um) {{ $um.State }} else {{ 'Missing' }}
  firewallEnabled = (@($fw | Where-Object {{ $_.Enabled -eq 'True' }}).Count -gt 0)
  firewallRuleCount = $fw.Count
  listening = ($listeners.Count -gt 0)
  listeners = $listeners
  qwinsta = $qwinsta
  events = $events
  dlls = $dlls
  windowsProductName = $edition.WindowsProductName
  windowsEditionId = $edition.WindowsEditionId
  osName = $edition.OsName
  osVersion = $edition.OsVersion
}} | ConvertTo-Json -Depth 7 -Compress
"""
    status = _run_json(script, "Unable to collect RDP diagnostics", timeout=60)
    port = int(status.get("port") or 3389)
    status["host"] = "127.0.0.1"
    status["socketReachable"] = _socket_reachable("127.0.0.1", port)
    status["listenerPresent"] = "rdp-tcp" in str(status.get("qwinsta") or "").lower()
    status["healthy"] = bool(status.get("enabled") and status.get("listening") and status.get("listenerPresent"))
    return status


def get_rdp_status() -> dict:
    diag = get_rdp_diagnostics()
    keys = (
        "enabled", "nla", "serviceStatus", "firewallEnabled", "listening", "port",
        "host", "socketReachable", "listenerPresent", "healthy", "listeners",
    )
    return {key: diag.get(key) for key in keys}


def enable_rdp() -> dict:
    script = rf"""
$ErrorActionPreference = 'Stop'
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 0
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name fEnableWinStation -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber -Type DWord -Value 3389
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Type DWord -Value 1
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic
Start-Service -Name TermService -ErrorAction SilentlyContinue
Start-Service -Name SessionEnv -ErrorAction SilentlyContinue
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to enable RDP").strip())
    for _ in range(8):
        status = get_rdp_status()
        if status.get("healthy"):
            return status
        time.sleep(1)
    return get_rdp_status()


def repair_rdp() -> dict:
    before = get_rdp_diagnostics()
    script = rf"""
$ErrorActionPreference = 'Stop'
# Re-apply Microsoft's standard RDP host settings without deleting the listener key.
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 0
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name fEnableWinStation -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber -Type DWord -Value 3389
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name SecurityLayer -Type DWord -Value 2
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name LanAdapter -Type DWord -Value 0
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic
Set-Service -Name SessionEnv -StartupType Manual -ErrorAction SilentlyContinue

# Restart the RDP service stack in dependency-safe order.
Stop-Service -Name UmRdpService -Force -ErrorAction SilentlyContinue
Stop-Service -Name TermService -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service -Name TermService
Start-Service -Name SessionEnv -ErrorAction SilentlyContinue
Start-Service -Name UmRdpService -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
"""
    completed = _powershell(script, timeout=60)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to repair RDP").strip())

    after = get_rdp_diagnostics()
    return {
        "repaired": bool(after.get("healthy")),
        "before": before,
        "after": after,
        "summary": "RDP listener restored." if after.get("healthy") else "Repair completed, but Windows still did not create the rdp-tcp listener. Review diagnostics/events.",
    }


def disable_rdp() -> dict:
    script = rf"""
$ErrorActionPreference = 'Stop'
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 1
Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to disable RDP").strip())
    return get_rdp_status()
