import base64
import os
import time
from datetime import datetime, timezone

import requests

from .checks import rtsp, capture_snapshot

SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
TOKEN = os.environ["SITEWATCH_AGENT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
SNAPSHOT_INTERVAL = max(30, int(os.getenv("SITEWATCH_SNAPSHOT_INTERVAL_SECONDS", "300")))


def api(method: str, path: str, **kwargs):
    return requests.request(method, SERVER + path, headers=HEADERS, timeout=25, **kwargs)


def _snapshot_payload(device_id: str, stream: dict, snapshot: dict):
    return {
        "deviceId": device_id,
        "streamId": stream["id"],
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "contentType": "image/jpeg",
        "imageBase64": base64.b64encode(snapshot["jpeg"]).decode("ascii"),
        "width": snapshot.get("width"),
        "height": snapshot.get("height"),
    }


def nvr_stream_loop():
    print(f"[nvr] camera stream worker started server={SERVER}", flush=True)
    next_due = {}
    last_snapshot = {}
    while True:
        try:
            cfg = api("GET", "/api/agent/config").json()
            now = time.time()
            for device in cfg.get("devices", []):
                streams = device.get("nvrStreams") or []
                if not streams:
                    continue
                interval = max(10, int(device.get("checkIntervalSeconds", 60)))
                timeout = max(1, int(device.get("timeoutSeconds", 8)))
                for stream in streams:
                    sid = stream.get("id")
                    url = stream.get("url")
                    if not sid or not url or now < next_due.get(sid, 0):
                        continue
                    next_due[sid] = now + interval
                    started = time.monotonic()
                    ok, latency, error = rtsp(url, stream.get("username"), stream.get("password"), timeout)
                    message = "RTSP stream available" if ok else (error or "RTSP stream unavailable")
                    payload = {
                        "deviceId": device["id"],
                        "streamId": sid,
                        "streamName": stream.get("name"),
                        "channel": stream.get("channel"),
                        "observedAt": datetime.now(timezone.utc).isoformat(),
                        "overallOk": ok,
                        "latencyMs": latency,
                        "message": message,
                        "url": url,
                    }
                    try:
                        r = api("POST", "/api/agent/nvr-stream-results", json=payload)
                        if not r.ok:
                            print(f"[nvr] {stream.get('name')}: result HTTP {r.status_code}: {r.text[:250]}", flush=True)
                    except Exception as exc:
                        print(f"[nvr] {stream.get('name')}: result upload failed: {exc}", flush=True)
                    print(f"[nvr] {stream.get('name')} channel={stream.get('channel')} {'UP' if ok else 'DOWN'} latency={latency}ms", flush=True)

                    if ok and now - last_snapshot.get(sid, 0) >= SNAPSHOT_INTERVAL:
                        fake_device = {
                            "id": f"nvr-{sid}",
                            "name": stream.get("name") or f"Channel {stream.get('channel')}",
                            "type": "camera",
                            "host": device.get("host"),
                            "timeoutSeconds": timeout,
                            "checks": [{
                                "type": "rtsp",
                                "url": url,
                                "username": stream.get("username"),
                                "password": stream.get("password"),
                            }],
                        }
                        try:
                            snapshot = capture_snapshot(fake_device)
                            if snapshot:
                                sr = api("POST", "/api/agent/nvr-stream-snapshot", json=_snapshot_payload(device["id"], stream, snapshot))
                                if sr.ok:
                                    last_snapshot[sid] = time.time()
                                else:
                                    print(f"[nvr] {stream.get('name')}: snapshot HTTP {sr.status_code}: {sr.text[:250]}", flush=True)
                        except Exception as exc:
                            print(f"[nvr] {stream.get('name')}: snapshot failed: {exc}", flush=True)
                    elapsed = time.monotonic() - started
                    if elapsed > timeout + 5:
                        print(f"[nvr] {stream.get('name')}: cycle took {elapsed:.1f}s", flush=True)
        except Exception as exc:
            print(f"[nvr] worker error: {exc}", flush=True)
        time.sleep(2)
