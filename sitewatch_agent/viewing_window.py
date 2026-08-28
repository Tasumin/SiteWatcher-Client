import threading
import time

_lock = threading.RLock()
_active = {}


def _key(source_type: str, source_id: str):
    return str(source_type), str(source_id)


def enter_viewing_window(source_type: str, source_id: str, session_id: str):
    key = _key(source_type, source_id)
    with _lock:
        sessions = _active.setdefault(key, {})
        sessions[str(session_id)] = time.time()


def leave_viewing_window(source_type: str, source_id: str, session_id: str):
    key = _key(source_type, source_id)
    with _lock:
        sessions = _active.get(key)
        if not sessions:
            return
        sessions.pop(str(session_id), None)
        if not sessions:
            _active.pop(key, None)


def is_viewing(source_type: str, source_id: str) -> bool:
    with _lock:
        return bool(_active.get(_key(source_type, source_id)))


def active_sessions():
    with _lock:
        return {
            f"{source_type}:{source_id}": list(sessions.keys())
            for (source_type, source_id), sessions in _active.items()
            if sessions
        }
