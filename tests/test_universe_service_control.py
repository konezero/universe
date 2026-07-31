from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_service_control import (  # noqa: E402
    load_state,
    pid_is_running,
    service_status,
    stop_service,
)


class UniverseServiceControlTests(unittest.TestCase):
    def test_missing_state_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.json"
            report = service_status(path)
            self.assertEqual("STOPPED", report["status"])
            self.assertFalse(report["pid_running"])

    def test_load_state_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.json"
            path.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:9",
                        "token": "x",
                        "pid": 1,
                    }
                ),
                encoding="utf-8",
            )
            state = load_state(path)
            self.assertEqual("http://127.0.0.1:9", state["endpoint"])

    def test_stop_when_already_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.json"
            report = stop_service(path)
            self.assertEqual("ALREADY_STOPPED", report["status"])

    def test_pid_is_running_for_self(self) -> None:
        self.assertTrue(pid_is_running(__import__("os").getpid()))

    def test_stale_pid_reports_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.json"
            path.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:1",
                        "token": "x",
                        "pid": 999_999_991,
                        "database": str(Path(temp) / "db.sqlite3"),
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "universe_service_control.pid_is_running", return_value=False
            ):
                with mock.patch(
                    "universe_service_control.probe_health", return_value=None
                ):
                    report = service_status(path)
            self.assertEqual("STOPPED", report["status"])
            self.assertFalse(report["pid_running"])


if __name__ == "__main__":
    unittest.main()
