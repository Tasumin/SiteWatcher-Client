from __future__ import annotations

import io
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import re

from PIL import Image, ImageDraw, ImageFont

from .ai_detector import DEFAULT_DETECTION_CLASSES, Detection, OnnxObjectDetector
from .viewing_window import enter_viewing_window, leave_viewing_window

IS_WINDOWS = os.name == "nt"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
IDLE_TIMEOUT_SECONDS = 20
MAX_FRAME_BYTES = 2 * 1024 * 1024


def _tool(name: str) -> str:
    exe = name + (".exe" if IS_WINDOWS else "")
    configured = os.getenv("SITEWATCH_FFMPEG_DIR", "").strip()
    if configured:
        candidate = os.path.join(configured, exe)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(exe) or shutil.which(name) or exe


def _rtsp_with_credentials(url: str, username: str | None, password: str | None) -> str:
    if not username:
        return url
    parsed = urlparse(url)
    auth = username if password is None else f"{username}:{password}"
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{auth}@{host}" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _safe_error(value: object) -> str:
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1", str(value))
    return text[-1200:]


def _iou(a: Detection, b: Detection) -> float:
    ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.width, a.y + a.height
    bx1, by1, bx2, by2 = b.x, b.y, b.x + b.width, b.y + b.height
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


def _merge(general: list[Detection], wildlife: list[Detection]) -> list[Detection]:
    merged = list(general)
    for candidate in wildlife:
        duplicate = next(
            (
                index
                for index, existing in enumerate(merged)
                if existing.label == candidate.label and _iou(existing, candidate) >= 0.45
            ),
            None,
        )
        if duplicate is None:
            merged.append(candidate)
        elif candidate.confidence > merged[duplicate].confidence:
            merged[duplicate] = candidate
    merged.sort(key=lambda item: item.confidence, reverse=True)
    return merged


