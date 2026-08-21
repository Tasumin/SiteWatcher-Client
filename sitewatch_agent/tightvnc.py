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
TIGHTVNC_VERSION = "2.8.88"
TIGHTVNC_MSI_URL = os.getenv(
    "SITEWATCH_TIGHTVNC_MSI_URL",
    f"https://www.tightvnc.com/download/{TIGHTVNC_VERSION}/tightvnc-{TIGHTVNC_VERSION}-gpl-setup-64bit.msi",
)


def _ps(script: str, timeout: int = 120):
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def _tcp(port: int = 5900) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def get_tightvnc_status() -> dict:
    script = r"""
$svc=Get-Service -Name tvnserver -ErrorAction SilentlyContinue
$exe=@('C:\Program Files\TightVNC\tvnserver.exe','C:\Program Files (x86)\TightVNC\tvnserver.exe')|?{Test-Path $_}|Select-Object -First 1
$ver=$null;if($exe){$ver=(Get-Item $exe).VersionInfo.ProductVersion}
$l=@(Get-NetTCPConnection -LocalPort 5900 -State Listen -ErrorAction SilentlyContinue|%{[pscustomobject]@{address=[string]$_.LocalAddress;port=[int]$_.LocalPort;pid=[int]$_.OwningProcess}})
[pscustomobject]@{installed=[bool]($svc -or $exe);serviceName=if($svc){$svc.Name}else{$null};serviceStatus=if($svc){[string]$svc.Status}else{'Missing'};startType=if($svc){[string]$svc.StartType}else{$null};exe=$exe;version=$ver;listening=($l.Count-gt 0);listeners=$l;port=5900}|ConvertTo-Json -Depth 5 -Compress
"""
    c = _ps(script, 30)
    if c.returncode != 0:
        raise RuntimeError((c.stderr or c.stdout or "Unable to query TightVNC").strip())
    data = json.loads((c.stdout or "{}").strip() or "{}")
    data["host"] = "127.0.0.1"
    data["socketReachable"] = _tcp(5900)
    data["ready"] = bool(data.get("installed") and data.get("serviceStatus") == "Running" and data.get("listening"))
    return data


def _password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def install_tightvnc(password: str | None = None) -> dict:
    if os.name != "nt":
        raise RuntimeError("TightVNC installation is supported only on Windows agents.")

    existing = get_tightvnc_status()
    if existing.get("installed"):
        existing["alreadyInstalled"] = True
        return existing

    password = password or _password()
    temp_dir = tempfile.gettempdir()
    msi = os.path.join(temp_dir, f"tightvnc-{TIGHTVNC_VERSION}-64bit.msi")
    msi_log = os.path.join(temp_dir, "sitewatcher-tightvnc-install.log")

    try:
        urllib.request.urlretrieve(TIGHTVNC_MSI_URL, msi)
        if not os.path.exists(msi) or os.path.getsize(msi) < 100000:
            raise RuntimeError("Downloaded TightVNC MSI is missing or unexpectedly small.")

        args = [
            "msiexec.exe", "/i", msi,
            "/quiet", "/norestart", "/L*v", msi_log,
            "ADDLOCAL=Server",
            "SERVER_REGISTER_AS_SERVICE=1",
            "SERVER_ADD_FIREWALL_EXCEPTION=0",
            "SET_ACCEPTRFBCONNECTIONS=1", "VALUE_OF_ACCEPTRFBCONNECTIONS=1",
            "SET_ALLOWLOOPBACK=1", "VALUE_OF_ALLOWLOOPBACK=1",
            "SET_LOOPBACKONLY=1", "VALUE_OF_LOOPBACKONLY=1",
            "SET_RFBPORT=1", "VALUE_OF_RFBPORT=5900",
            "SET_RUNCONTROLINTERFACE=1", "VALUE_OF_RUNCONTROLINTERFACE=0",
            "SET_USEVNCAUTHENTICATION=1", "VALUE_OF_USEVNCAUTHENTICATION=1",
            "SET_PASSWORD=1", f"VALUE_OF_PASSWORD={password}",
            "SET_USECONTROLAUTHENTICATION=1", "VALUE_OF_USECONTROLAUTHENTICATION=1",
            "SET_CONTROLPASSWORD=1", f"VALUE_OF_CONTROLPASSWORD={password}",
        ]
        c = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=240,
            creationflags=CREATE_NO_WINDOW,
        )
        if c.returncode not in (0, 3010):
            tail = ""
            try:
                with open(msi_log, "r", encoding="utf-16", errors="replace") as handle:
                    tail = "\n".join(handle.read().splitlines()[-40:])
            except Exception:
                try:
                    with open(msi_log, "r", encoding="utf-8", errors="replace") as handle:
                        tail = "\n".join(handle.read().splitlines()[-40:])
                except Exception:
                    pass
            detail = (c.stderr or c.stdout or "").strip()
            raise RuntimeError(f"TightVNC MSI exited with code {c.returncode}. {detail}\n{tail}".strip())

        _ps("Set-Service tvnserver -StartupType Automatic -ErrorAction SilentlyContinue; Start-Service tvnserver -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3", 30)
        status = get_tightvnc_status()
        status["generatedPassword"] = password
        status["restartRequired"] = c.returncode == 3010
        status["installerVersion"] = TIGHTVNC_VERSION
        if not status.get("installed"):
            raise RuntimeError("TightVNC MSI completed successfully, but the TightVNC service/executable was not detected afterward.")
        return status
    finally:
        try:
            os.remove(msi)
        except OSError:
            pass


def restart_tightvnc() -> dict:
    c = _ps("Restart-Service tvnserver -Force -ErrorAction Stop; Start-Sleep -Seconds 2", 30)
    if c.returncode != 0:
        raise RuntimeError((c.stderr or c.stdout or "Unable to restart TightVNC").strip())
    return get_tightvnc_status()


def uninstall_tightvnc() -> dict:
    script = r"""
$uninstall = @(
 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
) | ForEach-Object { Get-ItemProperty $_ -ErrorAction SilentlyContinue } |
    Where-Object { $_.DisplayName -like 'TightVNC*' } | Select-Object -First 1
if(-not $uninstall){ throw 'TightVNC is not installed.' }
$productCode = $uninstall.PSChildName
$p = Start-Process msiexec.exe -ArgumentList @('/x',$productCode,'/quiet','/norestart') -Wait -PassThru
if($p.ExitCode -notin 0,3010){ throw "TightVNC uninstall exited with code $($p.ExitCode)" }
"""
    c = _ps(script, 180)
    if c.returncode != 0:
        raise RuntimeError((c.stderr or c.stdout or "Unable to uninstall TightVNC").strip())
    return get_tightvnc_status()
