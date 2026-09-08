import os
import shutil
import socket
import subprocess


def _run(argv, timeout=120):
    result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _listening(port=22):
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            pass
    return False


def get_ssh_status():
    if os.name == "nt":
        raise RuntimeError("Linux SSH maintenance is only available on Linux agents.")
    installed = shutil.which("sshd") is not None
    active = False
    enabled = False
    service_status = "Not installed" if not installed else "Unknown"
    if installed and shutil.which("systemctl"):
        code, out, _ = _run(["systemctl", "is-active", "ssh"], 10)
        active = code == 0 and out.strip() == "active"
        code, out, _ = _run(["systemctl", "is-enabled", "ssh"], 10)
        enabled = code == 0 and out.strip() in {"enabled", "enabled-runtime", "static"}
        service_status = "Running" if active else "Stopped"
    listening = _listening(22) if installed else False
    return {
        "installed": installed,
        "serviceStatus": service_status,
        "enabled": enabled,
        "listening": listening,
        "ready": installed and active and listening,
        "port": 22,
    }


def install_ssh():
    if os.name == "nt":
        raise RuntimeError("Linux SSH maintenance is only available on Linux agents.")
    if os.geteuid() != 0:
        raise RuntimeError("NodeVyu Agent must run as root to install OpenSSH.")
    if not shutil.which("apt-get"):
        raise RuntimeError("Automatic SSH installation currently requires an apt-based Linux distribution.")
    env = dict(os.environ)
    env["DEBIAN_FRONTEND"] = "noninteractive"
    code, out, err = _run(["apt-get", "update"], 180)
    if code != 0:
        raise RuntimeError(err or out or "apt-get update failed")
    result = subprocess.run(["apt-get", "install", "-y", "openssh-server"], capture_output=True, text=True, errors="replace", timeout=300, env=env)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "openssh-server installation failed").strip())
    return repair_ssh()


def repair_ssh():
    if os.name == "nt":
        raise RuntimeError("Linux SSH maintenance is only available on Linux agents.")
    if os.geteuid() != 0:
        raise RuntimeError("NodeVyu Agent must run as root to configure OpenSSH.")
    if not shutil.which("sshd"):
        return install_ssh()
    if not shutil.which("systemctl"):
        raise RuntimeError("systemd is required for automatic SSH service management.")
    code, out, err = _run(["systemctl", "enable", "--now", "ssh"], 45)
    if code != 0:
        raise RuntimeError(err or out or "Unable to enable/start ssh.service")
    status = get_ssh_status()
    if not status["ready"]:
        code, out, err = _run(["systemctl", "restart", "ssh"], 30)
        if code != 0:
            raise RuntimeError(err or out or "Unable to restart ssh.service")
        status = get_ssh_status()
    return status


def restart_ssh():
    if os.name == "nt":
        raise RuntimeError("Linux SSH maintenance is only available on Linux agents.")
    if not shutil.which("sshd"):
        raise RuntimeError("OpenSSH server is not installed.")
    code, out, err = _run(["systemctl", "restart", "ssh"], 30)
    if code != 0:
        raise RuntimeError(err or out or "Unable to restart ssh.service")
    return get_ssh_status()
