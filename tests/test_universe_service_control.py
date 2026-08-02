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
    probe_health,
    service_status,
    start_service,
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

    def test_probe_health_returns_ready_payload(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"READY"}'
        with mock.patch("universe_service_control.urlopen", return_value=response):
            self.assertEqual("READY", probe_health("http://127.0.0.1:9")["status"])

    def test_probe_health_rejects_non_loopback_or_non_origin_endpoint(self) -> None:
        endpoints = (
            "https://127.0.0.1:9",
            "http://example.com:9",
            "http://user@127.0.0.1:9",
            "http://127.0.0.1:9/path",
            "http://127.0.0.1:9?token=secret",
        )
        with mock.patch("universe_service_control.urlopen") as urlopen:
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint):
                    self.assertIsNone(probe_health(endpoint))
        urlopen.assert_not_called()

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

    def test_start_detaches_all_parent_handles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = mock.MagicMock()
            process.pid = 123
            process.poll.return_value = None
            stopped = {"status": "STOPPED", "pid_running": False}
            ready = {"status": "READY", "pid_running": True, "pid": 123}
            with mock.patch(
                "universe_service_control.service_status",
                side_effect=[stopped, ready],
            ):
                with mock.patch(
                    "universe_service_control.subprocess.Popen",
                    return_value=process,
                ) as popen:
                    result = start_service(
                        state_path=root / "server.json",
                        database_path=root / "universe.sqlite3",
                        mode_registry=root / "mode_registry.json",
                        log_path=root / "service.log",
                        working_directory=root,
                        wait_seconds=0,
                    )
            self.assertEqual("READY", result["status"])
            self.assertTrue(popen.call_args.kwargs["close_fds"])

    def test_stop_uses_authenticated_graceful_shutdown_without_taskkill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.json"
            path.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:51702",
                        "token": "secret",
                        "pid": 42,
                    }
                ),
                encoding="utf-8",
            )
            ready = {
                "status": "READY",
                "pid": 42,
                "pid_running": True,
                "endpoint": "http://127.0.0.1:51702",
            }
            stopped = {
                "status": "STOPPED",
                "pid": 42,
                "pid_running": False,
                "endpoint": "http://127.0.0.1:51702",
            }
            with mock.patch(
                "universe_service_control.service_status",
                side_effect=[ready, stopped],
            ):
                with mock.patch(
                    "universe_service_control.request_graceful_shutdown",
                    return_value={"status": "SERVICE_SHUTDOWN_ACCEPTED"},
                ) as shutdown:
                    with mock.patch(
                        "universe_service_control.pid_is_running",
                        return_value=False,
                    ):
                        report = stop_service(path)
            self.assertEqual("STOPPED", report["status"])
            self.assertFalse(report["destructive_fallback_performed"])
            shutdown.assert_called_once_with(
                "http://127.0.0.1:51702", "secret"
            )

    def test_stop_fails_closed_when_service_cannot_authenticate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.json"
            path.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:51702",
                        "token": "secret",
                        "pid": 42,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "universe_service_control.service_status",
                return_value={
                    "status": "STARTING_OR_UNHEALTHY",
                    "pid": 42,
                    "pid_running": True,
                },
            ):
                with mock.patch(
                    "universe_service_control.request_graceful_shutdown",
                    return_value=None,
                ):
                    report = stop_service(path)
            self.assertEqual("STOP_FAILED", report["status"])
            self.assertFalse(report["destructive_fallback_performed"])


if __name__ == "__main__":
    unittest.main()
