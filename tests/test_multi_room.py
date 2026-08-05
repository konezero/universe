from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from session_supervisor import SessionSupervisorStore  # noqa: E402
from universe_multi_room import MultiRoomError, MultiRoomStore  # noqa: E402
from universe_server import (  # noqa: E402
    perform_session_ref_inject,
    supervisor_session_id_for,
)


class MultiRoomStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.temp.name) / "rooms.sqlite3")
        self.store = MultiRoomStore(self.db)
        self.supervisor = SessionSupervisorStore(Path(self.db))

    def tearDown(self) -> None:
        self.store = None
        self.supervisor = None
        self.temp.cleanup()

    def test_project_room_and_master_attach_bridge(self) -> None:
        room = self.store.ensure_project_room("proj_demo")
        self.assertEqual("PROJECT", room["room_type"])
        attached = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "GROK",
                "provider_session_ref": "sess-abc",
                "display_name": "Master",
            },
        )
        self.assertEqual("ROOM_SESSION_ATTACHED", attached["status"])
        self.assertIn("bridge_line", attached)
        self.assertEqual("UNASSIGNED", attached["authority"])
        snap = self.store.room_snapshot(room["room_id"])
        self.assertTrue(snap["user_may_write"])
        self.assertEqual(1, len(snap["bindings"]))

    def test_boss_room_user_cannot_write(self) -> None:
        room = self.store.create_boss_room(
            project_id="proj_demo",
            task_frame_id="tf_1",
            boss_session={"provider": "CLAUDE", "provider_session_ref": "boss-1"},
        )
        self.assertEqual("BOSS", room["room_type"])
        with self.assertRaises(MultiRoomError) as ctx:
            self.store.post_message(
                room["room_id"],
                {"author_role": "USER", "body_text": "do this now"},
            )
        self.assertEqual("ROOM_WRITE_FORBIDDEN", ctx.exception.code)
        report = self.store.worker_report(
            room["room_id"],
            {"body_text": "blocked on API", "severity": "BLOCKER"},
        )
        self.assertEqual("WORKER_REPORT_RECORDED", report["status"])
        called = self.store.call_master(
            room["room_id"],
            {"reason": "need scope change", "auto_attach_master": True},
        )
        self.assertEqual("MASTER_CALLED", called["status"])
        roles = {b["slot_role"] for b in self.store.list_bindings(room["room_id"])}
        self.assertIn("BOSS", roles)
        self.assertIn("MASTER", roles)

    def test_meeting_room_skill_shape(self) -> None:
        created = self.store.create_meeting_room(
            {
                "title": "Model debate",
                "topic": "API shape",
                "project_id": "proj_demo",
                "models": [
                    {"provider": "GROK", "display_name": "Grok"},
                    {
                        "provider": "CLAUDE",
                        "session_ref": "claude-xyz",
                        "display_name": "Claude",
                    },
                ],
            }
        )
        self.assertEqual("MEETING_ROOM_CREATED", created["status"])
        room_id = created["room"]["room_id"]
        self.store.post_message(
            room_id,
            {"author_role": "USER", "body_text": "focus on security"},
        )
        messages = self.store.list_messages(room_id)
        self.assertEqual(1, len(messages))
        self.assertEqual("USER", messages[0]["author_role"])

    def test_session_ref_inject(self) -> None:
        injected = self.store.inject_session_ref(
            {
                "project_id": "proj_inject",
                "room_type": "PROJECT",
                "slot_role": "MASTER",
                "provider": "CODEX",
                "session_ref": "thread-99",
            }
        )
        self.assertEqual("SESSION_REF_INJECTED", injected["status"])
        self.assertEqual(
            "thread-99",
            injected["binding"]["provider_session_ref"],
        )

    def test_full_session_ref_inject_registers_supervisor_and_default(self) -> None:
        injected = perform_session_ref_inject(
            session_supervisor=self.supervisor,
            multi_rooms=self.store,
            body={
                "project_id": "proj_full_inject",
                "room_type": "PROJECT",
                "slot_role": "MASTER",
                "provider": "CODEX",
                "session_ref": "thread-full-1",
            },
        )
        self.assertEqual("SESSION_REF_INJECTED", injected["status"])
        self.assertTrue(injected["supervisor_session_created"])
        expected_id = supervisor_session_id_for(
            node="proj_full_inject",
            mode="MASTER",
            provider="CODEX",
            provider_session_ref="thread-full-1",
        )
        self.assertEqual(expected_id, injected["supervisor_session"]["session_id"])
        self.assertTrue(injected["supervisor_session"]["is_default"])
        self.assertEqual(
            expected_id,
            injected["binding"]["supervisor_session_id"],
        )
        self.assertEqual(
            "thread-full-1",
            injected["binding"]["provider_session_ref"],
        )
        listed = self.supervisor.list_sessions(
            node="proj_full_inject", mode="MASTER"
        )
        self.assertEqual(1, len(listed))
        self.assertTrue(listed[0]["is_default"])

        # Idempotent re-inject reuses Supervisor row and rebinds room slot.
        again = perform_session_ref_inject(
            session_supervisor=self.supervisor,
            multi_rooms=self.store,
            body={
                "project_id": "proj_full_inject",
                "provider": "CODEX",
                "session_ref": "thread-full-1",
            },
        )
        self.assertFalse(again["supervisor_session_created"])
        self.assertEqual(expected_id, again["binding"]["supervisor_session_id"])

    def test_inject_model_slot_skips_default_by_default(self) -> None:
        room = self.store.create_meeting_room(
            {
                "title": "Inject meeting",
                "topic": "refs",
                "project_id": "proj_meet",
                "models": [{"provider": "GROK", "display_name": "Grok"}],
            }
        )
        room_id = room["room"]["room_id"]
        injected = perform_session_ref_inject(
            session_supervisor=self.supervisor,
            multi_rooms=self.store,
            body={
                "room_id": room_id,
                "project_id": "proj_meet",
                "room_type": "MEETING",
                "slot_role": "MODEL",
                "provider": "CLAUDE",
                "session_ref": "claude-meet-1",
                "display_name": "Claude",
            },
        )
        self.assertEqual("SESSION_REF_INJECTED", injected["status"])
        self.assertFalse(injected["make_default"])
        self.assertFalse(injected["supervisor_session"]["is_default"])
        self.assertEqual("MODEL", injected["binding"]["slot_role"])


if __name__ == "__main__":
    unittest.main()
