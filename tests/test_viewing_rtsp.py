import unittest
from unittest.mock import patch

from sitewatch_agent.checks import RTSPProbeSkipped, rtsp, run_device
from sitewatch_agent.viewing_window import enter_viewing_window, leave_viewing_window


class ViewingWindowRTSPTests(unittest.TestCase):
    def test_rtsp_probe_is_skipped_for_exact_viewed_device_after_slot_acquisition(self):
        enter_viewing_window("device", "camera-1", "session-1")
        try:
            with patch("sitewatch_agent.checks.subprocess.Popen") as popen:
                with self.assertRaises(RTSPProbeSkipped):
                    rtsp("rtsp://camera/stream", None, None, 2, viewing_source=("device", "camera-1"))
                popen.assert_not_called()
        finally:
            leave_viewing_window("device", "camera-1", "session-1")

    def test_device_result_keeps_non_rtsp_monitoring_healthy_when_rtsp_is_skipped(self):
        device = {
            "id": "camera-1",
            "name": "Front Door",
            "host": "192.0.2.10",
            "timeoutSeconds": 2,
            "checks": [{"type": "rtsp", "url": "rtsp://camera/stream"}],
        }
        enter_viewing_window("device", "camera-1", "session-2")
        try:
            with patch("sitewatch_agent.checks.subprocess.Popen") as popen:
                result = run_device(device)
                popen.assert_not_called()
        finally:
            leave_viewing_window("device", "camera-1", "session-2")
        self.assertTrue(result["overallOk"])
        self.assertTrue(result["checks"][0]["skipped"])
        self.assertEqual(result["checks"][0]["skipReason"], "viewing_window")

    def test_nvr_viewing_key_is_channel_specific(self):
        enter_viewing_window("nvr_stream", "stream-4", "session-3")
        try:
            with patch("sitewatch_agent.checks.subprocess.Popen") as popen:
                with self.assertRaises(RTSPProbeSkipped):
                    rtsp("rtsp://nvr/channel4", None, None, 2, viewing_source=("nvr_stream", "stream-4"))
                popen.assert_not_called()
        finally:
            leave_viewing_window("nvr_stream", "stream-4", "session-3")


if __name__ == "__main__":
    unittest.main()
