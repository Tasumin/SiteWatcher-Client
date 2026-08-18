import os, time, threading, requests, base64, socket, uuid, sys
from datetime import datetime, timezone
from . import __version__
from .checks import run_device, capture_snapshot
from .storage import Storage
from .discovery import scan_network
from .onvif import probe_onvif

SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
TOKEN = os.environ["SITEWATCH_AGENT_TOKEN"]
DB_PATH = os.getenv("SITEWATCH_DB", "/data/queue.db")
SNAPSHOT_INTERVAL = int(os.getenv("SITEWATCH_SNAPSHOT_INTERVAL_SECONDS", "300"))
DISCOVERY_INTERVAL = int(os.getenv("SITEWATCH_DISCOVERY_INTERVAL_SECONDS", "900"))
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

storage = Storage(DB_PATH)
config = {"devices": [], "defaults": {"heartbeatSeconds": 30, "configRefreshSeconds": 120}}
next_due = {}
last_snapshot = {}
preview_last_sent = {}
last_discovery_request = None
started_workers = set()
worker_start_lock = threading.Lock()

INSTANCE_ID = uuid.uuid4().hex[:8]
LOCK_FILE = os.getenv("SITEWATCH_LOCK_FILE", "/data/sitewatch-agent.lock")

def start_worker(name, target):
    with worker_start_lock:
        if name in started_workers:
            print(f"[startup] worker {name} already started; skipping duplicate", flush=True)
            return
        started_workers.add(name)
    thread = threading.Thread(target=target, name=f"sitewatch-{name}", daemon=True)
    thread.start()
    print(f"[startup] worker {name} started", flush=True)

def acquire_single_instance_lock():
    try: import fcntl
    except ImportError:
        print("[startup] WARNING: fcntl unavailable; single-instance lock disabled", flush=True)
        return None
    os.makedirs(os.path.dirname(LOCK_FILE) or ".", exist_ok=True)
    handle = open(LOCK_FILE, "a+")
    try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0); owner = handle.read().strip()
        print(f"[startup] another SiteWatch agent already owns {LOCK_FILE}: {owner or 'unknown owner'}", flush=True)
        print("[startup] refusing to start a duplicate worker.", flush=True)
        sys.exit(2)
    handle.seek(0); handle.truncate()
    handle.write(f"instance={INSTANCE_ID} pid={os.getpid()} host={socket.gethostname()} started={datetime.now(timezone.utc).isoformat()}\n")
    handle.flush()
    return handle

def api(method, path, **kwargs):
    return requests.request(method, SERVER + path, headers=HEADERS, timeout=20, **kwargs)

def fetch_config():
    global config, last_discovery_request
    r = api("GET", "/api/agent/config"); r.raise_for_status()
    previous_request = config.get("discoveryRequestedAt")
    config = r.json()
    for d in config.get("devices", []): next_due.setdefault(d["id"], 0)
    current_request = config.get("discoveryRequestedAt")
    if current_request and current_request != previous_request and current_request != last_discovery_request:
        print(f"[discovery] manual refresh requested at {current_request}", flush=True)
    print(f"[config] version={config.get('configVersion')} devices={len(config.get('devices', []))}", flush=True)

def heartbeat():
    while True:
        try:
            r = api("POST", "/api/agent/heartbeat", json={"version": __version__})
            if r.ok: fetch_config()
            else: print(f"[heartbeat] HTTP {r.status_code}", flush=True)
        except Exception as e: print(f"[heartbeat] {e}", flush=True)
        time.sleep(int(config.get("defaults", {}).get("heartbeatSeconds", 30)))

def flush_queue():
    batch = storage.batches(25)
    if not batch: return
    ids = [x[0] for x in batch]; results = [x[1] for x in batch]
    r = api("POST", "/api/agent/results", json={"results": results, "version": __version__}); r.raise_for_status()
    storage.delete(ids)
    print(f"[upload] sent {len(ids)} results", flush=True)

