from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from session_supervisor import SessionSupervisorStore  # noqa: E402
from universe_multi_room import (  # noqa: E402
    MultiRoomDeliveryCoordinator,
    MultiRoomError,
    MultiRoomMeetingCoordinator,
    MultiRoomNativeControlRegistry,
    MultiRoomStore,
)
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
                "session_anchor_ref": "MASTER-CURRENT-PROJ-DEMO",
                "display_name": "Master",
            },
        )
        self.assertEqual("ROOM_SESSION_ATTACHED", attached["status"])
        self.assertIn("bridge_line", attached)
        self.assertEqual("UNASSIGNED", attached["authority"])
        snap = self.store.room_snapshot(room["room_id"])
        self.assertTrue(snap["user_may_write"])
        self.assertEqual(1, len(snap["bindings"]))
        self.assertEqual(
            "MASTER-CURRENT-PROJ-DEMO",
            snap["bindings"][0]["session_anchor_ref"],
        )

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

    def test_round_robin_meeting_is_bounded_and_incremental(self) -> None:
        created = self.store.create_meeting_room(
            {
                "title": "Bounded round robin",
                "topic": "delta delivery",
                "models": [
                    {"provider": "CODEX", "display_name": "Codex"},
                    {"provider": "CLAUDE", "display_name": "Claude"},
                ],
            }
        )
        room_id = created["room"]["room_id"]
        model_bindings = sorted(
            [
                binding
                for binding in self.store.list_bindings(room_id)
                if binding["slot_role"] == "MODEL"
            ],
            key=lambda item: (item["created_at"], item["binding_id"]),
        )
        received: list[dict[str, object]] = []

        def invoke(binding, turn):
            received.append(
                {
                    "binding_id": binding["binding_id"],
                    "turn_number": turn["turn_number"],
                    "delta": turn["delta"]["body_text"],
                    "has_messages": "messages" in turn,
                    "transcript_forwarded": turn["transcript_forwarded"],
                }
            )
            return {
                "status": "COMPLETED",
                "body_text": f"delta-{turn['turn_number']}",
                "provider_event_id": f"provider-event-{turn['turn_number']}",
            }

        coordinator = MultiRoomMeetingCoordinator(self.store, invoke)
        summary = coordinator.run(
            room_id,
            prompt="seed question",
            max_turns=5,
            run_id="meeting-delta-1",
        )

        self.assertEqual("COMPLETED", summary["status"])
        self.assertEqual(5, summary["turn_count"])
        self.assertEqual("TURN_BOUNDARY_FAIL_CLOSED", summary["cancel_policy"])
        self.assertEqual(
            [
                model_bindings[index % len(model_bindings)]["binding_id"]
                for index in range(5)
            ],
            [item["binding_id"] for item in received],
        )
        self.assertEqual(
            [
                "seed question",
                "delta-0",
                "delta-1",
                "delta-2",
                "delta-3",
            ],
            [item["delta"] for item in received],
        )
        self.assertTrue(all(not item["has_messages"] for item in received))
        self.assertTrue(all(item["transcript_forwarded"] is False for item in received))
        self.assertEqual(
            summary,
            coordinator.summary(room_id, "meeting-delta-1"),
        )
        control_events = self.store.list_control_events(room_id)
        self.assertEqual(
            1,
            len(
                [
                    event
                    for event in control_events
                    if event["event_type"] == "MEETING_SUMMARY"
                ]
            ),
        )

    def test_round_robin_meeting_cancels_at_turn_boundary(self) -> None:
        room_id = self.store.create_meeting_room(
            {
                "title": "Cancelable meeting",
                "models": [{"provider": "GROK", "display_name": "Grok"}],
            }
        )["room"]["room_id"]
        calls: list[int] = []

        def invoke(_binding, turn):
            calls.append(int(turn["turn_number"]))
            return {"status": "COMPLETED", "body_text": "next delta"}

        coordinator = MultiRoomMeetingCoordinator(self.store, invoke)
        cancellation = coordinator.cancel("meeting-cancel-1", reason="user stop")
        self.assertEqual("MEETING_CANCEL_REQUESTED", cancellation["status"])
        summary = coordinator.run(
            room_id,
            prompt="cancel before provider call",
            max_turns=4,
            run_id="meeting-cancel-1",
        )
        self.assertEqual("INTERRUPTED", summary["status"])
        self.assertEqual("user stop", summary["reason"])
        self.assertEqual("TURN_BOUNDARY_FAIL_CLOSED", summary["cancel_policy"])
        self.assertEqual(0, summary["turn_count"])
        self.assertEqual([], calls)

    def test_round_robin_meeting_allows_only_one_active_run_per_room(self) -> None:
        room_id = self.store.create_meeting_room(
            {
                "title": "Single flight",
                "models": [{"provider": "CODEX", "display_name": "Codex"}],
            }
        )["room"]["room_id"]
        entered = threading.Event()
        release = threading.Event()

        def invoke(_binding, _turn):
            entered.set()
            self.assertTrue(release.wait(2))
            return {"status": "COMPLETED", "body_text": "done"}

        coordinator = MultiRoomMeetingCoordinator(self.store, invoke)
        result: list[dict[str, object]] = []
        worker = threading.Thread(
            target=lambda: result.append(
                coordinator.run(room_id, prompt="first", max_turns=1)
            )
        )
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(MultiRoomError) as context:
                coordinator.run(room_id, prompt="second", max_turns=1)
            self.assertEqual("MEETING_ROOM_BUSY", context.exception.code)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual("COMPLETED", result[0]["status"])

    def test_room_events_are_monotonic_and_provider_events_are_idempotent(self) -> None:
        room = self.store.create_meeting_room(
            {
                "title": "Live room",
                "topic": "incremental routing",
                "models": [{"provider": "CODEX", "display_name": "Codex"}],
            }
        )["room"]
        first = self.store.post_message(
            room["room_id"],
            {
                "author_role": "MODEL",
                "body_text": "first delta",
                "provider_event_id": "codex-event-1",
            },
        )
        duplicate = self.store.post_message(
            room["room_id"],
            {
                "author_role": "MODEL",
                "body_text": "duplicate delta",
                "provider_event_id": "codex-event-1",
            },
        )
        second = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "next input"},
        )

        self.assertEqual(first["message_id"], duplicate["message_id"])
        self.assertEqual(1, first["room_sequence"])
        self.assertEqual(2, second["room_sequence"])
        events = self.store.list_room_events(room["room_id"])
        self.assertEqual([1, 2], [item["room_sequence"] for item in events])

    def test_incremental_delivery_uses_independent_cursors_and_suppresses_echo(self) -> None:
        room = self.store.create_meeting_room(
            {
                "title": "Models",
                "topic": "fanout",
                "models": [
                    {"provider": "CODEX", "display_name": "Codex"},
                    {"provider": "CLAUDE", "display_name": "Claude"},
                ],
            }
        )["room"]
        bindings = [
            binding
            for binding in self.store.list_bindings(room["room_id"])
            if binding["slot_role"] == "MODEL"
        ]
        self.assertEqual(2, len(bindings))
        codex, claude = bindings
        self.store.set_participant_state(codex["binding_id"], "LIVE")
        self.store.set_participant_state(claude["binding_id"], "LIVE")
        own = self.store.post_message(
            room["room_id"],
            {
                "author_role": "MODEL",
                "author_binding_id": codex["binding_id"],
                "body_text": "Codex output",
                "provider_event_id": "codex-output-1",
            },
        )
        user = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "Review this"},
        )
        delivered_to: list[tuple[str, str]] = []

        def send(binding, event):
            delivered_to.append((binding["binding_id"], event["room_event_id"]))
            return {"status": "ACCEPTED", "provider_cursor": "native-1"}

        coordinator = MultiRoomDeliveryCoordinator(self.store, send)
        codex_result = coordinator.deliver_binding(codex["binding_id"])
        claude_result = coordinator.deliver_binding(claude["binding_id"])

        self.assertEqual(
            [(codex["binding_id"], user["room_event_id"])],
            [item for item in delivered_to if item[0] == codex["binding_id"]],
        )
        self.assertEqual(
            [own["room_event_id"], user["room_event_id"]],
            [item[1] for item in delivered_to if item[0] == claude["binding_id"]],
        )
        self.assertEqual(2, codex_result["cursor"]["delivery_sequence"])
        self.assertEqual(2, claude_result["cursor"]["delivery_sequence"])

    def test_uncertain_delivery_blocks_retry_until_explicit_resolution(self) -> None:
        room = self.store.create_meeting_room(
            {
                "title": "Uncertain",
                "topic": "provider acceptance",
                "models": [{"provider": "GROK", "display_name": "Grok"}],
            }
        )["room"]
        binding = self.store.list_bindings(room["room_id"])[0]
        self.store.set_participant_state(binding["binding_id"], "CONTROLLED")
        event = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "hello"},
        )
        calls: list[str] = []

        def uncertain(_binding, room_event):
            calls.append(room_event["room_event_id"])
            return {"status": "UNCERTAIN"}

        coordinator = MultiRoomDeliveryCoordinator(self.store, uncertain)
        first = coordinator.deliver_binding(binding["binding_id"])
        second = coordinator.deliver_binding(binding["binding_id"])

        self.assertEqual("PARTICIPANT_DELIVERY_BLOCKED", first["status"])
        self.assertEqual("PARTICIPANT_DELIVERY_BLOCKED", second["status"])
        self.assertEqual([event["room_event_id"]], calls)
        self.assertEqual([], second["delivered"])

    def test_native_control_queues_only_the_new_event_and_waits_for_acceptance(
        self,
    ) -> None:
        room = self.store.ensure_project_room("proj_native")
        binding = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "thread-native-1",
            },
        )["binding"]
        queued: list[dict[str, object]] = []
        registry = MultiRoomNativeControlRegistry(self.store)
        registered = registry.register(
            binding["binding_id"],
            provider="CODEX",
            provider_session_ref="thread-native-1",
            send_input=lambda _binding, event: queued.append(dict(event)) or True,
        )
        event = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "only this delta"},
        )

        result = MultiRoomDeliveryCoordinator(
            self.store,
            registry.send_input,
        ).deliver_binding(binding["binding_id"])

        self.assertEqual("CONTROLLED", registered["participant_state"])
        self.assertEqual("PARTICIPANT_DELIVERY_BLOCKED", result["status"])
        self.assertEqual("QUEUED", result["blocker"]["status"])
        self.assertEqual([event["room_event_id"]], [item["room_event_id"] for item in queued])
        self.assertEqual(
            0,
            self.store.participant_cursor(binding["binding_id"])[
                "delivery_sequence"
            ],
        )

        accepted = self.store.record_delivery_observation(
            binding["binding_id"],
            event["room_event_id"],
            status="ACCEPTED",
        )
        self.assertEqual(1, accepted["cursor"]["delivery_sequence"])
        self.assertTrue(registry.unregister(binding["binding_id"]))
        self.assertEqual(
            "DISCONNECTED",
            self.store.participant_cursor(binding["binding_id"])[
                "participant_state"
            ],
        )

    def test_native_control_identity_mismatch_fails_closed(self) -> None:
        room = self.store.ensure_project_room("proj_identity")
        binding = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-session-1",
            },
        )["binding"]
        registry = MultiRoomNativeControlRegistry(self.store)

        with self.assertRaises(MultiRoomError) as provider_error:
            registry.register(
                binding["binding_id"],
                provider="CODEX",
                provider_session_ref="claude-session-1",
                send_input=lambda _binding, _event: True,
            )
        self.assertEqual(
            "NATIVE_CONTROL_PROVIDER_MISMATCH",
            provider_error.exception.code,
        )
        with self.assertRaises(MultiRoomError) as session_error:
            registry.register(
                binding["binding_id"],
                provider="CLAUDE",
                provider_session_ref="other-session",
                send_input=lambda _binding, _event: True,
            )
        self.assertEqual(
            "NATIVE_CONTROL_SESSION_MISMATCH",
            session_error.exception.code,
        )

    def test_fast_native_acceptance_is_not_downgraded_to_queued(self) -> None:
        room = self.store.ensure_project_room("proj_race")
        binding = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "GROK",
                "provider_session_ref": "grok-session-1",
            },
        )["binding"]
        registry = MultiRoomNativeControlRegistry(self.store)

        def accept_inline(native_binding, event):
            self.store.record_delivery_observation(
                native_binding["binding_id"],
                event["room_event_id"],
                status="ACCEPTED",
            )
            return True

        registry.register(
            binding["binding_id"],
            provider="GROK",
            provider_session_ref="grok-session-1",
            send_input=accept_inline,
        )
        self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "race"},
        )
        result = MultiRoomDeliveryCoordinator(
            self.store,
            registry.send_input,
        ).deliver_binding(binding["binding_id"])

        self.assertEqual("PARTICIPANT_DELIVERY_COMPLETED", result["status"])
        self.assertEqual("ACCEPTED", result["delivered"][0]["status"])
        self.assertEqual(1, result["cursor"]["delivery_sequence"])

    def test_viewer_cursor_never_changes_participant_or_provider_cursor(self) -> None:
        room = self.store.ensure_project_room("proj_view")
        binding = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "thread-view",
            },
        )["binding"]
        self.store.record_provider_observation(
            binding["binding_id"], "provider-position-7"
        )
        before = self.store.participant_cursor(binding["binding_id"])
        viewed = self.store.update_viewer_cursor(room["room_id"], "browser-1", 9)
        after = self.store.participant_cursor(binding["binding_id"])

        self.assertEqual(9, viewed["room_sequence"])
        self.assertEqual(before, after)
        with self.assertRaises(MultiRoomError) as ctx:
            self.store.update_viewer_cursor(room["room_id"], "browser-1", 8)
        self.assertEqual("VIEWER_CURSOR_REGRESSION", ctx.exception.code)

    def test_new_participant_starts_after_existing_room_history(self) -> None:
        room = self.store.create_room(
            room_type="MEETING",
            title="Late join",
            host_role="CONDUCTOR",
        )
        old = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "before attach"},
        )
        binding = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MODEL",
                "provider": "CODEX",
                "participant_state": "LIVE",
            },
        )["binding"]
        calls: list[str] = []

        coordinator = MultiRoomDeliveryCoordinator(
            self.store,
            lambda _binding, event: (
                calls.append(event["room_event_id"]) or {"status": "ACCEPTED"}
            ),
        )
        self.assertEqual(
            old["room_sequence"],
            self.store.participant_cursor(binding["binding_id"])["delivery_sequence"],
        )
        coordinator.deliver_binding(binding["binding_id"])
        self.assertEqual([], calls)

        current = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "after attach"},
        )
        coordinator.deliver_binding(binding["binding_id"])
        self.assertEqual([current["room_event_id"]], calls)

    def test_singleton_replacement_retries_only_unaccepted_room_events(self) -> None:
        room = self.store.ensure_project_room("proj_master_restart")
        first = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "master-session-old",
            },
        )["binding"]
        delivered = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "already accepted"},
        )
        self.store.record_delivery_observation(
            first["binding_id"], delivered["room_event_id"], status="ACCEPTED"
        )
        pending = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "retry after restart"},
        )

        replacement = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "master-session-new",
                "participant_state": "LIVE",
            },
        )["binding"]
        received: list[str] = []
        self.assertEqual(
            1,
            self.store.participant_cursor(replacement["binding_id"])[
                "delivery_sequence"
            ],
        )
        result = MultiRoomDeliveryCoordinator(
            self.store,
            lambda _binding, event: (
                received.append(event["room_event_id"]) or {"status": "ACCEPTED"}
            ),
        ).deliver_binding(replacement["binding_id"])

        self.assertEqual(
            2,
            self.store.participant_cursor(replacement["binding_id"])[
                "delivery_sequence"
            ],
        )
        self.assertEqual([pending["room_event_id"]], received)
        self.assertEqual("PARTICIPANT_DELIVERY_COMPLETED", result["status"])

    def test_explicit_singleton_recovery_retries_prior_uncertain_delivery(self) -> None:
        room = self.store.ensure_project_room("proj_master_recovery")
        first = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "master-session-old",
            },
        )["binding"]
        pending = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "recover this delivery"},
        )
        self.store.record_delivery_observation(
            first["binding_id"], pending["room_event_id"], status="UNCERTAIN"
        )

        replacement = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "master-session-new",
                "participant_state": "LIVE",
                "resume_pending_delivery": True,
            },
        )["binding"]
        self.assertEqual(
            0,
            self.store.participant_cursor(replacement["binding_id"])[
                "delivery_sequence"
            ],
        )
        received: list[str] = []
        MultiRoomDeliveryCoordinator(
            self.store,
            lambda _binding, event: (
                received.append(event["room_event_id"]) or {"status": "ACCEPTED"}
            ),
        ).deliver_binding(replacement["binding_id"])

        self.assertEqual([pending["room_event_id"]], received)

    def test_legacy_room_rows_gain_events_and_non_replaying_cursors(self) -> None:
        legacy_db = Path(self.temp.name) / "legacy.sqlite3"
        now = "2026-08-09T00:00:00Z"
        message = {
            "schema": "universe.chat-room-message.v1",
            "message_id": "msg_legacy",
            "room_id": "room_legacy",
            "author_role": "USER",
            "author_binding_id": None,
            "body_text": "legacy history",
            "created_at": now,
        }
        with sqlite3.connect(legacy_db) as connection:
            connection.executescript(
                """
                CREATE TABLE chat_room (
                    room_id TEXT PRIMARY KEY, room_type TEXT NOT NULL,
                    project_id TEXT, task_frame_id TEXT, title TEXT NOT NULL,
                    host_role TEXT NOT NULL, state TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE chat_room_session (
                    binding_id TEXT PRIMARY KEY, room_id TEXT NOT NULL,
                    slot_role TEXT NOT NULL, provider TEXT,
                    provider_session_ref TEXT, supervisor_session_id TEXT,
                    display_name TEXT, state TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE chat_room_message (
                    message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, author_role TEXT NOT NULL,
                    author_binding_id TEXT, body_text TEXT NOT NULL,
                    message_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(room_id, idempotency_key)
                );
                CREATE TABLE chat_room_control_event (
                    event_id TEXT PRIMARY KEY, room_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO chat_room VALUES (?, 'PROJECT', 'legacy', NULL, ?, 'MASTER', 'OPEN', '{}', ?, ?)",
                ("room_legacy", "Legacy", now, now),
            )
            connection.execute(
                "INSERT INTO chat_room_session VALUES (?, ?, 'MASTER', 'CODEX', 'thread-1', NULL, 'Master', 'ACTIVE', '{}', ?, ?)",
                ("bind_legacy", "room_legacy", now, now),
            )
            connection.execute(
                "INSERT INTO chat_room_message VALUES (?, ?, ?, 'USER', NULL, ?, ?, ?)",
                (
                    "msg_legacy",
                    "room_legacy",
                    "idem_legacy",
                    "legacy history",
                    json.dumps(message),
                    now,
                ),
            )

        migrated = MultiRoomStore(str(legacy_db))
        events = migrated.list_room_events("room_legacy")
        cursor = migrated.participant_cursor("bind_legacy")
        snapshot = migrated.room_snapshot("room_legacy")

        self.assertEqual(1, len(events))
        self.assertEqual("msg_legacy", events[0]["message"]["message_id"])
        self.assertEqual(1, cursor["delivery_sequence"])
        self.assertEqual(1, len(snapshot["participant_cursors"]))

    def test_duplicate_accepted_delivery_is_idempotent_after_cursor_advance(self) -> None:
        room = self.store.create_room(
            room_type="MEETING",
            title="Duplicate acceptance",
            host_role="CONDUCTOR",
        )
        binding = self.store.attach_session(
            room["room_id"],
            {
                "slot_role": "MODEL",
                "provider": "CODEX",
                "participant_state": "LIVE",
            },
        )["binding"]
        first = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "first"},
        )
        second = self.store.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "second"},
        )
        self.store.record_delivery_observation(
            binding["binding_id"], first["room_event_id"], status="ACCEPTED"
        )
        self.store.record_delivery_observation(
            binding["binding_id"], second["room_event_id"], status="ACCEPTED"
        )
        duplicate = self.store.record_delivery_observation(
            binding["binding_id"], first["room_event_id"], status="ACCEPTED"
        )
        self.assertEqual(2, duplicate["cursor"]["delivery_sequence"])

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
        active = self.store.list_active_session_bindings()
        self.assertEqual(1, len(active))
        self.assertEqual("thread-99", active[0]["provider_session_ref"])
        self.assertEqual("PROJECT", active[0]["room_type"])
        room_id = injected["room"]["room_id"]
        self.store.post_message(
            room_id,
            {"author_role": "USER", "body_text": "first line about security"},
        )
        self.store.post_message(
            room_id,
            {"author_role": "MASTER", "body_text": "second line reply on auth"},
        )
        preview = self.store.preview_for_session(
            provider_session_ref="thread-99",
            project_id="proj_inject",
            limit=2,
            allow_project_fallback=False,
        )
        self.assertEqual("MULTI_ROOM", preview["source"])
        self.assertEqual("PROVIDER_REF", preview["match"])
        self.assertEqual(2, len(preview["lines"]))
        self.assertIn("second line", preview["lines"][-1]["text"])
        # Unrelated session id must not inherit this room's chat.
        orphan = self.store.preview_for_session(
            supervisor_session_id="session_does_not_exist",
            provider_session_ref="other-thread",
            project_id="proj_inject",
            allow_project_fallback=False,
        )
        self.assertEqual("NONE", orphan["source"])
        self.assertEqual([], orphan["lines"])

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
