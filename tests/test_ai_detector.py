import numpy as np
from PIL import Image

from sitewatch_agent.ai_detector import _decode_rows, _letterbox, _nms, _normalize_output


def test_normalize_ultralytics_feature_first_output():
    raw = np.zeros((1, 84, 8400), dtype=np.float32)
    rows = _normalize_output(raw)
    assert rows.shape == (8400, 84)


def test_decode_yolov8_style_rows():
    rows = np.zeros((2, 84), dtype=np.float32)
    rows[0, :4] = [320, 320, 100, 200]
    rows[0, 4] = 0.90  # person
    rows[1, :4] = [100, 100, 40, 40]
    rows[1, 6] = 0.80  # car, class id 2

    boxes, scores, classes = _decode_rows(rows, 0.50)
    assert boxes.shape == (2, 4)
    assert scores.tolist() == pytest.approx([0.90, 0.80])
    assert classes.tolist() == [0, 2]


def test_nms_suppresses_overlapping_boxes():
    boxes = np.array([
        [0, 0, 100, 100],
        [5, 5, 98, 98],
        [200, 200, 250, 250],
    ], dtype=np.float32)
    scores = np.array([0.95, 0.90, 0.80], dtype=np.float32)
    assert _nms(boxes, scores, 0.45) == [0, 2]


def test_letterbox_preserves_aspect_ratio():
    image = Image.new("RGB", (1280, 720))
    tensor, scale, pad_x, pad_y = _letterbox(image, 640, 640)
    assert tensor.shape == (1, 3, 640, 640)
    assert scale == 0.5
    assert pad_x == 0
    assert pad_y == 140


import pytest
