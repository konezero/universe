"""todo.move_project: Action IR work-surface gap closed 2026-09-17.

Narrow CAS project-scope write. NODE/goal/node_ref bindings must be cleared
first so cross-project coordinates cannot silently break.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures
from universe_action_registry import COVERED  # noqa: E402


class TodoMoveProjectActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request
        other_root = Path(cls.fixture.temp.name) / "OTHER_MOVE_PROJECT"
        if not other_root.exists():
            other_root.mkdir()
            (other_root / "REPOSITORY_MANIFEST.md").write_text("# OTHER\n", encoding="utf-8")
        cls.server.store.register_project({
            "project_id": "OTHER_MOVE_PROJECT",
            "project_root": str(other_root),
        })

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def act(self, action_id, request):
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": request})

    def make_todo(self, *, project_id="TEST", scope_kind="PROJECT", node_ref=None, goal_id=None):
        return self.server.store.create_todo({
            "scope_kind": scope_kind,
            "project_id": project_id,
            "node_ref": node_ref,
            "goal_id": goal_id,
            "title": "이동 대상 Todo",
            "detail": "테스트용",
            "priority": "P2",
            "state": "READY",
            "source_kind": "USER",
            "sort_order": 0,
        })

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.move_project", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.move_project", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.move_project"))

    def test_move_between_projects_then_read_back(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": "OTHER_MOVE_PROJECT",
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_PROJECT_MOVED", result["status"])
        self.assertEqual("PROJECT", result["todo"]["scope_kind"])
        self.assertEqual("OTHER_MOVE_PROJECT", result["todo"]["project_id"])
        self.assertEqual(todo["revision"] + 1, result["todo"]["revision"])

        status, read_back = self.act("todo.read", {"todo_id": todo["todo_id"]})
        self.assertEqual(200, status)
        self.assertEqual("OTHER_MOVE_PROJECT", read_back["todo"]["project_id"])

    def test_move_to_universe_null_project(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": None,
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_PROJECT_MOVED", result["status"])
        self.assertEqual("UNIVERSE", result["todo"]["scope_kind"])
        self.assertIsNone(result["todo"]["project_id"])

    def test_node_bound_todo_is_rejected(self):
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": "move-project-node",
            "title": "node",
            "intent_text": "테스트",
            "created_by_role": "USER",
        })
        todo = self.make_todo(scope_kind="NODE", node_ref=feature["feature_id"])
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": "OTHER_MOVE_PROJECT",
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_MOVE_PROJECT_SCOPE_CONFLICT", result["error_code"])

    def test_same_target_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": "TEST",
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_MOVE_PROJECT_SAME_TARGET", result["error_code"])

    def test_missing_project_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": "DOES_NOT_EXIST",
        })
        self.assertEqual(404, status)
        self.assertEqual("PROJECT_NOT_FOUND", result["error_code"])

    def test_stale_revision_conflicts(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"] + 3,
            "project_id": "OTHER_MOVE_PROJECT",
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", result["error_code"])

    def _contract(self):
        status, catalog = self.request("GET", "/v1/actions", None)
        self.assertEqual(200, status)
        contracts = {c["action_id"]: c for c in catalog["registry"]["contracts"]}
        self.assertIn("todo.move_project", contracts)
        return contracts["todo.move_project"]

    def test_catalog_publishes_an_accurate_request_schema(self):
        contract = self._contract()
        schema = contract["metadata"]["request_schema"]
        self.assertEqual(
            {"todo_id", "expected_revision", "project_id"}, set(schema["required"])
        )
        self.assertEqual(
            {"todo_id", "expected_revision", "project_id"}, set(schema["properties"])
        )
        self.assertFalse(schema["additionalProperties"])

    def test_unknown_field_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": "OTHER_MOVE_PROJECT",
            "extra": True,
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_title_untouched(self):
        todo = self.make_todo()
        status, result = self.act("todo.move_project", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "project_id": "OTHER_MOVE_PROJECT",
        })
        self.assertEqual(200, status, result)
        self.assertEqual("이동 대상 Todo", result["todo"]["title"])
        self.assertEqual("테스트용", result["todo"]["detail"])


if __name__ == "__main__":
    unittest.main()
