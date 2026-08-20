import json
import socket
import threading
import time
from urllib.parse import quote, urlencode, urlparse, urlunparse

import websocket


def _ws_base(server_url: str) -> str:
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))


def _control_url(server_url: str, token: str) -> str:
    return f"{_ws_base(server_url)}/tunnel/agent?{urlencode({'token': token})}"


def _data_url(server_url: str, token: str, connection_id: str) -> str:
    return f"{_ws_base(server_url)}/tunnel/data?{urlencode({'token': token, 'id': connection_id})}"


def _bridge_connection(server_url: str, token: str, command: dict) -> None:
    connection_id = str(command.get("connectionId") or "")
    target_host = str(command.get("targetHost") or "").strip()
    target_port = int(command.get("targetPort") or 0)
    if not connection_id or not target_host or target_port < 1 or target_port > 65535:
        print("[tunnel] rejected malformed open command", flush=True)
        return

    tcp = None
    data_ws = None
    stop = threading.Event()
    try:
        print(f"[tunnel] opening {target_host}:{target_port} connection={connection_id[:8]}", flush=True)
        tcp = socket.create_connection((target_host, target_port), timeout=12)
        tcp.settimeout(None)
        data_ws = websocket.create_connection(
            _data_url(server_url, token, connection_id),
            timeout=15,
            enable_multithread=True,
        )
        data_ws.settimeout(None)

        def tcp_to_ws():
            try:
                while not stop.is_set():
                    chunk = tcp.recv(65536)
                    if not chunk:
                        break
                    data_ws.send_binary(chunk)
            except Exception:
                pass
            finally:
                stop.set()
                try:
                    data_ws.close()
                except Exception:
                    pass

        sender = threading.Thread(target=tcp_to_ws, name=f"sitewatch-tunnel-up-{connection_id[:8]}", daemon=True)
        sender.start()

        while not stop.is_set():
            message = data_ws.recv()
            if message is None:
                break
            if isinstance(message, str):
                message = message.encode("utf-8")
            if message:
                tcp.sendall(message)
        print(f"[tunnel] closed {target_host}:{target_port} connection={connection_id[:8]}", flush=True)
    except Exception as exc:
        print(f"[tunnel] {target_host}:{target_port} failed: {exc}", flush=True)
    finally:
        stop.set()
        if data_ws is not None:
            try:
                data_ws.close()
            except Exception:
                pass
        if tcp is not None:
            try:
                tcp.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                tcp.close()
            except Exception:
                pass


def tunnel_loop(server_url: str, token: str) -> None:
    reconnect_delay = 2
    while True:
        control = None
        try:
            control = websocket.create_connection(
                _control_url(server_url, token),
                timeout=20,
                enable_multithread=True,
            )
            control.settimeout(None)
            reconnect_delay = 2
            print("[tunnel] remote management channel connected", flush=True)

            while True:
                raw = control.recv()
                if raw is None:
                    raise ConnectionError("remote management channel closed")
                if isinstance(raw, bytes):
                    continue
                command = json.loads(raw)
                command_type = str(command.get("type") or "")
                if command_type == "ready":
                    continue
                if command_type == "open":
                    thread = threading.Thread(
                        target=_bridge_connection,
                        args=(server_url, token, command),
                        name=f"sitewatch-tunnel-{str(command.get('connectionId') or '')[:8]}",
                        daemon=True,
                    )
                    thread.start()
        except Exception as exc:
            print(f"[tunnel] channel disconnected: {exc}; retrying in {reconnect_delay}s", flush=True)
        finally:
            if control is not None:
                try:
                    control.close()
                except Exception:
                    pass
        time.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 30)
