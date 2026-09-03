import os, subprocess, socket, time, ssl, shutil, re, threading
from urllib.parse import urlparse, urlunparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from .snmp import run_snmp_get
from .viewing_window import is_viewing

IS_WINDOWS = os.name == "nt"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
RTSP_PROBE_CONCURRENCY = max(1, min(16, int(os.getenv("SITEWATCH_MAX_RTSP_PROBES", "2"))))
_rtsp_probe_slots = threading.BoundedSemaphore(RTSP_PROBE_CONCURRENCY)


class RTSPProbeSkipped(RuntimeError):
    pass


def _tool(name: str) -> str:
    exe = name + (".exe" if IS_WINDOWS else "")
    configured = os.getenv("SITEWATCH_FFMPEG_DIR", "").strip()
    if configured:
        candidate = os.path.join(configured, exe)
        if os.path.isfile(candidate): return candidate
    found = shutil.which(exe) or shutil.which(name)
    return found or exe


class LegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context(); context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        try: context.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError: pass
        try: context.minimum_version = ssl.TLSVersion.TLSv1
        except Exception: pass
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"): context.options |= ssl.OP_LEGACY_SERVER_CONNECT
        pool_kwargs["ssl_context"] = context
        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, **pool_kwargs)


def ping(host: str, timeout: int):
    start = time.monotonic(); cmd = ["ping", "-n", "1", "-w", str(max(1, int(timeout)) * 1000), host] if IS_WINDOWS else ["ping", "-c", "1", "-W", str(timeout), host]
    try:
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_FLAGS)
        return p.returncode == 0, int((time.monotonic() - start) * 1000), None if p.returncode == 0 else "Ping failed"
    except Exception as e: return False, int((time.monotonic() - start) * 1000), str(e)


def tcp(host: str, port: int, timeout: int):
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout): return True, int((time.monotonic() - start) * 1000), None
    except Exception as e: return False, int((time.monotonic() - start) * 1000), str(e)


def http_check(url: str, timeout: int, verify_tls: bool, legacy_tls: bool = False):
    start = time.monotonic()
    try:
        if legacy_tls and url.lower().startswith("https://"):
            session = requests.Session(); session.mount("https://", LegacyTLSAdapter()); r = session.get(url, timeout=timeout, verify=False, allow_redirects=True)
        else: r = requests.get(url, timeout=timeout, verify=verify_tls, allow_redirects=True)
        ok = r.status_code < 500
        return ok, int((time.monotonic() - start) * 1000), None if ok else f"HTTP {r.status_code}"
    except Exception as e: return False, int((time.monotonic() - start) * 1000), str(e)


def _rtsp_with_credentials(url: str, username: str | None, password: str | None):
    if not username: return url
    p = urlparse(url); auth = username if password is None else f"{username}:{password}"
    return urlunparse((p.scheme, f"{auth}@{p.hostname}" + (f":{p.port}" if p.port else ""), p.path, p.params, p.query, p.fragment))


def _safe_target_url(value):
    if not value: return None
    text = str(value)
    try:
        p = urlparse(text)
        host = p.hostname or ""
        if ":" in host and not host.startswith("["): host = f"[{host}]"
        netloc = host + (f":{p.port}" if p.port else "")
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, ""))
    except Exception:
        return re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1", text)


def _safe_error(value):
    if value is None: return None
    return re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1", str(value))[-1000:]


def _url_port(value, default):
    try: return int(urlparse(str(value)).port or default)
    except Exception: return int(default)


