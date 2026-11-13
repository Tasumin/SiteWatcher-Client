import unittest
from unittest.mock import MagicMock, patch

from sitewatch_agent.nvr_diagnostics import _private_host, run_nvr_action


class NvrDiagnosticsTests(unittest.TestCase):
    def test_rejects_public_and_loopback_hosts(self):
        for host in ("8.8.8.8", "127.0.0.1", "169.254.1.1", "example.com"):
            with self.assertRaises(ValueError):
                _private_host(host)

    @patch("sitewatch_agent.nvr_diagnostics._HTTP")
    def test_rejects_nvr_not_assigned_to_agent(self, http):
        http.get.return_value.json.return_value = {"devices": []}
        with self.assertRaisesRegex(ValueError, "not found"):
            run_nvr_action("https://example.invalid", {}, "nvr-1", "reboot")
        http.post.assert_not_called()

    @patch("sitewatch_agent.nvr_diagnostics._HTTP")
    def test_reboot_requires_credentials(self, http):
        http.get.return_value.json.return_value = {
            "devices": [{"id": "nvr-1", "type": "nvr", "host": "192.168.1.150", "nvrStreams": []}]
        }
        with self.assertRaisesRegex(ValueError, "credentials"):
            run_nvr_action("https://example.invalid", {}, "nvr-1", "reboot")
        http.post.assert_not_called()

    @patch("sitewatch_agent.nvr_diagnostics._HTTP")
    def test_reboot_calls_authenticated_reolink_api(self, http):
        http.get.return_value.json.return_value = {"devices": [{
            "id": "nvr-1", "type": "nvr", "host": "192.168.1.150",
            "nvrStreams": [{"username": "monitor", "password": "secret"}]
        }]}
        login = MagicMock()
        login.json.return_value = [{"code": 0, "value": {"Token": {"name": "testtoken"}}}]
        reboot = MagicMock()
        reboot.json.return_value = [{"code": 0, "value": {"rspCode": 200}}]
        http.post.side_effect = [login, reboot]
        result = run_nvr_action("https://example.invalid", {}, "nvr-1", "reboot")
        self.assertTrue(result["rebootAccepted"])
        self.assertEqual(http.post.call_count, 2)
        self.assertEqual(http.post.call_args.kwargs["params"]["cmd"], "Reboot")
        self.assertNotIn("secret", str(result))
        self.assertNotIn("testtoken", str(result))


if __name__ == "__main__":
    unittest.main()
