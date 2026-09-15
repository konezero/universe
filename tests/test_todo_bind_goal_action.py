"""todo.bind_goal: the Action IR work-surface gap closed 2026-09-15.

docs/action-ir-work-surface.md's slice-3 decision left 8 fine-grained todo.*
Actions declared in the registry but unbound (PENDING_WORK_SURFACE_ACTION_IDS)
pending a real caller need. Persona automation's P3 judge/meeting loop
(tools/persona_automation.py) produces Work-Plan candidate Todos that, once
reviewed and adopted, need linking to the Goal that justified them -- that
link previously required a full todo.update PATCH body just to change one
field. This closes that one specific gap: a narrow, CAS-guarded, typed
Action mirroring the existing blocked_reason narrow-write pattern.

Runs against a real in-process server/DB via the existing
MemoryCandidateApiTests fixture, exercising the Action over real HTTP.
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


class TodoBindGoalActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def act(self, action_id, request):
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": request})

    def make_todo(self, title="분류 대상 Todo"):
        return self.server.store.create_todo({
            "scope_kind": "PROJECT",
            "project_id": "TEST",
            "title": title,
            "detail": "테스트용",
            "priority": "P2",
            "state": "READY",
            "source_kind": "USER",
            "sort_order": 0,
        })

    def make_goal(self, title="테스트 Goal"):
        return self.server.store.create_goal("TEST", {
            "title": title,
            "description": "Bounded outcome",
            "owner": "Project Master",
            "state": "DESIGNING",
            "sort_order": 0,
        })

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.bind_goal", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.bind_goal", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.bind_goal"))

    def test_bind_then_read_back_via_todo_read(self):
        todo = self.make_todo()
        goal = self.make_goal("A")
        status, result = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": todo["revision"], "goal_id": goal["goal_id"],
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_GOAL_BOUND", result["status"])
        self.assertEqual(goal["goal_id"], result["todo"]["goal_id"])
        self.assertEqual(todo["revision"] + 1, result["todo"]["revision"])

        status, read_back = self.act("todo.read", {"todo_id": todo["todo_id"]})
        self.assertEqual(200, status)
        self.assertEqual(goal["goal_id"], read_back["todo"]["goal_id"])

    def test_unbind_with_null_goal_id(self):
        todo = self.make_todo()
        goal = self.make_goal("B")
        _, bound = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": todo["revision"], "goal_id": goal["goal_id"],
        })
        status, unbound = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": bound["todo"]["revision"],
        })
        self.assertEqual(200, status, unbound)
        self.assertEqual("TODO_GOAL_UNBOUND", unbound["status"])
        self.assertIsNone(unbound["todo"]["goal_id"])

    def test_stale_revision_conflicts(self):
        todo = self.make_todo()
        goal = self.make_goal("C")
        status, result = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": todo["revision"] + 5, "goal_id": goal["goal_id"],
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", result["error_code"])

    def test_nonexistent_goal_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": todo["revision"], "goal_id": "goal_does_not_exist",
        })
        self.assertEqual(404, status)
        self.assertEqual("GOAL_NOT_FOUND", result["error_code"])

    def test_goal_from_a_different_project_is_rejected(self):
        other_root = Path(self.fixture.temp.name) / "OTHER_BIND_GOAL"
        if not other_root.exists():
            other_root.mkdir()
            (other_root / "REPOSITORY_MANIFEST.md").write_text("# OTHER\n", encoding="utf-8")
            self.server.store.register_project({"project_id": "OTHER_BIND_GOAL", "project_root": str(other_root)})
        other_goal = self.server.store.create_goal("OTHER_BIND_GOAL", {
            "title": "다른 프로젝트 Goal", "description": "x", "owner": "x", "state": "DESIGNING", "sort_order": 0,
        })
        todo = self.make_todo()
        status, result = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": todo["revision"], "goal_id": other_goal["goal_id"],
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_GOAL_BINDING_PROJECT_MISMATCH", result["error_code"])

    def test_title_detail_priority_untouched_by_bind(self):
        # A narrow write: binding a Goal must not touch any other field,
        # unlike a full todo.update PATCH body.
        todo = self.make_todo("원본 제목")
        goal = self.make_goal("D")
        _, result = self.act("todo.bind_goal", {
            "todo_id": todo["todo_id"], "expected_revision": todo["revision"], "goal_id": goal["goal_id"],
        })
        self.assertEqual("원본 제목", result["todo"]["title"])
        self.assertEqual(todo["detail"], result["todo"]["detail"])
        self.assertEqual(todo["priority"], result["todo"]["priority"])


if __name__ == "__main__":
    unittest.main()
