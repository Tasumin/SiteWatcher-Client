from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

from .ai_runtime import get_ai_runtime_status
from .ai_model_manager import configured_model_path, get_model_family, get_model_provision_status

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS_PATH = ROOT / "models" / "ai-detection" / "labels.json"

DEFAULT_DETECTION_CLASSES = (
    "person", "car", "truck", "bus", "motorcycle", "bicycle",
    "bird", "cat", "dog", "horse", "sheep", "cow", "bear", "zebra", "giraffe", "elephant",
)

WILDLIFE_SOURCE_LABELS = (
    "Mule Deer",
    "Coyote",
    "Grizzly Bear",
    "Swift Fox",
    "American Black Bear",
    "Black-tailed Jackrabbit",
)

WILDLIFE_CANONICAL_LABELS = {
    "mule deer": "deer",
    "coyote": "coyote",
    "grizzly bear": "bear",
    "swift fox": "fox",
    "american black bear": "bear",
    "black-tailed jackrabbit": "rabbit",
}


@dataclass(frozen=True)
class Detection:
    class_id: int
    label: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "classId": self.class_id,
            "label": self.label,
            "confidence": round(self.confidence, 6),
            "box": {
                "x": round(self.x, 6),
                "y": round(self.y, 6),
                "width": round(self.width, 6),
                "height": round(self.height, 6),
            },
        }


def canonical_wildlife_label(label: str) -> str:
    return WILDLIFE_CANONICAL_LABELS.get(str(label).strip().lower(), str(label).strip().lower())


def configured_labels_path() -> Path:
    value = os.getenv("SITEWATCH_AI_LABELS_PATH", "").strip()
    return Path(value).expanduser() if value else DEFAULT_LABELS_PATH


