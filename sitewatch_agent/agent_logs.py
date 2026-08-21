from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from . import __version__


MAX_OUTPUT_CHARS = 190000
MAX_FILES = 8
MAX_ZIP_FILES = 32


def _tail_text(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as exc:
        return f"[unable to read {path.name}: {exc}]"
    rows = text.splitlines()
    return "\n".join(rows[-lines:])


def collect_agent_logs(lines: int = 250) -> str:
    lines = max(50, min(1000, int(lines or 250)))
    root = Path(__file__).resolve().parent.parent
    log_dir = root / "logs"

    sections: list[str] = [
        "SiteWatcher Agent Diagnostics",
        f"Version: {__version__}",
        f"Computer: {os.environ.get('COMPUTERNAME') or os.environ.get('HOSTNAME') or 'unknown'}",
        f"Install path: {root}",
        f"Requested tail: {lines} lines per file",
        "",
    ]

    if not log_dir.exists():
        sections.append(f"Log directory does not exist: {log_dir}")
        return "\n".join(sections)

    files = [p for p in log_dir.glob("*.log") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    files = files[:MAX_FILES]

    if not files:
        sections.append(f"No .log files found in {log_dir}")
        return "\n".join(sections)

    for path in files:
        try:
            stat = path.stat()
            meta = f"{path.name} | {stat.st_size} bytes | modified {stat.st_mtime_ns}"
        except Exception:
            meta = path.name
        body = _tail_text(path, lines)
        sections.extend([
            "=" * 78,
            meta,
            "=" * 78,
            body,
            "",
        ])
        current = "\n".join(sections)
        if len(current) >= MAX_OUTPUT_CHARS:
            return current[:MAX_OUTPUT_CHARS] + "\n[log output truncated]"

    return "\n".join(sections)[:MAX_OUTPUT_CHARS]


def create_agent_logs_zip() -> str:
    """Create a ZIP containing the complete current SiteWatcher log files."""
    root = Path(__file__).resolve().parent.parent
    log_dir = root / "logs"
    fd, temp_path = tempfile.mkstemp(prefix="sitewatcher-logs-", suffix=".zip")
    os.close(fd)

    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        info = (
            f"SiteWatcher Agent Diagnostics\n"
            f"Version: {__version__}\n"
            f"Computer: {os.environ.get('COMPUTERNAME') or os.environ.get('HOSTNAME') or 'unknown'}\n"
            f"Install path: {root}\n"
        )
        archive.writestr("SiteWatcher-info.txt", info)

        if log_dir.exists():
            files = [p for p in log_dir.iterdir() if p.is_file() and p.suffix.lower() in {".log", ".out", ".err", ".txt"}]
            files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            for path in files[:MAX_ZIP_FILES]:
                try:
                    archive.write(path, arcname=path.name)
                except OSError:
                    continue

    return temp_path