def _annotate(image: Image.Image, detections: list[Detection]) -> bytes:
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    font = ImageFont.load_default()
    width, height = preview.size
    line_width = max(2, width // 500)
    for detection in detections:
        x1 = int(detection.x * width)
        y1 = int(detection.y * height)
        x2 = int((detection.x + detection.width) * width)
        y2 = int((detection.y + detection.height) * height)
        label = f"{detection.label} {detection.confidence * 100:.1f}%"
        draw.rectangle((x1, y1, x2, y2), outline="red", width=line_width)
        box = draw.textbbox((x1, y1), label, font=font)
        tw = max(1, box[2] - box[0])
        th = max(1, box[3] - box[1])
        ly = max(0, y1 - th - 6)
        draw.rectangle((x1, ly, x1 + tw + 8, ly + th + 6), fill="red")
        draw.text((x1 + 4, ly + 3), label, fill="white", font=font)
    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()


class LiveAISession:
    def __init__(self, device: dict, ai_fps: float = 1.0) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.device = device
        self.ai_fps = max(0.5, min(2.0, float(ai_fps)))
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.thread = threading.Thread(target=self._run, name=f"sitewatch-live-ai-{self.id}", daemon=True)
        self.created_at = time.time()
        self.last_touch = self.created_at
        self.last_frame_at: float | None = None
        self.latest_jpeg: bytes | None = None
        self.latest_detections: list[dict] = []
        self.general_provider: str | None = None
        self.wildlife_provider: str | None = None
        self.general_inference_ms = 0.0
        self.wildlife_inference_ms = 0.0
        self.total_inference_ms = 0.0
        self.frames_processed = 0
        self.started_at: float | None = None
        self.error: str | None = None
        self.running = False

    def start(self) -> None:
        self.thread.start()

    def touch(self) -> None:
        with self.lock:
            self.last_touch = time.time()

    def stop(self) -> None:
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def status(self) -> dict:
        self.touch()
        with self.lock:
            elapsed = max(0.001, time.time() - (self.started_at or self.created_at))
            effective_fps = self.frames_processed / elapsed if self.frames_processed else 0.0
            return {
                "sessionId": self.id,
                "cameraId": str(self.device.get("id")),
                "cameraName": str(self.device.get("name") or self.device.get("id")),
                "running": self.running,
                "error": self.error,
                "aiFpsTarget": self.ai_fps,
                "effectiveAiFps": effective_fps,
                "framesProcessed": self.frames_processed,
                "lastFrameAt": self.last_frame_at,
                "generalProvider": self.general_provider,
                "wildlifeEnabled": False,
                "wildlifeProvider": None,
                "generalInferenceMs": self.general_inference_ms,
                "wildlifeInferenceMs": 0.0,
                "totalInferenceMs": self.total_inference_ms,
                "detections": list(self.latest_detections),
                "hasFrame": self.latest_jpeg is not None,
            }

    def frame(self) -> bytes | None:
        self.touch()
        with self.lock:
            return self.latest_jpeg

    def _run(self) -> None:
        device_id = str(self.device.get("id"))
        enter_viewing_window("device", device_id, f"live-ai-{self.id}")
        self.started_at = time.time()
        self.running = True
        try:
            rtsp_check = next(
                (check for check in self.device.get("checks", []) if check.get("type") == "rtsp"),
                None,
            )
            if not rtsp_check:
                raise RuntimeError("Camera does not have an RTSP check.")

            host = self.device.get("host")
            url = rtsp_check.get("url") or f"rtsp://{host}:554/"
            target = _rtsp_with_credentials(url, rtsp_check.get("username"), rtsp_check.get("password"))
            timeout = max(1, int(self.device.get("timeoutSeconds", 8)))

            general = OnnxObjectDetector()
            self.general_provider = general.provider

            # Live RTSP testing intentionally uses the fast general detector only.
            # Wildlife remains available in still-image and one-frame tests, where
            # its much higher CPU latency does not throttle the live pipeline.
            self.wildlife_provider = None

            command = [
                _tool("ffmpeg"),
                "-hide_banner",
                "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-timeout", str(timeout * 1_000_000),
                "-fflags", "+genpts+discardcorrupt",
                "-flags", "low_delay",
                "-i", target,
                "-an",
                "-vf", f"fps={self.ai_fps},scale=960:-2:force_original_aspect_ratio=decrease",
                "-q:v", "5",
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "pipe:1",
            ]
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_FLAGS,
            )
            if self.process.stdout is None:
                raise RuntimeError("FFmpeg did not provide a video pipe.")

            buffer = bytearray()
            while not self.stop_event.is_set():
                if time.time() - self.last_touch > IDLE_TIMEOUT_SECONDS:
                    print(f"[ai-live] session={self.id} idle timeout", flush=True)
                    break
                chunk = self.process.stdout.read(65536)
                if not chunk:
                    if self.process.poll() is not None:
                        stderr = b""
                        if self.process.stderr:
                            stderr = self.process.stderr.read()[-1000:]
                        detail = _safe_error(stderr.decode("utf-8", errors="ignore").strip())
                        raise RuntimeError(detail or f"FFmpeg exited with code {self.process.returncode}")
                    time.sleep(0.02)
                    continue
                buffer.extend(chunk)
                if len(buffer) > MAX_FRAME_BYTES * 2:
                    start = buffer.rfind(b"\xff\xd8")
                    if start > 0:
                        del buffer[:start]
                    elif len(buffer) > MAX_FRAME_BYTES * 2:
                        buffer.clear()
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start > 0:
                            del buffer[:start]
                        break
                    frame_data = bytes(buffer[start:end + 2])
                    del buffer[:end + 2]
                    if len(frame_data) > MAX_FRAME_BYTES:
                        continue
                    self._process_frame(frame_data, general)
        except Exception as exc:
            with self.lock:
                self.error = f"{type(exc).__name__}: {_safe_error(exc)}"
            print(f"[ai-live] session={self.id} error={self.error}", flush=True)
        finally:
            self.running = False
            process = self.process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            leave_viewing_window("device", device_id, f"live-ai-{self.id}")
            print(f"[ai-live] session={self.id} stopped frames={self.frames_processed}", flush=True)

    def _process_frame(
        self,
        frame_data: bytes,
        general: OnnxObjectDetector,
    ) -> None:
        with Image.open(io.BytesIO(frame_data)) as source:
            source.load()
            image = source.convert("RGB")

        detections, general_metrics = general.detect(
            image,
            confidence_threshold=0.55,
            iou_threshold=0.45,
            class_filter=DEFAULT_DETECTION_CLASSES,
        )

        annotated = _annotate(image, detections)
        now = time.time()
        with self.lock:
            self.latest_jpeg = annotated
            self.latest_detections = [item.as_dict() for item in detections]
            self.general_inference_ms = float(general_metrics.get("inferenceMs") or 0)
            self.wildlife_inference_ms = 0.0
            self.total_inference_ms = self.general_inference_ms
            self.last_frame_at = now
            self.frames_processed += 1


_manager_lock = threading.RLock()
_session: LiveAISession | None = None


def start_live_ai(device: dict, ai_fps: float = 1.0) -> dict:
    global _session
    with _manager_lock:
        if _session is not None:
            previous = _session
            previous.stop()
            previous.thread.join(timeout=3)
        _session = LiveAISession(device, ai_fps)
        _session.start()
        return _session.status()


def stop_live_ai() -> dict:
    global _session
    with _manager_lock:
        session = _session
        _session = None
    if session is not None:
        session.stop()
        return {"ok": True, "sessionId": session.id}
    return {"ok": True, "sessionId": None}


def live_ai_status() -> dict:
    with _manager_lock:
        session = _session
    if session is None:
        return {"running": False, "sessionId": None, "hasFrame": False, "detections": []}
    return session.status()


def live_ai_frame() -> bytes | None:
    with _manager_lock:
        session = _session
    if session is None:
        return None
    return session.frame()
