import json
import socket
import threading
import time
from urllib.parse import quote

import websocket


def _ws_base(server_url: str) -> str:
    server_url = server_url.rstrip("/")
    if server_url.startswith("https://"):
        return "wss://" + server_url[len("https://"):]
    if server_url.startswith("http://"):
        return "ws://" + server_url[len("http://"):]
    raise ValueError("SITEWATCH_SERVER_URL must start with http:// or https://")


def _close_quietly(obj):
    try:
        obj.close()
    except Exception:
        pass


def _control_close_details(ws) -> str:
    code = getattr(ws, "close_status_code", None)
    reason = getattr(ws, "close_reason", None)
    sock = getattr(ws, "sock", None)
    if code is None and sock is not None:
        code = getattr(sock, "close_status_code", None)
    if reason is None and sock is not None:
        reason = getattr(sock, "close_reason", None)
    parts = []
    if code is not None:
        parts.append(f"code={code}")
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts) if parts else "no close code/reason supplied"


def _relay_connection(server_url: str, token: str, request: dict) -> None:
    connection_id = str(request.get("connectionId") or "")
    target_host = str(request.get("targetHost") or "").strip()
    try:
        target_port = int(request.get("targetPort") or 0)
    except Exception:
        target_port = 0

    if not connection_id or not target_host or target_port < 1 or target_port > 65535:
        print(f"[tunnel] ignoring malformed open request: {request}", flush=True)
        return

    tcp = None
    data_ws = None
    try:
        print(f"[tunnel] opening {target_host}:{target_port} connection={connection_id}", flush=True)
        tcp = socket.create_connection((target_host, target_port), timeout=15)
        tcp.settimeout(None)

        data_url = (
            f"{_ws_base(server_url)}/tunnel/data"
            f"?id={quote(connection_id, safe='')}"
            f"&token={quote(token, safe='')}"
        )
        data_ws = websocket.create_connection(
            data_url,
            timeout=20,
            enable_multithread=True,
        )
        data_ws.settimeout(None)

        stopped = threading.Event()

        def tcp_to_ws():
            try:
                while not stopped.is_set():
                    chunk = tcp.recv(65536)
                    if not chunk:
                        break
                    data_ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as exc:
                if not stopped.is_set():
                    print(f"[tunnel] TCP->WSS relay ended: {exc}", flush=True)
            finally:
                stopped.set()
                _close_quietly(data_ws)
                _close_quietly(tcp)

        sender = threading.Thread(
            target=tcp_to_ws,
            name=f"sitewatch-tunnel-tcp-{connection_id[:8]}",
            daemon=True,
        )
        sender.start()

        while not stopped.is_set():
            frame = data_ws.recv()
            if frame is None or frame == b"" or frame == "":
                break
            if isinstance(frame, str):
                frame = frame.encode("utf-8")
            tcp.sendall(frame)

        stopped.set()
    except Exception as exc:
        print(
            f"[tunnel] relay failed {target_host}:{target_port} "
            f"connection={connection_id}: {exc}",
            flush=True,
        )
    finally:
        if data_ws is not None:
            _close_quietly(data_ws)
        if tcp is not None:
            _close_quietly(tcp)


def remote_tunnel_loop(server_url: str, token: str) -> None:
    """Maintain the persistent outbound SiteWatcher remote-management channel."""
    retry_seconds = 2

    while True:
        control_ws = None
        try:
            control_url = (
                f"{_ws_base(server_url)}/tunnel/agent"
                f"?token={quote(token, safe='')}"
            )
            print(f"[tunnel] connecting remote management channel to {_ws_base(server_url)}", flush=True)
            control_ws = websocket.create_connection(
                control_url,
                timeout=20,
                enable_multithread=True,
            )
            control_ws.settimeout(None)
            print("[tunnel] remote management channel connected", flush=True)
            retry_seconds = 2

            while True:
                raw = control_ws.recv()
                if raw is None or raw == b"" or raw == "":
                    details = _control_close_details(control_ws)
                    raise ConnectionError(f"remote management channel closed by server ({details})")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError as exc:
                    print(f"[tunnel] ignoring non-JSON control frame: {exc}", flush=True)
                    continue

                if not isinstance(message, dict):
                    continue

                message_type = str(message.get("type") or "")
                if message_type == "ready":
                    print("[tunnel] server confirmed remote management channel", flush=True)
                    continue

                if message_type == "open":
                    worker = threading.Thread(
                        target=_relay_connection,
                        args=(server_url, token, message),
                        name=f"sitewatch-tunnel-data-{str(message.get('connectionId') or '')[:8]}",
                        daemon=True,
                    )
                    worker.start()
                    continue

        except Exception as exc:
            print(f"[tunnel] channel disconnected: {exc}; retrying in {retry_seconds}s", flush=True)
        finally:
            if control_ws is not None:
                _close_quietly(control_ws)

        time.sleep(retry_seconds)
        retry_seconds = min(retry_seconds * 2, 30)
