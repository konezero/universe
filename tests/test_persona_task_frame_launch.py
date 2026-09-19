"""The Master launches and directs a Host; the checks are mechanical and exact."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from persona_task_frame_launch import (  # noqa: E402
    LaunchError,
    frame_id_for,
    host_status,
    launch_frame,
    post_directive,
    validate_write_scope,
)
from task_frame_host import parse_directive  # noqa: E402

ANCHOR = "session_anchor_master1"


class FakeRooms:
    def __init__(self):
        self.created, self.posted = [], []

    def create_boss_room(self, **kwargs):
        self.created.append(kwargs)
        return {"room_id": "room_1"}

    def post_message(self, room_id, value):
        self.posted.append((room_id, value))
        return {"message_id": "msg_1", "room_sequence": 5, "author_role": "MASTER", "body_text": value["body_text"]}


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = (Path(self.temp.name) / "project").resolve()
        self.project.mkdir()
        self.state = Path(self.temp.name) / "hosts"
        self.reset_records()

    def reset_records(self):
        self.rooms = FakeRooms()
        self.launched = []
        self.run = {"run_id": "run_1", "state": "RUNNING", "project_id": "p1",
                    "node_ref": "node_1", "session_anchor_ref": ANCHOR}
        self.todo = {"todo_id": "todo_1", "state": "IN_PROGRESS", "project_id": "p1",
                     "node_ref": "node_1", "title": "Fix", "detail": "d", "archived_at": None}

    def tearDown(self):
        self.temp.cleanup()

    def launcher(self, spec, spec_path, heartbeat_path):
        self.launched.append(spec)
        return 4242

    def launch(self, **overrides):
        value = {"owner_ref": ANCHOR, "request_id": "r1", "provider": "codex"}
        value.update(overrides.pop("value", {}))
        kwargs = dict(
            run=self.run, value=value, todo=self.todo, project_root=self.project,
            repository_root=ROOT, persona_text="be careful",
            runtime_binding={"endpoint": "http://127.0.0.1:1", "token": "t"},
            base_url="http://127.0.0.1:9", rooms=self.rooms,
            bus_to={"mode": "MASTER"}, bus_from={"provider": "UNIVERSE"},
            state_root=self.state, launcher=self.launcher,
        )
        kwargs.update(overrides)
        return launch_frame(**kwargs)

    def scope(self, *names, ops=("MODIFY",)):
        return {"repository_write_scope": "BOUNDED",
                "mutation_scope": {"operations": list(ops), "targets": [str(self.project / n) for n in names]}}

    def test_launch_builds_the_spec_from_the_masters_decision(self):
        result = self.launch(value={"worker_write_scope": self.scope("a.py"), "persona_id": "ignored"})
        self.assertEqual("TASK_FRAME_HOST_LAUNCHED", result["status"])
        self.assertEqual(frame_id_for("run_1", "r1"), result["task_frame_id"])
        spec = self.launched[0]
        self.assertEqual("CODEX", spec["provider"])
        self.assertEqual("be careful", spec["persona_text"])
        self.assertEqual("BOUNDED", spec["worker_write_scope"]["repository_write_scope"])
        self.assertEqual([str(self.project / "a.py")], spec["worker_write_scope"]["mutation_scope"]["targets"])
        self.assertEqual("room_1", spec["room_id"])
        self.assertEqual("task_frame_host_runner:make", spec["runner"])
        self.assertEqual(1, len(self.rooms.created))

    def test_no_scope_means_a_read_only_worker(self):
        self.launch()
        self.assertEqual("NONE", self.launched[0]["worker_write_scope"]["repository_write_scope"])

    def test_a_replay_never_starts_a_second_host(self):
        self.launch()
        again = self.launch()
        self.assertEqual("TASK_FRAME_HOST_REPLAYED", again["status"])
        self.assertEqual(1, len(self.launched))
        self.assertEqual("room_1", again["room_id"])
        self.launch(value={"request_id": "r2"})
        self.assertEqual(2, len(self.launched), "a new request is a new frame")

    def test_scope_rules_are_exact(self):
        bad = [
            self.scope("a.py", ops=("DELETE",)),
            self.scope("a.py", ops=("MODIFY", "MOVE")),
            self.scope("*.py"),
            {"repository_write_scope": "BOUNDED", "mutation_scope": {"operations": ["MODIFY"], "targets": ["relative.py"]}},
            {"repository_write_scope": "BOUNDED", "mutation_scope": {"operations": [], "targets": []}},
            {"repository_write_scope": "BOUNDED", "mutation_scope": {"operations": ["MODIFY"],
                                                                     "targets": [str(self.project.parent / "outside.py")]}},
            {"repository_write_scope": "MAYBE"},
        ]
        for scope in bad:
            with self.subTest(scope=scope), self.assertRaises(LaunchError):
                validate_write_scope(scope, self.project)
        self.assertEqual(self.launched, [])
        with self.assertRaises(LaunchError) as escaped:
            validate_write_scope(self.scope("..", "..", "x"), self.project)
        self.assertIn(escaped.exception.code, {"TASK_FRAME_SCOPE_OUTSIDE_PROJECT", "TASK_FRAME_SCOPE_INVALID"})

    def test_run_and_todo_must_match_the_caller(self):
        cases = [
            ({"owner_ref": "session_anchor_other"}, {}, {}, "TASK_FRAME_OWNER_MISMATCH"),
            ({}, {"state": "PAUSED"}, {}, "TASK_FRAME_RUN_NOT_RUNNING"),
            ({}, {}, {"state": "DONE"}, "TASK_FRAME_TODO_NOT_LIVE"),
            ({}, {}, {"archived_at": "2026-01-01"}, "TASK_FRAME_TODO_NOT_LIVE"),
            ({}, {}, {"node_ref": "node_2"}, "TASK_FRAME_TODO_NODE_MISMATCH"),
            ({}, {}, {"project_id": "p2"}, "TASK_FRAME_TODO_PROJECT_MISMATCH"),
        ]
        for value, run, todo, code in cases:
            with self.subTest(code=code):
                self.run, self.todo = {**self.run, **run}, {**self.todo, **todo}
                with self.assertRaises(LaunchError) as caught:
                    self.launch(value=value)
                self.assertEqual(code, caught.exception.code)
                self.reset_records()
        self.assertEqual([], self.launched)

    def test_directives_are_posted_as_the_master_and_parse_back(self):
        result = self.launch()
        frame = result["task_frame_id"]
        reply = post_directive(
            rooms=self.rooms, state_root=self.state, task_frame_id=frame,
            value={"directive": "rework", "role": "worker", "feedback": "cover empty", "request_id": "d1"},
        )
        self.assertEqual("TASK_FRAME_DIRECTIVE_POSTED", reply["status"])
        room_id, message = self.rooms.posted[0]
        self.assertEqual("room_1", room_id)
        self.assertEqual("MASTER", message["author_role"])
        parsed = parse_directive({"room_sequence": 5, "message": {"author_role": "MASTER", "body_text": message["body_text"]}})
        self.assertEqual({"directive": "REWORK", "role": "WORKER", "feedback": "cover empty"}, parsed)
        with self.assertRaises(LaunchError):
            post_directive(rooms=self.rooms, state_root=self.state, task_frame_id=frame,
                           value={"directive": "REWORK", "request_id": "d2"})
        with self.assertRaises(LaunchError) as unknown:
            post_directive(rooms=self.rooms, state_root=self.state, task_frame_id="host_none",
                           value={"directive": "DONE", "request_id": "d3"})
        self.assertEqual("TASK_FRAME_HOST_UNKNOWN", unknown.exception.code)

    def test_status_reads_the_heartbeat_over_the_launch_marker(self):
        frame = self.launch()["task_frame_id"]
        self.assertTrue(host_status(self.state, frame)["known"])
        (self.state / frame / "heartbeat.json").write_text(
            json.dumps({"pid": 1, "phase": "WAITING", "room_id": "room_1", "updated_at": "t"}), encoding="utf-8")
        status = host_status(self.state, frame)
        self.assertEqual("WAITING", status["phase"])
        self.assertFalse(host_status(self.state, "host_missing")["known"])


if __name__ == "__main__":
    unittest.main()