def _stop_process(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate(); proc.wait(timeout=2)
    except Exception:
        try: proc.kill(); proc.wait(timeout=2)
        except Exception: pass


def rtsp(url: str, username: str | None, password: str | None, timeout: int, *, probe_context: str = "scheduled", viewing_source=None, use_probe_limit: bool = True):
    target = _rtsp_with_credentials(url, username, password); start = time.monotonic()
    cmd = [_tool("ffprobe"), "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000), "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "json", target]
    acquired = False; proc = None
    try:
        if use_probe_limit:
            _rtsp_probe_slots.acquire(); acquired = True
        if viewing_source and is_viewing(str(viewing_source[0]), str(viewing_source[1])):
            raise RTSPProbeSkipped("Viewing Window active")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=CREATE_FLAGS)
        print(f"[check] RTSP probe started source={probe_context} pid={proc.pid} scheduled={'yes' if use_probe_limit else 'no'}", flush=True)
        try:
            stdout, stderr = proc.communicate(timeout=timeout + 3)
        except subprocess.TimeoutExpired:
            _stop_process(proc)
            return False, int((time.monotonic() - start) * 1000), "RTSP connection timeout"
        ok = proc.returncode == 0 and '"streams"' in stdout and '"codec_name"' in stdout
        err = None if ok else (stderr.strip()[-500:] or "RTSP stream unavailable")
        return ok, int((time.monotonic() - start) * 1000), err
    except RTSPProbeSkipped:
        raise
    except Exception as e:
        _stop_process(proc)
        return False, int((time.monotonic() - start) * 1000), str(e)
    finally:
        if acquired:
            _rtsp_probe_slots.release()


def _numeric(value):
    try: return float(str(value).strip())
    except Exception: return None


def evaluate_snmp_value(value, operator, threshold):
    text = "" if value is None else str(value); expected = "" if threshold is None else str(threshold); op = str(operator or "exists")
    if op == "exists": return value is not None and text.strip() != "" and "no such" not in text.lower()
    if op in {"gt","gte","lt","lte"}:
        actual_num, expected_num = _numeric(text), _numeric(expected)
        if actual_num is None or expected_num is None: raise ValueError("Numeric SNMP comparison requires numeric value and threshold")
        return {"gt":actual_num>expected_num,"gte":actual_num>=expected_num,"lt":actual_num<expected_num,"lte":actual_num<=expected_num}[op]
    if op == "eq": return text == expected
    if op == "ne": return text != expected
    if op == "contains": return expected in text
    if op == "not_contains": return expected not in text
    if op == "regex": return re.search(expected, text) is not None
    if op == "not_regex": return re.search(expected, text) is None
    raise ValueError(f"Unknown SNMP operator: {op}")


def snmp_check(host: str, check: dict, timeout: int):
    start=time.monotonic(); result=run_snmp_get(host, check.get("community") or "", check.get("oid") or "", int(check.get("port") or 161), str(check.get("version") or "2c"), max(0.5,float(timeout)), 1)
    latency=int((time.monotonic()-start)*1000)
    if result.get("status") != "success": return False, latency, result.get("message") or "SNMP GET failed", result.get("value")
    value=result.get("value")
    try: ok=evaluate_snmp_value(value,check.get("operator"),check.get("threshold"))
    except Exception as exc: return False,latency,str(exc),value
    if ok: return True,latency,None,value
    return False,latency,f"SNMP value {value!r} did not satisfy {check.get('operator') or 'exists'} {check.get('threshold') or ''}".strip(),value


def _viewing_source(device: dict):
    device_id = str(device.get("id") or "")
    if not device_id:
        return None
    if device_id.startswith("nvr-"):
        return "nvr_stream", device_id[4:]
    return "device", device_id


