"""Task Frame Host lifecycle: Todo-bound lifetime, Master-driven, failure-tolerant."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from task_frame_host import (  # noqa: E402
    DIRECTIVE_SCHEMA,
    HostConfig,
    RoleResult,
    TaskFrameHost,
    parse_directive,
)

FRAME = "persona_tf_test0001"
TODO = "todo_test0001"


def directive(sequence, name, role=None, feedback=None, author="MASTER"):
    body = {"schema": DIRECTIVE_SCHEMA, "directive": name}
    if role:
        body["role"] = role
    if feedback:
        body["feedback"] = feedback
    return {"room_sequence": sequence, "message": {"author_role": author, "body_text": json.dumps(body)}}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeRoom:
    def __init__(self, scripted=None):
        self.reports, self.calls = [], []
        self.events = []          # every event ever posted, in order
        self.scripted = scripted or {}   # poll number -> events to appear
        self.polls = 0
        self.open = True
        self.fail = False

    def post_report(self, *, body_text, severity, idempotency_key):
        if self.fail:
            raise ConnectionError("room down")
        self.reports.append((idempotency_key, severity, body_text))

    def call_master(self, *, reason):
        if self.fail:
            raise ConnectionError("room down")
        self.calls.append(reason)

    def events_after(self, sequence):
        if self.fail:
            raise ConnectionError("room down")
        self.polls += 1
        self.events.extend(self.scripted.get(self.polls, []))
        return [e for e in self.events if e["room_sequence"] > sequence]

    def is_open(self):
        return self.open


class FakeBus:
    def __init__(self):
        self.notices = []
        self.fail = False

    def notify(self, *, idempotency_key, body_text, payload):
        if self.fail:
            raise ConnectionError("bus down")
        self.notices.append((idempotency_key, payload))


class FakeTodo:
    def __init__(self, script=None):
        self.script = script or {}
        self.calls = 0
        self.current = ("IN_PROGRESS", False)

    def state(self):
        self.calls += 1
        self.current = self.script.get(self.calls, self.current)
        return self.current


class ScriptedRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def run(self, role, *, attempt, feedback):
        self.calls.append((role, attempt, feedback))
        outcome = self.outcomes.pop(0) if self.outcomes else RoleResult("COMPLETED")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_host(runner, room=None, bus=None, todo=None, **config):
    clock = FakeClock()
    room = room or FakeRoom()
    bus = bus or FakeBus()
    todo = todo or FakeTodo()
    host = TaskFrameHost(
        HostConfig(task_frame_id=FRAME, todo_id=TODO, poll_interval_seconds=1.0, **config),
        runner=runner, room=room, bus=bus, todo=todo, clock=clock, sleep=clock.sleep,
    )
    return host, room, bus, todo, clock


class TaskFrameHostLifecycleTests(unittest.TestCase):
    def test_a_finished_role_is_reported_and_the_host_waits_for_the_master(self):
        runner = ScriptedRunner([RoleResult("COMPLETED", "worker done", "task-frame-result://f/worker/1", "a" * 64)])
        room = FakeRoom(scripted={3: [directive(1, "DONE")]})
        host, room, bus, _todo, _clock = make_host(runner, room=room)
        outcome = host.run()
        # It did NOT exit after the result: it waited (polled) until the Master said DONE.
        self.assertEqual("MASTER_DONE", outcome.exit_reason)
        self.assertGreaterEqual(room.polls, 3)
        self.assertEqual([("WORKER", 1, "COMPLETED")], outcome.roles_run)
        key, payload = bus.notices[0]
        self.assertEqual(f"host:{FRAME}:WORKER:1:COMPLETED", key)
        self.assertEqual("task-frame-result://f/worker/1", payload["result_ref"])
        self.assertEqual("a" * 64, payload["result_digest"])
        self.assertEqual(TODO, payload["todo_id"])
        self.assertEqual("INFO", room.reports[0][1])
        self.assertEqual([], room.calls)

    def test_master_directs_the_reviewer_and_rework_with_feedback(self):
        runner = ScriptedRunner([RoleResult("COMPLETED"), RoleResult("COMPLETED"), RoleResult("COMPLETED")])
        room = FakeRoom(scripted={1: [directive(1, "RUN_ROLE", "REVIEWER")],
                                  2: [directive(2, "REWORK", "WORKER", "cover the empty case")],
                                  3: [directive(3, "DONE")]})
        host, _room, bus, _todo, _clock = make_host(runner, room=room)
        outcome = host.run()
        self.assertEqual("MASTER_DONE", outcome.exit_reason)
        self.assertEqual(
            [("WORKER", 1, None), ("REVIEWER", 1, None), ("WORKER", 2, "cover the empty case")],
            runner.calls,
        )
        self.assertEqual(
            [f"host:{FRAME}:WORKER:1:COMPLETED", f"host:{FRAME}:REVIEWER:1:COMPLETED",
             f"host:{FRAME}:WORKER:2:COMPLETED", f"host:{FRAME}:EXIT:MASTER_DONE"],
            [key for key, _ in bus.notices],
        )

    def test_a_failure_is_reported_and_the_host_waits_instead_of_retrying_or_exiting(self):
        runner = ScriptedRunner([RoleResult("FAILED", "sandbox missing", error_code="SANDBOX"),
                                 RoleResult("COMPLETED", "fixed")])
        room = FakeRoom(scripted={4: [directive(1, "REWORK", "WORKER", "use the other codex")],
                                  6: [directive(2, "DONE")]})
        host, room, bus, _todo, _clock = make_host(runner, room=room)
        outcome = host.run()
        self.assertEqual([("WORKER", 1, "FAILED"), ("WORKER", 2, "COMPLETED")], outcome.roles_run)
        self.assertEqual(("ERROR",), tuple({sev for _k, sev, _t in room.reports[:1]}))
        self.assertEqual(1, len(room.calls), "the Master is called exactly once for the failure")
        self.assertIn("SANDBOX", room.calls[0])
        self.assertEqual("FAILED", bus.notices[0][1]["status"])
        self.assertEqual("SANDBOX", bus.notices[0][1]["error_code"])
        # Between the failure and the Master's directive the Host ran nothing on its own.
        self.assertEqual(2, len(runner.calls))

    def test_a_runner_that_raises_is_a_failed_role_not_a_dead_host(self):
        runner = ScriptedRunner([RuntimeError("boom")])
        room = FakeRoom(scripted={2: [directive(1, "DONE")]})
        host, _room, bus, _todo, _clock = make_host(runner, room=room)
        outcome = host.run()
        self.assertEqual("MASTER_DONE", outcome.exit_reason)
        self.assertEqual("RUNNER_EXCEPTION", bus.notices[0][1]["error_code"])

    def test_the_host_exits_when_the_todo_is_finished_archived_or_blocked(self):
        for script, reason in (
            ({2: ("DONE", False)}, "TODO_DONE"),
            ({2: ("IN_PROGRESS", True)}, "TODO_ARCHIVED"),
            ({2: ("BLOCKED", False)}, "TODO_BLOCKED"),
        ):
            with self.subTest(reason=reason):
                host, *_ = make_host(ScriptedRunner([]), todo=FakeTodo(script))
                self.assertEqual(reason, host.run().exit_reason)

    def test_the_host_exits_when_its_room_is_closed(self):
        room = FakeRoom()
        room.open = False
        host, *_ = make_host(ScriptedRunner([]), room=room)
        self.assertEqual("ROOM_CLOSED", host.run().exit_reason)

    def test_a_silent_master_ends_the_host_with_a_failure_notice(self):
        host, room, bus, _todo, clock = make_host(ScriptedRunner([]), idle_timeout_seconds=30.0)
        outcome = host.run()
        self.assertEqual("IDLE_TIMEOUT", outcome.exit_reason)
        self.assertGreaterEqual(clock.now, 30.0)
        self.assertEqual("EXITED_FAILED", bus.notices[-1][1]["status"])
        self.assertTrue(room.calls, "an unattended exit calls the Master")

    def test_only_master_authored_directives_are_obeyed_and_replays_are_ignored(self):
        runner = ScriptedRunner([])
        room = FakeRoom(scripted={
            1: [directive(1, "DONE", author="WORKER"),        # wrong author
                {"room_sequence": 2, "message": {"author_role": "MASTER", "body_text": "please stop"}},  # prose
                directive(3, "RUN_ROLE", "REVIEWER")],
            2: [directive(3, "RUN_ROLE", "REVIEWER"),         # replay of the same sequence
                directive(4, "DONE")],
        })
        # The replay is a second copy at the same sequence: it must not run the role twice.
        host, _room, _bus, _todo, _clock = make_host(runner, room=room)
        outcome = host.run()
        self.assertEqual("MASTER_DONE", outcome.exit_reason)
        self.assertEqual([("WORKER", 1, None), ("REVIEWER", 1, None)], runner.calls)

    def test_unreachable_ports_never_kill_the_host_and_notices_are_resent(self):
        runner = ScriptedRunner([RoleResult("COMPLETED", "worker done")])
        room, bus = FakeRoom(), FakeBus()
        room.fail = bus.fail = True
        host, _r, _b, _todo, _clock = make_host(runner, room=room, bus=bus)
        host._run_role("WORKER", feedback=None)      # first run happens while both are down
        self.assertEqual(1, host.pending_notices)
        self.assertGreater(host.outcome.port_errors, 0)
        room.fail = bus.fail = False
        host._flush_outbox()                          # they come back
        self.assertEqual(0, host.pending_notices)
        self.assertEqual(1, len(bus.notices))
        self.assertEqual(1, len(room.reports))
        # No duplicate on a further flush.
        host._flush_outbox()
        self.assertEqual(1, len(bus.notices))

    def test_directive_parsing_is_strict(self):
        self.assertIsNone(parse_directive(directive(1, "RUN_ROLE", "ARCHITECT")))
        self.assertIsNone(parse_directive(directive(1, "RUN_ROLE")))
        self.assertIsNone(parse_directive(directive(1, "SELFDESTRUCT", "WORKER")))
        self.assertIsNone(parse_directive({"room_sequence": 1, "message": "not a mapping"}))
        # The role is normalised; the Master's feedback text is passed through verbatim.
        parsed = parse_directive(directive(1, "REWORK", "reviewer", "  tighten  "))
        self.assertEqual({"directive": "REWORK", "role": "REVIEWER", "feedback": "  tighten  "}, parsed)
        # A blank feedback is no feedback.
        self.assertIsNone(parse_directive(directive(1, "REWORK", "WORKER", "   "))["feedback"])
        self.assertEqual("DONE", parse_directive(directive(1, "DONE"))["directive"])


if __name__ == "__main__":
    unittest.main()
