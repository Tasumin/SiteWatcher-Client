from __future__ import annotations

import sys
import threading
from pathlib import Path


ROUTES = {
    "tunnel-live.log": ("[live]", "[tunnel]"),
    "update.log": ("[update]",),
    "snmp.log": ("[snmp]",),
    "host-monitor.log": ("[host]",),
    "camera-monitor.log": ("[camera]", "[snapshot]", "[preview]", "[onvif]", "[nvr]", "[rtsp]"),
}


class RoutedLogStream:
    """Route stdout/stderr lines to subsystem log files without changing callers."""

    def __init__(self, log_dir: Path, fallback_name: str = "agent.log") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.fallback_name = fallback_name
        self._handles: dict[str, object] = {}
        self._buffers: dict[int, str] = {}
        self._lock = threading.RLock()

    def _handle(self, name: str):
        handle = self._handles.get(name)
        if handle is None:
            handle = open(self.log_dir / name, "a", encoding="utf-8", buffering=1)
            self._handles[name] = handle
        return handle

    def _destination(self, line: str) -> str:
        stripped = line.lstrip()
        for name, prefixes in ROUTES.items():
            if any(stripped.startswith(prefix) for prefix in prefixes):
                return name
        return self.fallback_name

    def _emit(self, line: str) -> None:
        name = self._destination(line)
        self._handle(name).write(line + "\n")

    def write(self, text: str) -> int:
        if not text:
            return 0
        tid = threading.get_ident()
        with self._lock:
            pending = self._buffers.get(tid, "") + str(text)
            parts = pending.split("\n")
            self._buffers[tid] = parts.pop()
            for line in parts:
                self._emit(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                try:
                    handle.flush()
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            for tid, pending in list(self._buffers.items()):
                if pending:
                    self._emit(pending.rstrip("\r"))
                self._buffers.pop(tid, None)
            for handle in self._handles.values():
                try:
                    handle.flush()
                    handle.close()
                except Exception:
                    pass
            self._handles.clear()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        return False


def install_log_router(log_dir: Path) -> RoutedLogStream:
    router = RoutedLogStream(log_dir)
    sys.stdout = router
    sys.stderr = router
    return router