def run_device(device: dict):
    host = device["host"]; timeout = int(device.get("timeoutSeconds", 8)); details = []; max_latency = 0
    for check in device.get("checks", []):
        typ = check["type"]; extra={"host":host}
        if typ == "ping":
            ok, latency, error = ping(host, timeout)
        elif typ == "tcp":
            port = int(check.get("port") or 80); ok, latency, error = tcp(host, port, timeout); extra.update({"port":port})
        elif typ in ("http", "https"):
            default_port = 443 if typ == "https" else 80
            configured_port = int(check.get("port") or default_port)
            path = check.get("path") or "/"
            port_suffix = "" if configured_port == default_port else f":{configured_port}"
            url = check.get("url") or f"{typ}://{host}{port_suffix}{path}"
            ok, latency, error = http_check(url, timeout, check.get("verifyTls", True), check.get("legacyTls", False))
            extra.update({"port":_url_port(url,configured_port),"url":_safe_target_url(url),"path":path})
        elif typ == "rtsp":
            configured_port = int(check.get("port") or 554)
            url = check.get("url") or f"rtsp://{host}:{configured_port}/"
            extra.update({"port":_url_port(url,configured_port),"url":_safe_target_url(url),"path":urlparse(url).path or "/"})
            source = _viewing_source(device)
            try:
                ok, latency, error = rtsp(url, check.get("username"), check.get("password"), timeout, probe_context=f"device={device.get('id')} name={device.get('name')}", viewing_source=source)
            except RTSPProbeSkipped:
                ok, latency, error = True, 0, None
                extra.update({"skipped":True,"skipReason":"viewing_window"})
                print(f"[check] RTSP probe skipped device={device.get('id')} name={device.get('name')} reason=viewing_window", flush=True)
        elif typ == "snmp":
            port=int(check.get("port") or 161)
            ok,latency,error,value=snmp_check(host,check,timeout); extra.update({"port":port,"checkId":check.get("id"),"name":check.get("name"),"oid":check.get("oid"),"value":value,"operator":check.get("operator"),"threshold":check.get("threshold")})
        else: ok, latency, error = False, 0, f"Unknown check type: {typ}"
        max_latency = max(max_latency, latency); details.append({"type": typ, "ok": ok, "latencyMs": latency, "error": _safe_error(error), **extra})
    overall = all(x["ok"] for x in details) if details else False; failed = [x.get("name") or x["type"] for x in details if not x["ok"]]
    return {"deviceId": device["id"], "deviceName": device["name"], "observedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "overallOk": overall, "latencyMs": max_latency, "message": "All checks passed" if overall else f"Failed checks: {', '.join(failed)}", "checks": details}


def _valid_jpeg(data: bytes):
    if not data or len(data) < 1024: return False, "JPEG output is empty or too small"
    if not data.startswith(b"\xff\xd8"): return False, "JPEG start marker missing"
    if not data.rstrip().endswith(b"\xff\xd9"): return False, "JPEG end marker missing"
    try:
        from PIL import Image
        with Image.open(__import__("io").BytesIO(data)) as image:
            image.load(); width, height = image.size
            if width < 160 or height < 120: return False, f"JPEG dimensions are unexpectedly small: {width}x{height}"
            return True, (width, height)
    except Exception as e: return False, f"JPEG decode failed: {e}"


def _snapshot_paused_for_viewing(device: dict) -> bool:
    source = _viewing_source(device)
    return bool(source and is_viewing(source[0], source[1]))


def capture_snapshot(device: dict):
    if device.get("type") != "camera": return None
    if _snapshot_paused_for_viewing(device):
        return None
    rtsp_check = next((c for c in device.get("checks", []) if c.get("type") == "rtsp"), None)
    if not rtsp_check: return None
    host = device["host"]; timeout = int(device.get("timeoutSeconds", 8)); url = rtsp_check.get("url") or f"rtsp://{host}:554/"; target = _rtsp_with_credentials(url, rtsp_check.get("username"), rtsp_check.get("password")); errors = []
    for attempt in range(1, 3):
        cmd = [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000), "-fflags", "+genpts+discardcorrupt", "-flags", "low_delay", "-i", target, "-vf", "fps=2,select='gte(n\\,3)',scale=1280:-2:force_original_aspect_ratio=decrease", "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
        try: p = subprocess.run(cmd, capture_output=True, timeout=max(timeout + 8, 15), creationflags=CREATE_FLAGS)
        except Exception as e: errors.append(f"attempt {attempt}: {e}"); time.sleep(1); continue
        stderr = p.stderr.decode("utf-8", errors="ignore").strip()
        if p.returncode != 0: errors.append(f"attempt {attempt}: {stderr[-500:] or 'FFmpeg failed'}"); time.sleep(1); continue
        jpeg = p.stdout
        if len(jpeg) > 900_000: errors.append(f"attempt {attempt}: snapshot too large ({len(jpeg)} bytes)"); time.sleep(1); continue
        valid, info = _valid_jpeg(jpeg)
        if valid:
            width, height = info; return {"jpeg": jpeg, "width": width, "height": height}
        errors.append(f"attempt {attempt}: {info}"); time.sleep(1)
    raise RuntimeError("Snapshot validation failed; " + " | ".join(errors))
