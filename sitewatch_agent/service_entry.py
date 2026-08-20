import os
import threading
from pathlib import Path


def load_env(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        raise RuntimeError(f"Missing SiteWatcher configuration: {env_file}")

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
    load_env(root)

    # Import only after .env is loaded because the agent reads SiteWatcher
    # configuration from environment variables at import time.
    from .main import main as agent_main
    from .tunnel import tunnel_loop

    server_url = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
    token = os.environ["SITEWATCH_AGENT_TOKEN"]
    tunnel_thread = threading.Thread(
        target=tunnel_loop,
        args=(server_url, token),
        name="sitewatch-remote-tunnel",
        daemon=True,
    )
    tunnel_thread.start()
    print("[startup] worker remote-tunnel started", flush=True)

    agent_main()


if __name__ == "__main__":
    main()
