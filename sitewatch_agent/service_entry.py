import os
import threading
from pathlib import Path


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
    # Keep the existing environment variable names and filenames for backward
    # compatibility with deployed agents and existing local queue databases.
    os.environ.setdefault("SITEWATCH_DB", str(data_dir / "queue.db"))
    os.environ.setdefault("SITEWATCH_LOCK_FILE", str(data_dir / "sitewatch-agent.lock"))

    ffmpeg_dir = root / "bin"
    if (ffmpeg_dir / "ffmpeg.exe").exists():
        os.environ.setdefault("SITEWATCH_FFMPEG_DIR", str(ffmpeg_dir))


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    load_env(root)

    from .main import main as agent_main
    from . import remote_console
    from .host_monitor import host_monitor_loop
    from .update_launcher import launch_self_update

    remote_console._launch_self_update = launch_self_update

    console_thread = threading.Thread(
        target=remote_console.remote_console_loop,
        name="nodevyu-remote-console",
        daemon=True,
    )
    console_thread.start()
    print("[startup] NodeVyu worker remote-console started", flush=True)

    host_thread = threading.Thread(
        target=host_monitor_loop,
        name="nodevyu-host-monitor",
        daemon=True,
    )
    host_thread.start()
    print("[startup] NodeVyu worker host-monitor started", flush=True)

    agent_main()


if __name__ == "__main__":
    main()
