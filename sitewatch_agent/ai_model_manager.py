from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "data" / "ai-models"
MANAGED_MODEL_PATH = MODEL_DIR / "yolox-tiny-coco-0.1.1rc0.onnx"
MODEL_METADATA_PATH = MODEL_DIR / "model.json"

MODEL_ID = "yolox-tiny-coco"
MODEL_VERSION = "0.1.1rc0"
MODEL_FAMILY = "yolox"
MODEL_SOURCE_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"
MODEL_SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
MODEL_SIZE_BYTES = 20_219_662
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024

WILDLIFE_MODEL_PATH = MODEL_DIR / "north-american-wildlife-yolo26s-2026-05-28.onnx"
WILDLIFE_METADATA_PATH = MODEL_DIR / "wildlife-model.json"
WILDLIFE_MODEL_ID = "north-american-wildlife-yolo26s"
WILDLIFE_MODEL_VERSION = "2026-05-28"
WILDLIFE_MODEL_FAMILY = "yolo26"
WILDLIFE_MODEL_SOURCE_URL = "https://huggingface.co/UWyo/wildlife-north-american-wildlife/resolve/main/yolo26s_finetuned_26-wildlife-class_by_J.Gong_uwyo_2026-05-28.onnx?download=true"
WILDLIFE_MODEL_SHA256 = "95016107c641f667dfd344a70abc54c542e01395f9f9b61a600884481fd63a26"
WILDLIFE_MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024

_lock = threading.Lock()
_last_error: str | None = None
_last_attempt_at: str | None = None
_last_downloaded_at: str | None = None
_wildlife_lock = threading.Lock()
_wildlife_last_error: str | None = None
_wildlife_last_attempt_at: str | None = None
_wildlife_last_downloaded_at: str | None = None


def configured_model_path() -> Path:
    value = os.getenv("SITEWATCH_AI_MODEL_PATH", "").strip()
    return Path(value).expanduser() if value else MANAGED_MODEL_PATH


def model_is_managed() -> bool:
    return not bool(os.getenv("SITEWATCH_AI_MODEL_PATH", "").strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_metadata() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": MODEL_ID,
        "version": MODEL_VERSION,
        "family": MODEL_FAMILY,
        "sourceUrl": MODEL_SOURCE_URL,
        "sha256": MODEL_SHA256,
        "installedAt": datetime.now(timezone.utc).isoformat(),
    }
    temp = MODEL_METADATA_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, MODEL_METADATA_PATH)


def _read_metadata() -> dict[str, Any]:
    if not MODEL_METADATA_PATH.is_file():
        return {}
    try:
        value = json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def get_model_family(path: str | os.PathLike[str] | None = None) -> str:
    target = Path(path) if path else configured_model_path()
    metadata = _read_metadata()
    if target == MANAGED_MODEL_PATH and metadata.get("family"):
        return str(metadata["family"])
    name = target.name.lower()
    if "yolox" in name:
        return "yolox"
    if "yolo26" in name or "wildlife" in name:
        return "yolo26"
    return "generic-yolo"


def get_model_provision_status(verify: bool = False) -> dict[str, Any]:
    path = configured_model_path()
    present = path.is_file()
    digest = None
    verified = None
    managed = model_is_managed()
    if present and managed and verify:
        try:
            digest = _sha256(path)
            verified = digest.lower() == MODEL_SHA256.lower()
        except Exception:
            verified = False
    elif present and managed:
        metadata = _read_metadata()
        verified = (
            metadata.get("sha256") == MODEL_SHA256
            and metadata.get("version") == MODEL_VERSION
        )

    return {
        "id": MODEL_ID if managed else "custom",
        "version": MODEL_VERSION if managed else None,
        "family": get_model_family(path),
        "path": str(path),
        "present": present,
        "sizeBytes": path.stat().st_size if present else 0,
        "managed": managed,
        "verified": verified,
        "sha256": digest if digest else (MODEL_SHA256 if managed else None),
        "sourceUrl": MODEL_SOURCE_URL if managed else None,
        "lastAttemptAt": _last_attempt_at,
        "lastDownloadedAt": _last_downloaded_at,
        "error": _last_error,
    }


