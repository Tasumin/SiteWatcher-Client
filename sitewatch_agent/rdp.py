from __future__ import annotations

import json
import socket
import subprocess
import time


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TERMINAL_SERVER_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server"
RDP_TCP_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
RDP_POLICY_KEY = r"HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
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
$policy = Get-ItemProperty -LiteralPath '{RDP_POLICY_KEY}' -ErrorAction SilentlyContinue
$term = Get-CimInstance Win32_Service -Filter "Name='TermService'"
$session = Get-CimInstance Win32_Service -Filter "Name='SessionEnv'"
$um = Get-CimInstance Win32_Service -Filter "Name='UmRdpService'"
$fw = @(Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue)
$port = if ($rdp.PortNumber) {{ [int]$rdp.PortNumber }} else {{ 3389 }}
$listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{
  [pscustomobject]@{{ address=[string]$_.LocalAddress; port=[int]$_.LocalPort; pid=[int]$_.OwningProcess }}
}})
$qwinsta = (& qwinsta.exe 2>&1 | Out-String).Trim()
$events = @(Get-WinEvent -LogName '{RDP_EVENT_LOG}' -MaxEvents 30 -ErrorAction SilentlyContinue | ForEach-Object {{
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
$tsSetting = Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting -ErrorAction SilentlyContinue | Select-Object -First 1
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
  policyPresent = [bool]$policy
  policyDenyTSConnections = if ($policy -and $null -ne $policy.fDenyTSConnections) {{ [int]$policy.fDenyTSConnections }} else {{ $null }}
  policyUserAuthentication = if ($policy -and $null -ne $policy.UserAuthentication) {{ [int]$policy.UserAuthentication }} else {{ $null }}
  cimProviderAvailable = [bool]$tsSetting
  cimAllowTSConnections = if ($tsSetting) {{ [int]$tsSetting.AllowTSConnections }} else {{ $null }}
}} | ConvertTo-Json -Depth 7 -Compress
"""
    status = _run_json(script, "Unable to collect RDP diagnostics", timeout=60)
    port = int(status.get("port") or 3389)
    status["host"] = "127.0.0.1"
    status["socketReachable"] = _socket_reachable("127.0.0.1", port)
    status["listenerPresent"] = "rdp-tcp" in str(status.get("qwinsta") or "").lower()
    status["policyBlocksRdp"] = status.get("policyDenyTSConnections") == 1
    status["healthy"] = bool(status.get("enabled") and status.get("listening") and status.get("listenerPresent"))
    return status


def get_rdp_status() -> dict:
    diag = get_rdp_diagnostics()
    keys = (
        "enabled", "nla", "serviceStatus", "firewallEnabled", "listening", "port",
        "host", "socketReachable", "listenerPresent", "healthy", "listeners",
        "policyPresent", "policyDenyTSConnections", "policyBlocksRdp",
        "cimProviderAvailable", "cimAllowTSConnections",
    )
    return {key: diag.get(key) for key in keys}


def _enable_with_windows_provider() -> subprocess.CompletedProcess:
    script = rf"""
$ErrorActionPreference = 'Stop'
$setting = Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting | Select-Object -First 1
if (-not $setting) {{ throw 'Win32_TerminalServiceSetting provider is unavailable.' }}
$result = Invoke-CimMethod -InputObject $setting -MethodName SetAllowTSConnections -Arguments @{{AllowTSConnections=1;ModifyFirewallException=1}}
if ($null -ne $result.ReturnValue -and [int]$result.ReturnValue -ne 0) {{ throw "SetAllowTSConnections failed with return value $($result.ReturnValue)" }}
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 0
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name fEnableWinStation -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber -Type DWord -Value 3389
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name SecurityLayer -Type DWord -Value 2
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name LanAdapter -Type DWord -Value 0
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic
Start-Service -Name TermService -ErrorAction SilentlyContinue
Start-Service -Name SessionEnv -ErrorAction SilentlyContinue
"""
    return _powershell(script, timeout=60)


def enable_rdp() -> dict:
    completed = _enable_with_windows_provider()
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to enable RDP").strip())
    for _ in range(10):
        status = get_rdp_status()
        if status.get("healthy"):
            return status
        time.sleep(1)
    return get_rdp_status()


def repair_rdp() -> dict:
    before = get_rdp_diagnostics()
    script = rf"""
$ErrorActionPreference = 'Stop'

# Use Windows' supported Terminal Services provider so Windows itself updates
# the effective Remote Desktop configuration and firewall exceptions.
$setting = Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting | Select-Object -First 1
if (-not $setting) {{ throw 'Win32_TerminalServiceSetting provider is unavailable.' }}
$result = Invoke-CimMethod -InputObject $setting -MethodName SetAllowTSConnections -Arguments @{{AllowTSConnections=1;ModifyFirewallException=1}}
if ($null -ne $result.ReturnValue -and [int]$result.ReturnValue -ne 0) {{ throw "SetAllowTSConnections failed with return value $($result.ReturnValue)" }}

# If an explicit local policy value is blocking RDP, repair it as part of this
# administrator-requested operation. Domain policy may reapply it later; the
# diagnostics panel will make that visible.
if (Test-Path -LiteralPath '{RDP_POLICY_KEY}') {{
  $policy = Get-ItemProperty -LiteralPath '{RDP_POLICY_KEY}' -ErrorAction SilentlyContinue
  if ($null -ne $policy.fDenyTSConnections -and [int]$policy.fDenyTSConnections -eq 1) {{
    Set-ItemProperty -LiteralPath '{RDP_POLICY_KEY}' -Name fDenyTSConnections -Type DWord -Value 0
  }}
}}

Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 0
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name fEnableWinStation -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber -Type DWord -Value 3389
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Type DWord -Value 1
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name SecurityLayer -Type DWord -Value 2
Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name LanAdapter -Type DWord -Value 0
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
Set-Service -Name TermService -StartupType Automatic
Set-Service -Name SessionEnv -StartupType Manual -ErrorAction SilentlyContinue

Stop-Service -Name UmRdpService -Force -ErrorAction SilentlyContinue
Stop-Service -Name TermService -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service -Name TermService
Start-Service -Name SessionEnv -ErrorAction SilentlyContinue
Start-Service -Name UmRdpService -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
"""
    completed = _powershell(script, timeout=75)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to repair RDP").strip())

    samples = []
    for delay in (0, 2, 5, 10, 15):
        if delay:
            time.sleep(delay)
        sample = get_rdp_status()
        samples.append({"afterSeconds": sum((0, 2, 5, 10, 15)[: len(samples) + 1]), "status": sample})
        if sample.get("healthy"):
            break

    after = get_rdp_diagnostics()
    if after.get("healthy"):
        summary = "RDP listener restored and is listening on TCP 3389."
    elif after.get("policyBlocksRdp"):
        summary = "Windows policy is blocking Remote Desktop. The local repair was applied, but policy still reports RDP denied."
    elif before.get("policyDenyTSConnections") == 1 and after.get("policyDenyTSConnections") == 0:
        summary = "A policy override blocking RDP was corrected, but Windows still did not keep the rdp-tcp listener active."
    else:
        summary = "Repair completed through the Windows Terminal Services provider, but Windows still did not keep the rdp-tcp listener active. Review diagnostics/events."

    return {
        "repaired": bool(after.get("healthy")),
        "before": before,
        "after": after,
        "samples": samples,
        "summary": summary,
    }


def disable_rdp() -> dict:
    script = rf"""
$ErrorActionPreference = 'Stop'
$setting = Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting -ErrorAction SilentlyContinue | Select-Object -First 1
if ($setting) {{
  $result = Invoke-CimMethod -InputObject $setting -MethodName SetAllowTSConnections -Arguments @{{AllowTSConnections=0;ModifyFirewallException=1}}
}}
Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 1
Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
"""
    completed = _powershell(script)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Unable to disable RDP").strip())
    return get_rdp_status()
