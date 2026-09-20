import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from persona_automation import PersonaAutomationStore, PersonaAutomationError
from universe_server import STALL_SECONDS, UniverseHTTPServer


def old(minutes=11):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def old_seconds(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class Bus:
    def __init__(self, messages=None): self.messages = messages or []
    def inbox(self, *_args, **_kwargs): return {"messages": self.messages}
    def transition(self, message_id, **kwargs):
        for row in self.messages:
            if row["message_id"] == message_id:
                row["lifecycle_state"] = kwargs["state"]
                return dict(row)
        raise AssertionError(message_id)


class Rooms:
    def __init__(self, events): self.events = events
    def list_room_events(self, *_args, **_kwargs): return self.events


class StallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = UniverseHTTPServer.__new__(UniverseHTTPServer)
        self.server.persona_automation = PersonaAutomationStore(Path(self.tmp.name) / "db.sqlite3")
        created = self.server.persona_automation.start_run(
            {"project_id": "P", "session_anchor_ref": "anchor-1", "scope": "test",
             "instruction": "test", "request_id": "start-1"},
            {"state": "ACTIVE", "project_id": "P", "session_anchor_ref": "anchor-1",
             "persona_id": "p", "persona_revision": 1, "assignment_revision": 1},
        )
        self.run_id = created["run"]["run_id"]
        self.server.session_bus = Bus()
        self.server.multi_rooms = Rooms([])
        self.server._persona_task_frame_state_root = lambda: Path(self.tmp.name)
        self.server._session_anchor_terminal_host = lambda: object()

    def tearDown(self): self.tmp.cleanup()

    def run_data(self, state="RUNNING"):
        return {"run_id": self.run_id, "session_anchor_ref": "anchor-1", "state": state}

    def test_queue_blocked_has_threshold_and_diagnostic(self):
        queued = {"message_id": "queued", "kind": "INSTRUCTION", "lifecycle_state": "QUEUED", "created_at": old_seconds(STALL_SECONDS - 1), "recipient_anchor_ref": "anchor-1", "run_id": self.run_id}
        active = {"message_id": "blocker", "kind": "INSTRUCTION", "lifecycle_state": "STARTED", "created_at": old_seconds(STALL_SECONDS - 1), "thread_id": "t", "body_text": "x", "recipient_anchor_ref": "anchor-1", "run_id": self.run_id}
        self.server.session_bus = Bus([queued, active])
        self.assertIsNone(self.server._persona_automation_stall(self.run_data()))
        queued["created_at"] = active["created_at"] = old_seconds(STALL_SECONDS + 1)
        stall = self.server._persona_automation_stall(self.run_data())
        self.assertEqual("QUEUE_BLOCKED", stall["items"][0]["kind"])

        other_run = {**active, "message_id": "other-run", "run_id": "different-run"}
        wrong_anchor = {**active, "message_id": "wrong-anchor", "recipient_anchor_ref": "different-anchor"}
        self.server.session_bus.messages.extend([other_run, wrong_anchor])
        with self.assertRaises(PersonaAutomationError):
            self.server._clear_persona_automation_blocker({"run_id": self.run_id, "message_id": "other-run", "request_id": "other"})
        with self.assertRaises(PersonaAutomationError):
            self.server._clear_persona_automation_blocker({"run_id": self.run_id, "message_id": "wrong-anchor", "request_id": "wrong"})

        missing_anchor = {**active, "message_id": "missing-anchor"}
        missing_anchor.pop("recipient_anchor_ref")
        self.server.session_bus.messages.append(missing_anchor)
        self.assertNotIn("missing-anchor", {
            item.get("message_id") for item in self.server._persona_automation_stall(self.run_data())["items"]
        })
        with self.assertRaises(PersonaAutomationError):
            self.server._clear_persona_automation_blocker({"run_id": self.run_id, "message_id": "missing-anchor", "request_id": "missing"})

    def test_permission_pending_and_orphaned_have_threshold(self):
        body = {"schema": "universe.task-frame-host-permission-request.v1", "request_id": "req", "role": "WORKER", "targets": ["x"]}
        event = {"message": {"body_text": json.dumps(body), "created_at": old(11)}}
        self.server.multi_rooms = Rooms([event])
        self.server.persona_automation.list_host_frames = lambda _run: [{"task_frame_id": "frame", "room_id": "room", "launched_at": old(11)}]
        self.server._persona_task_frame_state_root = lambda: Path(self.tmp.name)
        with patch("universe_server.task_frame_host_status", return_value={"pid_alive": True, "phase": "WAITING"}):
            event["message"]["created_at"] = old_seconds(STALL_SECONDS - 1)
            self.server.persona_automation.list_host_frames = lambda _run: [{"task_frame_id": "frame", "room_id": "room", "launched_at": old_seconds(STALL_SECONDS - 1)}]
            self.assertIsNone(self.server._persona_automation_stall(self.run_data("STOPPED")))
            event["message"]["created_at"] = old_seconds(STALL_SECONDS + 1)
            self.server.persona_automation.list_host_frames = lambda _run: [{"task_frame_id": "frame", "room_id": "room", "launched_at": old_seconds(STALL_SECONDS + 1)}]
            stall = self.server._persona_automation_stall(self.run_data("STOPPED"))
        self.assertEqual({"HOST_PERMISSION_PENDING", "HOST_ORPHANED"}, {i["kind"] for i in stall["items"]})

    def test_clear_only_reported_blocker_and_is_idempotent(self):
        blocker = {"message_id": "blocker", "kind": "INSTRUCTION", "lifecycle_state": "STARTED", "created_at": old(11), "recipient_anchor_ref": "anchor-1", "run_id": self.run_id}
        queued = {"message_id": "queued", "kind": "INSTRUCTION", "lifecycle_state": "QUEUED", "created_at": old(11), "recipient_anchor_ref": "anchor-1", "run_id": self.run_id}
        self.server.session_bus = Bus([queued, blocker])
        value = {"run_id": self.run_id, "message_id": "blocker", "request_id": "req-1"}
        first = self.server._clear_persona_automation_blocker(value)
        self.assertEqual("CANCELLED", first["message"]["lifecycle_state"])
        self.assertEqual("CANCELLED", self.server.session_bus.messages[1]["lifecycle_state"])
        with self.assertRaises(PersonaAutomationError):
            self.server._clear_persona_automation_blocker({**value, "message_id": "queued", "request_id": "req-2"})
        second = self.server._clear_persona_automation_blocker(value)
        self.assertEqual("PERSONA_AUTOMATION_BLOCKER_CLEARED_REPLAYED", second["status"])
        events = self.server.persona_automation.events(self.run_id, 50)
        self.assertEqual(1, sum(e.get("event_type") == "BLOCKER_CLEARED" for e in events))

    def test_threshold_is_strictly_greater_than_600_for_each_kind(self):
        fixed_now = datetime.now(timezone.utc)
        exact = (fixed_now - timedelta(seconds=STALL_SECONDS)).isoformat()
        over = (fixed_now - timedelta(seconds=STALL_SECONDS + 1)).isoformat()
        with patch("universe_server.datetime") as clock:
            clock.now.return_value = fixed_now
            clock.fromisoformat.side_effect = datetime.fromisoformat

            self.server.session_bus = Bus([
                {"message_id": "queued", "kind": "INSTRUCTION", "lifecycle_state": "QUEUED",
                 "created_at": exact, "recipient_anchor_ref": "anchor-1", "run_id": self.run_id},
                {"message_id": "blocker", "kind": "INSTRUCTION", "lifecycle_state": "STARTED",
                 "created_at": exact, "recipient_anchor_ref": "anchor-1", "run_id": self.run_id},
            ])
            self.assertIsNone(self.server._persona_automation_stall(self.run_data()))
            for message in self.server.session_bus.messages:
                message["created_at"] = over
            self.assertEqual("QUEUE_BLOCKED", self.server._persona_automation_stall(self.run_data())["items"][0]["kind"])

            self.server.session_bus = Bus([])
            body = {"schema": "universe.task-frame-host-permission-request.v1", "request_id": "req", "role": "WORKER"}
            event = {"message": {"body_text": json.dumps(body), "created_at": exact}}
            self.server.multi_rooms = Rooms([event])
            self.server.persona_automation.list_host_frames = lambda _run: [
                {"task_frame_id": "frame", "room_id": "room", "launched_at": exact}
            ]
            with patch("universe_server.task_frame_host_status", return_value={"pid_alive": True, "phase": "WAITING"}):
                self.assertIsNone(self.server._persona_automation_stall(self.run_data("STOPPED")))
                event["message"]["created_at"] = over
                self.server.persona_automation.list_host_frames = lambda _run: [
                    {"task_frame_id": "frame", "room_id": "room", "launched_at": over}
                ]
                kinds = {item["kind"] for item in self.server._persona_automation_stall(self.run_data("STOPPED"))["items"]}
            self.assertEqual({"HOST_PERMISSION_PENDING", "HOST_ORPHANED"}, kinds)


if __name__ == "__main__": unittest.main()
