from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ANCHOR = "session_anchor_test"
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.pty_supervisor import (  # noqa: E402
    SCHEMA,
    SupervisedTerminalHost,
    restart_supervisor,
    spawn_supervisor,
)
from universe_app.terminal_host import TerminalHost  # noqa: E402
from universe_pty_supervisor import (  # noqa: E402
    PtySupervisor,
    Server,
    reconnection_registry_from_environment,
)


class FakePty:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.size = (120, 32)
        self.pid = 4242
        self._chunks = [b"hello-supervisor"]

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read(self, timeout: float = 0.2) -> bytes:
        del timeout
        if self._chunks:
            return self._chunks.pop(0)
        time.sleep(0.01)
        return b""

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def close(self) -> None:
        self.closed = True


class PtySupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit_dir = tempfile.TemporaryDirectory(prefix="pty-audit-test-")
        audit_path = Path(self.audit_dir.name) / "audit.sqlite3"
        host = TerminalHost(
            spawn=lambda *_args, **_kwargs: FakePty(),
            audit_database_path=audit_path,
        )
        self.server = Server("sup-token", host=host)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host_name, port = self.server.server_address[:2]
        self.base = f"http://{host_name}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.supervisor.close()
        self.server.server_close()
        self.audit_dir.cleanup()

    def test_supervisor_polls_orphan_reclaim_without_ui_clients(self) -> None:
        observed = threading.Event()

        class FakeHost:
            def __init__(self) -> None:
                self.calls = 0

            def reclaim_orphaned_managed_shells(self):
                self.calls += 1
                if self.calls >= 2:
                    observed.set()
                return []

        host = FakeHost()
        supervisor = PtySupervisor(host=host, reclaim_poll_seconds=0.01)
        try:
            self.assertTrue(observed.wait(timeout=1.0))
            self.assertGreaterEqual(host.calls, 2)
        finally:
            supervisor.close()

    def test_supervisor_reconciles_hosts_before_orphan_reclaim(self) -> None:
        calls: list[str] = []

        class FakeHost:
            def reconcile_reconnection_hosts(self):
                calls.append("reconcile")
                return []

            def reclaim_orphaned_managed_shells(self):
                calls.append("reclaim")
                return []

            def cleanup_reconnection_host_registry(self):
                calls.append("cleanup")
                return []

        supervisor = PtySupervisor(host=FakeHost(), reclaim_poll_seconds=60)
        try:
            self.assertEqual(["reconcile", "cleanup", "reclaim"], calls[:3])
        finally:
            supervisor.close()

    def test_reconnection_registry_requires_explicit_feature_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "universe-session-host.exe"
            binary.write_bytes(b"test")
            state_path = root / "pty-supervisor.json"
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(reconnection_registry_from_environment(state_path))
            with patch.dict(
                os.environ,
                {
                    "UNIVERSE_RECONNECTION_HOST_ENABLED": "1",
                    "UNIVERSE_RECONNECTION_HOST_BINARY": str(binary),
                    "UNIVERSE_RECONNECTION_HOST_REGISTRY": str(root / "registry"),
                },
                clear=True,
            ), patch(
                "universe_pty_supervisor.ReconnectionHostRegistry.prepare"
            ) as prepare:
                registry = reconnection_registry_from_environment(state_path)
            self.assertIsNotNone(registry)
            self.assertEqual(binary, registry.binary)
            self.assertEqual(root / "registry", registry.root)
            self.assertEqual(86400, registry.stale_after_seconds)
            prepare.assert_called_once_with()

    def test_spawn_supervisor_hides_its_windows_console(self) -> None:
        script = ROOT / "tools" / "universe_pty_supervisor.py"
        with patch("universe_app.pty_supervisor.os.name", "nt"), patch.object(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 1, create=True
        ), patch.object(
            subprocess, "DETACHED_PROCESS", 2, create=True
        ), patch.object(
            subprocess, "CREATE_NO_WINDOW", 4, create=True
        ), patch(
            "universe_app.pty_supervisor.supervisor_script", return_value=script
        ), patch(
            "universe_app.pty_supervisor._reconnection_host_binary_available",
            return_value=True,
        ), patch(
            "universe_app.pty_supervisor.subprocess.Popen"
        ) as popen:
            spawn_supervisor()

        self.assertEqual(1 | 4, popen.call_args.kwargs["creationflags"])
        self.assertEqual(
            "1",
            popen.call_args.kwargs["env"]["UNIVERSE_RECONNECTION_HOST_ENABLED"],
        )

    def test_restart_ends_existing_supervisor_before_starting_replacement(self) -> None:
        state_path = Path(tempfile.mkdtemp(prefix="pty-restart-")) / "pty-supervisor.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "READY",
                    "pid": 1234,
                    "endpoint": "http://127.0.0.1:50000",
                    "token": "test-token",
                }
            ),
            encoding="utf-8",
        )
        replacement = {"pid": 5678, "endpoint": "http://127.0.0.1:50001"}
        with patch(
            "universe_app.pty_supervisor.pid_is_running", side_effect=[True, False, False]
        ), patch(
            "universe_app.pty_supervisor._terminate_supervisor_process", return_value=True
        ) as terminate, patch(
            "universe_app.pty_supervisor.ensure_supervisor", return_value=replacement
        ) as ensure:
            result = restart_supervisor(state_path=state_path)

        terminate.assert_called_once_with(1234)
        ensure.assert_called_once_with(state_path=state_path, timeout=8.0)
        self.assertEqual("RESTARTED", result["status"])
        self.assertEqual(1234, result["previous_pid"])
        self.assertEqual(5678, result["pid"])
        self.assertTrue(result["active_terminals_ended"])

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = None
        headers = {"Authorization": "Bearer sup-token", "Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_managed_attach_route_forwards_to_terminal_host(self) -> None:
        evidence = {
            "status": "OBSERVED",
            "terminal_id": "term_owned",
            "session_anchor_ref": TEST_ANCHOR,
            "shell_pid": 4242,
            "shell_started_at": 123.5,
        }
        with patch.object(
            self.server.supervisor.host,
            "record_managed_attach",
            return_value={"status": "MANAGED_SHELL_ATTACHED"},
        ) as record:
            status, payload = self.request(
                "POST",
                "/v1/terminals/term_owned/managed-attach",
                evidence,
            )
        self.assertEqual(200, status)
        self.assertEqual("MANAGED_SHELL_ATTACHED", payload["status"])
        record.assert_called_once_with("term_owned", evidence)

    def test_create_list_and_read_survives_client_disconnect_model(self) -> None:
        status, created = self.request(
            "POST",
            "/v1/terminals",
            {
                "project_id": "universe",
                "mode": "MASTER",
                "cwd": str(ROOT),
                "provider": "GROK",
                "model_ref": "grok-4.6",
                "effort": "MAX",
            },
        )
        self.assertEqual(201, status)
        terminal_id = created["terminal"]["terminal_id"]
        self.assertEqual(4242, created["terminal"]["pid"])
        self.assertEqual("grok-4.6", created["terminal"]["model_ref"])
        self.assertEqual("MAX", created["terminal"]["effort"])
        _status, listed = self.request("GET", "/v1/terminals")
        self.assertEqual(1, len(listed["terminals"]))
        _status, attached = self.request("POST", f"/v1/terminals/{terminal_id}/attach")
        attach_id = attached["attach_id"]
        seen = b""
        deadline = time.time() + 1
        while time.time() < deadline and b"hello-supervisor" not in seen:
            _status, chunk = self.request(
                "GET",
                f"/v1/terminals/{terminal_id}/attach/{attach_id}/read?timeout=0.2",
            )
            if chunk.get("data_b64"):
                seen += base64.b64decode(chunk["data_b64"])
        self.assertIn(b"hello-supervisor", seen)
        self.request("DELETE", f"/v1/terminals/{terminal_id}/attach/{attach_id}")
        _status, listed_after = self.request("GET", "/v1/terminals")
        self.assertEqual(1, len(listed_after["terminals"]))
        self.assertEqual(4242, listed_after["terminals"][0]["pid"])

    def test_supervised_client_reattaches_after_universe_exit_model(self) -> None:
        state_dir = Path(tempfile.mkdtemp(prefix="pty-sup-"))
        state_path = state_dir / "pty-supervisor.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "READY",
                    "pid": os.getpid(),
                    "started_at": "2026-08-19T00:00:00Z",
                    "endpoint": self.base,
                    "token": "sup-token",
                }
            ),
            encoding="utf-8",
        )
        first = SupervisedTerminalHost(state_path=state_path)
        created = first.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="GROK",
            supervisor_session_id="session_master_1",
        )
        terminal_id = created["terminal_id"]
        self.assertEqual(4242, created["pid"])
        first.emit_output(terminal_id, b"history-before-universe-restart")
        del first

        second = SupervisedTerminalHost(state_path=state_path)
        listed = second.list_sessions()
        self.assertEqual(1, len(listed))
        self.assertEqual(terminal_id, listed[0]["terminal_id"])
        history = second.history(terminal_id, limit=10)
        replay = b"".join(
            base64.b64decode(chunk["data_base64"])
            for chunk in history["chunks"]
        )
        self.assertIn(b"history-before-universe-restart", replay)
        found = second.find_live(
            project_id="universe",
            mode="MASTER",
            provider="GROK",
            supervisor_session_id="session_master_1",
        )
        self.assertIsNotNone(found)
        self.assertEqual(terminal_id, found["terminal_id"])
        self.assertIsNone(
            second.find_live(
                project_id="universe",
                mode="MASTER",
                provider="GROK",
                supervisor_session_id="session_master_2",
            )
        )
        waiter = second.subscribe(terminal_id)
        seen = b""
        deadline = time.time() + 1
        while time.time() < deadline and b"hello-supervisor" not in seen:
            try:
                chunk = waiter.get(timeout=0.2)
            except Exception:
                continue
            if chunk:
                seen += chunk
        self.assertIn(b"hello-supervisor", seen)
        second.unsubscribe(terminal_id, waiter)


    def test_http_control_and_delete_are_durably_attributed(self) -> None:
        _status, created = self.request(
            "POST",
            "/v1/terminals",
            {
                "project_id": "universe",
                "mode": "MASTER",
                "cwd": str(ROOT),
                "provider": "CODEX",
                "supervisor_session_id": "session_master_1",
            },
        )
        terminal_id = created["terminal"]["terminal_id"]
        self.request(
            "POST",
            f"/v1/terminals/{terminal_id}/write",
            {"data_b64": base64.b64encode(b"\x03").decode("ascii")},
        )
        self.request("DELETE", f"/v1/terminals/{terminal_id}")
        _status, payload = self.request(
            "GET", f"/v1/audit-events?terminal_id={terminal_id}"
        )
        events = list(reversed(payload["events"]))
        self.assertEqual(
            [
                "TERMINAL_CREATED",
                "INPUT_CONTROL_WRITTEN",
                "CLOSE_REQUESTED",
                "TERMINAL_CLOSED",
            ],
            [event["event_type"] for event in events],
        )
        self.assertEqual("PTY_SUPERVISOR_WRITE", events[1]["source"])
        self.assertEqual(["CTRL_C"], events[1]["details"]["control_classes"])
        self.assertEqual("PTY_SUPERVISOR_DELETE", events[2]["source"])


if __name__ == "__main__":
    unittest.main()