def maybe_upload_snapshot(device, result, now):
    if device.get("type") != "camera" or not result.get("overallOk"): return
    rtsp_result = next((c for c in result.get("checks", []) if c.get("type") == "rtsp"), None)
    if not rtsp_result or not rtsp_result.get("ok"): return
    if now - last_snapshot.get(device["id"], 0) < SNAPSHOT_INTERVAL: return
    try:
        snapshot = capture_snapshot(device)
        if not snapshot: return
        jpeg = snapshot["jpeg"]
        payload = {"deviceId": device["id"], "capturedAt": datetime.now(timezone.utc).isoformat(), "contentType": "image/jpeg", "imageBase64": base64.b64encode(jpeg).decode("ascii"), "width": snapshot.get("width"), "height": snapshot.get("height")}
        r = api("POST", "/api/agent/snapshot", json=payload); r.raise_for_status()
        last_snapshot[device["id"]] = now
        print(f"[snapshot] {device['name']}: uploaded {len(jpeg)//1024} KB JPEG", flush=True)
    except Exception as e: print(f"[snapshot] {device['name']}: {e}", flush=True)

def preview_loop():
    time.sleep(3)
    while True:
        try:
            r = api("GET", "/api/agent/previews")
            if not r.ok:
                if r.status_code != 404: print(f"[preview] server HTTP {r.status_code}: {r.text[:300]}", flush=True)
                time.sleep(2); continue
            sessions = r.json().get("sessions", []); now = time.time()
            for session in sessions:
                session_id = session.get("id")
                if not session_id or now - preview_last_sent.get(session_id, 0) < 2: continue
                device = {"id": f"preview-{session_id}", "name": f"Preview {session.get('host')}", "type": "camera", "host": session.get("host"), "timeoutSeconds": 8, "checks": [{"type": "rtsp", "url": session.get("url"), "username": session.get("username"), "password": session.get("password")}]} 
                try:
                    snapshot = capture_snapshot(device)
                    if not snapshot: raise RuntimeError("No preview frame returned")
                    jpeg = snapshot["jpeg"]
                    post = api("POST", "/api/agent/previews", json={"sessionId": session_id, "contentType": "image/jpeg", "imageBase64": base64.b64encode(jpeg).decode("ascii"), "width": snapshot.get("width"), "height": snapshot.get("height")}); post.raise_for_status()
                    preview_last_sent[session_id] = time.time()
                    print(f"[preview] {session.get('host')}: sent {len(jpeg)//1024} KB frame", flush=True)
                except Exception as e:
                    print(f"[preview] {session.get('host')}: {e}", flush=True)
                    try: api("POST", "/api/agent/previews", json={"sessionId": session_id, "error": str(e)})
                    except Exception: pass
                    preview_last_sent[session_id] = time.time()
        except Exception as e: print(f"[preview] poll error: {e}", flush=True)
        time.sleep(1)

def run_discovery(reason="scheduled"):
    global last_discovery_request
    networks, devices, elapsed = scan_network()
    network_text = ", ".join(str(n) for n in networks)
    print(f"[discovery] {reason}: scanned {network_text} in {elapsed:.1f}s; found {len(devices)} candidates", flush=True)
    r = api("POST", "/api/agent/discovery", json={"devices": devices, "network": network_text, "networks": [str(n) for n in networks], "version": __version__})
    if not r.ok:
        print(f"[discovery] server HTTP {r.status_code}: {r.text[:300]}", flush=True); return False
    print(f"[discovery] reported {r.json().get('accepted', 0)} candidates", flush=True)
    if reason == "manual": last_discovery_request = config.get("discoveryRequestedAt")
    return True

def discovery_loop():
    time.sleep(10); last_scheduled = 0.0
    while True:
        try:
            now = time.time(); request_id = config.get("discoveryRequestedAt")
            if request_id and request_id != last_discovery_request:
                run_discovery("manual")
                if request_id != last_discovery_request: time.sleep(10)
            if now - last_scheduled >= max(60, DISCOVERY_INTERVAL):
                if run_discovery("scheduled"): last_scheduled = time.time()
        except Exception as e: print(f"[discovery] {e}", flush=True)
        time.sleep(2)

