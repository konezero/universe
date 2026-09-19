"""todo.redo: start a Todo over as a fresh READY copy and archive the original.

A node that is stuck on a half-finished dispatch can continue from a clean Todo
while the original keeps its full history on the archived row.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures  # noqa: E402
from universe_action_registry import COVERED  # noqa: E402


class TodoRedoActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def act(self, request):
        return self.request("POST", "/v1/actions", {"action_id": "todo.redo", "request": request})

    def make_node(self, key):
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": key, "title": key, "intent_text": "redo 테스트 노드",
            "created_by_role": "USER",
        })
        return feature["feature_id"]

    def make_todo(self, node_ref, *, state="IN_PROGRESS", priority="P1", title="다시 시작할 Todo"):
        return self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref, "title": title,
            "detail": "원본 상세", "priority": priority, "state": state, "source_kind": "MASTER",
            "sort_order": -3,
        })

    def rid(self):
        return "redo-" + uuid.uuid4().hex[:16]

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.redo", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.redo", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.redo"))

    def test_redo_copies_as_ready_and_archives_the_original(self):
        node = self.make_node("redo-copy")
        source = self.make_todo(node)
        status, result = self.act({
            "todo_id": source["todo_id"], "expected_revision": source["revision"],
            "request_id": self.rid(), "reason": "Reviewer left a dangling dispatch",
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_REDONE", result["status"])
        new = result["todo"]
        self.assertNotEqual(source["todo_id"], new["todo_id"])
        self.assertEqual("READY", new["state"])
        for field in ("node_ref", "priority", "sort_order", "title", "scope_kind", "project_id"):
            self.assertEqual(source[field], new[field], field)
        self.assertIn(source["todo_id"], new["detail"])
        self.assertIn("Reviewer left a dangling dispatch", new["detail"])
        self.assertTrue(result["source_todo"]["archived_at"])
        # State stays on the archived row: the history is preserved, not rewritten.
        self.assertEqual("IN_PROGRESS", result["source_todo"]["state"])

    def test_default_todo_list_shows_only_the_copy(self):
        node = self.make_node("redo-list")
        source = self.make_todo(node)
        _status, result = self.act({
            "todo_id": source["todo_id"], "expected_revision": source["revision"],
            "request_id": self.rid(),
        })
        listed = {
            item["todo_id"] for item in self.server.store.list_todos()
            if item.get("node_ref") == node
        }
        self.assertEqual({result["todo"]["todo_id"]}, listed)

    def test_replay_returns_the_first_result_and_does_not_copy_again(self):
        node = self.make_node("redo-replay")
        source = self.make_todo(node)
        request_id = self.rid()
        body = {"todo_id": source["todo_id"], "expected_revision": source["revision"],
                "request_id": request_id}
        _s, first = self.act(dict(body))
        status, second = self.act(dict(body))
        self.assertEqual(200, status, second)
        self.assertEqual("TODO_REDO_REPLAYED", second["status"])
        self.assertEqual(first["todo"]["todo_id"], second["todo"]["todo_id"])
        copies = [item for item in self.server.store.list_todos() if item.get("node_ref") == node]
        self.assertEqual(1, len(copies))
        # The same request_id with a different request is a conflict, not a copy.
        status, conflict = self.act({**body, "reason": "different"})
        self.assertEqual(409, status, conflict)
        self.assertEqual("TODO_REDO_REQUEST_CONFLICT", conflict["error_code"])

    def test_done_archived_stale_and_unknown_todos_are_refused(self):
        node = self.make_node("redo-refuse")
        done = self.make_todo(node, state="DONE")
        ready = self.make_todo(node, state="READY", title="다른 Todo")
        cases = []
        cases.append(({"todo_id": done["todo_id"], "expected_revision": done["revision"]},
                      409, "TODO_REDO_DONE_NOT_ALLOWED"))
        cases.append(({"todo_id": ready["todo_id"], "expected_revision": ready["revision"] + 5},
                      409, "TODO_REVISION_CONFLICT"))
        cases.append(({"todo_id": "todo_does_not_exist", "expected_revision": 1},
                      404, "TODO_NOT_FOUND"))
        for body, expected_status, code in cases:
            status, result = self.act({**body, "request_id": self.rid()})
            self.assertEqual(expected_status, status, (code, result))
            self.assertEqual(code, result["error_code"], result)
        # An already archived Todo cannot be redone again.
        self.server.store.set_todo_archived(ready["todo_id"], {"expected_revision": ready["revision"]})
        archived = self.server.store.get_todo(ready["todo_id"])
        status, result = self.act({"todo_id": ready["todo_id"],
                                   "expected_revision": archived["revision"], "request_id": self.rid()})
        self.assertEqual(409, status, result)
        self.assertEqual("TODO_ALREADY_ARCHIVED", result["error_code"])

    def test_todo_with_an_active_run_must_be_stopped_first(self):
        node = self.make_node("redo-active-run")
        source = self.make_todo(node)
        with self.server.store._connection() as connection:
            connection.execute(
                "INSERT INTO persona_automation_run(run_id, project_id, session_anchor_ref, persona_id, "
                "persona_revision, assignment_revision, scope_text, instruction_text, budget_json, state, "
                "cursor_json, idempotency_key, request_digest, created_at, updated_at, node_ref, "
                "current_assignment_json) VALUES (?, 'TEST', 'anchor-x', 'p', 1, 1, 's', 'i', '{}', "
                "'RUNNING', '{}', ?, 'd', 't', 't', ?, ?)",
                ("persona_run_redo_active", "redo-key-" + uuid.uuid4().hex[:8], node,
                 '{"todo_id": "%s"}' % source["todo_id"]),
            )
        status, result = self.act({"todo_id": source["todo_id"],
                                   "expected_revision": source["revision"], "request_id": self.rid()})
        self.assertEqual(409, status, result)
        self.assertEqual("TODO_REDO_ACTIVE_RUN", result["error_code"])
        with self.server.store._connection() as connection:
            connection.execute(
                "UPDATE persona_automation_run SET state = 'STOPPED' WHERE run_id = ?",
                ("persona_run_redo_active",),
            )
        status, result = self.act({"todo_id": source["todo_id"],
                                   "expected_revision": source["revision"], "request_id": self.rid()})
        self.assertEqual(200, status, result)

    def test_extra_fields_are_rejected_by_the_schema(self):
        node = self.make_node("redo-extra")
        source = self.make_todo(node)
        status, result = self.act({"todo_id": source["todo_id"], "expected_revision": source["revision"],
                                   "request_id": self.rid(), "state": "DONE"})
        self.assertGreaterEqual(status, 400, result)


if __name__ == "__main__":
    unittest.main()
