import subprocess, socket, time
from urllib.parse import urlparse, urlunparse
import requests

def ping(host: str, timeout: int):
    start = time.monotonic()
    p = subprocess.run(["ping", "-c", "1", "-W", str(timeout), host], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0, int((time.monotonic() - start) * 1000), None if p.returncode == 0 else "Ping failed"

def tcp(host: str, port: int, timeout: int):
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, int((time.monotonic() - start) * 1000), None
    except Exception as e:
        return False, int((time.monotonic() - start) * 1000), str(e)

def http_check(url: str, timeout: int, verify_tls: bool):
    start = time.monotonic()
    try:
        r = requests.get(url, timeout=timeout, verify=verify_tls, allow_redirects=True)
        ok = r.status_code < 500
        return ok, int((time.monotonic() - start) * 1000), None if ok else f"HTTP {r.status_code}"
    except Exception as e:
        return False, int((time.monotonic() - start) * 1000), str(e)

def _rtsp_with_credentials(url: str, username: str | None, password: str | None):
    if not username: return url
    p = urlparse(url)
    auth = username if password is None else f"{username}:{password}"
    return urlunparse((p.scheme, f"{auth}@{p.hostname}" + (f":{p.port}" if p.port else ""), p.path, p.params, p.query, p.fragment))

def rtsp(url: str, username: str | None, password: str | None, timeout: int):
    target = _rtsp_with_credentials(url, username, password)
    start = time.monotonic()
    cmd = ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000), "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "json", target]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
        ok = p.returncode == 0 and '"streams"' in p.stdout and '"codec_name"' in p.stdout
        err = None if ok else (p.stderr.strip()[-500:] or "RTSP stream unavailable")
        return ok, int((time.monotonic() - start) * 1000), err
    except Exception as e:
        return False, int((time.monotonic() - start) * 1000), str(e)

def run_device(device: dict):
    host = device["host"]
    timeout = int(device.get("timeoutSeconds", 8))
    details = []
    max_latency = 0
    for check in device.get("checks", []):
        typ = check["type"]
        if typ == "ping": ok, latency, error = ping(host, timeout)
        elif typ == "tcp": ok, latency, error = tcp(host, check.get("port") or 80, timeout)
        elif typ in ("http", "https"):
            url = check.get("url") or f"{typ}://{host}{check.get('path') or '/'}"
            ok, latency, error = http_check(url, timeout, check.get("verifyTls", True))
        elif typ == "rtsp":
            url = check.get("url") or f"rtsp://{host}:554/"
            ok, latency, error = rtsp(url, check.get("username"), check.get("password"), timeout)
        else:
            ok, latency, error = False, 0, f"Unknown check type: {typ}"
        max_latency = max(max_latency, latency)
        details.append({"type": typ, "ok": ok, "latencyMs": latency, "error": error})
    overall = all(x["ok"] for x in details) if details else False
    failed = [x["type"] for x in details if not x["ok"]]
    return {"deviceId": device["id"], "deviceName": device["name"], "observedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "overallOk": overall, "latencyMs": max_latency, "message": "All checks passed" if overall else f"Failed checks: {', '.join(failed)}", "checks": details}

def _valid_jpeg(data: bytes):
    if not data or len(data) < 1024: return False, "JPEG output is empty or too small"
    if not data.startswith(b"\xff\xd8"): return False, "JPEG start marker missing"
    if not data.rstrip().endswith(b"\xff\xd9"): return False, "JPEG end marker missing"
    try:
        from PIL import Image
        with Image.open(__import__("io").BytesIO(data)) as image:
            image.load()
            width, height = image.size
            if width < 160 or height < 120: return False, f"JPEG dimensions are unexpectedly small: {width}x{height}"
            return True, (width, height)
    except Exception as e:
        return False, f"JPEG decode failed: {e}"

def capture_snapshot(device: dict):
    if device.get("type") != "camera": return None
    rtsp_check = next((c for c in device.get("checks", []) if c.get("type") == "rtsp"), None)
    if not rtsp_check: return None
    host = device["host"]
    timeout = int(device.get("timeoutSeconds", 8))
    url = rtsp_check.get("url") or f"rtsp://{host}:554/"
    target = _rtsp_with_credentials(url, rtsp_check.get("username"), rtsp_check.get("password"))
    errors = []
    for attempt in range(1, 3):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000), "-fflags", "+genpts+discardcorrupt", "-flags", "low_delay", "-i", target, "-vf", "fps=2,select='gte(n\\,3)',scale=1280:-2:force_original_aspect_ratio=decrease", "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=max(timeout + 8, 15))
        except Exception as e:
            errors.append(f"attempt {attempt}: {e}"); time.sleep(1); continue
        stderr = p.stderr.decode("utf-8", errors="ignore").strip()
        if p.returncode != 0:
            errors.append(f"attempt {attempt}: {stderr[-500:] or 'FFmpeg failed'}"); time.sleep(1); continue
        jpeg = p.stdout
        if len(jpeg) > 900_000:
            errors.append(f"attempt {attempt}: snapshot too large ({len(jpeg)} bytes)"); time.sleep(1); continue
        valid, info = _valid_jpeg(jpeg)
        if valid:
            width, height = info
            return {"jpeg": jpeg, "width": width, "height": height}
        errors.append(f"attempt {attempt}: {info}"); time.sleep(1)
    raise RuntimeError("Snapshot validation failed; " + " | ".join(errors))
