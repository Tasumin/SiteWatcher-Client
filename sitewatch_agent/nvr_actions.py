"""Run fixed NVR maintenance actions from the recorder's site network."""

import ipaddress
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests

REOLINK_ACTIONS = {
    "device-info": "GetDevInfo",
    "channels": "GetChannelstatus",
    "storage": "GetHddInfo",
    "reboot": "Reboot",
}
ACTION_PATH = "/api/agent/nvr-actions"
MAX_RESPONSE_BYTES = 256 * 1024


class ActionError(Exception):
    """Fixed, safe error suitable for the dashboard."""


class RebootUnconfirmed(ActionError):
    pass


class CgiNotFound(ActionError):
    pass


def _host(value):
    if not isinstance(value, str):
        raise ActionError("The NVR host is invalid.")
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
        return str(address)
    except ValueError:
        if re.fullmatch(r"(?=.{1,253}\.?$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.?", host):
            return host
    raise ActionError("The NVR host is invalid.")


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ActionError("The NVR port is invalid.")
    return value


def _https_origin(host, port):
    address = f"[{host}]" if ":" in host else host
    return f"https://{address}:{port}"


def _cgi(session, origin, path, verify, command, token="null", param=None):
    try:
        with session.post(
            origin + path, params={"cmd": command, "token": token},
            json=[{"cmd": command, "action": 0, "param": param or {}}],
            verify=verify, allow_redirects=False, timeout=(3, 4), stream=True,
        ) as response:
            if response.status_code == 404:
                raise CgiNotFound("The Reolink HTTPS CGI endpoint was not found.")
            if response.status_code in (401, 403):
                raise ActionError("The recorder rejected the saved RTSP account or its permissions.")
            if response.status_code != 200:
                if command == "Reboot":
                    raise RebootUnconfirmed("No reboot acknowledgment. Check the recorder before trying again.")
                raise ActionError(f"Recorder HTTPS API returned HTTP {response.status_code}.")
            chunks = bytearray()
            deadline = time.monotonic() + 5
            for chunk in response.iter_content(8192):
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                    raise ValueError("Response exceeded limits")
            rows = json.loads(chunks)
            if not isinstance(rows, list):
                raise ValueError("Unexpected response")
            row = next((item for item in rows if isinstance(item, dict) and item.get("cmd") == command), None)
            if row is None or type(row.get("code")) is not int:
                raise ValueError("Missing response code")
    except requests.exceptions.SSLError as exc:
        if command == "Reboot":
            raise RebootUnconfirmed("No reboot acknowledgment. Check the recorder before trying again.") from exc
        raise ActionError("HTTPS certificate or TLS verification failed. Check the recorder’s certificate and NodeVyu HTTPS check settings.") from exc
    except (requests.RequestException, ValueError) as exc:
        if command == "Reboot":
            raise RebootUnconfirmed("No reboot acknowledgment. Check the recorder before trying again.") from exc
        raise ActionError("Could not read the recorder’s HTTPS API. Check that HTTPS and CGI are enabled.") from exc

    if row["code"] != 0:
        if command == "Login":
            raise ActionError("Reolink CGI login failed. Check the saved RTSP username and password and account access.")
        raise ActionError("The recorder rejected this action. Check the account permissions and firmware support.")
    value = row.get("value")
    return value if isinstance(value, dict) else {}


def _selected_fields(row, keys, secrets):
    selected = {}
    if not isinstance(row, dict):
        return selected
    for key in keys:
        value = row.get(key)
        if type(value) in (int, bool):
            selected[key] = value
        elif isinstance(value, str):
            if any(secret and secret in value for secret in secrets):
                value = "[redacted]"
            selected[key] = value[:100]
    return selected


