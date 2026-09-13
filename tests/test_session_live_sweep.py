from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from session_supervisor import SessionSupervisorStore  # noqa: E402
from universe_multi_room import MultiRoomStore  # noqa: E402
from universe_server import perform_session_ref_inject  # noqa: E402


class LiveSessionSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "supervisor.sqlite3"
        self.store = SessionSupervisorStore(self.db)

    def tearDown(self) -> None:
        self.store = None
        self.temp.cleanup()

    def test_partial_hook_inventory_does_not_disconnect_other_live_masters(self) -> None:
        sessions = [self.store.register_session({"node": "demo", "mode": "MASTER",
            "provider": provider, "state": "LIVE", "currentness": "UNKNOWN"})[0]
            for provider in ("CODEX", "CLAUDE", "GROK")]
        observed = sessions[0]
        sweep = self.store.sweep_stale_live_sessions(
            live_session_anchors={observed["session_id"]: observed["session_anchor_ref"]},
            inventory_complete=False)
        self.assertEqual(0, sweep["demoted_count"])
        for session in sessions:
            self.assertEqual("LIVE", self.store.get_session(session["session_id"])["state"])

    def test_wrong_provider_hook_cannot_rebind_stale_projection_of_live_host(self) -> None:
        from universe_server import UniverseError
        from unittest.mock import Mock
        for provider in ("CODEX", "CLAUDE", "GROK"):
            session, _ = self.store.register_session({"node": "demo", "mode": "MASTER",
                "provider": provider, "state": "DISCONNECTED", "currentness": "UNKNOWN"})
            host = Mock()
            host.list_sessions.return_value = [{"supervisor_session_id": session["session_id"],
                "provider": provider, "project_id": "demo", "mode": "MASTER", "state": "LIVE"}]
            for wrong in {"CODEX", "CLAUDE", "GROK"} - {provider}:
                with self.subTest(owner=provider, hook=wrong), self.assertRaises(UniverseError) as raised:
                    perform_session_ref_inject(session_supervisor=self.store, multi_rooms=Mock(),
                        terminal_host=host, environment={}, body={"provider": wrong,
                            "provider_session_ref": "foreign", "project_id": "demo",
                            "supervisor_session_id": session["session_id"]})
                self.assertEqual("SESSION_HOOK_HOST_IDENTITY_MISMATCH", raised.exception.code)
                self.assertEqual(provider, self.store.get_session(session["session_id"])["provider"])

    def test_live_without_lease_is_demoted(self) -> None:
        registered, created = self.store.register_session(
            {
                "session_id": "session_test_nolease",
                "node": "demo",
                "mode": "MASTER",
                "provider": "GROK",
                "provider_session_ref": "ref-1",
                "session_kind": "PERSISTENT_MODE_SESSION",
                "state": "LIVE",
                "currentness": "UNKNOWN",
            }
        )
        self.assertTrue(created)
        self.assertEqual("LIVE", registered["state"])
        # A caller without a PTY inventory cannot declare another Host dead.
        unobserved = self.store.sweep_stale_live_sessions()
        self.assertEqual(0, unobserved["demoted_count"])
        self.assertEqual(1, unobserved["unknown_probe_count"])
        self.assertEqual("LIVE", self.store.get_session("session_test_nolease")["state"])
        sweep = self.store.sweep_stale_live_sessions(live_session_anchors={})
        self.assertGreaterEqual(sweep["demoted_count"], 1)
        reasons = {item["session_id"]: item["reason"] for item in sweep["demoted"]}
        self.assertEqual("NO_PROCESS_LEASE", reasons["session_test_nolease"])
        again = self.store.get_session("session_test_nolease")
        self.assertEqual("DISCONNECTED", again["state"])

    def test_exact_live_pty_binding_preserves_session_without_lease(self) -> None:
        registered, _ = self.store.register_session(
            {
                "session_id": "session_test_live_pty",
                "node": "demo",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        sweep = self.store.sweep_stale_live_sessions(
            live_session_anchors={
                registered["session_id"]: registered["session_anchor_ref"]
            }
        )

        self.assertEqual(0, sweep["demoted_count"])
        self.assertEqual(1, sweep["kept_live_count"])
        self.assertEqual(1, sweep["pty_kept_live_count"])
        again = self.store.get_session(registered["session_id"])
        self.assertEqual("LIVE", again["state"])
        self.assertEqual("CURRENT", again["currentness"])

    def test_exact_live_pty_binding_promotes_starting_session(self) -> None:
        registered, _ = self.store.register_session(
            {
                "session_id": "session_test_starting_pty",
                "node": "demo",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "state": "STARTING",
                "currentness": "UNKNOWN",
            }
        )

        sweep = self.store.sweep_stale_live_sessions(
            live_session_anchors={
                registered["session_id"]: registered["session_anchor_ref"]
            }
        )

        self.assertEqual(1, sweep["restored_live_count"])
        promoted = self.store.get_session(registered["session_id"])
        self.assertEqual("LIVE", promoted["state"])
        self.assertEqual("ATTACHED", promoted["current_activity_state"])

    def test_managed_attach_returns_live_supervisor_projection(self) -> None:
        registered, _ = self.store.register_session(
            {
                "session_id": "session_test_attach_projection",
                "node": "universe",
                "mode": "CONDUCTOR",
                "provider": "CLAUDE",
                "state": "STARTING",
                "currentness": "UNKNOWN",
            }
        )
        anchor = registered["session_anchor_ref"]

        class Host:
            def get(self, terminal_id: str):
                return SimpleNamespace(
                    public=lambda: {
                        "terminal_id": terminal_id,
                        "supervisor_session_id": registered["session_id"],
                        "session_anchor_ref": anchor,
                    }
                )

            def list_sessions(self):
                return [{"supervisor_session_id": registered["session_id"],
                         "session_anchor_ref": anchor, "provider": "CLAUDE",
                         "project_id": "universe", "mode": registered["mode"], "state": "LIVE"}]

            def record_managed_attach(self, terminal_id: str, _evidence):
                return {"status": "MANAGED_SHELL_ATTACHED", "terminal_id": terminal_id}

        result = perform_session_ref_inject(
            session_supervisor=self.store,
            multi_rooms=MultiRoomStore(str(Path(self.temp.name) / "rooms.sqlite3")),
            terminal_host=Host(),
            environment={},
            body={
                "provider": "CLAUDE",
                "project_id": "universe",
                "supervisor_session_id": registered["session_id"],
                "managed_shell_attach": {
                    "terminal_id": "term_attach_projection",
                    "session_anchor_ref": anchor,
                },
            },
        )

        self.assertEqual("MANAGED_SHELL_ATTACHED", result["managed_shell_attachment"]["status"])
        self.assertEqual("LIVE", result["supervisor_session"]["state"])

    def test_exact_host_termination_stops_only_matching_session(self) -> None:
        registered, _ = self.store.register_session(
            {
                "session_id": "session_test_host_terminated",
                "node": "demo",
                "mode": "MASTER",
                "provider": "CODEX",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        stopped = self.store.record_host_termination(
            registered["session_id"],
            session_anchor_ref=registered["session_anchor_ref"],
        )

        self.assertEqual("STOPPED", stopped["state"])
        self.assertEqual("TERMINATED", stopped["current_activity_state"])
        self.assertEqual("CURRENT", stopped["currentness"])

    def test_exact_live_pty_binding_restores_only_matching_anchor(self) -> None:
        registered, _ = self.store.register_session(
            {
                "session_id": "session_test_restore_pty",
                "node": "demo",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "state": "DISCONNECTED",
                "currentness": "STALE",
            }
        )

        mismatch = self.store.sweep_stale_live_sessions(
            live_session_anchors={registered["session_id"]: "wrong-anchor"}
        )
        self.assertEqual(0, mismatch["restored_live_count"])
        self.assertEqual(
            "DISCONNECTED", self.store.get_session(registered["session_id"])["state"]
        )

        restored = self.store.sweep_stale_live_sessions(
            live_session_anchors={
                registered["session_id"]: registered["session_anchor_ref"]
            }
        )
        self.assertEqual(1, restored["restored_live_count"])
        self.assertEqual(1, restored["pty_kept_live_count"])
        again = self.store.get_session(registered["session_id"])
        self.assertEqual("LIVE", again["state"])
        self.assertEqual("STALE", again["currentness"])

    def test_live_with_dead_pid_is_demoted(self) -> None:
        def observer(pid: int, created_at: str):
            return {
                "status": "ORIGINAL_PROCESS_ABSENT",
                "reason": "PID_NOT_RUNNING",
                "pid": pid,
                "expected_process_created_at": created_at,
            }

        store = SessionSupervisorStore(self.db, process_observer=observer)
        store.register_session(
            {
                "session_id": "session_test_deadpid",
                "node": "demo",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "ref-2",
                "session_kind": "PERSISTENT_MODE_SESSION",
                "state": "REGISTERED",
                "currentness": "UNKNOWN",
            }
        )
        identity = {
            "pid": 999999,
            "process_created_at": "2026-01-01T00:00:00.000000Z",
            "executable": "C:\\fake\\python.exe",
            "command": ["python", "serve"],
            "endpoint": "http://127.0.0.1:1",
            "handshake_fingerprint": "a" * 64,
        }
        leased = store.acquire_lease(
            "session_test_deadpid",
            identity,
            expected_lease_version=0,
        )
        self.assertEqual("PROCESS_LEASE_ACQUIRED", leased["status"])
        self.assertEqual("LIVE", store.get_session("session_test_deadpid")["state"])
        sweep = store.sweep_stale_live_sessions()
        demoted_ids = {item["session_id"] for item in sweep["demoted"]}
        self.assertIn("session_test_deadpid", demoted_ids)
        again = store.get_session("session_test_deadpid")
        self.assertEqual("DISCONNECTED", again["state"])
        self.assertEqual("STALE", again["process_lease"]["lease_state"])


if __name__ == "__main__":
    unittest.main()
