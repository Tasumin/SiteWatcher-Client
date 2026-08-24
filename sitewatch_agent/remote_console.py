import concurrent.futures
import ipaddress
import json
import os
import socket
import subprocess
import time

import requests

from .agent_logs import collect_agent_logs, create_agent_logs_zip
from .tightvnc import get_tightvnc_status, install_tightvnc, restart_tightvnc, uninstall_tightvnc
from .virtual_display import get_virtual_display_status, manage_virtual_display

SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
TOKEN = os.environ["SITEWATCH_AGENT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
UPDATE_COMMAND = "__SITEWATCH_UPDATE_AGENT__"
SCAN_PREFIX = "__SITEWATCH_IP_SCAN__|"
LOG_PREFIX = "__SITEWATCH_GET_LOGS__|"
LOG_BUNDLE_COMMAND = "__SITEWATCH_DOWNLOAD_LOGS__"
VNC_STATUS_COMMAND = "__SITEWATCH_VNC_STATUS__"
VNC_INSTALL_COMMAND = "__SITEWATCH_VNC_INSTALL__"
VNC_RESTART_COMMAND = "__SITEWATCH_VNC_RESTART__"
VNC_UNINSTALL_COMMAND = "__SITEWATCH_VNC_UNINSTALL__"
VDD_STATUS_COMMAND = "__SITEWATCH_VDD_STATUS__"
VDD_INSTALL_COMMAND = "__SITEWATCH_VDD_INSTALL__"
VDD_ENABLE_COMMAND = "__SITEWATCH_VDD_ENABLE__"
VDD_DISABLE_COMMAND = "__SITEWATCH_VDD_DISABLE__"
VDD_REPAIR_COMMAND = "__SITEWATCH_VDD_REPAIR__"
SCAN_PORTS = (22, 53, 80, 443, 554, 8000, 8080, 9000)
BLOCKED_TOKENS = (";", "&&", "||", "|", ">", "<", "`", "$(", "@(")
ALLOWED_PREFIXES = ("ping ","ping.exe ","tracert ","tracert.exe ","pathping ","pathping.exe ","nslookup ","nslookup.exe ","curl ","curl.exe ","arp ","arp.exe ","ipconfig","route print","route.exe print","netstat ","netstat.exe ","test-netconnection ","resolve-dnsname ","get-netipaddress","get-netroute","get-netadapter","get-nettcpconnection","get-netneighbor","get-dnsclient","get-dnsclientserveraddress","invoke-webrequest ","invoke-restmethod ")

def _allowed(command:str):
    text=command.strip(); lower=text.lower()
    if not text:return False,"Command is empty."
    if any(t in text for t in BLOCKED_TOKENS):return False,"Command chaining, pipelines, redirection, and subexpressions are disabled in Remote Console."
    if "\n" in text or "\r" in text:return False,"Only one diagnostic command may be run at a time."
    if not any(lower==p.strip() or lower.startswith(p) for p in ALLOWED_PREFIXES):return False,"Command is not in the SiteWatcher diagnostic allowlist."
    return True,""

def _run(command,shell,timeout_seconds=60):
    ok,reason=_allowed(command)
    if not ok:return {"stdout":"","stderr":reason,"exitCode":None,"rejected":True}
    argv=["cmd.exe","/d","/s","/c",command] if shell=="cmd" else ["powershell.exe","-NoLogo","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",command]
    try:
        c=subprocess.run(argv,capture_output=True,text=True,errors="replace",timeout=timeout_seconds,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0));return {"stdout":(c.stdout or "")[:200000],"stderr":(c.stderr or "")[:200000],"exitCode":c.returncode}
    except subprocess.TimeoutExpired as e:return {"stdout":str(e.stdout or "")[:200000],"stderr":"Command timed out after 60 seconds.","exitCode":None,"timedOut":True}
    except Exception as e:return {"stdout":"","stderr":str(e),"exitCode":1}

def _launch_self_update():
    from .update_launcher import launch_self_update
    return launch_self_update()

def _scan_host(ip, ports):
    found=[]
    for port in ports:
        try:
            with socket.create_connection((ip,port),timeout=.25):found.append(port)
        except OSError:pass
    alive=bool(found)
    if not alive:
        try:alive=subprocess.run(["ping.exe","-n","1","-w","350",ip],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)).returncode==0
        except Exception:pass
    if not alive:return None
    try:hostname=socket.gethostbyaddr(ip)[0]
    except Exception:hostname=""
    return {"ip":ip,"hostname":hostname,"ports":found}

def _scan_network(cidr, ports=None):
    n=ipaddress.ip_network(cidr.strip(),strict=False)
    if n.version!=4:raise ValueError("Only IPv4 networks are supported.")
    if n.prefixlen<24:raise ValueError("Remote IP Scanner is limited to /24 or smaller networks.")
    scan_ports=[]
    for value in (ports or SCAN_PORTS):
        port=int(value)
        if port<1 or port>65535:raise ValueError(f"Invalid TCP port: {port}")
        if port not in scan_ports:scan_ports.append(port)
    if len(scan_ports)>72:raise ValueError("Too many scan ports requested.")
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        r=[x for x in pool.map(lambda ip:_scan_host(ip,scan_ports),[str(i) for i in n.hosts()]) if x]
    r.sort(key=lambda row:tuple(int(x) for x in row["ip"].split(".")));return r

