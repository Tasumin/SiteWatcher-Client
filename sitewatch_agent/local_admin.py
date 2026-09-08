from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from . import __version__
from .agent_logs import collect_agent_log, create_agent_logs_zip

STARTED_AT = time.time()
ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs"
AI_EVIDENCE_DIR = ROOT / "data" / "ai-evidence"

SAFE_CONFIG_KEYS = {
    "SITEWATCH_DISCOVERY_CIDRS",
    "SITEWATCH_DISCOVERY_INTERVAL_SECONDS",
    "SITEWATCH_SNAPSHOT_INTERVAL_SECONDS",
    "SITEWATCH_MONITOR_WORKERS",
    "SITEWATCH_SNAPSHOT_WORKERS",
    "SITEWATCH_LOCAL_ADMIN_ENABLED",
    "SITEWATCH_LOCAL_ADMIN_PORT",
    "SITEWATCH_LOCAL_ADMIN_LAN_ACCESS",
    "SITEWATCH_BETA_AI_DETECTION",
    "SITEWATCH_AI_MODEL_PATH",
    "SITEWATCH_AI_LABELS_PATH",
}
SECRET_KEYS = {"SITEWATCH_AGENT_TOKEN"}

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NodeVyu Agent</title>
<style>
:root{color-scheme:light;--blue:#2167b2;--line:#d9e4ef;--muted:#66778a;--bg:#f4f8fc;--good:#18794e;--warn:#9a6700;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#1f2d3d;font-family:Arial,Helvetica,sans-serif}.wrap{max-width:1180px;margin:auto;padding:24px}
header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}.brand h1{margin:0 0 4px;font-size:28px}.brand p{margin:0;color:var(--muted)}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 3px 12px rgba(30,70,110,.05)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.metric{padding:12px;border:1px solid var(--line);border-radius:10px;background:#f9fbfe}.metric b,.metric span{display:block}.metric span{font-size:12px;color:var(--muted);margin-bottom:5px}
button,.button{border:1px solid #1d5b9b;background:var(--blue);color:#fff;border-radius:8px;padding:9px 12px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}.secondary{background:#fff;color:#24415f;border-color:#b9c9d9}.danger{background:#b42318;border-color:#b42318}button:disabled{opacity:.55;cursor:not-allowed}
.actions{display:flex;gap:8px;flex-wrap:wrap}.pill{display:inline-block;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:700;background:#edf2f7}.good{background:#e7f6ee;color:var(--good)}.warn{background:#fff4d6;color:var(--warn)}.bad{background:#feeceb;color:var(--bad)}
h2{font-size:18px;margin:0 0 12px}.muted{color:var(--muted);font-size:13px}pre{background:#0d1724;color:#e8eef6;border-radius:10px;padding:14px;min-height:260px;max-height:520px;overflow:auto;white-space:pre-wrap;word-break:break-word}
label{display:block;font-size:12px;color:var(--muted);font-weight:700}input,select{width:100%;margin-top:5px;padding:9px;border:1px solid #c9d6e3;border-radius:8px;background:#fff;color:#1f2d3d}.configGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.notice{padding:10px 12px;border-radius:8px;background:#eef5fd;border:1px solid #c8ddf3;margin-top:10px;white-space:pre-wrap}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid #edf2f7}.row:first-child{border-top:0}.row span:first-child{color:var(--muted)}
@media(max-width:700px){.wrap{padding:12px}header{flex-direction:column}.actions button{flex:1}}
</style>
</head>
<body><div class="wrap">
<header><div class="brand"><h1>NodeVyu Agent</h1><p>Local troubleshooting and recovery interface</p></div><span id="platform" class="pill">loading</span></header>

<section class="card"><h2>Agent Status</h2><div id="status" class="grid"></div><div class="actions" style="margin-top:12px"><button onclick="loadAll()">Refresh</button><button class="secondary" onclick="action('restart_agent')">Restart Agent</button><button class="secondary" onclick="action('update_agent')">Update Agent</button></div><div id="actionMessage"></div></section>

<section class="card"><h2>Connectivity</h2><div id="connectivity" class="grid"></div><div class="actions" style="margin-top:12px"><button class="secondary" onclick="loadConnectivity()">Run Connectivity Test</button></div></section>

<section class="card"><h2>AI Detection <span class="pill warn">Beta</span></h2><div id="aiStatus" class="grid"></div><div class="actions" style="margin-top:12px"><select id="aiCamera" style="max-width:360px"></select><button class="secondary" onclick="runAiTest()">Run Detection Test</button></div><div id="aiTest"></div><p class="muted" style="margin-top:10px">AI testing is local to this agent. A test captures one inference frame plus a short evidence clip and does not create a NodeVyu event.</p></section>

<section class="card"><h2>Remote Access</h2><div id="remoteAccess"></div></section>

<section class="card"><h2>Logs</h2><div class="actions"><select id="logFile" style="max-width:340px" onchange="loadLog()"></select><select id="logLines" style="max-width:140px" onchange="loadLog()"><option>100</option><option selected>250</option><option>500</option><option>1000</option></select><button class="secondary" onclick="loadLog()">Refresh Log</button><a class="button secondary" href="/api/log-bundle">Download Bundle</a></div><pre id="logOutput">Loading logs…</pre></section>

<section class="card"><h2>Safe Configuration</h2><p class="muted">Secrets are never shown here. Changes take effect after the agent restarts.</p><div id="config" class="configGrid"></div><div class="actions" style="margin-top:12px"><button onclick="saveConfig()">Save Configuration</button></div><div id="configMessage"></div></section>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function j(path,opts={}){const r=await fetch(path,{cache:"no-store",headers:{"Content-Type":"application/json",...(opts.headers||{})},...opts});const b=await r.json().catch(()=>({}));if(!r.ok)throw new Error(b.error||("HTTP "+r.status));return b}
function metric(label,value,klass=""){return '<div class="metric"><span>'+esc(label)+'</span><b class="'+klass+'">'+esc(value)+'</b></div>'}
async function loadStatus(){try{const s=await j("/api/status");$("platform").textContent=s.platform.system+" "+s.platform.release;$("status").innerHTML=metric("Installed version",s.version)+metric("Latest version",s.latestVersion||"Unknown",s.updateAvailable?"warn":"good")+metric("Update status",s.updateAvailable?"Update available":"Current",s.updateAvailable?"warn":"good")+metric("Hostname",s.hostname)+metric("Agent service",s.service.status,s.service.running?"good":"bad")+metric("PID",s.pid)+metric("Process uptime",s.uptime)+metric("Server",s.serverUrl)+metric("Local UI",s.localAdminUrl)}catch(e){$("status").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadConnectivity(){try{$("connectivity").innerHTML=metric("Status","Testing…");const c=await j("/api/connectivity");$("connectivity").innerHTML=metric("DNS",c.dns.ok?c.dns.addresses.join(", "):c.dns.error,c.dns.ok?"good":"bad")+metric("TCP "+c.tcp.port,c.tcp.ok?"Connected":c.tcp.error,c.tcp.ok?"good":"bad")+metric("NodeVyu API",c.api.ok?"Authenticated":c.api.error,c.api.ok?"good":"bad")}catch(e){$("connectivity").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadAi(){try{const p=await j("/api/ai"),cams=await j("/api/ai-cameras");const r=p.runtime||{},m=p.model||{};$("aiStatus").innerHTML=metric("Beta opt-in",p.enabled?"Enabled":"Disabled",p.enabled?"good":"warn")+metric("ONNX Runtime",r.installed?("v"+(r.version||"unknown")):"Not installed",r.ready?"good":"warn")+metric("Provider",r.preferredProvider||"None",r.ready?"good":"warn")+metric("Available providers",(r.providers||[]).join(", ")||"None")+metric("Detection model",m.present?"Ready":"Not installed",m.present?"good":"warn")+metric("Model path",m.path||"Not configured");$("aiCamera").innerHTML=(cams.cameras||[]).map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+'</option>').join("")||'<option value="">No standalone RTSP cameras assigned</option>'}catch(e){$("aiStatus").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function runAiTest(){const cameraId=$("aiCamera").value;if(!cameraId)return;try{$("aiTest").innerHTML='<div class="notice">Capturing frame, running inference, and recording evidence clip…</div>';const r=await j("/api/ai-test",{method:"POST",body:JSON.stringify({cameraId})});let h='<div class="notice"><b>'+esc(r.cameraName)+'</b> • '+esc(r.provider)+' • '+Number(r.metrics.totalMs||0).toFixed(1)+' ms total • '+Number(r.metrics.inferenceMs||0).toFixed(1)+' ms inference';if((r.detections||[]).length){h+='<div style="margin-top:8">'+r.detections.map(d=>esc(d.label)+' '+(Number(d.confidence||0)*100).toFixed(1)+'%').join('<br>')+'</div>'}else h+='<div style="margin-top:8">No matching detections.</div>';if(r.previewUrl)h+='<div style="margin-top:12px"><b>Detection preview</b><br><img src="'+esc(r.previewUrl)+'" style="margin-top:8px;max-width:100%;border-radius:10px;border:1px solid var(--line)"></div>';if(r.clipUrl)h+='<div style="margin-top:12px"><b>Evidence clip</b><br><video controls preload="metadata" src="'+esc(r.clipUrl)+'" style="margin-top:8px;max-width:100%;width:720px;border-radius:10px;border:1px solid var(--line)"></video></div>';h+='</div>';$("aiTest").innerHTML=h}catch(e){$("aiTest").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadRemote(){try{const r=await j("/api/remote-access");let h='<div class="grid">';if(r.platform==="linux"){h+=metric("OpenSSH",r.status.installed?"Installed":"Not installed",r.status.installed?"good":"warn")+metric("ssh.service",r.status.serviceStatus,r.status.ready?"good":"warn")+metric("Port 22",r.status.listening?"Listening":"Not listening",r.status.listening?"good":"warn");h+='</div><div class="actions" style="margin-top:12px"><button onclick="action(\'ssh_install\')">Install & Enable SSH</button><button class="secondary" onclick="action(\'ssh_repair\')">Repair SSH</button><button class="secondary" onclick="action(\'ssh_restart\')">Restart SSH</button>'}else{h+=metric("TightVNC",r.status.installed?"Installed":"Not installed",r.status.installed?"good":"warn")+metric("Service",r.status.serviceStatus||"Unknown",r.status.ready?"good":"warn")+metric("Port 5900",r.status.listening?"Listening":"Not listening",r.status.listening?"good":"warn");h+='</div><div class="actions" style="margin-top:12px"><button onclick="action(\'vnc_install\')">Install TightVNC</button><button class="secondary" onclick="action(\'vnc_restart\')">Restart TightVNC</button><button class="danger" onclick="action(\'vnc_uninstall\')">Uninstall TightVNC</button>'}h+='<button class="secondary" onclick="loadRemote()">Refresh Status</button></div>';$("remoteAccess").innerHTML=h}catch(e){$("remoteAccess").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function action(name){if(name==="vnc_uninstall"&&!confirm("Uninstall TightVNC?"))return;try{$("actionMessage").innerHTML='<div class="notice">Working…</div>';const r=await j("/api/action",{method:"POST",body:JSON.stringify({action:name})});$("actionMessage").innerHTML='<div class="notice">'+esc(r.message||"Action completed.")+'</div>';setTimeout(()=>{loadStatus();loadRemote()},1200)}catch(e){$("actionMessage").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadLogs(){const b=await j("/api/logs");$("logFile").innerHTML=b.files.map(x=>'<option value="'+esc(x.name)+'">'+esc(x.name)+" — "+esc(x.size)+" bytes</option>").join("");if(b.files.length)loadLog();else $("logOutput").textContent="No log files found."}
async function loadLog(){const name=$("logFile").value;if(!name)return;try{const b=await j("/api/log?name="+encodeURIComponent(name)+"&lines="+encodeURIComponent($("logLines").value));$("logOutput").textContent=b.content||"";$("logOutput").scrollTop=$("logOutput").scrollHeight}catch(e){$("logOutput").textContent=e.message}}
async function loadConfig(){const b=await j("/api/config");$("config").innerHTML=Object.entries(b.values).map(([k,v])=>'<label>'+esc(k)+'<input data-key="'+esc(k)+'" value="'+esc(v)+'"></label>').join("")}
async function saveConfig(){try{const values={};document.querySelectorAll("#config input[data-key]").forEach(x=>values[x.dataset.key]=x.value);const r=await j("/api/config",{method:"POST",body:JSON.stringify({values})});$("configMessage").innerHTML='<div class="notice">'+esc(r.message)+'</div>'}catch(e){$("configMessage").innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadAll(){await Promise.all([loadStatus(),loadConnectivity(),loadAi(),loadRemote(),loadLogs(),loadConfig()])}
loadAll();
</script></body></html>"""


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def local_admin_enabled() -> bool:
    return _truthy(os.getenv("SITEWATCH_LOCAL_ADMIN_ENABLED", "true"))


def local_admin_bind() -> str:
    return "0.0.0.0" if _truthy(os.getenv("SITEWATCH_LOCAL_ADMIN_LAN_ACCESS", "false")) else "127.0.0.1"


def local_admin_port() -> int:
    try:
        return max(1024, min(65535, int(os.getenv("SITEWATCH_LOCAL_ADMIN_PORT", "8765"))))
    except ValueError:
        return 8765


def _service_state() -> dict:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["sc.exe", "query", "NodeVyuAgent"],
                capture_output=True, text=True, errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            running = "RUNNING" in (result.stdout or "").upper()
            return {"name": "NodeVyuAgent", "running": running, "status": "Running" if running else "Stopped"}
        except Exception as exc:
            return {"name": "NodeVyuAgent", "running": False, "status": f"Unknown: {exc}"}
    try:
        result = subprocess.run(["systemctl", "is-active", "nodevyu-agent"], capture_output=True, text=True, errors="replace", timeout=10)
        running = result.returncode == 0 and result.stdout.strip() == "active"
        return {"name": "nodevyu-agent", "running": running, "status": "Running" if running else (result.stdout.strip() or "Stopped")}
    except Exception as exc:
        return {"name": "nodevyu-agent", "running": False, "status": f"Unknown: {exc}"}


def _format_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for item in str(value or "").strip().split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _latest_agent_version() -> str | None:
    server = os.getenv("SITEWATCH_SERVER_URL", "").rstrip("/")
    token = os.getenv("SITEWATCH_AGENT_TOKEN", "")
    if not server or not token:
        return None
    try:
        response = requests.get(
            server + "/api/agent/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if not response.ok:
            return None
        body = response.json()
        value = str(body.get("latestVersion") or "").strip()
        return value or None
    except Exception:
        return None


def _status() -> dict:
    bind = local_admin_bind()
    display_host = socket.gethostname() if bind == "0.0.0.0" else bind
    latest_version = _latest_agent_version()
    return {
        "version": __version__,
        "latestVersion": latest_version,
        "updateAvailable": bool(latest_version and _version_tuple(latest_version) > _version_tuple(__version__)),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "uptime": _format_uptime(time.time() - STARTED_AT),
        "serverUrl": os.getenv("SITEWATCH_SERVER_URL", ""),
        "platform": {"os": "windows" if os.name == "nt" else "linux", "system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "service": _service_state(),
        "localAdminUrl": f"http://{display_host}:{local_admin_port()}",
    }


def _agent_config() -> dict:
    server = os.getenv("SITEWATCH_SERVER_URL", "").rstrip("/")
    token = os.getenv("SITEWATCH_AGENT_TOKEN", "")
    if not server or not token:
        return {}
    response = requests.get(
        server + "/api/agent/config",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _connectivity() -> dict:
    server = os.getenv("SITEWATCH_SERVER_URL", "").rstrip("/")
    parsed = urllib.parse.urlparse(server)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    dns = {"ok": False, "addresses": [], "error": ""}
    tcp = {"ok": False, "port": port, "error": ""}
    api = {"ok": False, "error": ""}
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        dns = {"ok": bool(addresses), "addresses": addresses, "error": ""}
    except Exception as exc:
        dns["error"] = str(exc)
    try:
        with socket.create_connection((host, port), timeout=5):
            tcp["ok"] = True
    except Exception as exc:
        tcp["error"] = str(exc)
    try:
        token = os.getenv("SITEWATCH_AGENT_TOKEN", "")
        response = requests.get(server + "/api/agent/config", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        api["ok"] = response.ok
        if not response.ok:
            api["error"] = f"HTTP {response.status_code}"
    except Exception as exc:
        api["error"] = str(exc)
    return {"dns": dns, "tcp": tcp, "api": api}


def _log_files() -> list[dict]:
    if not LOG_DIR.exists():
        return []
    rows = []
    for path in LOG_DIR.glob("*.log"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            rows.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime})
        except OSError:
            pass
    return sorted(rows, key=lambda row: row["modified"], reverse=True)


def _ai_cameras() -> list[dict]:
    config = _agent_config()
    cameras = []
    for device in config.get("devices", []):
        if device.get("type") != "camera":
            continue
        if not any(check.get("type") == "rtsp" for check in device.get("checks", [])):
            continue
        cameras.append({"id": str(device.get("id")), "name": str(device.get("name") or device.get("id"))})
    return cameras


AI_SECURITY_CLASSES = [
    "person", "car", "truck", "bus", "motorcycle", "bicycle",
    "bird", "cat", "dog", "horse", "sheep", "cow", "bear", "zebra", "giraffe", "elephant",
]


def _cleanup_ai_evidence(max_age_seconds: int = 3600) -> None:
    if not AI_EVIDENCE_DIR.exists():
        return
    cutoff = time.time() - max_age_seconds
    for path in AI_EVIDENCE_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _annotated_preview(image, detections) -> bytes:
    import io
    from PIL import ImageDraw, ImageFont

    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    font = ImageFont.load_default()
    width, height = preview.size
    for detection in detections:
        x1 = int(detection.x * width)
        y1 = int(detection.y * height)
        x2 = int((detection.x + detection.width) * width)
        y2 = int((detection.y + detection.height) * height)
        label = f"{detection.label} {detection.confidence * 100:.1f}%"
        draw.rectangle((x1, y1, x2, y2), outline="red", width=max(2, width // 500))
        text_box = draw.textbbox((x1, y1), label, font=font)
        text_w = max(1, text_box[2] - text_box[0])
        text_h = max(1, text_box[3] - text_box[1])
        label_y = max(0, y1 - text_h - 6)
        draw.rectangle((x1, label_y, x1 + text_w + 8, label_y + text_h + 6), fill="red")
        draw.text((x1 + 4, label_y + 3), label, fill="white", font=font)

    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def _save_ai_evidence(preview_jpeg: bytes, clip_mp4: bytes | None) -> tuple[str, str | None]:
    AI_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_ai_evidence()
    evidence_id = f"{int(time.time())}-{os.getpid()}-{threading.get_ident()}"
    preview_name = f"{evidence_id}.jpg"
    (AI_EVIDENCE_DIR / preview_name).write_bytes(preview_jpeg)
    clip_name = None
    if clip_mp4:
        clip_name = f"{evidence_id}.mp4"
        (AI_EVIDENCE_DIR / clip_name).write_bytes(clip_mp4)
    return preview_name, clip_name


def _run_ai_test(camera_id: str) -> dict:
    import io
    from PIL import Image
    from .ai_detector import OnnxObjectDetector
    from .beta_features import resolve_ai_detection_beta
    from .checks import capture_snapshot, capture_clip

    config = _agent_config()
    enabled, source = resolve_ai_detection_beta(config)
    if not enabled:
        raise ValueError(f"AI Detection beta is disabled (source={source}).")

    device = next((item for item in config.get("devices", []) if str(item.get("id")) == str(camera_id)), None)
    if not device or device.get("type") != "camera":
        raise ValueError("Camera was not found in this agent configuration.")
    if not any(check.get("type") == "rtsp" for check in device.get("checks", [])):
        raise ValueError("Camera does not have an RTSP check.")

    snapshot = capture_snapshot(device)
    if not snapshot:
        raise RuntimeError("Camera did not return a snapshot.")
    with Image.open(io.BytesIO(snapshot["jpeg"])) as source:
        source.load()
        image = source.convert("RGB")

    detector = OnnxObjectDetector()
    detections, metrics = detector.detect(
        image,
        confidence_threshold=0.55,
        iou_threshold=0.45,
        class_filter=AI_SECURITY_CLASSES,
    )

    preview_jpeg = _annotated_preview(image, detections)
    clip_data = None
    clip_error = None
    try:
        clip = capture_clip(device, duration_seconds=4)
        clip_data = clip.get("mp4") if clip else None
    except Exception as exc:
        clip_error = str(exc)
        print(f"[ai] evidence clip failed camera={device.get('id')}: {clip_error}", flush=True)

    preview_name, clip_name = _save_ai_evidence(preview_jpeg, clip_data)
    return {
        "cameraId": str(device.get("id")),
        "cameraName": str(device.get("name") or device.get("id")),
        "provider": detector.provider,
        "sourceWidth": image.width,
        "sourceHeight": image.height,
        "metrics": metrics,
        "detections": [item.as_dict() for item in detections],
        "previewUrl": f"/api/ai-evidence?name={urllib.parse.quote(preview_name)}",
        "clipUrl": f"/api/ai-evidence?name={urllib.parse.quote(clip_name)}" if clip_name else None,
        "clipError": clip_error,
    }


def _remote_access_status() -> dict:
    if os.name == "nt":
        from .tightvnc import get_tightvnc_status
        return {"platform": "windows", "status": get_tightvnc_status()}
    from .linux_ssh import get_ssh_status
    return {"platform": "linux", "status": get_ssh_status()}


def _schedule_restart() -> None:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
             "Start-Sleep -Seconds 3; Restart-Service -Name 'NodeVyuAgent' -Force"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=flags,
        )
        return
    unit = f"nodevyu-agent-local-restart-{os.getpid()}"
    result = subprocess.run(
        ["systemd-run", "--quiet", "--collect", f"--unit={unit}", "--on-active=3s", "/bin/systemctl", "restart", "nodevyu-agent"],
        capture_output=True, text=True, errors="replace", timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Unable to schedule service restart").strip())


def _run_action(name: str) -> str:
    if name == "restart_agent":
        _schedule_restart()
        return "Agent restart scheduled."
    if name == "update_agent":
        from .update_launcher import launch_self_update
        launch_self_update()
        return "Agent update launched. The service will restart automatically when the update completes."
    if os.name == "nt":
        from .tightvnc import install_tightvnc, restart_tightvnc, uninstall_tightvnc
        if name == "vnc_install":
            result = install_tightvnc()
        elif name == "vnc_restart":
            result = restart_tightvnc()
        elif name == "vnc_uninstall":
            result = uninstall_tightvnc()
        else:
            raise ValueError("Unsupported Windows action")
        return json.dumps(result, indent=2)
    from .linux_ssh import install_ssh, repair_ssh, restart_ssh
    if name == "ssh_install":
        result = install_ssh()
    elif name == "ssh_repair":
        result = repair_ssh()
    elif name == "ssh_restart":
        result = restart_ssh()
    else:
        raise ValueError("Unsupported Linux action")
    return json.dumps(result, indent=2)


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _safe_config_values() -> dict[str, str]:
    current = _read_env()
    defaults = {
        "SITEWATCH_DISCOVERY_CIDRS": "",
        "SITEWATCH_DISCOVERY_INTERVAL_SECONDS": "900",
        "SITEWATCH_SNAPSHOT_INTERVAL_SECONDS": "300",
        "SITEWATCH_MONITOR_WORKERS": "8",
        "SITEWATCH_SNAPSHOT_WORKERS": "2",
        "SITEWATCH_LOCAL_ADMIN_ENABLED": "true",
        "SITEWATCH_LOCAL_ADMIN_PORT": "8765",
        "SITEWATCH_LOCAL_ADMIN_LAN_ACCESS": "false",
        "SITEWATCH_BETA_AI_DETECTION": "false",
        "SITEWATCH_AI_MODEL_PATH": "",
        "SITEWATCH_AI_LABELS_PATH": "",
    }
    return {key: current.get(key, os.getenv(key, default)) for key, default in defaults.items()}


def _write_safe_config(values: dict) -> None:
    requested = {str(k): str(v).strip() for k, v in values.items() if str(k) in SAFE_CONFIG_KEYS}
    port_text = requested.get("SITEWATCH_LOCAL_ADMIN_PORT")
    if port_text:
        port = int(port_text)
        if port < 1024 or port > 65535:
            raise ValueError("Local admin port must be between 1024 and 65535.")
    existing_lines = ENV_FILE.read_text(encoding="utf-8-sig", errors="replace").splitlines() if ENV_FILE.exists() else []
    output: list[str] = []
    seen: set[str] = set()
    for raw in existing_lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in requested:
                output.append(f"{key}={requested[key]}")
                seen.add(key)
                continue
        output.append(raw)
    for key in sorted(requested):
        if key not in seen:
            output.append(f"{key}={requested[key]}")
    temp = ENV_FILE.with_suffix(".env.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.replace(temp, ENV_FILE)


class LocalAdminHandler(BaseHTTPRequestHandler):
    server_version = "NodeVyuLocalAdmin/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[local-admin] {self.address_string()} {fmt % args}", flush=True)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = min(1_000_000, int(self.headers.get("Content-Length", "0") or "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/":
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/api/status":
                return self._json(_status())
            if url.path == "/api/connectivity":
                return self._json(_connectivity())
            if url.path == "/api/ai":
                from .beta_features import plugin_capabilities
                try:
                    agent_config = _agent_config()
                except Exception:
                    agent_config = {}
                plugin = plugin_capabilities(agent_config).get("plugins", [{}])[0]
                return self._json(plugin)
            if url.path == "/api/ai-cameras":
                return self._json({"cameras": _ai_cameras()})
            if url.path == "/api/ai-evidence":
                query = urllib.parse.parse_qs(url.query)
                name = (query.get("name") or [""])[0]
                safe_name = Path(name).name
                if not safe_name or safe_name != name:
                    return self._json({"error": "Invalid evidence file"}, 400)
                path = AI_EVIDENCE_DIR / safe_name
                if not path.is_file():
                    return self._json({"error": "Evidence not found"}, 404)
                data = path.read_bytes()
                content_type = "video/mp4" if path.suffix.lower() == ".mp4" else "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if url.path == "/api/remote-access":
                return self._json(_remote_access_status())
            if url.path == "/api/logs":
                return self._json({"files": _log_files()})
            if url.path == "/api/log":
                query = urllib.parse.parse_qs(url.query)
                name = (query.get("name") or [""])[0]
                lines = int((query.get("lines") or ["250"])[0])
                return self._json(collect_agent_log(name, lines))
            if url.path == "/api/config":
                return self._json({"values": _safe_config_values()})
            if url.path == "/api/log-bundle":
                bundle = Path(create_agent_logs_zip())
                try:
                    data = bundle.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", 'attachment; filename="nodevyu-agent-logs.zip"')
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    try:
                        bundle.unlink()
                    except OSError:
                        pass
                return
            self._json({"error": "Not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        url = urllib.parse.urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/action":
                return self._json({"ok": True, "message": _run_action(str(body.get("action") or ""))})
            if url.path == "/api/ai-test":
                return self._json(_run_ai_test(str(body.get("cameraId") or "")))
            if url.path == "/api/config":
                values = body.get("values") if isinstance(body.get("values"), dict) else {}
                _write_safe_config(values)
                return self._json({"ok": True, "message": "Configuration saved. Restart the NodeVyu Agent to apply changes."})
            self._json({"error": "Not found"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


def local_admin_loop() -> None:
    if not local_admin_enabled():
        print("[local-admin] disabled by SITEWATCH_LOCAL_ADMIN_ENABLED", flush=True)
        return
    bind, port = local_admin_bind(), local_admin_port()
    server = ThreadingHTTPServer((bind, port), LocalAdminHandler)
    server.daemon_threads = True
    print(f"[local-admin] listening on http://{bind}:{port} no-auth localhost={bind == '127.0.0.1'}", flush=True)
    server.serve_forever(poll_interval=0.5)