def _load_labels(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if isinstance(raw, dict):
        pairs = sorted(((int(key), str(value)) for key, value in raw.items()), key=lambda row: row[0])
        if not pairs:
            return []
        labels = [f"class-{index}" for index in range(pairs[-1][0] + 1)]
        for index, value in pairs:
            labels[index] = value
        return labels
    raise ValueError("labels.json must be a JSON array or object keyed by class id")


def get_ai_model_status() -> dict[str, Any]:
    status = get_model_provision_status(verify=False)
    labels = configured_labels_path()
    return {
        **status,
        "labelsPath": str(labels),
        "labelsPresent": labels.is_file(),
    }


def _prepare_image(image: Image.Image, width: int, height: int, family: str) -> tuple[np.ndarray, float, int, int]:
    source = image.convert("RGB")
    src_w, src_h = source.size
    scale = min(width / src_w, height / src_h)
    resized_w = max(1, int(src_w * scale))
    resized_h = max(1, int(src_h * scale))
    resized = source.resize((resized_w, resized_h), Image.Resampling.BILINEAR)

    if family == "yolox":
        canvas = Image.new("RGB", (width, height), (114, 114, 114))
        canvas.paste(resized, (0, 0))
        array = np.asarray(canvas, dtype=np.float32)[..., ::-1]
        tensor = np.transpose(array, (2, 0, 1))[None, ...]
        return np.ascontiguousarray(tensor), scale, 0, 0

    pad_x = (width - resized_w) // 2
    pad_y = (height - resized_h) // 2
    canvas = Image.new("RGB", (width, height), (114, 114, 114))
    canvas.paste(resized, (pad_x, pad_y))
    array = np.asarray(canvas, dtype=np.float32) / 255.0
    tensor = np.transpose(array, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(tensor), scale, pad_x, pad_y


def _letterbox(image: Image.Image, width: int, height: int) -> tuple[np.ndarray, float, int, int]:
    return _prepare_image(image, width, height, "generic-yolo")


def _iou_xyxy(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    area_a = np.maximum(0.0, box[2] - box[0]) * np.maximum(0.0, box[3] - box[1])
    area_b = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0.0, boxes[:, 3] - boxes[:, 1])
    union = area_a + area_b - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    if not len(boxes):
        return []
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        current = int(order[0])
        keep.append(current)
        if order.size == 1:
            break
        rest = order[1:]
        order = rest[_iou_xyxy(boxes[current], boxes[rest]) <= threshold]
    return keep


def _normalize_output(output: np.ndarray) -> np.ndarray:
    rows = np.asarray(output)
    while rows.ndim > 2 and rows.shape[0] == 1:
        rows = rows[0]
    if rows.ndim != 2:
        raise ValueError(f"Unsupported detector output shape: {tuple(np.asarray(output).shape)}")
    # Common Ultralytics export: [features, candidates].
    if rows.shape[0] < rows.shape[1] and rows.shape[0] <= 256:
        rows = rows.T
    return rows.astype(np.float32, copy=False)


def _decode_yolox_rows(rows: np.ndarray, input_width: int, input_height: int) -> np.ndarray:
    strides = (8, 16, 32)
    grids = []
    expanded = []
    for stride in strides:
        hsize = input_height // stride
        wsize = input_width // stride
        xv, yv = np.meshgrid(np.arange(wsize), np.arange(hsize))
        grid = np.stack((xv, yv), axis=2).reshape(-1, 2)
        grids.append(grid)
        expanded.append(np.full((grid.shape[0], 1), stride, dtype=np.float32))
    grid = np.concatenate(grids, axis=0).astype(np.float32)
    expanded_strides = np.concatenate(expanded, axis=0)
    if rows.shape[0] != grid.shape[0]:
        raise ValueError(f"YOLOX output candidate count mismatch: got {rows.shape[0]}, expected {grid.shape[0]}")
    decoded = rows.copy()
    decoded[:, :2] = (decoded[:, :2] + grid) * expanded_strides
    decoded[:, 2:4] = np.exp(decoded[:, 2:4]) * expanded_strides
    return decoded


def _decode_rows(rows: np.ndarray, confidence_threshold: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if rows.shape[1] < 6:
        raise ValueError(f"Detector output has too few columns: {rows.shape[1]}")

    # YOLOv5/v7 exports commonly use xywh + objectness + class scores.
    # Ultralytics YOLOv8/11 exports commonly use xywh + class scores.
    use_objectness = rows.shape[1] == 85
    if use_objectness:
        objectness = rows[:, 4]
        class_scores = rows[:, 5:]
        class_ids = class_scores.argmax(axis=1)
        scores = objectness * class_scores[np.arange(len(rows)), class_ids]
    else:
        class_scores = rows[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(rows)), class_ids]

    mask = np.isfinite(scores) & (scores >= confidence_threshold)
    rows = rows[mask]
    scores = scores[mask]
    class_ids = class_ids[mask].astype(np.int32)
    if not len(rows):
        return np.empty((0, 4), np.float32), scores, class_ids

    cx, cy, w, h = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    boxes = np.stack((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), axis=1)
    return boxes, scores, class_ids


class OnnxObjectDetector:
    def __init__(
        self,
        model_path: str | os.PathLike[str] | None = None,
        labels_path: str | os.PathLike[str] | None = None,
        provider: str | None = None,
    ) -> None:
        import onnxruntime as ort

        self.model_path = Path(model_path) if model_path else configured_model_path()
        self.labels_path = Path(labels_path) if labels_path else configured_labels_path()
        if not self.model_path.is_file():
            raise FileNotFoundError(f"AI model not found: {self.model_path}")

        runtime = get_ai_runtime_status()
        selected = provider or runtime.get("preferredProvider") or "CPUExecutionProvider"
        available = [str(value) for value in ort.get_available_providers()]
        if selected not in available:
            raise RuntimeError(f"ONNX provider {selected} is unavailable; available={available}")

        self.provider = selected
        self.session = ort.InferenceSession(str(self.model_path), providers=[selected])
        inputs = self.session.get_inputs()
        if len(inputs) != 1:
            raise ValueError(f"Expected one image input, found {len(inputs)}")
        self.input = inputs[0]
        shape = list(self.input.shape)
        self.input_height = int(shape[2]) if len(shape) >= 4 and isinstance(shape[2], int) else 640
        self.input_width = int(shape[3]) if len(shape) >= 4 and isinstance(shape[3], int) else 640
        self.labels = _load_labels(self.labels_path)
        self.family = get_model_family(self.model_path)

    def detect(
        self,
        image: Image.Image,
        confidence_threshold: float = 0.55,
        iou_threshold: float = 0.45,
        class_filter: Iterable[str] | None = None,
    ) -> tuple[list[Detection], dict[str, float]]:
        start = time.perf_counter()
        tensor, scale, pad_x, pad_y = _prepare_image(image, self.input_width, self.input_height, self.family)
        prepared = time.perf_counter()

        output = self.session.run(None, {self.input.name: tensor})[0]
        inferred = time.perf_counter()

        rows = _normalize_output(output)
        if self.family == "yolox":
            rows = _decode_yolox_rows(rows, self.input_width, self.input_height)

        end_to_end = bool(self.family == "yolo26" and rows.shape[1] == 6)
        if end_to_end:
            scores = rows[:, 4].astype(np.float32)
            class_ids = rows[:, 5].astype(np.int32)
            valid = np.isfinite(scores) & (scores >= confidence_threshold)
            boxes = rows[valid, :4].astype(np.float32)
            scores = scores[valid]
            class_ids = class_ids[valid]
        else:
            boxes, scores, class_ids = _decode_rows(rows, confidence_threshold)
        allowed = {str(value).strip().lower() for value in class_filter or [] if str(value).strip()}
        src_w, src_h = image.size

        detections: list[Detection] = []
        for class_id in np.unique(class_ids):
            indexes = np.flatnonzero(class_ids == class_id)
            kept = list(range(len(indexes))) if end_to_end else _nms(boxes[indexes], scores[indexes], iou_threshold)
            for relative in kept:
                index = int(indexes[relative])
                x1, y1, x2, y2 = boxes[index]
                x1 = max(0.0, min(float(src_w), (float(x1) - pad_x) / scale))
                y1 = max(0.0, min(float(src_h), (float(y1) - pad_y) / scale))
                x2 = max(0.0, min(float(src_w), (float(x2) - pad_x) / scale))
                y2 = max(0.0, min(float(src_h), (float(y2) - pad_y) / scale))
                if x2 <= x1 or y2 <= y1:
                    continue
                label = self.labels[int(class_id)] if int(class_id) < len(self.labels) else f"class-{int(class_id)}"
                if allowed and label.lower() not in allowed:
                    continue
                detections.append(Detection(
                    class_id=int(class_id),
                    label=label,
                    confidence=float(scores[index]),
                    x=x1 / src_w,
                    y=y1 / src_h,
                    width=(x2 - x1) / src_w,
                    height=(y2 - y1) / src_h,
                ))

        detections.sort(key=lambda item: item.confidence, reverse=True)
        finished = time.perf_counter()
        metrics = {
            "preprocessMs": (prepared - start) * 1000,
            "inferenceMs": (inferred - prepared) * 1000,
            "postprocessMs": (finished - inferred) * 1000,
            "totalMs": (finished - start) * 1000,
        }
        return detections, metrics