def ensure_managed_model(force: bool = False) -> dict[str, Any]:
    """Ensure the pinned NodeVyu beta model exists and matches its SHA-256.

    A custom SITEWATCH_AI_MODEL_PATH is never overwritten or downloaded.
    """
    global _last_error, _last_attempt_at, _last_downloaded_at

    if not model_is_managed():
        return get_model_provision_status(verify=False)

    with _lock:
        _last_attempt_at = datetime.now(timezone.utc).isoformat()
        path = MANAGED_MODEL_PATH
        try:
            if path.is_file() and not force:
                if path.stat().st_size == MODEL_SIZE_BYTES and _sha256(path).lower() == MODEL_SHA256.lower():
                    _last_error = None
                    metadata = _read_metadata()
                    if metadata.get("sha256") != MODEL_SHA256 or metadata.get("version") != MODEL_VERSION:
                        _write_metadata()
                    return get_model_provision_status(verify=False)

            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".onnx.download")
            try:
                temp.unlink(missing_ok=True)
            except TypeError:
                if temp.exists():
                    temp.unlink()

            print(f"[ai-model] downloading {MODEL_ID} v{MODEL_VERSION}", flush=True)
            digest = hashlib.sha256()
            total = 0
            with requests.get(
                MODEL_SOURCE_URL,
                stream=True,
                timeout=(15, 180),
                allow_redirects=True,
                headers={"User-Agent": "NodeVyu-Agent/AI-Model"},
            ) as response:
                response.raise_for_status()
                declared = int(response.headers.get("content-length") or 0)
                if declared and declared > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"AI model download is unexpectedly large: {declared} bytes")
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("AI model exceeded maximum allowed download size")
                        digest.update(chunk)
                        handle.write(chunk)

            actual_sha = digest.hexdigest()
            if total != MODEL_SIZE_BYTES:
                raise RuntimeError(f"AI model size mismatch: expected {MODEL_SIZE_BYTES}, got {total}")
            if actual_sha.lower() != MODEL_SHA256.lower():
                raise RuntimeError(f"AI model SHA-256 mismatch: expected {MODEL_SHA256}, got {actual_sha}")

            os.replace(temp, path)
            _write_metadata()
            _last_downloaded_at = datetime.now(timezone.utc).isoformat()
            _last_error = None
            print(f"[ai-model] ready {path} bytes={total} sha256={actual_sha[:12]}…", flush=True)
            return get_model_provision_status(verify=False)
        except Exception as exc:
            _last_error = f"{type(exc).__name__}: {exc}"
            try:
                path.with_suffix(".onnx.download").unlink(missing_ok=True)
            except Exception:
                pass
            print(f"[ai-model] provisioning failed: {_last_error}", flush=True)
            return get_model_provision_status(verify=False)


def wildlife_model_path() -> Path:
    return WILDLIFE_MODEL_PATH


def wildlife_labels_path() -> Path:
    return ROOT / "models" / "ai-detection" / "wildlife-labels.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_wildlife_metadata() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": WILDLIFE_MODEL_ID,
        "version": WILDLIFE_MODEL_VERSION,
        "family": WILDLIFE_MODEL_FAMILY,
        "sourceUrl": WILDLIFE_MODEL_SOURCE_URL,
        "sha256": WILDLIFE_MODEL_SHA256,
        "installedAt": datetime.now(timezone.utc).isoformat(),
    }
    temp = WILDLIFE_METADATA_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, WILDLIFE_METADATA_PATH)