def _reolink(job, action, host, port):
    username, password = job.get("username"), job.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str):
        raise ActionError("Save the recorder’s RTSP username and password first.")
    origin = _https_origin(host, port)
    verify = job.get("verifyTls") is not False
    with requests.Session() as session:
        # Never use a proxy or ambient .netrc credentials for local recorder requests.
        session.trust_env = False
        path = "/cgi-bin/api.cgi"
        login = {"User": {"userName": username, "password": password}}
        try:
            value = _cgi(session, origin, path, verify, "Login", param=login)
        except CgiNotFound:
            path = "/api.cgi"
            value = _cgi(session, origin, path, verify, "Login", param=login)
        token_data = value.get("Token")
        token = token_data.get("name") if isinstance(token_data, dict) else None
        if not isinstance(token, str) or not token:
            raise ActionError("Reolink CGI login did not return a session token.")
        secrets = (username, password, token)

        acknowledged = False
        try:
            if action == "reboot":
                device = _cgi(session, origin, path, verify, "GetDevInfo", token).get("DevInfo")
                if not isinstance(device, dict) or not any(
                    str(device.get(field, "")).upper() in ("NVR", "WIFI_NVR")
                    for field in ("type", "exactType")
                ):
                    raise ActionError("The device did not identify itself as a Reolink NVR. No reboot was sent.")
            value = _cgi(session, origin, path, verify, REOLINK_ACTIONS[action], token)
            if action == "reboot":
                if value.get("rspCode") != 200:
                    raise RebootUnconfirmed("No reboot acknowledgment. Check the recorder before trying again.")
                acknowledged = True
                return "Reboot accepted. Check port or RTSP status after the recorder restarts.", None
            if action == "device-info":
                result = _selected_fields(value.get("DevInfo"), ("model", "firmVer", "hardVer", "channelNum", "diskNum", "type"), secrets)
                if not result:
                    raise ActionError("The recorder did not return device information.")
                return "Recorder information retrieved.", result
            key, fields, label = (
                ("status", ("channel", "online", "name", "typeInfo"), "channels") if action == "channels"
                else ("HddInfo", ("id", "number", "capacity", "size", "free", "format", "mount", "storageType", "status"), "disks")
            )
            rows = value.get(key)
            if not isinstance(rows, list):
                raise ActionError("The recorder did not return the requested status data.")
            entries = []
            for row in rows[:64]:
                if not isinstance(row, dict):
                    continue
                entry = _selected_fields(row, fields, secrets)
                if action == "storage":
                    if "capacity" in entry:
                        entry["capacityMB"] = entry.pop("capacity")
                    if "size" in entry:
                        entry["remainingMB"] = entry.pop("size")
                entries.append(entry)
            return f"Recorder reported {len(rows)} {label}.", {label: entries}
        finally:
            if not acknowledged:
                try:
                    _cgi(session, origin, path, verify, "Logout", token)
                except ActionError:
                    pass


def _port_check(host, port):
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=2):
            return {"port": port, "reachable": True, "latencyMs": round((time.monotonic() - start) * 1000)}
    except OSError:
        return {"port": port, "reachable": False}


def _rtsp_check(job, host, port):
    from .checks import rtsp

    url = job.get("rtspUrl")
    if not isinstance(url, str):
        raise ActionError("No RTSP stream is configured.")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ("rtsp", "rtsps") or _host(parsed.hostname).lower() != host.lower()
                or (parsed.port or 554) != port):
            raise ValueError("Unexpected RTSP destination")
    except (ValueError, ActionError) as exc:
        raise ActionError("The RTSP URL must point to this recorder and its configured RTSP port.") from exc
    username = job.get("username")
    password = job.get("password")
    ok, elapsed, detail = rtsp(url, quote(username, safe="") if username else None,
                               quote(password, safe="") if password is not None else None,
                               6, probe_context="nvr-action", use_probe_limit=False)
    message = "The stream returned video."
    if not ok:
        detail = str(detail or "").lower()
        if "401" in detail or "unauthorized" in detail:
            message = "The RTSP server rejected authentication."
        elif "timeout" in detail or "timed out" in detail:
            message = "The RTSP stream timed out."
        else:
            message = "No video returned. Check the channel, stream path, and credentials."
    return ("completed" if ok else "failed"), message, {"channel": job.get("channel"), "latencyMs": elapsed}


def run_nvr_action(job):
    action = job.get("action")
    try:
        if action not in ("ports", "rtsp", *REOLINK_ACTIONS):
            raise ActionError("Unsupported NVR action.")
        expires = datetime.fromisoformat(job["expiresAt"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= expires:
            raise ActionError("The action expired before the agent could run it.")
        host = _host(job.get("host"))
        status = "completed"
        if action == "ports":
            ports = list(dict.fromkeys((80, _port(job.get("httpsPort")), _port(job.get("rtspPort")), 9000)))
            with ThreadPoolExecutor(max_workers=len(ports)) as executor:
                result = {"ports": list(executor.map(lambda port: _port_check(host, port), ports))}
            message = "TCP port check complete."
        elif action == "rtsp":
            status, message, result = _rtsp_check(job, host, _port(job.get("rtspPort")))
        else:
            message, result = _reolink(job, action, host, _port(job.get("httpsPort")))
        return {"status": status, "message": message, "result": result}
    except RebootUnconfirmed as exc:
        return {"status": "unknown", "message": str(exc)}
    except ActionError as exc:
        return {"status": "failed", "message": str(exc)}
    except Exception:
        return {"status": "unknown" if action == "reboot" else "failed",
                "message": "The agent could not finish the action. Check the recorder’s status."}


def nvr_action_loop(api):
    while True:
        interval = 4
        try:
            response = api("GET", ACTION_PATH)
            if response.status_code == 404:
                interval = 30  # Older NodeVyu server.
            elif response.ok:
                job = response.json().get("job")
                if isinstance(job, dict) and job.get("id"):
                    report = {"id": job["id"], **run_nvr_action(job)}
                    # Only retry delivery of the result, never the actual command.
                    for _ in range(2):
                        try:
                            result = api("POST", ACTION_PATH, json=report)
                            if result.status_code < 500:
                                break
                        except requests.RequestException:
                            pass
            else:
                interval = 15
        except (requests.RequestException, ValueError):
            interval = 15
        time.sleep(interval)
