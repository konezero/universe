"""Out-of-scope writes are asked upward; only an explicit Master APPROVE allows one."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from task_frame_host_permission import (  # noqa: E402
    PERMISSION_DECISION_SCHEMA,
    HostPermissionEscalator,
    parse_permission_decision,
)
from universe_runtime_worker_dispatch import RuntimeWorkerDispatcher  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Room:
    def __init__(self):
        self.reports, self.calls, self.events, self.fail = [], [], [], False
        self.by_poll = {}
        self.polls = 0

    def post_report(self, *, body_text, severity, idempotency_key):
        if self.fail:
            raise ConnectionError("down")
        self.reports.append((severity, json.loads(body_text)))

    def call_master(self, *, reason):
        self.calls.append(reason)

    def events_after(self, sequence):
        self.polls += 1
        self.events.extend(self.by_poll.get(self.polls, []))
        return [e for e in self.events if e["room_sequence"] > sequence]

    def is_open(self):
        return True


class Bus:
    def __init__(self):
        self.notices = []

    def notify(self, *, idempotency_key, body_text, payload):
        self.notices.append((idempotency_key, payload))


def decision(sequence, request_id, verdict, author="MASTER"):
    body = {"schema": PERMISSION_DECISION_SCHEMA, "request_id": request_id, "decision": verdict}
    return {"room_sequence": sequence, "message": {"author_role": author, "body_text": json.dumps(body)}}


DESCRIPTION = {"tool": "Write", "targets": ["C:/p/a.py"], "operations": ["CREATE"], "destructive": False}


class EscalatorTests(unittest.TestCase):
    def make(self, room, **kwargs):
        clock = Clock()
        bus = Bus()
        escalator = HostPermissionEscalator(
            task_frame_id="tf1", todo_id="todo_1", room=room, bus=bus,
            timeout_seconds=kwargs.get("timeout", 20.0), poll_interval_seconds=1.0,
            clock=clock, sleep=clock.sleep,
        )
        return escalator, bus

    def request_id(self, room):
        return room.reports[-1][1]["request_id"]

    def test_the_request_reaches_the_room_and_the_master_then_waits_for_the_decision(self):
        room = Room()
        escalator, bus = self.make(room)

        # The Master answers once the request is in the room.
        def events_after(sequence):
            if room.reports and not room.events:
                room.events.append(decision(1, self.request_id(room), "APPROVE"))
            return [e for e in room.events if e["room_sequence"] > sequence]

        room.events_after = events_after
        self.assertEqual("APPROVE", escalator(DESCRIPTION))
        severity, payload = room.reports[0]
        self.assertEqual("PERMISSION", severity)
        self.assertEqual(["C:/p/a.py"], payload["targets"])
        self.assertEqual("WORKER", payload["role"])
        self.assertFalse(payload["destructive"])
        self.assertEqual(1, len(bus.notices))
        self.assertEqual(1, len(room.calls))

    def test_deny_timeout_and_unreachable_room_are_all_denials(self):
        room = Room()
        escalator, _ = self.make(room, timeout=5.0)
        self.assertEqual("DENY", escalator(DESCRIPTION), "no answer in time")
        down = Room()
        down.fail = True
        escalator, _ = self.make(down)
        self.assertEqual("DENY", escalator(DESCRIPTION), "room unreachable")

        room = Room()
        escalator, _ = self.make(room)

        def events_after(sequence):
            if room.reports and not room.events:
                room.events.append(decision(1, self.request_id(room), "DENY"))
            return [e for e in room.events if e["room_sequence"] > sequence]

        room.events_after = events_after
        self.assertEqual("DENY", escalator(DESCRIPTION))

    def test_with_no_timeout_it_waits_as_long_as_the_todo_and_room_live(self):
        clock, bus, room = Clock(), Bus(), Room()
        state = {"alive": True}
        escalator = HostPermissionEscalator(
            task_frame_id="tf1", todo_id="todo_1", room=room, bus=bus, timeout_seconds=None,
            poll_interval_seconds=1.0, alive=lambda: state["alive"], clock=clock, sleep=clock.sleep,
        )

        def events_after(sequence):
            # The operator answers through the Conductor and Master a long while later.
            if clock.now >= 5000 and not room.events:
                room.events.append(decision(1, self.request_id(room), "APPROVE"))
            return [e for e in room.events if e["room_sequence"] > sequence]

        room.events_after = events_after
        self.assertEqual("APPROVE", escalator(DESCRIPTION))
        self.assertGreaterEqual(clock.now, 5000)

        # The Todo ending while it waits is a denial, not an endless wait.
        room2 = Room()
        escalator = HostPermissionEscalator(
            task_frame_id="tf1", todo_id="todo_1", room=room2, bus=bus, timeout_seconds=None,
            poll_interval_seconds=1.0, alive=lambda: room2.polls < 3, clock=clock, sleep=clock.sleep,
        )
        self.assertEqual("DENY", escalator(DESCRIPTION))

    def test_only_the_masters_reply_to_this_exact_request_counts(self):
        self.assertIsNone(parse_permission_decision(decision(1, "perm_a", "APPROVE", author="WORKER"), "perm_a"))
        self.assertIsNone(parse_permission_decision(decision(1, "perm_a", "APPROVE"), "perm_b"))
        self.assertIsNone(parse_permission_decision(decision(1, "perm_a", "MAYBE"), "perm_a"))
        self.assertEqual("APPROVE", parse_permission_decision(decision(1, "perm_a", "approve"), "perm_a"))
        room = Room()
        escalator, _ = self.make(room, timeout=4.0)

        def events_after(sequence):
            if room.reports and not room.events:
                room.events.extend([
                    decision(1, self.request_id(room), "APPROVE", author="WORKER"),
                    decision(2, "perm_someone_else", "APPROVE"),
                ])
            return [e for e in room.events if e["room_sequence"] > sequence]

        room.events_after = events_after
        self.assertEqual("DENY", escalator(DESCRIPTION))

    def test_a_destructive_request_is_marked_for_the_conductor(self):
        room = Room()
        escalator, bus = self.make(room, timeout=2.0)
        escalator({"tool": "item/fileChange/requestApproval", "targets": ["C:/p/a.py"],
                   "operations": ["DELETE"], "destructive": True})
        self.assertTrue(room.reports[0][1]["destructive"])
        self.assertIn("Conductor", room.calls[0])


class DispatcherEscalationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dispatcher = RuntimeWorkerDispatcher(Path(self.temp.name))
        self.target = str(Path(self.temp.name, "in.py").resolve())
        self.other = str(Path(self.temp.name, "out.py").resolve())
        self.options = [
            {"kind": "allow_once", "optionId": "allow"},
            {"kind": "reject_once", "optionId": "reject"},
        ]

    def tearDown(self):
        self.temp.cleanup()

    def request(self, scope="BOUNDED"):
        return {"repository_write_scope": scope,
                "mutation_scope": {"operations": ["CREATE", "MODIFY"], "targets": [self.target]}}

    def write(self, path, tool="Write"):
        return {"options": self.options,
                "tool_call": {"toolName": tool, "input": {"file_path": path}, "cwd": self.temp.name}}

    def change(self, kind, path, move=None):
        change = {"type": kind, "path": path}
        if move:
            change["move_path"] = move
        return {"options": self.options,
                "tool_call": {"toolName": "item/fileChange/requestApproval", "fileChanges": [change],
                              "cwd": self.temp.name}}

    def test_in_scope_writes_never_ask_and_out_of_scope_ones_do(self):
        asked = []
        self.dispatcher.permission_escalator = lambda d: asked.append(d) or "APPROVE"
        self.assertEqual("allow", self.dispatcher._task_frame_permission(self.request(), self.write(self.target)))
        self.assertEqual([], asked)
        self.assertEqual("allow", self.dispatcher._task_frame_permission(self.request(), self.write(self.other)))
        self.assertEqual([self.other], asked[0]["targets"])
        self.assertEqual(["CREATE"], asked[0]["operations"])
        self.assertFalse(asked[0]["destructive"])

    def test_without_an_escalator_or_a_verdict_the_write_stays_refused(self):
        self.assertEqual("reject", self.dispatcher._task_frame_permission(self.request(), self.write(self.other)))
        self.dispatcher.permission_escalator = lambda d: "DENY"
        self.assertEqual("reject", self.dispatcher._task_frame_permission(self.request(), self.write(self.other)))

        def broken(_d):
            raise RuntimeError("boom")

        self.dispatcher.permission_escalator = broken
        self.assertEqual("reject", self.dispatcher._task_frame_permission(self.request(), self.write(self.other)))

    def test_a_read_only_turn_and_non_write_tools_are_never_escalated(self):
        asked = []
        self.dispatcher.permission_escalator = lambda d: asked.append(d) or "APPROVE"
        self.assertEqual("reject", self.dispatcher._task_frame_permission(self.request("NONE"), self.write(self.other)))
        shell = {"options": self.options, "tool_call": {"toolName": "Bash", "input": {"command": "rm -rf x"}}}
        self.assertEqual("reject", self.dispatcher._task_frame_permission(self.request(), shell))
        self.assertEqual([], asked)

    def test_delete_and_move_are_described_as_destructive(self):
        asked = []
        self.dispatcher.permission_escalator = lambda d: asked.append(d) or "DENY"
        self.dispatcher._task_frame_permission(self.request(), self.change("delete", self.target))
        self.dispatcher._task_frame_permission(self.request(), self.change("update", self.target, move=self.other))
        self.assertEqual([True, True], [d["destructive"] for d in asked])
        self.assertEqual(["DELETE"], asked[0]["operations"])
        self.assertEqual(["MOVE"], asked[1]["operations"])


if __name__ == "__main__":
    unittest.main()
