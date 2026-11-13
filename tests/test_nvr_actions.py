import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

from sitewatch_agent import nvr_actions


def recorder_response(command, value=None, *, code=0, http=200):
    response = MagicMock(status_code=http)
    response.__enter__.return_value = response
    response.iter_content.return_value = [json.dumps([{
        "cmd": command, "code": code, "value": value or {}
    }]).encode()]
    return response


class NvrActionTests(unittest.TestCase):
    def job(self, action, **overrides):
        return {
            "action": action, "host": "192.0.2.21", "httpsPort": 443, "rtspPort": 554,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            "username": "rtsp-user", "password": "secret123", **overrides,
        }

    def login(self):
        return recorder_response("Login", {"Token": {"name": "login-token"}})

    def nvr(self):
        return recorder_response("GetDevInfo", {"DevInfo": {"type": "NVR", "model": "RLN36"}})

    def execute(self, job, *responses):
        with patch.object(nvr_actions.requests, "Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = responses
            result = nvr_actions.run_nvr_action(job)
        return result, session.post.call_args_list

    def test_reboot_reuses_stream_login_and_requires_acknowledgment(self):
        result, calls = self.execute(self.job("reboot"), self.login(), self.nvr(),
                                     recorder_response("Reboot", {"rspCode": 200}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call.kwargs["params"]["cmd"] for call in calls], ["Login", "GetDevInfo", "Reboot"])
        self.assertEqual(calls[0].kwargs["json"][0]["param"]["User"],
                         {"userName": "rtsp-user", "password": "secret123"})
        self.assertEqual(calls[2].kwargs["params"]["token"], "login-token")
        self.assertTrue(all(call.args[0].startswith("https://192.0.2.21:443/") for call in calls))
        self.assertTrue(all(call.kwargs["verify"] and not call.kwargs["allow_redirects"] for call in calls))

    def test_reboot_disconnect_is_unknown_and_is_not_retried(self):
        result, calls = self.execute(self.job("reboot"), self.login(), self.nvr(),
                                     requests.ConnectionError("login-token/secret123"), recorder_response("Logout"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual([call.kwargs["params"]["cmd"] for call in calls].count("Reboot"), 1)
        self.assertNotIn("secret123", json.dumps(result))

    def test_reboot_rejects_a_camera(self):
        result, calls = self.execute(self.job("reboot"), self.login(),
                                     recorder_response("GetDevInfo", {"DevInfo": {"type": "IPC"}}),
                                     recorder_response("Logout"))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("Reboot", [call.kwargs["params"]["cmd"] for call in calls])

    def test_login_failure_does_not_try_more_commands(self):
        result, calls = self.execute(self.job("reboot"), recorder_response("Login", code=1))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(calls), 1)

    def test_older_https_endpoint_and_redacted_channel_output(self):
        result, calls = self.execute(self.job("channels", httpsPort=8443),
                                     recorder_response("Login", http=404), self.login(),
                                     recorder_response("GetChannelstatus", {"status": [
                                         {"channel": 0, "online": 1, "name": "secret123", "token": "login-token"}
                                     ]}), recorder_response("Logout"))
        self.assertEqual(result["result"]["channels"], [{"channel": 0, "online": 1, "name": "[redacted]"}])
        self.assertEqual(calls[1].args[0], "https://192.0.2.21:8443/api.cgi")

    def test_storage_status_is_read_only(self):
        result, calls = self.execute(self.job("storage"), self.login(),
                                     recorder_response("GetHddInfo", {"HddInfo": [
                                         {"id": 0, "mount": 1, "size": 500, "capacity": 1000}
                                     ]}), recorder_response("Logout"))
        self.assertEqual(result["result"]["disks"], [{"id": 0, "mount": 1, "remainingMB": 500, "capacityMB": 1000}])
        self.assertEqual([call.kwargs["params"]["cmd"] for call in calls], ["Login", "GetHddInfo", "Logout"])

    def test_expired_unknown_and_malformed_targets_do_not_connect(self):
        for job in (self.job("reboot", expiresAt="2000-01-01T00:00:00Z"),
                    self.job("shell"), self.job("device-info", host="192.0.2.21@other.example")):
            result, calls = self.execute(job)
            self.assertEqual(result["status"], "failed")
            self.assertFalse(calls)

    @patch("sitewatch_agent.nvr_actions._port_check")
    def test_port_check_uses_configured_ports_and_no_login(self, check):
        check.side_effect = lambda host, port: {"port": port, "reachable": port == 8554}
        result, calls = self.execute(self.job("ports", httpsPort=8443, rtspPort=8554))
        self.assertEqual([port["port"] for port in result["result"]["ports"]], [80, 8443, 8554, 9000])
        self.assertFalse(calls)

    @patch("sitewatch_agent.checks.rtsp")
    def test_rtsp_keeps_saved_login_and_drops_raw_errors(self, probe):
        probe.return_value = (False, 100, "401 rtsp://test:secret123@192.0.2.21:554/stream")
        result, calls = self.execute(self.job("rtsp", rtspUrl="rtsp://192.0.2.21:554/stream", channel=1))
        self.assertEqual(result["status"], "failed")
        self.assertIn("authentication", result["message"])
        self.assertNotIn("secret123", json.dumps(result))
        self.assertEqual(probe.call_args.args[1:3], ("rtsp-user", "secret123"))
        self.assertFalse(calls)

    @patch("sitewatch_agent.checks.rtsp")
    def test_rtsp_does_not_send_credentials_to_another_host(self, probe):
        result, _ = self.execute(self.job("rtsp", rtspUrl="rtsp://other.example:554/stream"))
        self.assertEqual(result["status"], "failed")
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