def get_wildlife_model_status(verify: bool = False) -> dict[str, Any]:
    path = WILDLIFE_MODEL_PATH
    present = path.is_file()
    metadata = _read_json(WILDLIFE_METADATA_PATH)
    verified = None
    digest = None
    if present and verify:
        try:
            digest = _sha256(path)
            verified = digest.lower() == WILDLIFE_MODEL_SHA256.lower()
        except Exception:
            verified = False
    elif present:
        verified = (
            metadata.get("sha256") == WILDLIFE_MODEL_SHA256
            and metadata.get("version") == WILDLIFE_MODEL_VERSION
        )
    return {
        "id": WILDLIFE_MODEL_ID,
        "version": WILDLIFE_MODEL_VERSION,
        "family": WILDLIFE_MODEL_FAMILY,
        "path": str(path),
        "present": present,
        "sizeBytes": path.stat().st_size if present else 0,
        "managed": True,
        "verified": verified,
        "sha256": digest or WILDLIFE_MODEL_SHA256,
        "sourceUrl": WILDLIFE_MODEL_SOURCE_URL,
        "labelsPath": str(wildlife_labels_path()),
        "labelsPresent": wildlife_labels_path().is_file(),
        "lastAttemptAt": _wildlife_last_attempt_at,
        "lastDownloadedAt": _wildlife_last_downloaded_at,
        "error": _wildlife_last_error,
    }


def ensure_wildlife_model(force: bool = False) -> dict[str, Any]:
    global _wildlife_last_error, _wildlife_last_attempt_at, _wildlife_last_downloaded_at
    with _wildlife_lock:
        _wildlife_last_attempt_at = datetime.now(timezone.utc).isoformat()
        path = WILDLIFE_MODEL_PATH
        try:
            if path.is_file() and not force:
                if _sha256(path).lower() == WILDLIFE_MODEL_SHA256.lower():
                    _wildlife_last_error = None
                    metadata = _read_json(WILDLIFE_METADATA_PATH)
                    if metadata.get("sha256") != WILDLIFE_MODEL_SHA256 or metadata.get("version") != WILDLIFE_MODEL_VERSION:
                        _write_wildlife_metadata()
                    return get_wildlife_model_status(False)

            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".onnx.download")
            try:
                temp.unlink(missing_ok=True)
            except TypeError:
                if temp.exists():
                    temp.unlink()

            print(f"[ai-model] downloading {WILDLIFE_MODEL_ID} v{WILDLIFE_MODEL_VERSION}", flush=True)
            digest = hashlib.sha256()
            total = 0
            with requests.get(
                WILDLIFE_MODEL_SOURCE_URL,
                stream=True,
                timeout=(15, 240),
                allow_redirects=True,
                headers={"User-Agent": "NodeVyu-Agent/AI-Model"},
            ) as response:
                response.raise_for_status()
                declared = int(response.headers.get("content-length") or 0)
                if declared and declared > WILDLIFE_MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Wildlife model download is unexpectedly large: {declared} bytes")
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > WILDLIFE_MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("Wildlife model exceeded maximum allowed download size")
                        digest.update(chunk)
                        handle.write(chunk)

            actual_sha = digest.hexdigest()
            if actual_sha.lower() != WILDLIFE_MODEL_SHA256.lower():
                raise RuntimeError(f"Wildlife model SHA-256 mismatch: expected {WILDLIFE_MODEL_SHA256}, got {actual_sha}")

            os.replace(temp, path)
            _write_wildlife_metadata()
            _wildlife_last_downloaded_at = datetime.now(timezone.utc).isoformat()
            _wildlife_last_error = None
            print(f"[ai-model] wildlife ready {path} bytes={total} sha256={actual_sha[:12]}…", flush=True)
            return get_wildlife_model_status(False)
        except Exception as exc:
            _wildlife_last_error = f"{type(exc).__name__}: {exc}"
            try:
                path.with_suffix(".onnx.download").unlink(missing_ok=True)
            except Exception:
                pass
            print(f"[ai-model] wildlife provisioning failed: {_wildlife_last_error}", flush=True)
            return get_wildlife_model_status(False)
