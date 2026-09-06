from sitewatch_agent.beta_features import plugin_capabilities, resolve_ai_detection_beta


def test_ai_detection_beta_defaults_off():
    enabled, source = resolve_ai_detection_beta(config={}, environ={})
    assert enabled is False
    assert source == "default"


def test_ai_detection_beta_can_be_enabled_locally():
    enabled, source = resolve_ai_detection_beta(
        config={},
        environ={"SITEWATCH_BETA_AI_DETECTION": "true"},
    )
    assert enabled is True
    assert source == "agent"


def test_server_admin_setting_overrides_local_setting():
    enabled, source = resolve_ai_detection_beta(
        config={"betaFeatures": {"aiDetection": {"enabled": False}}},
        environ={"SITEWATCH_BETA_AI_DETECTION": "true"},
    )
    assert enabled is False
    assert source == "server"


def test_plugin_shape_advertises_beta_capability():
    payload = plugin_capabilities(
        config={"plugins": {"ai-detection": {"enabled": True}}},
        environ={},
    )
    plugin = payload["plugins"][0]
    assert plugin["id"] == "ai-detection"
    assert plugin["stage"] == "beta"
    assert plugin["available"] is True
    assert plugin["enabled"] is True
    assert plugin["enabledSource"] == "server"
