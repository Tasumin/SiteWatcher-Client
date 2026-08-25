import os
import subprocess
import time
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil


class NodeVyuService(win32serviceutil.ServiceFramework):
    _svc_name_ = "NodeVyuAgent"
    _svc_display_name_ = "NodeVyu Agent"
    _svc_description_ = "NodeVyu native Windows monitoring agent"

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None
        self.root = Path(__file__).resolve().parent.parent
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "agent.log"

    def _load_env(self):
        env_file = self.root / ".env"
        if not env_file.exists():
            raise RuntimeError(f"Missing NodeVyu configuration: {env_file}")
        for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()

        data_dir = self.root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("SITEWATCH_DB", str(data_dir / "queue.db"))
        os.environ.setdefault("SITEWATCH_LOCK_FILE", str(data_dir / "sitewatch-agent.lock"))
        ffmpeg_dir = self.root / "bin"
        if (ffmpeg_dir / "ffmpeg.exe").exists():
            os.environ.setdefault("SITEWATCH_FFMPEG_DIR", str(ffmpeg_dir))

    def _python_executable(self):
        python = self.root / ".venv" / "Scripts" / "python.exe"
        if not python.exists():
            raise RuntimeError(f"NodeVyu Python environment is missing: {python}")
        return str(python)

    def _start_agent(self):
        self._load_env()
        log_handle = open(self.log_path, "a", encoding="utf-8", buffering=1)
        log_handle.write(f"\n===== NodeVyu Windows Service start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [self._python_executable(), "-u", "-m", "sitewatch_agent.main"],
            cwd=str(self.root),
            env=os.environ.copy(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        self._log_handle = log_handle

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=15)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("NodeVyu Agent service starting")
        try:
            self._start_agent()
            while True:
                wait = win32event.WaitForSingleObject(self.stop_event, 1000)
                if wait == win32event.WAIT_OBJECT_0:
                    break
                if self.process and self.process.poll() is not None:
                    code = self.process.returncode
                    servicemanager.LogErrorMsg(f"NodeVyu agent exited unexpectedly with code {code}")
                    raise RuntimeError(f"NodeVyu agent exited with code {code}")
        except Exception as exc:
            servicemanager.LogErrorMsg(f"NodeVyu Agent service error: {exc}")
            raise
        finally:
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=10)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
            handle = getattr(self, "_log_handle", None)
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
            servicemanager.LogInfoMsg("NodeVyu Agent service stopped")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(NodeVyuService)
