from unittest.mock import patch

from sitewatch_agent.discovery import neighbor_mac, normalize_mac


def test_normalize_mac_accepts_common_formats():
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "AA:BB:CC:DD:EE:FF"


def test_normalize_mac_rejects_invalid_values():
    assert normalize_mac("") is None
    assert normalize_mac("192.168.1.20") is None
    assert normalize_mac("not-a-mac") is None


@patch("sitewatch_agent.discovery.os.name", "posix")
@patch("sitewatch_agent.discovery._run_neighbor_command")
def test_neighbor_mac_linux_prefers_ip_neigh(run_command):
    run_command.side_effect = [
        "192.168.1.20 dev eth0 lladdr 0a:1b:2c:3d:4e:5f REACHABLE",
        "",
    ]
    assert neighbor_mac("192.168.1.20") == "0A:1B:2C:3D:4E:5F"
    assert run_command.call_count == 1


@patch("sitewatch_agent.discovery.os.name", "nt")
@patch("sitewatch_agent.discovery._run_neighbor_command")
def test_neighbor_mac_windows_parses_arp(run_command):
    run_command.return_value = "  192.168.1.20          0a-1b-2c-3d-4e-5f     dynamic"
    assert neighbor_mac("192.168.1.20") == "0A:1B:2C:3D:4E:5F"
