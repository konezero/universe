"""todo.restore: Action IR work-surface gap closed 2026-09-17.

Narrow CAS clear of archived_at. Rejects Todos that are not archived.
Pairs with todo.archive; hard delete remains separate.
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


class TodoRestoreActionTests(unittest.TestCase):
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

    def make_todo(self, *, title="복원 대상 Todo"):
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

    def archive(self, todo):
        status, result = self.act("todo.archive", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
        })
        self.assertEqual(200, status, result)
        return result["todo"]

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.restore", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.restore", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.restore"))

    def test_restore_returns_to_default_list(self):
        todo = self.make_todo()
        archived = self.archive(todo)
        status, result = self.act("todo.restore", {
            "todo_id": archived["todo_id"],
            "expected_revision": archived["revision"],
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_RESTORED", result["status"])
        self.assertIsNone(result["todo"]["archived_at"])
        self.assertEqual(archived["revision"] + 1, result["todo"]["revision"])

        listed_ids = {item["todo_id"] for item in self.server.store.list_todos()}
        self.assertIn(todo["todo_id"], listed_ids)

    def test_not_archived_conflicts(self):
        todo = self.make_todo(title="미보관")
        status, result = self.act("todo.restore", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_NOT_ARCHIVED", result["error_code"])

    def test_stale_revision_conflicts(self):
        todo = self.make_todo(title="stale restore")
        archived = self.archive(todo)
        status, result = self.act("todo.restore", {
            "todo_id": archived["todo_id"],
            "expected_revision": archived["revision"] + 3,
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", result["error_code"])

    def _contract(self):
        status, catalog = self.request("GET", "/v1/actions", None)
        self.assertEqual(200, status)
        contracts = {c["action_id"]: c for c in catalog["registry"]["contracts"]}
        self.assertIn("todo.restore", contracts)
        return contracts["todo.restore"]

    def test_catalog_publishes_an_accurate_request_schema(self):
        contract = self._contract()
        schema = contract["metadata"]["request_schema"]
        self.assertEqual({"todo_id", "expected_revision"}, set(schema["required"]))
        self.assertEqual({"todo_id", "expected_revision"}, set(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])

    def test_unknown_field_rejected(self):
        todo = self.make_todo(title="extra field restore")
        archived = self.archive(todo)
        status, result = self.act("todo.restore", {
            "todo_id": archived["todo_id"],
            "expected_revision": archived["revision"],
            "extra": True,
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_title_and_state_untouched(self):
        todo = self.make_todo(title="내용 유지 복원")
        archived = self.archive(todo)
        status, result = self.act("todo.restore", {
            "todo_id": archived["todo_id"],
            "expected_revision": archived["revision"],
        })
        self.assertEqual(200, status, result)
        self.assertEqual("내용 유지 복원", result["todo"]["title"])
        self.assertEqual("READY", result["todo"]["state"])
        self.assertEqual("테스트용", result["todo"]["detail"])


if __name__ == "__main__":
    unittest.main()
