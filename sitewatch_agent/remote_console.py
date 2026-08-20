import os
import subprocess
import time

import requests


SERVER = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
TOKEN = os.environ["SITEWATCH_AGENT_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
UPDATE_COMMAND = "__SITEWATCH_UPDATE_AGENT__"

# Diagnostic-only console. Reject shell composition and only allow known
# troubleshooting commands/cmdlets.
BLOCKED_TOKENS = (";", "&&", "||", "|", ">", "<", "`", "$(", "@(")
ALLOWED_PREFIXES = (
    "ping ", "ping.exe ", "tracert ", "tracert.exe ", "pathping ", "pathping.exe ",
    "nslookup ", "nslookup.exe ", "curl ", "curl.exe ", "arp ", "arp.exe ",
    "ipconfig", "route print", "route.exe print", "netstat ", "netstat.exe ",
    "test-netconnection ", "resolve-dnsname ", "get-netipaddress", "get-netroute",
    "get-netadapter", "get-nettcpconnection", "get-netneighbor", "get-dnsclient",
    "get-dnsclientserveraddress", "invoke-webrequest ", "invoke-restmethod "
)


def _allowed(command: str) -> tuple[bool, str]:
    text = command.strip()
    if not text:
        return False, "Command is empty."
    lower = text.lower()
    if any(token in text for token in BLOCKED_TOKENS):
        return False, "Command chaining, pipelines, redirection, and subexpressions are disabled in Remote Console."
    if "\n" in text or "\r" in text:
        return False, "Only one diagnostic command may be run at a time."
    if not any(lower == prefix.strip() or lower.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return False, "Command is not in the SiteWatcher diagnostic allowlist."
    return True, ""


def _run(command: str, shell: str, timeout_seconds: int = 60):
    ok, reason = _allowed(command)
    if not ok:
        return {"stdout": "", "stderr": reason, "exitCode": None, "rejected": True}

    if shell == "cmd":
        argv = ["cmd.exe", "/d", "/s", "/c", command]
    else:
        argv = [
            "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", command
        ]

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {"stdout": (completed.stdout or "")[:200000], "stderr": (completed.stderr or "")[:200000], "exitCode": completed.returncode}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {"stdout": stdout[:200000], "stderr": (stderr + "\nCommand timed out after 60 seconds.").strip()[:200000], "exitCode": None, "timedOut": True}
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exitCode": 1}


def _launch_self_update():
    installer_url = SERVER + "/downloads/sitewatcher-agent"
    script = (
        "$ErrorActionPreference='Stop'; "
        "$i=Join-Path $env:TEMP 'install-sitewatcher-agent.ps1'; "
        f"Invoke-WebRequest -Uri '{installer_url}' -OutFile $i -UseBasicParsing; "
        f"& $i -ServerUrl '{SERVER}'"
    )
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def remote_console_loop():
    time.sleep(3)
    while True:
        try:
            response = requests.get(SERVER + "/api/agent/commands", headers=HEADERS, timeout=20)
            if not response.ok:
                if response.status_code != 404:
                    print(f"[console] server HTTP {response.status_code}: {response.text[:200]}", flush=True)
                time.sleep(3)
                continue

            item = response.json().get("command")
            if not item:
                time.sleep(2)
                continue

            command_id = str(item.get("id") or "")
            command = str(item.get("command") or "")
            shell = str(item.get("shell") or "powershell").lower()

            if command == UPDATE_COMMAND and shell == "system":
                print(f"[update] launching SiteWatcher self-update job id={command_id[:8]}", flush=True)
                try:
                    _launch_self_update()
                    result = {"id":command_id,"stdout":"SiteWatcher agent update launched. The service will restart automatically.","stderr":"","exitCode":0}
                except Exception as exc:
                    result = {"id":command_id,"stdout":"","stderr":f"Unable to launch SiteWatcher update: {exc}","exitCode":1}
                post = requests.post(SERVER + "/api/agent/commands", headers=HEADERS, json=result, timeout=20)
                post.raise_for_status()
                print(f"[update] update job id={command_id[:8]} acknowledged", flush=True)
                time.sleep(10)
                continue

            print(f"[console] executing diagnostic command id={command_id[:8]} shell={shell}", flush=True)
            result = _run(command, shell)
            result["id"] = command_id

            post = requests.post(SERVER + "/api/agent/commands", headers=HEADERS, json=result, timeout=20)
            post.raise_for_status()
            print(f"[console] command id={command_id[:8]} completed", flush=True)
        except Exception as exc:
            print(f"[console] worker error: {exc}", flush=True)
            time.sleep(5)
