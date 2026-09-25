from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from todo_execution_journal import append, journal_path, read_reference, records  # noqa: E402
from universe_server import UniverseHTTPServer  # noqa: E402


class ConductorReworkRequestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = journal_path(self.root, "todo_1")
        self.identity = dict(todo_id="todo_1", owner_ref="master_1",
                             run_id="run_1", task_frame_id="frame_1")
        append(self.path, **self.identity, event_id="launch", kind="ASSIGNED",
               payload={"role": "WORKER", "attempt": 1, "todo": {},
                        "persona": "", "worker_write_scope": {},
                        "feedback": None, "collected_results": {}})
        append(self.path, **self.identity, event_id="collected", kind="RESULT_COLLECTED",
               payload={"role": "WORKER", "result_ref": "result_1",
                        "result_digest": "digest_1"})
        self.posted = []
        self.dispatched = []
        bus = SimpleNamespace(post=lambda host, value: self._post(value))
        terminal_host = SimpleNamespace(find_live=lambda **kwargs: {
            "terminal_id": "terminal_master"})
        self.server = SimpleNamespace(
            persona_automation=SimpleNamespace(
                get_run=lambda run_id: {"project_id": "project_1",
                                        "session_anchor_ref": "master_1"},
                host_frame_launched=lambda run_id, frame_id: {
                    "todo_id": "todo_1", "todo_journal": {"event_id": "launch"}}),
            store=SimpleNamespace(get_project=lambda project_id: {
                "project_root": str(self.root)}),
            _validate_persona_assignment_anchor=lambda project_id, anchor: None,
            _anchor_modes=lambda anchor: {"CONDUCTOR"},
            _session_anchor_terminal_host=lambda: terminal_host,
            session_bus=bus,
            _dispatch_live_posted_session_instructions=lambda posted: self.dispatched.append(posted),
        )
        self.request = {"run_id": "run_1", "task_frame_id": "frame_1",
                        "conductor_anchor_ref": "conductor_1", "request_id": "request_1",
                        "feedback": "fix the empty state",
                        "based_on_result_ref": "result_1",
                        "based_on_result_digest": "digest_1"}

    def _post(self, value):
        self.posted.append(value)
        return {"messages": [{"message_id": "message_1"}]}

    def test_journal_record_precedes_bus_and_replay_reuses_record(self):
        def post_after_record(value):
            self.assertEqual("CONDUCTOR_REWORK_REQUESTED", records(self.path)[-1][0]["kind"])
            self.assertNotIn("fix the empty state", value["body_text"])
            return self._post(value)
        self.server.session_bus.post = lambda host, value: post_after_record(value)
        result = UniverseHTTPServer._request_conductor_rework(self.server, self.request)
        self.assertEqual("TASK_FRAME_REWORK_REQUESTED", result["status"])
        self.assertEqual("CONDUCTOR_REWORK_REQUESTED", read_reference(
            result["todo_journal"], project_root=self.root)["kind"])
        self.assertEqual(result["todo_journal"], UniverseHTTPServer._request_conductor_rework(
            self.server, self.request)["todo_journal"])
        self.assertEqual(3, len(records(self.path)))
        self.assertEqual(self.posted[0]["idempotency_key"], self.posted[1]["idempotency_key"])
        self.assertEqual(2, len(self.dispatched))

    def test_offline_master_still_receives_anchor_queued_bus_message(self):
        self.server._session_anchor_terminal_host = lambda: SimpleNamespace(
            find_live=lambda **kwargs: None)
        result = UniverseHTTPServer._request_conductor_rework(self.server, self.request)
        self.assertEqual("TASK_FRAME_REWORK_REQUESTED", result["status"])
        self.assertEqual("master_1", self.posted[0]["to"]["session_anchor_ref"])
        self.assertNotIn("terminal_id", self.posted[0]["to"])


if __name__ == "__main__":
    unittest.main()
