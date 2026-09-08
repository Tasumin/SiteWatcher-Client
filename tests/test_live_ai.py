from sitewatch_agent.ai_detector import Detection
from sitewatch_agent.live_ai import LiveAISession, _merge, _safe_error


def _d(label, confidence, x=0.1, y=0.1, width=0.2, height=0.2):
    return Detection(
        class_id=0,
        label=label,
        confidence=confidence,
        x=x,
        y=y,
        width=width,
        height=height,
    )


def test_live_ai_fps_is_bounded():
    device = {"id": "camera-1", "name": "Camera", "checks": []}
    assert LiveAISession(device, 0.1).ai_fps == 0.5
    assert LiveAISession(device, 5).ai_fps == 2.0


def test_live_ai_merge_prefers_stronger_duplicate():
    general = [_d("bear", 0.60)]
    wildlife = [_d("bear", 0.92)]
    merged = _merge(general, wildlife)
    assert len(merged) == 1
    assert merged[0].confidence == 0.92


def test_live_ai_error_redacts_rtsp_credentials():
    text = _safe_error("rtsp://admin:secret@192.168.1.20:554/stream failed")
    assert "secret" not in text
    assert "admin" not in text
    assert "192.168.1.20" in text
