"""Agent-side Reolink NVR diagnostics. Credentials never enter the command queue."""
import ipaddress
import json
import socket
import subprocess
from urllib.parse import urlparse

import requests

_HTTP = requests.Session()
_HTTP.trust_env = False


def _private_host(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("NVR must have a private IP address") from exc
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast:
        raise ValueError("NVR address must be a private LAN address")
    return str(address)


def _port(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _login(host, username, password):
    response = _HTTP.post(
        f"http://{host}/cgi-bin/api.cgi?cmd=Login",
        json=[{"cmd": "Login", "param": {"User": {"userName": username, "password": password}}}],
        timeout=(4, 8),
    )
    response.raise_for_status()
    result = response.json()[0]
    if result.get("code") != 0:
        raise RuntimeError("NVR API authentication rejected")
    token = result.get("value", {}).get("Token", {}).get("name")
    if not token:
        raise RuntimeError("NVR API returned no session token")
    return token


def run_nvr_action(server, headers, device_id, action):
    if action not in ("diagnose", "reboot"):
        raise ValueError("Unsupported NVR action")
    # Fetch the agent's own scoped configuration; never put passwords in job records.
    response = _HTTP.get(server + "/api/agent/config", headers=headers, timeout=20)
    response.raise_for_status()
    devices = response.json().get("devices") or []
    device = next((d for d in devices if str(d.get("id")) == device_id and d.get("type") == "nvr"), None)
    if not device:
        raise ValueError("NVR not found in this agent's active location configuration")
    host = _private_host(str(device.get("host") or ""))
    streams = device.get("nvrStreams") or []
    checks = device.get("checks") or []
    stream = next((s for s in streams if s.get("username") and s.get("password")), None)
    check = next((c for c in checks if c.get("type") == "rtsp" and c.get("username") and c.get("password")), None)
    username = (stream or check or {}).get("username")
    password = (stream or check or {}).get("password")
    if action == "reboot":
        if not username or not password:
            raise ValueError("Configure NVR RTSP credentials before attempting a reboot")
        token = _login(host, username, password)
        response = _HTTP.post(
            f"http://{host}/cgi-bin/api.cgi",
            params={"cmd": "Reboot", "token": token},
            json=[{"cmd": "Reboot", "action": 0, "param": {}}],
            timeout=(4, 12),
        )
        response.raise_for_status()
        result = response.json()[0]
        if result.get("code") != 0:
            raise RuntimeError("NVR rejected reboot request")
        return {"host": host, "rebootAccepted": True, "note": "NVR accepted reboot; recovery not yet verified"}

    result = {"host": host, "tcp": {str(port): _port(host, port) for port in (80, 554)}}
    ping = ["ping", "-n", "1", "-w", "2000", host] if __import__("os").name == "nt" else ["ping", "-c", "1", "-W", "2", host]
    try:
        result["ping"] = subprocess.run(ping, capture_output=True, timeout=4).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        result["ping"] = False
    if result["tcp"]["80"]:
        try:
            r = _HTTP.get(f"http://{host}/cgi-bin/api.cgi", timeout=(3, 5))
            result["apiHttpStatus"] = r.status_code
            result["apiResponding"] = r.status_code == 200
        except requests.RequestException:
            result["apiResponding"] = False
    if username and password and result["tcp"]["80"]:
        try:
            _login(host, username, password)
            result["apiLogin"] = "success"
        except Exception:
            result["apiLogin"] = "failed"
    else:
        result["apiLogin"] = "credentials not configured"
    if streams and result["tcp"]["554"]:
        s = streams[0]
        url = str(s.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme == "rtsp" and parsed.hostname == host:
            from .checks import rtsp
            ok, latency, error = rtsp(url, s.get("username"), s.get("password"), 8, probe_context="nvr_diagnostics", use_probe_limit=False)
            result["rtsp"] = {"ok": ok, "latencyMs": latency, "error": "RTSP probe failed" if error else None}
    return result
