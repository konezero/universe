import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from persona_automation import PersonaAutomationStore
from universe_server import UniverseHTTPServer


class ProjectionStore:
    def __init__(self, root):
        self.database_path = Path(root) / "automation.sqlite3"
        self.todos = {
            "todo-live": {"todo_id": "todo-live", "project_id": "P1", "node_ref": "node-a"},
            "todo-dead": {"todo_id": "todo-dead", "project_id": "P1", "node_ref": "node-b"},
            "todo-none": {"todo_id": "todo-none", "project_id": "P1", "node_ref": ""},
        }

    def get_todo(self, todo_id):
        return self.todos[todo_id]


class TaskFrameHostProjectionTests(unittest.TestCase):
    def test_projection_reads_real_persona_events_and_heartbeat_pid_liveness(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectionStore(root)
            automation = PersonaAutomationStore(store.database_path)
            run = automation.start_run(
                {
                    "project_id": "P1",
                    "session_anchor_ref": "anchor-test",
                    "scope": "projection test",
                    "instruction": "record a host launch",
                    "request_id": "projection-start",
                    "idempotency_key": "projection-start",
                    "goal_ref": "goal-test",
                    "goal_version": "1",
                    "budget": {"max_dispatches": 1},
                },
                {
                    "session_anchor_ref": "anchor-test",
                    "project_id": "P1",
                    "persona_id": "persona-test",
                    "persona_revision": 1,
                    "assignment_revision": 1,
                    "state": "ACTIVE",
                },
            )["run"]
            for frame, todo, room, pid, phase in (
                ("frame-live", "todo-live", "room-live", os.getpid(), "RUNNING_ROLE"),
                ("frame-dead", "todo-dead", "room-dead", 2**31 - 1, "RUNNING_ROLE"),
                ("frame-none", "todo-none", "room-none", os.getpid(), "WAITING_DIRECTIVE"),
            ):
                automation.record_host_event(
                    run["run_id"],
                    "TASK_FRAME_HOST_LAUNCHED",
                    frame,
                    {"todo_id": todo, "room_id": room, "first_role": "WORKER", "pid": pid},
                )
                folder = Path(root) / "task_frame_hosts" / frame
                folder.mkdir(parents=True)
                (folder / "heartbeat.json").write_text(
                    json.dumps({"pid": pid, "phase": phase}), encoding="utf-8"
                )

            server = object.__new__(UniverseHTTPServer)
            server.store = store
            server.persona_automation = automation
            result = server.task_frame_host_projection("P1")
            by_id = {row["task_frame_id"]: row for row in result["frames"]}
            self.assertEqual({"frame-live", "frame-dead", "frame-none"}, set(by_id))
            self.assertTrue(by_id["frame-live"]["alive"])
            self.assertFalse(by_id["frame-dead"]["alive"])
            self.assertEqual("node-a", by_id["frame-live"]["node_ref"])
            self.assertIsNone(by_id["frame-none"]["node_ref"])
            self.assertEqual("room-live", by_id["frame-live"]["room_id"])


if __name__ == "__main__":
    unittest.main()
