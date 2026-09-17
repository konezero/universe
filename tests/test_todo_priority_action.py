"""todo.priority: Action IR work-surface gap closed 2026-09-16.

Mirrors todo.bind_goal / todo.bind_node: a narrow CAS write for priority so
LLM/UI callers need not resend a full todo.update body to change one field.
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


class TodoPriorityActionTests(unittest.TestCase):
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

    def make_todo(self, title="우선순위 대상 Todo", priority="P2"):
        return self.server.store.create_todo({
            "scope_kind": "PROJECT",
            "project_id": "TEST",
            "title": title,
            "detail": "테스트용",
            "priority": priority,
            "state": "READY",
            "source_kind": "USER",
            "sort_order": 0,
        })

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.priority", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.priority", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.priority"))

    def test_set_priority_then_read_back(self):
        todo = self.make_todo()
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "priority": "P0",
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_PRIORITY_SET", result["status"])
        self.assertEqual("P0", result["todo"]["priority"])
        self.assertEqual(todo["revision"] + 1, result["todo"]["revision"])

        status, read_back = self.act("todo.read", {"todo_id": todo["todo_id"]})
        self.assertEqual(200, status)
        self.assertEqual("P0", read_back["todo"]["priority"])

    def test_auto_resolves_to_concrete_priority(self):
        todo = self.make_todo(title="critical runtime failure", priority="P3")
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "priority": "AUTO",
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_PRIORITY_SET", result["status"])
        self.assertIn(result["todo"]["priority"], {"P0", "P1", "P2", "P3"})
        self.assertNotEqual("AUTO", result["todo"]["priority"])

    def test_stale_revision_conflicts(self):
        todo = self.make_todo()
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"] + 5,
            "priority": "P1",
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", result["error_code"])

    def test_invalid_priority_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "priority": "P9",
        })
        self.assertEqual(400, status)
        self.assertIn(result["error_code"], {"REQUEST_INVALID", "TODO_PRIORITY_INVALID"})

    def _priority_contract(self):
        status, catalog = self.request("GET", "/v1/actions", None)
        self.assertEqual(200, status)
        contracts = {c["action_id"]: c for c in catalog["registry"]["contracts"]}
        self.assertIn("todo.priority", contracts)
        return contracts["todo.priority"]

    def test_catalog_publishes_an_accurate_request_schema(self):
        contract = self._priority_contract()
        schema = contract["metadata"]["request_schema"]
        self.assertEqual(
            {"todo_id", "expected_revision", "priority"}, set(schema["required"])
        )
        self.assertEqual(
            {"todo_id", "expected_revision", "priority"}, set(schema["properties"])
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            ["AUTO", "P0", "P1", "P2", "P3"],
            schema["properties"]["priority"]["enum"],
        )
        self.assertEqual(
            "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
            contract["metadata"]["replay"],
        )

    def test_field_missing_from_catalog_required_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_field_not_in_catalog_properties_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "priority": "P1",
            "not_a_published_field": "x",
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_repeating_an_applied_call_is_not_idempotent_success(self):
        todo = self.make_todo()
        request = {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "priority": "P0",
        }
        status1, _ = self.act("todo.priority", request)
        self.assertEqual(200, status1)
        status2, result2 = self.act("todo.priority", request)
        self.assertEqual(409, status2)
        self.assertEqual("TODO_REVISION_CONFLICT", result2["error_code"])

    def test_title_detail_goal_untouched_by_priority(self):
        todo = self.make_todo("원본 제목")
        status, result = self.act("todo.priority", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "priority": "P0",
        })
        self.assertEqual(200, status, result)
        self.assertEqual("원본 제목", result["todo"]["title"])
        self.assertEqual("테스트용", result["todo"]["detail"])
        self.assertIsNone(result["todo"]["goal_id"])
        self.assertEqual(todo["node_ref"], result["todo"]["node_ref"])


if __name__ == "__main__":
    unittest.main()
