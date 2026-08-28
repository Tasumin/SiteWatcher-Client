import os
import sys
import threading
import time
from pathlib import Path

_SERVICE_ENTRY_MUTEX = None
_SERVICE_ENTRY_LOCK = None


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
    if error == 183:  # ERROR_ALREADY_EXISTS
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


def load_env(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        raise RuntimeError(f"Missing NodeVyu configuration: {env_file}")

    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
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


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)

    # These guards must run before environment loading, logging, imports, or
    # worker startup.  If any service/update path recursively launches another
    # service_entry process, the child exits without touching the relay.
    if not acquire_service_entry_mutex():
        return
    if not acquire_service_entry_file_lock(root):
        return

    load_env(root)

    # One append-only application log for now. No application-level rotation.
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_dir / "agent.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = log_handle
    sys.stderr = log_handle

    from .main import main as agent_main
    from . import remote_console
    from .host_monitor import host_monitor_loop
    from .live_stream import live_stream_loop
    from .update_launcher import launch_self_update

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

    live_thread = threading.Thread(
        target=delayed_live_stream,
        name="nodevyu-live-stream",
        daemon=True,
    )
    live_thread.start()
    print("[startup] NodeVyu worker live-stream scheduled", flush=True)

    # NVR stream worker is started by agent_main so there is exactly one copy.
    agent_main()


if __name__ == "__main__":
    main()
