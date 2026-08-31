import tempfile
import unittest
from pathlib import Path

from sitewatch_agent.log_router import RoutedLogStream


class LogRouterTests(unittest.TestCase):
    def test_routes_and_timestamps_new_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = RoutedLogStream(Path(tmp))
            router.write("[live] connected\n")
            router.write("plain message\n")
            router.close()
            live = (Path(tmp) / "tunnel-live.log").read_text(encoding="utf-8")
            agent = (Path(tmp) / "agent.log").read_text(encoding="utf-8")
            self.assertRegex(live, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}\] \[live\] connected")
            self.assertIn("plain message", agent)

    def test_preserves_existing_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = RoutedLogStream(Path(tmp))
            line = "[2026-08-31 11:00:00.000 -04:00] existing"
            router.write(line + "\n")
            router.close()
            agent = (Path(tmp) / "agent.log").read_text(encoding="utf-8").strip()
            self.assertEqual(agent, line)


if __name__ == "__main__":
    unittest.main()
