from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from session_supervisor import SessionSupervisorError, SessionSupervisorStore  # noqa: E402


class RuntimeExecutorSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "universe.sqlite3"
        self.present = True
        self.store = SessionSupervisorStore(
            self.database,
            process_observer=self._observe,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _observe(self, pid: int, created: str) -> dict[str, object]:
        return (
            {
                "status": "PROCESS_PRESENT_EXACT",
                "pid": pid,
                "process_created_at": created,
            }
            if self.present
            else {
                "status": "ORIGINAL_PROCESS_ABSENT",
                "reason": "TEST_PROCESS_EXITED",
                "pid": pid,
                "expected_process_created_at": created,
            }
        )

    @staticmethod
    def session(session_id: str = "runtime-executor") -> dict[str, object]:
        return {
            "session_id": session_id,
            "node": "universe",
            "mode": "MASTER",
            "provider": "RUNTIME",
            "provider_session_ref": "universe-MASTER-boot-01",
            "anchor_ref": "MASTER-CURRENT-BOOT-01",
            "state": "REGISTERED",
            "currentness": "CURRENT",
        }

    @staticmethod
    def process(pid: int = 4242) -> dict[str, object]:
        return {
            "pid": pid,
            "process_created_at": "2026-08-11T00:00:00Z",
            "executable": "C:\\Python\\python.exe",
            "command": ["python.exe", "session-boot", "serve", "--token", "secret"],
            "endpoint": "http://127.0.0.1:58333",
            "handshake_fingerprint": hashlib.sha256(b"secret").hexdigest(),
        }

    def test_capability_is_protected_and_survives_store_reopen(self) -> None:
        session, _ = self.store.register_session(self.session())
        acquired = self.store.acquire_lease(
            session["session_id"],
            self.process(),
            stop_capability="runtime-stop-secret",
        )
        raw = self.database.read_bytes()
        self.assertNotIn("runtime-stop-secret", raw.decode("latin-1"))
        self.assertNotIn("stop_capability_protected", acquired["lease"])
        reopened = SessionSupervisorStore(self.database, process_observer=self._observe)
        self.assertEqual(
            "runtime-stop-secret",
            reopened.get_stop_capability(session["session_id"]),
        )

    def test_managed_stop_is_fail_closed_until_process_absence(self) -> None:
        session, _ = self.store.register_session(self.session())
        self.store.acquire_lease(
            session["session_id"],
            self.process(),
            stop_capability="runtime-stop-secret",
        )
        authorization = self.store.authorize_managed_stop(
            session["session_id"], expected_lease_version=1
        )
        self.assertEqual("MANAGED_STOP_AUTHORIZED", authorization["status"])
        with self.assertRaisesRegex(SessionSupervisorError, "process absence"):
            self.store.complete_managed_stop(
                session["session_id"],
                expected_lease_version=authorization["lease_version"],
            )
        self.present = False
        stopped = self.store.complete_managed_stop(
            session["session_id"],
            expected_lease_version=authorization["lease_version"],
        )
        self.assertEqual("STOPPED", stopped["state"])
        self.assertIsNone(self.store.get_stop_capability(session["session_id"]))

    def test_runtime_adoption_requires_runtime_provider_and_exact_process(self) -> None:
        adopted = self.store.adopt_runtime_executor(
            self.session(),
            self.process(),
            stop_capability="runtime-stop-secret",
        )
        self.assertEqual("RUNTIME_EXECUTOR_ADOPTED", adopted["status"])
        self.assertEqual("RUNTIME", adopted["session"]["provider"])
        bad = self.session("bad-provider")
        bad["provider"] = "CODEX"
        with self.assertRaisesRegex(SessionSupervisorError, "provider=RUNTIME"):
            self.store.adopt_runtime_executor(
                bad,
                self.process(pid=4243),
                stop_capability="runtime-stop-secret",
            )


if __name__ == "__main__":
    unittest.main()
