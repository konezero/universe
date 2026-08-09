from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from session_supervisor import (  # noqa: E402
    SessionSupervisorError,
    SessionSupervisorStore,
)


class SessionSupervisorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "universe.sqlite3"
        self.store = SessionSupervisorStore(
            self.database,
            process_observer=lambda pid, created: {
                "status": "PROCESS_PRESENT_EXACT",
                "pid": pid,
                "process_created_at": created,
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def session(session_id: str = "session-gcs-master") -> dict[str, object]:
        return {
            "session_id": session_id,
            "node": "GCS",
            "mode": "MASTER",
            "provider": "CODEX",
            "provider_session_ref": "provider-session-1",
            "anchor_ref": "MASTER-CURRENT-GCS",
            "state": "REGISTERED",
            "currentness": "UNKNOWN",
        }

    @staticmethod
    def process(**overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "pid": 4242,
            "process_created_at": "2026-08-02T12:00:00Z",
            "executable": "C:\\Tools\\codex.exe",
            "command": [
                "C:\\Tools\\codex.exe",
                "resume",
                "provider-session-1",
                "--token",
                "runtime-secret",
            ],
            "endpoint": "http://127.0.0.1:51702",
            "handshake_fingerprint": hashlib.sha256(b"handshake").hexdigest(),
        }
        value.update(overrides)
        return value

    def test_schema_is_durable_and_excludes_transcripts_and_raw_tokens(self) -> None:
        expected = {
            "session_record",
            "session_binding_history",
            "process_lease",
            "target_default_session",
            "supervisor_event",
        }
        connection = sqlite3.connect(self.database)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(expected.issubset(tables))
            columns = {
                row[1]
                for table in expected
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
        finally:
            connection.close()
        self.assertNotIn("transcript", columns)
        self.assertNotIn("handshake_token", columns)
        self.assertIn("handshake_fingerprint", columns)
        self.assertIn("lease_token_sha256", columns)
        self.assertIn("anchor_ref", columns)

    def test_only_persistent_mode_sessions_can_register(self) -> None:
        candidate = self.session()
        candidate["session_kind"] = "TASK_FRAME_WORKER"
        with self.assertRaisesRegex(SessionSupervisorError, "persistent Mode"):
            self.store.register_session(candidate)

    def test_purge_inactive_keeps_live_only(self) -> None:
        dead, _ = self.store.register_session(self.session("session-dead"))
        live_candidate = self.session("session-live")
        live_candidate["provider_session_ref"] = "provider-session-live"
        live_candidate["state"] = "LIVE"
        live, _ = self.store.register_session(live_candidate)
        self.store.set_default(dead["session_id"], expected_pointer_version=0)
        result = self.store.purge_inactive_sessions()
        self.assertEqual("SESSIONS_PURGED", result["status"])
        self.assertEqual(1, result["removed_count"])
        self.assertEqual(1, result["kept_count"])
        remaining = self.store.list_sessions()
        self.assertEqual(1, len(remaining))
        self.assertEqual(live["session_id"], remaining[0]["session_id"])
        self.assertEqual("LIVE", remaining[0]["state"])

    def test_registration_is_idempotent_and_identity_conflict_fails(self) -> None:
        first, created = self.store.register_session(self.session())
        second, created_again = self.store.register_session(self.session())
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual("GCS MASTER", first["alias"])

        conflict = self.session()
        conflict["mode"] = "CONDUCTOR"
        with self.assertRaisesRegex(SessionSupervisorError, "different identity"):
            self.store.register_session(conflict)

    def test_alias_is_display_only_and_uses_session_row_cas(self) -> None:
        session, _ = self.store.register_session(self.session())
        updated = self.store.update_alias(
            session["session_id"],
            alias="GCS Control Room",
            expected_version=session["row_version"],
        )
        self.assertEqual("GCS Control Room", updated["alias"])
        self.assertEqual("GCS", updated["node"])
        self.assertEqual("MASTER", updated["mode"])
        with self.assertRaisesRegex(SessionSupervisorError, "version changed"):
            self.store.update_alias(
                session["session_id"],
                alias="Stale Alias",
                expected_version=session["row_version"],
            )

    def test_provider_rebind_preserves_anchor_session_identity(self) -> None:
        first, created = self.store.register_session(self.session())
        replacement = self.session()
        replacement["provider"] = "CLAUDE"
        replacement["provider_session_ref"] = "claude-thread-2"
        rebound, created_again = self.store.register_session(replacement)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["session_id"], rebound["session_id"])
        self.assertEqual(first["anchor_ref"], rebound["anchor_ref"])
        self.assertEqual("CLAUDE", rebound["provider"])
        self.assertEqual(2, len(rebound["binding_history"]))

        anchored = self.store.bind_current_anchor(
            rebound["session_id"],
            anchor_ref="MASTER-CURRENT-GCS-NEXT",
            expected_version=rebound["row_version"],
        )
        self.assertEqual("MASTER-CURRENT-GCS-NEXT", anchored["anchor_ref"])
        self.assertEqual("CURRENT", anchored["currentness"])

    def test_initialize_migrates_only_legacy_provider_aliases(self) -> None:
        legacy, _ = self.store.register_session(self.session("legacy-alias"))
        replacement = self.session("legacy-alias")
        replacement["provider"] = "CLAUDE"
        replacement["provider_session_ref"] = "claude-thread-2"
        self.store.register_session(replacement)
        custom, _ = self.store.register_session(self.session("custom-alias"))
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE session_record SET alias = ? WHERE session_id = ?",
                ("GCS MASTER | CODEX", legacy["session_id"]),
            )
            connection.execute(
                "UPDATE session_record SET alias = ? WHERE session_id = ?",
                ("GCS MASTER | Operations", custom["session_id"]),
            )
            connection.commit()
        finally:
            connection.close()

        reopened = SessionSupervisorStore(
            self.database,
            process_observer=lambda pid, created: {
                "status": "PROCESS_PRESENT_EXACT",
                "pid": pid,
                "process_created_at": created,
            },
        )

        self.assertEqual(
            "GCS MASTER",
            reopened.get_session(legacy["session_id"])["alias"],
        )
        self.assertEqual(
            "GCS MASTER | Operations",
            reopened.get_session(custom["session_id"])["alias"],
        )

    def test_default_pointer_is_provider_neutral_and_uses_cas(self) -> None:
        first, _ = self.store.register_session(self.session("session-one"))
        second_candidate = self.session("session-two")
        second_candidate["provider"] = "CLAUDE"
        second_candidate["provider_session_ref"] = "claude-session"
        second, _ = self.store.register_session(second_candidate)

        pointer = self.store.set_default(
            first["session_id"], expected_pointer_version=0
        )
        self.assertEqual(1, pointer["pointer_version"])
        pointer = self.store.set_default(
            second["session_id"], expected_pointer_version=1
        )
        self.assertEqual(2, pointer["pointer_version"])
        sessions = self.store.list_sessions(node="GCS", mode="MASTER")
        self.assertEqual(2, len(sessions))
        self.assertEqual(
            ["session-two"],
            [item["session_id"] for item in sessions if item["is_default"]],
        )
        with self.assertRaisesRegex(SessionSupervisorError, "pointer version changed"):
            self.store.set_default(first["session_id"], expected_pointer_version=1)

    def test_reconcile_requires_all_six_process_identity_fields(self) -> None:
        session, _ = self.store.register_session(self.session())
        acquired = self.store.acquire_lease(session["session_id"], self.process())
        self.assertNotIn(
            acquired["lease_token"], self.database.read_bytes().decode("latin-1")
        )
        self.assertNotIn(
            "runtime-secret", self.database.read_bytes().decode("latin-1")
        )
        result = self.store.reconcile(
            session["session_id"],
            self.process(),
            expected_lease_version=1,
        )
        self.assertTrue(result["exact_match"])
        self.assertEqual("LIVE", result["session"]["state"])

        mismatch = self.process(endpoint="http://127.0.0.1:59999")
        result = self.store.reconcile(
            session["session_id"],
            mismatch,
            expected_lease_version=2,
        )
        self.assertFalse(result["exact_match"])
        self.assertFalse(result["destructive_action_permitted"])
        self.assertEqual("UNKNOWN", result["session"]["state"])
        self.assertEqual(
            "UNKNOWN", result["session"]["process_lease"]["lease_state"]
        )

    def test_reconcile_fails_closed_without_independent_process_evidence(self) -> None:
        session, _ = self.store.register_session(self.session("probe-unknown"))
        self.store.acquire_lease(session["session_id"], self.process())
        self.store.process_observer = lambda _pid, _created: {
            "status": "UNKNOWN",
            "reason": "PROCESS_QUERY_FAILED",
        }

        result = self.store.reconcile(
            session["session_id"], self.process(), expected_lease_version=1
        )

        self.assertFalse(result["exact_match"])
        self.assertFalse(result["destructive_action_permitted"])
        self.assertEqual("UNKNOWN", result["session"]["state"])

    def test_process_lease_redacts_inline_secret_argv(self) -> None:
        session, _ = self.store.register_session(self.session("inline-secret"))
        identity = self.process()
        identity["command"] = [
            "worker.exe",
            "positional-runtime-secret",
            "--token=inline-runtime-secret",
        ]

        self.store.acquire_lease(session["session_id"], identity)

        material = self.store.get_session(session["session_id"])
        command = material["process_lease"]["process_identity"]["command"]
        self.assertTrue(all(item.startswith("sha256:") for item in command))
        persisted = self.database.read_bytes().decode("latin-1")
        self.assertNotIn("inline-runtime-secret", persisted)
        self.assertNotIn("positional-runtime-secret", persisted)
        self.assertNotIn("worker.exe", persisted)

    def test_process_identity_normalizes_timestamp_and_rejects_secret_endpoint(self) -> None:
        session, _ = self.store.register_session(self.session("canonical-process"))
        acquired = self.store.acquire_lease(
            session["session_id"],
            self.process(process_created_at="2026-08-02T21:00:00.1234567+09:00"),
        )
        self.assertEqual(
            "2026-08-02T12:00:00.123456Z",
            acquired["lease"]["process_identity"]["process_created_at"],
        )
        other, _ = self.store.register_session(self.session("secret-endpoint"))
        with self.assertRaisesRegex(SessionSupervisorError, "secret-free loopback"):
            self.store.acquire_lease(
                other["session_id"],
                self.process(endpoint="http://127.0.0.1:51702/?token=secret"),
            )

    def test_stop_authorization_needs_current_capability_and_exact_identity(self) -> None:
        session, _ = self.store.register_session(self.session())
        acquired = self.store.acquire_lease(session["session_id"], self.process())
        receipt = self.store.authorize_stop(
            session["session_id"],
            self.process(),
            lease_token=acquired["lease_token"],
            expected_lease_version=1,
        )
        self.assertEqual("STOP_AUTHORIZED", receipt["status"])
        self.assertNotIn("lease_token", receipt)

        wrong_token_session, _ = self.store.register_session(
            self.session("wrong-token")
        )
        wrong_token_lease = self.store.acquire_lease(
            wrong_token_session["session_id"], self.process(pid=4243)
        )
        with self.assertRaisesRegex(SessionSupervisorError, "lease capability"):
            self.store.authorize_stop(
                wrong_token_session["session_id"],
                self.process(pid=4243),
                lease_token="wrong-token",
                expected_lease_version=1,
            )
        unchanged = self.store.get_session(wrong_token_session["session_id"])
        self.assertEqual("LIVE", unchanged["state"])
        self.assertEqual(1, unchanged["process_lease"]["lease_version"])
        self.assertEqual("OWNED", unchanged["process_lease"]["lease_state"])
        self.assertNotEqual("wrong-token", wrong_token_lease["lease_token"])

        other, _ = self.store.register_session(self.session("session-other"))
        other_lease = self.store.acquire_lease(other["session_id"], self.process(pid=4343))
        with self.assertRaisesRegex(SessionSupervisorError, "exact process identity"):
            self.store.authorize_stop(
                other["session_id"],
                self.process(pid=9999),
                lease_token=other_lease["lease_token"],
                expected_lease_version=1,
            )
        self.assertEqual("UNKNOWN", self.store.get_session(other["session_id"])["state"])

    def test_unknown_lease_recovers_only_after_supervisor_proves_process_absent(self) -> None:
        session, _ = self.store.register_session(self.session("recover-unknown"))
        self.store.acquire_lease(session["session_id"], self.process())
        mismatch = self.process(endpoint="http://127.0.0.1:59999")
        self.store.reconcile(
            session["session_id"], mismatch, expected_lease_version=1
        )
        self.store.process_observer = lambda _pid, _created: {
            "status": "PROCESS_PRESENT_EXACT"
        }
        with self.assertRaisesRegex(SessionSupervisorError, "process absence"):
            self.store.recover_unknown_lease_if_process_absent(
                session["session_id"],
                expected_lease_version=2,
                operator_evidence_ref="operator:test",
            )

        self.store.process_observer = lambda _pid, _created: (_ for _ in ()).throw(
            OSError("probe failed")
        )
        with self.assertRaisesRegex(SessionSupervisorError, "process absence"):
            self.store.recover_unknown_lease_if_process_absent(
                session["session_id"],
                expected_lease_version=2,
                operator_evidence_ref="operator:test",
            )

        self.store.process_observer = lambda pid, created: {
            "status": "ORIGINAL_PROCESS_ABSENT",
            "reason": "PID_NOT_RUNNING",
            "pid": pid,
            "expected_process_created_at": created,
        }
        recovered = self.store.recover_unknown_lease_if_process_absent(
            session["session_id"],
            expected_lease_version=2,
            operator_evidence_ref="operator:test",
        )
        self.assertEqual("DISCONNECTED", recovered["state"])
        self.assertEqual("RELEASED", recovered["process_lease"]["lease_state"])
        self.assertEqual("NOT_PERFORMED", recovered["recovery"]["process_termination"])

        reacquired = self.store.acquire_lease(
            session["session_id"], self.process(pid=5252), expected_lease_version=3
        )
        self.assertEqual("OWNED", reacquired["lease"]["lease_state"])

    def test_released_terminal_lease_cannot_be_reconciled(self) -> None:
        session, _ = self.store.register_session(self.session("terminal"))
        acquired = self.store.acquire_lease(session["session_id"], self.process())
        authorized = self.store.authorize_stop(
            session["session_id"],
            self.process(),
            lease_token=acquired["lease_token"],
            expected_lease_version=1,
        )
        stopped = self.store.complete_stop(
            session["session_id"],
            lease_token=acquired["lease_token"],
            expected_lease_version=authorized["lease_version"],
        )

        with self.assertRaisesRegex(SessionSupervisorError, "explicit lease acquisition"):
            self.store.reconcile(
                session["session_id"],
                self.process(),
                expected_lease_version=stopped["process_lease"]["lease_version"],
            )
        preserved = self.store.get_session(session["session_id"])
        self.assertEqual("STOPPED", preserved["state"])
        self.assertEqual("RELEASED", preserved["process_lease"]["lease_state"])

    def test_terminal_metadata_updates_do_not_reactivate_session(self) -> None:
        session, _ = self.store.register_session(self.session("terminal-metadata"))
        acquired = self.store.acquire_lease(session["session_id"], self.process())
        authorized = self.store.authorize_stop(
            session["session_id"],
            self.process(),
            lease_token=acquired["lease_token"],
            expected_lease_version=1,
        )
        stopped = self.store.complete_stop(
            session["session_id"],
            lease_token=acquired["lease_token"],
            expected_lease_version=authorized["lease_version"],
        )
        renamed = self.store.update_alias(
            session["session_id"],
            alias="Archived terminal session",
            expected_version=stopped["row_version"],
        )
        rebound = self.store.bind_provider_session(
            session["session_id"],
            provider="CLAUDE",
            provider_session_ref="archived-provider-ref",
            expected_version=renamed["row_version"],
        )

        self.assertEqual("STOPPED", rebound["state"])
        self.assertEqual("RELEASED", rebound["process_lease"]["lease_state"])

    def test_stale_lease_requires_reacquisition_and_rotates_capability(self) -> None:
        session, _ = self.store.register_session(self.session("stale-reacquire"))
        acquired = self.store.acquire_lease(session["session_id"], self.process())
        stale = self.store.mark_lease_stale(
            session["session_id"],
            self.process(),
            lease_token=acquired["lease_token"],
            expected_lease_version=1,
            reason="OWNER_DISCONNECTED",
        )
        with self.assertRaisesRegex(SessionSupervisorError, "explicit lease acquisition"):
            self.store.reconcile(
                session["session_id"],
                self.process(),
                expected_lease_version=stale["process_lease"]["lease_version"],
            )
        reacquired = self.store.acquire_lease(
            session["session_id"],
            self.process(),
            expected_lease_version=stale["process_lease"]["lease_version"],
        )
        self.assertNotEqual(acquired["lease_token"], reacquired["lease_token"])
        self.assertEqual("OWNED", reacquired["lease"]["lease_state"])

    def test_concurrent_default_updates_allow_only_one_cas_winner(self) -> None:
        first, _ = self.store.register_session(self.session("session-one"))
        second, _ = self.store.register_session(self.session("session-two"))
        self.store.set_default(first["session_id"], expected_pointer_version=0)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def update(session_id: str) -> None:
            barrier.wait()
            try:
                self.store.set_default(session_id, expected_pointer_version=1)
            except SessionSupervisorError as error:
                outcomes.append(error.code)
            else:
                outcomes.append("PASS")

        threads = [
            threading.Thread(target=update, args=(first["session_id"],)),
            threading.Thread(target=update, args=(second["session_id"],)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(1, outcomes.count("PASS"))
        self.assertEqual(1, outcomes.count("DEFAULT_SESSION_VERSION_CONFLICT"))


if __name__ == "__main__":
    unittest.main()
