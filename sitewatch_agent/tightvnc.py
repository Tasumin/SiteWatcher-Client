from __future__ import annotations

import json
import os
import secrets
import socket
import string
import subprocess
import tempfile
import urllib.request

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# TightVNC publishes its current Windows MSI at this stable download URL.
TIGHTVNC_MSI_URL = os.getenv("SITEWATCH_TIGHTVNC_MSI_URL", "https://www.tightvnc.com/download/2.8.85/tightvnc-2.8.85-gpl-setup-64bit.msi")
SERVICE_NAMES = ("tvnserver", "TightVNC Server")


def _ps(script: str, timeout: int = 120):
    return subprocess.run(["powershell.exe","-NoLogo","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",script],capture_output=True,text=True,errors="replace",timeout=timeout,creationflags=CREATE_NO_WINDOW)


def _tcp(port: int = 5900) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1): return True
    except OSError: return False


def get_tightvnc_status() -> dict:
    script = r"""
$svc=Get-Service -Name tvnserver -ErrorAction SilentlyContinue
$exe=@('C:\Program Files\TightVNC\tvnserver.exe','C:\Program Files (x86)\TightVNC\tvnserver.exe')|?{Test-Path $_}|Select-Object -First 1
$ver=$null;if($exe){$ver=(Get-Item $exe).VersionInfo.ProductVersion}
$l=@(Get-NetTCPConnection -LocalPort 5900 -State Listen -ErrorAction SilentlyContinue|%{[pscustomobject]@{address=[string]$_.LocalAddress;port=[int]$_.LocalPort;pid=[int]$_.OwningProcess}})
[pscustomobject]@{installed=[bool]($svc -or $exe);serviceName=if($svc){$svc.Name}else{$null};serviceStatus=if($svc){[string]$svc.Status}else{'Missing'};startType=if($svc){[string]$svc.StartType}else{$null};exe=$exe;version=$ver;listening=($l.Count-gt 0);listeners=$l;port=5900}|ConvertTo-Json -Depth 5 -Compress
"""
    c=_ps(script,30)
    if c.returncode != 0: raise RuntimeError((c.stderr or c.stdout or "Unable to query TightVNC").strip())
    data=json.loads((c.stdout or "{}").strip() or "{}")
    data["host"]="127.0.0.1"; data["socketReachable"]=_tcp(5900); data["ready"]=bool(data.get("installed") and data.get("serviceStatus")=="Running" and data.get("listening")); return data


def _password(length: int = 16) -> str:
    alphabet=string.ascii_letters+string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def install_tightvnc(password: str | None = None) -> dict:
    if os.name != "nt": raise RuntimeError("TightVNC installation is supported only on Windows agents.")
    password=password or _password()
    msi=os.path.join(tempfile.gettempdir(),"sitewatcher-tightvnc.msi")
    try:
        urllib.request.urlretrieve(TIGHTVNC_MSI_URL,msi)
        # TightVNC MSI properties configure the service and authentication without opening a public router/NAT port.
        args=["msiexec.exe","/i",msi,"/quiet","/norestart","ADDLOCAL=Server","SET_USEVNCAUTHENTICATION=1","VALUE_OF_USEVNCAUTHENTICATION=1","SET_PASSWORD=1",f"VALUE_OF_PASSWORD={password}","SET_USECONTROLAUTHENTICATION=1","VALUE_OF_USECONTROLAUTHENTICATION=1","SET_CONTROLPASSWORD=1",f"VALUE_OF_CONTROLPASSWORD={password}"]
        c=subprocess.run(args,capture_output=True,text=True,errors="replace",timeout=180,creationflags=CREATE_NO_WINDOW)
        if c.returncode not in (0,3010): raise RuntimeError((c.stderr or c.stdout or f"TightVNC MSI exited with code {c.returncode}").strip())
        _ps("Set-Service tvnserver -StartupType Automatic -ErrorAction SilentlyContinue; Start-Service tvnserver -ErrorAction SilentlyContinue",30)
        status=get_tightvnc_status(); status["generatedPassword"]=password; status["restartRequired"]=(c.returncode==3010); return status
    finally:
        try: os.remove(msi)
        except OSError: pass


def restart_tightvnc() -> dict:
    c=_ps("Restart-Service tvnserver -Force -ErrorAction Stop; Start-Sleep -Seconds 2",30)
    if c.returncode != 0: raise RuntimeError((c.stderr or c.stdout or "Unable to restart TightVNC").strip())
    return get_tightvnc_status()


def uninstall_tightvnc() -> dict:
    script=r"""$p=Get-CimInstance Win32_Product -ErrorAction SilentlyContinue|?{$_.Name -like 'TightVNC*'}|Select-Object -First 1;if($p){$r=Invoke-CimMethod -InputObject $p -MethodName Uninstall;if($r.ReturnValue -ne 0){throw "TightVNC uninstall returned $($r.ReturnValue)"}}else{throw 'TightVNC is not installed.'}"""
    c=_ps(script,180)
    if c.returncode != 0: raise RuntimeError((c.stderr or c.stdout or "Unable to uninstall TightVNC").strip())
    return get_tightvnc_status()
