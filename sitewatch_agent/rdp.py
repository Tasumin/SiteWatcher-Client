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
RDP_RELATED_LOGS = (
    "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational",
    "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational",
    "Microsoft-Windows-RemoteDesktopServices-RdpCoreTS/Operational",
)


def _powershell(script: str, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
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
        with socket.create_connection((host, port), timeout=1): return True
    except OSError: return False


def get_rdp_diagnostics() -> dict:
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$ts=Get-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}'; $rdp=Get-ItemProperty -LiteralPath '{RDP_TCP_KEY}'; $policy=Get-ItemProperty -LiteralPath '{RDP_POLICY_KEY}' -ErrorAction SilentlyContinue
$term=Get-CimInstance Win32_Service -Filter "Name='TermService'"; $session=Get-CimInstance Win32_Service -Filter "Name='SessionEnv'"; $um=Get-CimInstance Win32_Service -Filter "Name='UmRdpService'"
$fw=@(Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue); $port=if($rdp.PortNumber){{[int]$rdp.PortNumber}}else{{3389}}
$listeners=@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue|%{{[pscustomobject]@{{address=[string]$_.LocalAddress;port=[int]$_.LocalPort;pid=[int]$_.OwningProcess}}}})
$qwinsta=(& qwinsta.exe 2>&1|Out-String).Trim()
$events=@(Get-WinEvent -LogName '{RDP_EVENT_LOG}' -MaxEvents 30 -ErrorAction SilentlyContinue|%{{[pscustomobject]@{{time=$_.TimeCreated.ToString('o');id=$_.Id;level=$_.LevelDisplayName;message=($_.Message -replace '\r?\n',' ')}}}})
$edition=Get-ComputerInfo -Property WindowsProductName,WindowsEditionId,OsName,OsVersion -ErrorAction SilentlyContinue
$tsSetting=Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting -ErrorAction SilentlyContinue|Select-Object -First 1
[pscustomobject]@{{enabled=($ts.fDenyTSConnections-eq 0);fDenyTSConnections=$ts.fDenyTSConnections;fEnableWinStation=$rdp.fEnableWinStation;nla=($rdp.UserAuthentication-eq 1);userAuthentication=$rdp.UserAuthentication;securityLayer=$rdp.SecurityLayer;minEncryptionLevel=$rdp.MinEncryptionLevel;lanAdapter=$rdp.LanAdapter;port=$port;serviceStatus=if($term){{$term.State}}else{{'Missing'}};termServiceStartMode=if($term){{$term.StartMode}}else{{'Missing'}};sessionEnvStatus=if($session){{$session.State}}else{{'Missing'}};umRdpServiceStatus=if($um){{$um.State}}else{{'Missing'}};firewallEnabled=(@($fw|?{{$_.Enabled-eq'True'}}).Count-gt 0);listening=($listeners.Count-gt 0);listeners=$listeners;qwinsta=$qwinsta;events=$events;windowsProductName=$edition.WindowsProductName;windowsEditionId=$edition.WindowsEditionId;osName=$edition.OsName;osVersion=$edition.OsVersion;policyDenyTSConnections=if($policy-and$null-ne$policy.fDenyTSConnections){{[int]$policy.fDenyTSConnections}}else{{$null}};cimProviderAvailable=[bool]$tsSetting;cimAllowTSConnections=if($tsSetting){{[int]$tsSetting.AllowTSConnections}}else{{$null}}}}|ConvertTo-Json -Depth 7 -Compress
"""
    status=_run_json(script,"Unable to collect RDP diagnostics",60); port=int(status.get("port") or 3389)
    status["host"]="127.0.0.1"; status["socketReachable"]=_socket_reachable("127.0.0.1",port); status["listenerPresent"]="rdp-tcp" in str(status.get("qwinsta") or "").lower(); status["policyBlocksRdp"]=status.get("policyDenyTSConnections")==1; status["healthy"]=bool(status.get("enabled") and status.get("listening") and status.get("listenerPresent")); return status


def get_rdp_status() -> dict:
    d=get_rdp_diagnostics(); keys=("enabled","nla","serviceStatus","firewallEnabled","listening","port","host","socketReachable","listenerPresent","healthy","listeners","policyDenyTSConnections","policyBlocksRdp","cimProviderAvailable","cimAllowTSConnections"); return {k:d.get(k) for k in keys}


def _enable_with_windows_provider():
    script=rf"""$ErrorActionPreference='Stop';$s=Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting|Select-Object -First 1;if(-not$s){{throw 'Win32_TerminalServiceSetting provider is unavailable.'}};$r=Invoke-CimMethod -InputObject $s -MethodName SetAllowTSConnections -Arguments @{{AllowTSConnections=1;ModifyFirewallException=1}};if($null-ne$r.ReturnValue-and[int]$r.ReturnValue-ne 0){{throw "SetAllowTSConnections failed: $($r.ReturnValue)"}};Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 0;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name fEnableWinStation -Type DWord -Value 1;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber -Type DWord -Value 3389;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Type DWord -Value 1;Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue;Set-Service TermService -StartupType Automatic;Start-Service TermService -ErrorAction SilentlyContinue;Start-Service SessionEnv -ErrorAction SilentlyContinue"""
    return _powershell(script,60)


def enable_rdp() -> dict:
    c=_enable_with_windows_provider()
    if c.returncode!=0: raise RuntimeError((c.stderr or c.stdout or "Unable to enable RDP").strip())
    for _ in range(10):
        s=get_rdp_status()
        if s.get("healthy"): return s
        time.sleep(1)
    return get_rdp_status()


def _collect_lifecycle_events(since_iso: str) -> list:
    logs_ps=",".join("'"+x.replace("'","''")+"'" for x in RDP_RELATED_LOGS)
    script=rf"""
