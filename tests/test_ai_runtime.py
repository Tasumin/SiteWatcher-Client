import sys
import types

from sitewatch_agent import ai_runtime


def test_preferred_provider_prioritizes_acceleration():
    assert ai_runtime._preferred_provider([
        "CPUExecutionProvider",
        "CUDAExecutionProvider",
    ]) == "CUDAExecutionProvider"


def test_runtime_status_reports_installed_provider(monkeypatch):
    fake = types.SimpleNamespace(
        __version__="9.9.9-test",
        get_available_providers=lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    ai_runtime.clear_ai_runtime_cache()
    status = ai_runtime.get_ai_runtime_status()
    assert status["installed"] is True
    assert status["version"] == "9.9.9-test"
    assert status["providers"] == ["CPUExecutionProvider"]
    assert status["preferredProvider"] == "CPUExecutionProvider"
    assert status["ready"] is True
    ai_runtime.clear_ai_runtime_cache()
