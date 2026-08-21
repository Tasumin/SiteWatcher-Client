from __future__ import annotations

import json
import socket
import subprocess
import time


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TERMINAL_SERVER_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server"
RDP_TCP_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"


def _powershell(script: str) -> subprocess.CompletedProcess:
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
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )


def _socket_reachable(host: str = "127.0.0.1", port: int = 3389) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def get_rdp_status() -> dict:
    # Ask Windows for the listener directly instead of relying solely on a
    # localhost TCP connect. RDP can be bound in ways where the socket probe is
    # not a reliable indication of whether TermService has created a listener.
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$deny = (Get-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections).fDenyTSConnections
$nla = (Get-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication).UserAuthentication
$port = (Get-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber).PortNumber
$svc = Get-Service -Name TermService
$fw = Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
$fwEnabled = @($fw | Where-Object {{$_.Enabled -eq 'True'}}).Count -gt 0

$listeners = @()
if ($port) {{
  $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {{
      [pscustomobject]@{{
        address = [string]$_.LocalAddress
        port = [int]$_.LocalPort
        pid = [int]$_.OwningProcess
      }}
    }})
}}

# Older/odd Windows networking stacks occasionally do not return the listener
# through Get-NetTCPConnection. Fall back to netstat before declaring it down.
if ($listeners.Count -eq 0 -and $port) {{
  $pattern = ':' + [string]$port
  $netstat = @(netstat.exe -ano -p tcp 2>$null | Where-Object {{ $_ -match 'LISTENING' -and $_ -match [regex]::Escape($pattern) }})
  foreach ($line in $netstat) {{
    $parts = ($line.Trim() -split '\s+')
    if ($parts.Count -ge 5) {{
      $local = $parts[1]
      $pidValue = 0
      [void][int]::TryParse($parts[4], [ref]$pidValue)
      $listeners += [pscustomobject]@{{ address = $local; port = [int]$port; pid = $pidValue }}
    }}
  }}
}}

[pscustomobject]@{{
  enabled = ($deny -eq 0)
  nla = ($nla -eq 1)
  port = if ($port) {{ [int]$port }} else {{ 3389 }}
  serviceStatus = if ($svc) {{ [string]$svc.Status }} else {{ 'Missing' }}
  firewallEnabled = [bool]$fwEnabled
  listening = ($listeners.Count -gt 0)
  listeners = @($listeners)
}} | ConvertTo-Json -Depth 5 -Compress
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to read RDP status").strip())
    try:
        status = json.loads((completed.stdout or "{}").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse RDP status: {completed.stdout!r}") from exc

    port = int(status.get("port") or 3389)
    status["host"] = "127.0.0.1"
    status["socketReachable"] = _socket_reachable("127.0.0.1", port)
    status["listening"] = bool(status.get("listening"))
    return status


def enable_rdp() -> dict:
    script = rf"""
$ErrorActionPreference = 'Stop'
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Value 0
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Value 1
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic
Start-Service -Name TermService -ErrorAction SilentlyContinue
Start-Service -Name SessionEnv -ErrorAction SilentlyContinue
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to enable RDP").strip())

    # Give TermService a few seconds to create the listener before returning
    # status to the UI.
    last = None
    for _ in range(6):
        last = get_rdp_status()
        if last.get("listening"):
            return last
        time.sleep(1)
    return last or get_rdp_status()


def disable_rdp() -> dict:
    script = rf"""
$ErrorActionPreference = 'Stop'
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Value 1
Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to disable RDP").strip())
    return get_rdp_status()
