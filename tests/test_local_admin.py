import os
from pathlib import Path

from sitewatch_agent import local_admin


def test_local_admin_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("SITEWATCH_LOCAL_ADMIN_LAN_ACCESS", raising=False)
    assert local_admin.local_admin_bind() == "127.0.0.1"


def test_local_admin_lan_toggle(monkeypatch):
    monkeypatch.setenv("SITEWATCH_LOCAL_ADMIN_LAN_ACCESS", "true")
    assert local_admin.local_admin_bind() == "0.0.0.0"


def test_safe_config_never_exposes_agent_token(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SITEWATCH_SERVER_URL=https://nodevyu.com\n"
        "SITEWATCH_AGENT_TOKEN=super-secret\n"
        "SITEWATCH_DISCOVERY_CIDRS=192.168.1.0/24\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local_admin, "ENV_FILE", env_file)
    values = local_admin._safe_config_values()
    assert "SITEWATCH_AGENT_TOKEN" not in values
    assert values["SITEWATCH_DISCOVERY_CIDRS"] == "192.168.1.0/24"


def test_safe_config_update_preserves_secrets(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SITEWATCH_SERVER_URL=https://nodevyu.com\n"
        "SITEWATCH_AGENT_TOKEN=super-secret\n"
        "SITEWATCH_DISCOVERY_CIDRS=192.168.1.0/24\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local_admin, "ENV_FILE", env_file)
    local_admin._write_safe_config({
        "SITEWATCH_DISCOVERY_CIDRS": "10.10.10.0/24",
        "SITEWATCH_LOCAL_ADMIN_PORT": "8766",
        "SITEWATCH_AGENT_TOKEN": "replacement-attempt",
    })
    text = env_file.read_text(encoding="utf-8")
    assert "SITEWATCH_AGENT_TOKEN=super-secret" in text
    assert "replacement-attempt" not in text
    assert "SITEWATCH_DISCOVERY_CIDRS=10.10.10.0/24" in text
    assert "SITEWATCH_LOCAL_ADMIN_PORT=8766" in text
