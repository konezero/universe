from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from session_supervisor import SessionSupervisorStore  # noqa: E402


class LiveSessionSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "supervisor.sqlite3"
        self.store = SessionSupervisorStore(self.db)

    def tearDown(self) -> None:
        self.store = None
        self.temp.cleanup()

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
        sweep = self.store.sweep_stale_live_sessions()
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