$ErrorActionPreference='SilentlyContinue';$since=[datetime]::Parse('{since_iso}');$items=@();foreach($log in @({logs_ps})){{$items+=Get-WinEvent -FilterHashtable @{{LogName=$log;StartTime=$since}} -ErrorAction SilentlyContinue|%{{[pscustomobject]@{{time=$_.TimeCreated.ToString('o');log=$log;provider=$_.ProviderName;id=$_.Id;level=$_.LevelDisplayName;message=($_.Message -replace '\r?\n',' ')}}}}}};$items+=Get-WinEvent -FilterHashtable @{{LogName='System';StartTime=$since;ProviderName='Service Control Manager'}} -ErrorAction SilentlyContinue|?{{$_.Message -match 'TermService|Remote Desktop|Remote Desktop Services|SessionEnv|UmRdpService'}}|%{{[pscustomobject]@{{time=$_.TimeCreated.ToString('o');log='System';provider=$_.ProviderName;id=$_.Id;level=$_.LevelDisplayName;message=($_.Message -replace '\r?\n',' ')}}}};$items|Sort-Object time|ConvertTo-Json -Depth 5 -Compress
"""
    c=_powershell(script,60)
    if c.returncode!=0 or not (c.stdout or "").strip(): return []
    try:
        data=json.loads(c.stdout.strip()); return data if isinstance(data,list) else [data]
    except json.JSONDecodeError: return []


def repair_rdp() -> dict:
    before=get_rdp_diagnostics(); started=time.strftime("%Y-%m-%dT%H:%M:%S",time.localtime())
    script=rf"""
$ErrorActionPreference='Stop';$s=Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting|Select-Object -First 1;if(-not$s){{throw 'Win32_TerminalServiceSetting provider is unavailable.'}};$r=Invoke-CimMethod -InputObject $s -MethodName SetAllowTSConnections -Arguments @{{AllowTSConnections=1;ModifyFirewallException=1}};if($null-ne$r.ReturnValue-and[int]$r.ReturnValue-ne 0){{throw "SetAllowTSConnections failed: $($r.ReturnValue)"}};Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 0;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name fEnableWinStation -Type DWord -Value 1;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name PortNumber -Type DWord -Value 3389;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name UserAuthentication -Type DWord -Value 1;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name SecurityLayer -Type DWord -Value 2;Set-ItemProperty -LiteralPath '{RDP_TCP_KEY}' -Name LanAdapter -Type DWord -Value 0;Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue;Set-Service TermService -StartupType Automatic;Stop-Service UmRdpService -Force -ErrorAction SilentlyContinue;Stop-Service TermService -Force -ErrorAction SilentlyContinue;Start-Sleep 2;Start-Service TermService;Start-Service SessionEnv -ErrorAction SilentlyContinue;Start-Service UmRdpService -ErrorAction SilentlyContinue
"""
    c=_powershell(script,75)
    if c.returncode!=0: raise RuntimeError((c.stderr or c.stdout or "Unable to repair RDP").strip())
    samples=[]; began=time.monotonic(); ever=False; disappeared=False; previous=False
    for target in (1,5,10,20,30):
        wait=target-(time.monotonic()-began)
        if wait>0: time.sleep(wait)
        s=get_rdp_status(); active=bool(s.get("listenerPresent") or s.get("listening")); ever=ever or active
        if previous and not active: disappeared=True
        previous=active
        samples.append({"afterSeconds":target,"listenerPresent":s.get("listenerPresent"),"listening":s.get("listening"),"socketReachable":s.get("socketReachable"),"serviceStatus":s.get("serviceStatus"),"listeners":s.get("listeners")})
    after=get_rdp_diagnostics(); events=_collect_lifecycle_events(started)
    if after.get("healthy"): summary="RDP listener is healthy after the 30-second verification window."
    elif disappeared or (ever and not after.get("listenerPresent")): summary="RDP-Tcp started during repair but subsequently stopped. Review the lifecycle timeline and related Windows events below."
    elif ever: summary="RDP transport appeared during repair but was not healthy at the end of verification. Review the lifecycle timeline and Windows events."
    else: summary="Windows accepted the RDP configuration, but RDP-Tcp never appeared during the 30-second verification window. Review the related Windows events."
    return {"repaired":bool(after.get("healthy")),"before":before,"after":after,"samples":samples,"lifecycleEvents":events,"listenerEverStarted":ever,"listenerDisappeared":disappeared,"summary":summary}


def disable_rdp() -> dict:
    script=rf"""$ErrorActionPreference='Stop';$s=Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting -ErrorAction SilentlyContinue|Select-Object -First 1;if($s){{$null=Invoke-CimMethod -InputObject $s -MethodName SetAllowTSConnections -Arguments @{{AllowTSConnections=0;ModifyFirewallException=1}}}};Set-ItemProperty -LiteralPath '{TERMINAL_SERVER_KEY}' -Name fDenyTSConnections -Type DWord -Value 1;Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue"""
    c=_powershell(script)
    if c.returncode!=0: raise RuntimeError((c.stderr or c.stdout or "Unable to disable RDP").strip())
    return get_rdp_status()
