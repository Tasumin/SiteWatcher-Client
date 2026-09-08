from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import time
from pathlib import Path

import requests
from PIL import Image

from .ai_detector import DEFAULT_DETECTION_CLASSES, OnnxObjectDetector, configured_model_path
from .beta_features import resolve_ai_detection_beta
from .checks import capture_snapshot
from .service_entry import load_env


def _load_agent_environment() -> Path:
    root = Path(__file__).resolve().parent.parent
    load_env(root)
    return root


def _api_config() -> dict:
    server = os.environ["SITEWATCH_SERVER_URL"].rstrip("/")
    token = os.environ["SITEWATCH_AGENT_TOKEN"]
    response = requests.get(
        server + "/api/agent/config",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _camera_from_config(config: dict, device_id: str) -> dict:
    for device in config.get("devices", []):
        if str(device.get("id")) == device_id:
            if device.get("type") != "camera":
                raise ValueError(f"Device {device_id} is not a standalone camera")
            if not any(check.get("type") == "rtsp" for check in device.get("checks", [])):
                raise ValueError(f"Device {device_id} does not have an RTSP check")
            return device
    raise ValueError(f"Camera {device_id} was not found in the agent configuration")


def _capture_camera(device: dict) -> Image.Image:
    snapshot = capture_snapshot(device)
    if not snapshot:
        raise RuntimeError("Camera did not return a snapshot")
    with Image.open(io.BytesIO(snapshot["jpeg"])) as image:
        image.load()
        return image.convert("RGB")


def _print_detections(detections) -> None:
    if not detections:
        print("Detections: none")
        return
    print(f"Detections: {len(detections)}")
    for detection in detections:
        box = detection.as_dict()["box"]
        print(
            f"  {detection.label:<18} {detection.confidence * 100:6.2f}% "
            f"x={box['x']:.3f} y={box['y']:.3f} w={box['width']:.3f} h={box['height']:.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="NodeVyu AI Detection beta single-frame benchmark")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--device-id", help="Standalone NodeVyu camera id from this agent's configuration")
    source.add_argument("--image", help="Local JPEG/PNG image path")
    parser.add_argument("--model", help="ONNX detector path; defaults to SITEWATCH_AI_MODEL_PATH/models/ai-detection/model.onnx")
    parser.add_argument("--provider", help="Override ONNX Runtime execution provider")
    parser.add_argument("--confidence", type=float, default=0.55)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--classes", default=",".join(DEFAULT_DETECTION_CLASSES), help="Comma-separated labels; blank means all")
    parser.add_argument("--json", action="store_true", help="Print final result as JSON")
    args = parser.parse_args()

    config = {}
    if args.device_id:
        _load_agent_environment()
        config = _api_config()
        enabled, source_name = resolve_ai_detection_beta(config)
        if not enabled:
            raise SystemExit(
                f"AI Detection beta is disabled (source={source_name}). Enable it locally or from the NodeVyu admin UI first."
            )
        device = _camera_from_config(config, args.device_id)
        print(f"Camera: {device.get('name')} ({device.get('id')})")
        image = _capture_camera(device)
    else:
        image_path = Path(args.image).expanduser()
        with Image.open(image_path) as source_image:
            source_image.load()
            image = source_image.convert("RGB")

    model = Path(args.model).expanduser() if args.model else configured_model_path()
    detector = OnnxObjectDetector(model_path=model, provider=args.provider)
    classes = [value.strip() for value in args.classes.split(",") if value.strip()]
    runs = max(1, min(100, args.runs))

    print(f"Model: {model}")
    print(f"Provider: {detector.provider}")
    print(f"Input: {detector.input_width}x{detector.input_height}")
    print(f"Source frame: {image.width}x{image.height}")
    print(f"Runs: {runs}")

    all_metrics = []
    detections = []
    for index in range(runs):
        detections, metrics = detector.detect(
            image,
            confidence_threshold=max(0.01, min(0.99, args.confidence)),
            iou_threshold=max(0.01, min(0.99, args.iou)),
            class_filter=classes,
        )
        all_metrics.append(metrics)
        print(
            f"Run {index + 1}: total={metrics['totalMs']:.1f}ms "
            f"inference={metrics['inferenceMs']:.1f}ms "
            f"pre={metrics['preprocessMs']:.1f}ms post={metrics['postprocessMs']:.1f}ms"
        )

    inference = [row["inferenceMs"] for row in all_metrics]
    total = [row["totalMs"] for row in all_metrics]
    summary = {
        "provider": detector.provider,
        "model": str(model),
        "sourceWidth": image.width,
        "sourceHeight": image.height,
        "runs": runs,
        "averageInferenceMs": statistics.fmean(inference),
        "medianInferenceMs": statistics.median(inference),
        "averageTotalMs": statistics.fmean(total),
        "estimatedInferenceFps": 1000.0 / statistics.fmean(inference) if statistics.fmean(inference) > 0 else 0,
        "detections": [item.as_dict() for item in detections],
    }

    _print_detections(detections)
    print(
        f"Average inference: {summary['averageInferenceMs']:.1f}ms "
        f"(~{summary['estimatedInferenceFps']:.2f} inference FPS)"
    )
    print(f"Average total: {summary['averageTotalMs']:.1f}ms")
    if args.json:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
