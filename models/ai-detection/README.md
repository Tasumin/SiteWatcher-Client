# NodeVyu AI Detection model directory

The AI Detection beta automatically provisions its managed model after the beta is enabled.

The initial managed model is YOLOX-Tiny COCO 0.1.1rc0 and is stored at:

```text
data/ai-models/yolox-tiny-coco-0.1.1rc0.onnx
```

The file is downloaded from the official YOLOX GitHub release, validated by exact size and SHA-256, and atomically promoted only after verification.

The path can be overridden with:

```text
SITEWATCH_AI_MODEL_PATH=/path/to/model.onnx
SITEWATCH_AI_LABELS_PATH=/path/to/labels.json
```

The current detector supports common YOLO-style ONNX exports that return a
single tensor containing `xywh` boxes and class scores, including the common
Ultralytics `[1,84,8400]` / `[1,8400,84]` layout and YOLOv5-style
`[1,N,85]` output with objectness.

Model weights are intentionally not committed to this repository. This keeps
agent releases small while NodeVyu provisions a pinned, verified model at runtime.
The managed model lives below `data/` so Windows and Linux agent upgrades preserve it.

The bundled `labels.json` contains the standard 80 COCO object labels used by
many compatible general-purpose object detection models.

## Single-frame benchmark

After enabling the AI Detection beta and placing a compatible model in this
directory, test a standalone camera assigned to the local agent:

```bash
python -m sitewatch_agent.ai_test --device-id CAMERA_UUID --runs 10
```

Or benchmark a local image without contacting NodeVyu:

```bash
python -m sitewatch_agent.ai_test --image test.jpg --runs 10
```

By default the benchmark reports the security-relevant classes person, car,
truck, bus, motorcycle and bicycle. Pass an empty `--classes ""` value to
show every class.
