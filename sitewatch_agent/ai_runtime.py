from __future__ import annotations

import platform
from functools import lru_cache
from typing import Any, Dict, List

PREFERRED_PROVIDER_ORDER = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "DmlExecutionProvider",
    "OpenVINOExecutionProvider",
    "CPUExecutionProvider",
)


def _preferred_provider(providers: List[str]) -> str | None:
    for provider in PREFERRED_PROVIDER_ORDER:
        if provider in providers:
            return provider
    return providers[0] if providers else None


@lru_cache(maxsize=1)
def get_ai_runtime_status() -> Dict[str, Any]:
    """Return the locally available ONNX Runtime inference capability.

    This is intentionally capability-only. Loading a model and processing RTSP
    frames are separate stages so enabling the beta does not silently start
    inference until a model/configuration is present.
    """
    result: Dict[str, Any] = {
        "engine": "onnxruntime",
        "installed": False,
        "version": None,
        "providers": [],
        "preferredProvider": None,
        "ready": False,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
        },
        "error": None,
    }

    try:
        import onnxruntime as ort
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    try:
        providers = [str(value) for value in ort.get_available_providers()]
        result.update({
            "installed": True,
            "version": str(getattr(ort, "__version__", "unknown")),
            "providers": providers,
            "preferredProvider": _preferred_provider(providers),
            "ready": "CPUExecutionProvider" in providers or bool(providers),
        })
    except Exception as exc:
        result["installed"] = True
        result["version"] = str(getattr(ort, "__version__", "unknown"))
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def clear_ai_runtime_cache() -> None:
    get_ai_runtime_status.cache_clear()
