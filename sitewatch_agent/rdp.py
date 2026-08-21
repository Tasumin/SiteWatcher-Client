from __future__ import annotations

import json
import socket
import subprocess


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


def _port_listening(host: str = "127.0.0.1", port: int = 3389) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def get_rdp_status() -> dict:
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$deny = (Get-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections).fDenyTSConnections
$nla = (Get-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication).UserAuthentication
$svc = Get-Service -Name TermService
$fw = Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
$fwEnabled = @($fw | Where-Object {{$_.Enabled -eq 'True'}}).Count -gt 0
[pscustomobject]@{{
  enabled = ($deny -eq 0)
  nla = ($nla -eq 1)
  serviceStatus = if ($svc) {{ [string]$svc.Status }} else {{ 'Missing' }}
  firewallEnabled = [bool]$fwEnabled
}} | ConvertTo-Json -Compress
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to read RDP status").strip())
    try:
        status = json.loads((completed.stdout or "{}").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse RDP status: {completed.stdout!r}") from exc
    status["listening"] = _port_listening()
    status["port"] = 3389
    status["host"] = "127.0.0.1"
    return status


def enable_rdp() -> dict:
    script = rf"""
$ErrorActionPreference = 'Stop'
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Value 0
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Value 1
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic
Start-Service -Name TermService -ErrorAction SilentlyContinue
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to enable RDP").strip())
    return get_rdp_status()


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
