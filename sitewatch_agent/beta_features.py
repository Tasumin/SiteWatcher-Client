import os
from typing import Any, Dict, Mapping, Optional, Tuple

AI_DETECTION_PLUGIN_ID = "ai-detection"
AI_DETECTION_PLUGIN_VERSION = "0.1.0-beta.1"
AI_DETECTION_ENV = "SITEWATCH_BETA_AI_DETECTION"


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _server_ai_detection_setting(config: Mapping[str, Any]) -> Optional[bool]:
    beta_features = config.get("betaFeatures")
    if isinstance(beta_features, Mapping):
        ai_detection = beta_features.get("aiDetection")
        if isinstance(ai_detection, Mapping):
            parsed = _parse_bool(ai_detection.get("enabled"))
            if parsed is not None:
                return parsed
        else:
            parsed = _parse_bool(ai_detection)
            if parsed is not None:
                return parsed

    plugins = config.get("plugins")
    if isinstance(plugins, Mapping):
        ai_detection = plugins.get("aiDetection") or plugins.get(AI_DETECTION_PLUGIN_ID)
        if isinstance(ai_detection, Mapping):
            parsed = _parse_bool(ai_detection.get("enabled"))
            if parsed is not None:
                return parsed
        else:
            parsed = _parse_bool(ai_detection)
            if parsed is not None:
                return parsed

    return None


def resolve_ai_detection_beta(
    config: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Tuple[bool, str]:
    """Resolve AI Detection beta opt-in.

    An explicit server/admin value wins so an administrator can remotely enable
    or disable the beta. If the server does not provide a value, the local
    agent environment setting is used. The safe default is disabled.
    """
    server_value = _server_ai_detection_setting(config or {})
    if server_value is not None:
        return server_value, "server"

    env = os.environ if environ is None else environ
    local_value = _parse_bool(env.get(AI_DETECTION_ENV))
    if local_value is not None:
        return local_value, "agent"

    return False, "default"


def plugin_capabilities(
    config: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    enabled, source = resolve_ai_detection_beta(config=config, environ=environ)
    return {
        "plugins": [
            {
                "id": AI_DETECTION_PLUGIN_ID,
                "name": "AI Detection",
                "version": AI_DETECTION_PLUGIN_VERSION,
                "stage": "beta",
                "available": True,
                "enabled": enabled,
                "enabledSource": source,
            }
        ]
    }
