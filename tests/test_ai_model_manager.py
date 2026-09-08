from pathlib import Path

from sitewatch_agent import ai_model_manager


def test_custom_model_path_is_not_managed(monkeypatch, tmp_path):
    custom = tmp_path / "custom.onnx"
    monkeypatch.setenv("SITEWATCH_AI_MODEL_PATH", str(custom))
    assert ai_model_manager.configured_model_path() == custom
    assert ai_model_manager.model_is_managed() is False


def test_managed_model_defaults_to_preserved_data_directory(monkeypatch):
    monkeypatch.delenv("SITEWATCH_AI_MODEL_PATH", raising=False)
    path = ai_model_manager.configured_model_path()
    assert "data" in path.parts
    assert "ai-models" in path.parts
    assert path.name.endswith(".onnx")


def test_model_family_detects_yolox_name(monkeypatch, tmp_path):
    path = tmp_path / "yolox_tiny.onnx"
    monkeypatch.setenv("SITEWATCH_AI_MODEL_PATH", str(path))
    assert ai_model_manager.get_model_family(path) == "yolox"
