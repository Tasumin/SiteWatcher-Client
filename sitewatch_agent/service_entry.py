import os
import sys
import threading
import time
import warnings
from pathlib import Path

from urllib3.exceptions import InsecureRequestWarning

_SERVICE_ENTRY_MUTEX = None
_SERVICE_ENTRY_LOCK = None
_CANONICAL_SERVER_URL = "https://nodevyu.com"
_LEGACY_SERVER_URLS = {
    "https://monitoring.talondns.com",
    "http://monitoring.talondns.com",
}
_CA_OVERRIDE_ENV = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR")


def acquire_service_entry_mutex() -> bool:
    """Prevent duplicate Windows service_entry processes before any workers start."""
    global _SERVICE_ENTRY_MUTEX
    if os.name != "nt":
        return True

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, "Global\\NodeVyuAgent-ServiceEntry")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())

    error = ctypes.get_last_error()
    if error == 183:
        kernel32.CloseHandle(mutex)
        return False

    _SERVICE_ENTRY_MUTEX = mutex
    return True


def acquire_service_entry_file_lock(root: Path) -> bool:
    """Second singleton guard using a byte-range lock held for process lifetime."""
    global _SERVICE_ENTRY_LOCK
    if os.name != "nt":
        return True

    import msvcrt

    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / "service-entry.lock"
    handle = open(lock_path, "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    _SERVICE_ENTRY_LOCK = handle
    return True


def _migrate_legacy_server_url(env_file: Path, rows: list[str]) -> list[str]:
    changed = False
    migrated: list[str] = []
    for raw in rows:
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == "SITEWATCH_SERVER_URL" and value.strip().rstrip("/").lower() in _LEGACY_SERVER_URLS:
                migrated.append(f"SITEWATCH_SERVER_URL={_CANONICAL_SERVER_URL}")
                changed = True
                continue
        migrated.append(raw)
    if changed:
        env_file.write_text("\n".join(migrated).rstrip() + "\n", encoding="utf-8")
    return migrated


def load_env(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        raise RuntimeError(f"Missing NodeVyu configuration: {env_file}")

    rows = env_file.read_text(encoding="utf-8-sig").splitlines()
    rows = _migrate_legacy_server_url(env_file, rows)

    for raw in rows:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()

    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SITEWATCH_DB", str(data_dir / "queue.db"))
    os.environ.setdefault("SITEWATCH_LOCK_FILE", str(data_dir / "sitewatch-agent.lock"))

    ffmpeg_dir = root / "bin"
    if (ffmpeg_dir / "ffmpeg.exe").exists():
        os.environ.setdefault("SITEWATCH_FFMPEG_DIR", str(ffmpeg_dir))


def _clear_external_ca_overrides() -> list[str]:
    cleared: list[str] = []
    for key in _CA_OVERRIDE_ENV:
        if os.environ.pop(key, None) is not None:
            cleared.append(key)
    return cleared


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)

    if not acquire_service_entry_mutex():
        return
    if not acquire_service_entry_file_lock(root):
        return

    load_env(root)
    cleared_ca = _clear_external_ca_overrides()

    # Local appliances may intentionally use verify=False. Suppress only that
    # warning; NodeVyu API requests continue to validate certificates normally.
    warnings.filterwarnings("ignore", category=InsecureRequestWarning, module=r"urllib3\.connectionpool")

    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from .log_router import install_log_router
    install_log_router(log_dir)

    from .main import main as agent_main
    from . import remote_console
    from .host_monitor import host_monitor_loop
    from .live_stream import live_stream_loop
    from .update_launcher import launch_self_update

    print(f"[startup] logging split enabled path={log_dir}", flush=True)
    print(f"[startup] server={os.environ.get('SITEWATCH_SERVER_URL')}", flush=True)
    if cleared_ca:
        print(f"[startup] ignored external CA overrides for NodeVyu API: {','.join(cleared_ca)}", flush=True)

    remote_console._launch_self_update = launch_self_update

    console_thread = threading.Thread(target=remote_console.remote_console_loop, name="nodevyu-remote-console", daemon=True)
    console_thread.start()
    print("[startup] NodeVyu worker remote-console started", flush=True)

    host_thread = threading.Thread(target=host_monitor_loop, name="nodevyu-host-monitor", daemon=True)
    host_thread.start()
    print("[startup] NodeVyu worker host-monitor started", flush=True)

    server_url = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
    agent_token = os.environ["SITEWATCH_AGENT_TOKEN"]

    def delayed_live_stream() -> None:
        time.sleep(3)
        live_stream_loop(server_url, agent_token)

    live_thread = threading.Thread(target=delayed_live_stream, name="nodevyu-live-stream", daemon=True)
    live_thread.start()
    print("[startup] NodeVyu worker live-stream scheduled", flush=True)

    # Keep the service process and already-running live/tunnel workers alive if
    # the monitoring scheduler ever escapes unexpectedly.
    while True:
        try:
            agent_main()
            print("[startup] monitoring main loop returned unexpectedly; restarting in 5s", flush=True)
        except KeyboardInterrupt:
            print("[shutdown] service stop requested; exiting cleanly", flush=True)
            return
        except BaseException as exc:
            print(f"[startup] monitoring main loop crashed: {type(exc).__name__}: {exc}; restarting in 5s", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