def _post_result(command_id,result):
    result["id"]=command_id;r=requests.post(SERVER+"/api/agent/commands",headers=HEADERS,json=result,timeout=20);r.raise_for_status()

def _upload_log_bundle(command_id):
    z=create_agent_logs_zip()
    try:
        with open(z,"rb") as h:r=requests.post(SERVER+f"/api/agent/log-bundles?id={command_id}",headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/zip"},data=h,timeout=180);r.raise_for_status()
    finally:
        try:os.remove(z)
        except OSError:pass

def _handle_vnc(command):
    if command==VNC_STATUS_COMMAND:r=get_tightvnc_status()
    elif command==VNC_INSTALL_COMMAND:r=install_tightvnc()
    elif command==VNC_RESTART_COMMAND:r=restart_tightvnc()
    elif command==VNC_UNINSTALL_COMMAND:r=uninstall_tightvnc()
    else:raise ValueError("Unknown TightVNC maintenance command")
    return {"stdout":json.dumps(r),"stderr":"","exitCode":0}

def _handle_vdd(command):
    if command==VDD_STATUS_COMMAND:r=get_virtual_display_status()
    elif command==VDD_INSTALL_COMMAND:r=manage_virtual_display("install")
    elif command==VDD_ENABLE_COMMAND:r=manage_virtual_display("enable")
    elif command==VDD_DISABLE_COMMAND:r=manage_virtual_display("disable")
    elif command==VDD_REPAIR_COMMAND:r=manage_virtual_display("repair")
    else:raise ValueError("Unknown virtual display maintenance command")
    return {"stdout":json.dumps(r),"stderr":"","exitCode":0}

def remote_console_loop():
    time.sleep(3)
    while True:
        try:
            response=requests.get(SERVER+"/api/agent/commands",headers=HEADERS,timeout=20)
            if not response.ok:
                if response.status_code!=404:print(f"[console] server HTTP {response.status_code}: {response.text[:200]}",flush=True)
                time.sleep(3);continue
            item=response.json().get("command")
            if not item:time.sleep(2);continue
            command_id=str(item.get("id") or "");command=str(item.get("command") or "");shell=str(item.get("shell") or "powershell").lower()
            if command==UPDATE_COMMAND and shell=="system":
                try:_launch_self_update();_post_result(command_id,{"stdout":"SiteWatcher agent update launched as SYSTEM. See logs/update.log for detailed progress.","stderr":"","exitCode":0})
                except Exception as e:_post_result(command_id,{"stdout":"","stderr":f"Unable to start SiteWatcher update: {e}","exitCode":1})
                time.sleep(10);continue
            if command in (VNC_STATUS_COMMAND,VNC_INSTALL_COMMAND,VNC_RESTART_COMMAND,VNC_UNINSTALL_COMMAND) and shell=="system":
                print(f"[vnc] maintenance command={command} job id={command_id[:8]}",flush=True)
                try:_post_result(command_id,_handle_vnc(command))
                except Exception as e:_post_result(command_id,{"stdout":"","stderr":f"TightVNC operation failed: {e}","exitCode":1})
                continue
            if command in (VDD_STATUS_COMMAND,VDD_INSTALL_COMMAND,VDD_ENABLE_COMMAND,VDD_DISABLE_COMMAND,VDD_REPAIR_COMMAND) and shell=="system":
                print(f"[vdd] maintenance command={command} job id={command_id[:8]}",flush=True)
                try:_post_result(command_id,_handle_vdd(command))
                except Exception as e:_post_result(command_id,{"stdout":"","stderr":f"Virtual display operation failed: {e}","exitCode":1})
                continue
            if command==LOG_BUNDLE_COMMAND and shell=="system":
                try:_upload_log_bundle(command_id);_post_result(command_id,{"stdout":"Full log bundle ready for download.","stderr":"","exitCode":0})
                except Exception as e:_post_result(command_id,{"stdout":"","stderr":f"Unable to upload full log bundle: {e}","exitCode":1})
                continue
            if command.startswith(LOG_PREFIX) and shell=="system":
                try:lines=max(50,min(1000,int(command[len(LOG_PREFIX):].strip() or "250")))
                except ValueError:lines=250
                try:_post_result(command_id,{"stdout":collect_agent_logs(lines),"stderr":"","exitCode":0})
                except Exception as e:_post_result(command_id,{"stdout":"","stderr":f"Unable to collect agent logs: {e}","exitCode":1})
                continue
            if command.startswith(SCAN_PREFIX) and shell=="system":
                try:
                    payload=command[len(SCAN_PREFIX):]
                    parts=payload.split("|",1)
                    cidr=parts[0].strip()
                    ports=None
                    if len(parts)>1 and parts[1].strip():ports=[int(x) for x in parts[1].split(",") if x.strip()]
                    hosts=_scan_network(cidr,ports)
                    _post_result(command_id,{"stdout":json.dumps({"cidr":cidr,"ports":ports or list(SCAN_PORTS),"hosts":hosts}),"stderr":"","exitCode":0})
                except Exception as e:_post_result(command_id,{"stdout":"","stderr":str(e),"exitCode":1})
                continue
            _post_result(command_id,_run(command,shell))
        except Exception as e:print(f"[console] worker error: {e}",flush=True);time.sleep(5)