def monitor_retry_loop():
    time.sleep(5)
    while True:
        try:
            r = api("GET", "/api/agent/retries")
            if not r.ok:
                if r.status_code != 404: print(f"[retry] server HTTP {r.status_code}: {r.text[:300]}", flush=True)
                time.sleep(3); continue
            retry = r.json().get("retry")
            if not retry: time.sleep(2); continue
            retry_id = retry.get("id"); device = retry.get("device")
            if not retry_id or not device: time.sleep(2); continue
            print(f"[retry] {device.get('name')}: running requested monitor check", flush=True)
            try:
                result = run_device(device); result["manualRetry"] = True
                post = api("POST", "/api/agent/retries", json={"retryId": retry_id, "result": result}); post.raise_for_status()
                print(f"[retry] {device.get('name')}: {'PASS' if result.get('overallOk') else 'FAIL'} - {result.get('message')}", flush=True)
            except Exception as e:
                error_result = {"deviceId": device.get("id"), "deviceName": device.get("name"), "observedAt": datetime.now(timezone.utc).isoformat(), "overallOk": False, "latencyMs": None, "message": f"Manual retry error: {e}", "checks": [], "manualRetry": True}
                try: api("POST", "/api/agent/retries", json={"retryId": retry_id, "result": error_result}).raise_for_status()
                except Exception as post_error: print(f"[retry] failed to report retry error: {post_error}", flush=True)
                print(f"[retry] {device.get('name')}: ERROR - {e}", flush=True)
        except Exception as e: print(f"[retry] poll error: {e}", flush=True)
        time.sleep(2)

def onvif_loop():
    time.sleep(4)
    while True:
        try:
            r = api("GET", "/api/agent/onvif")
            if not r.ok:
                if r.status_code != 404: print(f"[onvif] server HTTP {r.status_code}: {r.text[:300]}", flush=True)
                time.sleep(3); continue
            body = r.json() if r.content else {}
            probe = body.get("probe") or body.get("request") or body.get("job")
            if probe is None and isinstance(body, dict) and (body.get("id") or body.get("probeId")): probe = body
            if not probe: time.sleep(2); continue
            probe_id = probe.get("id") or probe.get("probeId")
            host = probe.get("host") or probe.get("ip") or probe.get("cameraIp") or probe.get("camera_ip")
            port = int(probe.get("port") or probe.get("onvifPort") or probe.get("onvif_port") or 8000)
            credentials = probe.get("credentials") if isinstance(probe.get("credentials"), dict) else {}
            username = probe.get("username") or credentials.get("username")
            password = probe.get("password") if "password" in probe else credentials.get("password")
            if not probe_id or not host:
                print("[onvif] received malformed probe without id/host", flush=True); time.sleep(2); continue
            print(f"[onvif] {host}:{port}: probing device service", flush=True)
            outcome = probe_onvif(str(host), port, username, password)
            status = outcome.get("status", "error"); message = outcome.get("message") or status
            report = {"probeId": probe_id, "status": status, "message": message, "result": outcome.get("result"), "completedAt": datetime.now(timezone.utc).isoformat(), "version": __version__}
            api("POST", "/api/agent/onvif", json=report).raise_for_status()
            print(f"[onvif] {host}:{port}: {status} - {message}", flush=True)
        except Exception as e: print(f"[onvif] worker error: {e}", flush=True)
        time.sleep(2)

def main():
    lock_handle = acquire_single_instance_lock()
    print(f"[startup] SiteWatch agent v{__version__} instance={INSTANCE_ID} pid={os.getpid()} host={socket.gethostname()}", flush=True)
    while True:
        try: fetch_config(); break
        except Exception as e: print(f"[startup] waiting for server: {e}", flush=True); time.sleep(10)
    start_worker("heartbeat", heartbeat)
    start_worker("discovery", discovery_loop)
    start_worker("preview", preview_loop)
    start_worker("retry", monitor_retry_loop)
    start_worker("onvif", onvif_loop)
    last_config = time.time()
    while True:
        now = time.time()
        if now - last_config >= int(config.get("defaults", {}).get("configRefreshSeconds", 120)):
            try: fetch_config()
            except Exception as e: print(f"[config] {e}", flush=True)
            last_config = now
        for device in list(config.get("devices", [])):
            if now < next_due.get(device["id"], 0): continue
            try:
                result = run_device(device); storage.enqueue(result)
                print(f"[check] {device['name']}: {'UP' if result['overallOk'] else 'DOWN'} - {result['message']}", flush=True)
                maybe_upload_snapshot(device, result, now)
            except Exception as e:
                storage.enqueue({"deviceId": device["id"], "deviceName": device["name"], "observedAt": datetime.now(timezone.utc).isoformat(), "overallOk": False, "latencyMs": None, "message": f"Agent check error: {e}", "checks": []})
            next_due[device["id"]] = now + int(device.get("checkIntervalSeconds", 60))
        try: flush_queue()
        except Exception as e: print(f"[upload] queued locally: {e}", flush=True)
        time.sleep(1)

if __name__ == "__main__":
    main()
