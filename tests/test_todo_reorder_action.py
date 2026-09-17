"""todo.reorder: Action IR work-surface gap closed 2026-09-16.

Mirrors todo.priority: a narrow CAS write for sort_order so LLM/UI callers
need not resend a full todo.update body to change list order.
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


class TodoReorderActionTests(unittest.TestCase):
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

    def make_todo(self, title="정렬 대상 Todo", sort_order=10):
        return self.server.store.create_todo({
            "scope_kind": "PROJECT",
            "project_id": "TEST",
            "title": title,
            "detail": "테스트용",
            "priority": "P2",
            "state": "READY",
            "source_kind": "USER",
            "sort_order": sort_order,
        })

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.reorder", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.reorder", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.reorder"))

    def test_set_sort_order_then_read_back(self):
        todo = self.make_todo()
        status, result = self.act("todo.reorder", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "sort_order": -100,
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_REORDERED", result["status"])
        self.assertEqual(-100, result["todo"]["sort_order"])
        self.assertEqual(todo["revision"] + 1, result["todo"]["revision"])

        status, read_back = self.act("todo.read", {"todo_id": todo["todo_id"]})
        self.assertEqual(200, status)
        self.assertEqual(-100, read_back["todo"]["sort_order"])

    def test_stale_revision_conflicts(self):
        todo = self.make_todo()
        status, result = self.act("todo.reorder", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"] + 5,
            "sort_order": 1,
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", result["error_code"])

    def test_non_integer_sort_order_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.reorder", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "sort_order": "front",
        })
        self.assertEqual(400, status)
        self.assertIn(result["error_code"], {"REQUEST_INVALID", "TODO_SORT_ORDER_INVALID"})

    def _reorder_contract(self):
        status, catalog = self.request("GET", "/v1/actions", None)
        self.assertEqual(200, status)
        contracts = {c["action_id"]: c for c in catalog["registry"]["contracts"]}
        self.assertIn("todo.reorder", contracts)
        return contracts["todo.reorder"]

    def test_catalog_publishes_an_accurate_request_schema(self):
        contract = self._reorder_contract()
        schema = contract["metadata"]["request_schema"]
        self.assertEqual(
            {"todo_id", "expected_revision", "sort_order"}, set(schema["required"])
        )
        self.assertEqual(
            {"todo_id", "expected_revision", "sort_order"}, set(schema["properties"])
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
            contract["metadata"]["replay"],
        )

    def test_field_missing_from_catalog_required_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.reorder", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_field_not_in_catalog_properties_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.reorder", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "sort_order": 1,
            "not_a_published_field": "x",
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_repeating_an_applied_call_is_not_idempotent_success(self):
        todo = self.make_todo()
        request = {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "sort_order": 42,
        }
        status1, _ = self.act("todo.reorder", request)
        self.assertEqual(200, status1)
        status2, result2 = self.act("todo.reorder", request)
        self.assertEqual(409, status2)
        self.assertEqual("TODO_REVISION_CONFLICT", result2["error_code"])

    def test_title_detail_priority_untouched_by_reorder(self):
        todo = self.make_todo("원본 제목", sort_order=3)
        status, result = self.act("todo.reorder", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "sort_order": 99,
        })
        self.assertEqual(200, status, result)
        self.assertEqual("원본 제목", result["todo"]["title"])
        self.assertEqual("테스트용", result["todo"]["detail"])
        self.assertEqual("P2", result["todo"]["priority"])
        self.assertEqual(99, result["todo"]["sort_order"])


if __name__ == "__main__":
    unittest.main()
