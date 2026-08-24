from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def apply_pending_rekey(server: str, token: str) -> dict:
    response = requests.get(
        server.rstrip("/") + "/api/agent/rekey",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    new_token = str(response.json().get("token") or "").strip()
    if not new_token:
        raise RuntimeError("SiteWatcher did not return the staged replacement token.")

    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        raise RuntimeError(f"SiteWatcher configuration file was not found: {env_path}")

    lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    updated = []
    replaced = False
    for line in lines:
        if line.strip().startswith("SITEWATCH_AGENT_TOKEN="):
            updated.append(f"SITEWATCH_AGENT_TOKEN={new_token}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"SITEWATCH_AGENT_TOKEN={new_token}")

    temp_path = env_path.with_suffix(".env.rekey")
    temp_path.write_text("\n".join(updated) + "\n", encoding="ascii")
    os.replace(temp_path, env_path)

    # Restart out-of-process after the current command result has enough time to
    # post using the old credential. The restarted service loads the staged token,
    # and the server promotes it on first successful authentication.
    restart_script = "Start-Sleep -Seconds 10; Restart-Service -Name 'SiteWatcherAgent' -Force"
    subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", restart_script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return {"ok": True, "message": "Replacement agent key written. SiteWatcher Agent will restart and reconnect with the new key."}
