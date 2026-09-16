"""todo.bind_node: Action IR work-surface gap closed 2026-09-16.

Mirrors todo.bind_goal: a narrow CAS write for Feature Node binding so LLM/UI
callers need not resend a full todo.update body to change one coordinate.
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


class TodoBindNodeActionTests(unittest.TestCase):
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

    def make_todo(self, title="노드 분류 대상 Todo"):
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

    def make_feature(self, key="feature_bind_node_test"):
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": key,
            "title": key,
            "intent_text": "테스트용 노드",
            "created_by_role": "USER",
        })
        return feature

    def test_registered_and_covered(self):
        from universe_action_registry import (
            IMPLEMENTED_WORK_SURFACE_ACTION_IDS,
            PENDING_WORK_SURFACE_ACTION_IDS,
        )
        self.assertIn("todo.bind_node", IMPLEMENTED_WORK_SURFACE_ACTION_IDS)
        self.assertNotIn("todo.bind_node", PENDING_WORK_SURFACE_ACTION_IDS)
        self.assertEqual(COVERED, self.server.action_registry.classify_surface("todo.bind_node"))

    def test_bind_then_read_back_via_todo_read(self):
        todo = self.make_todo()
        feature = self.make_feature("feature_bind_readback")
        status, result = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "node_ref": feature["feature_id"],
        })
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_NODE_BOUND", result["status"])
        self.assertEqual("NODE", result["todo"]["scope_kind"])
        self.assertEqual(feature["feature_id"], result["todo"]["node_ref"])
        self.assertEqual(todo["revision"] + 1, result["todo"]["revision"])

        status, read_back = self.act("todo.read", {"todo_id": todo["todo_id"]})
        self.assertEqual(200, status)
        self.assertEqual(feature["feature_id"], read_back["todo"]["node_ref"])
        self.assertEqual("NODE", read_back["todo"]["scope_kind"])

    def test_unbind_with_null_node_ref(self):
        todo = self.make_todo()
        feature = self.make_feature("feature_bind_unbind")
        _, bound = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "node_ref": feature["feature_id"],
        })
        status, unbound = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": bound["todo"]["revision"],
        })
        self.assertEqual(200, status, unbound)
        self.assertEqual("TODO_NODE_UNBOUND", unbound["status"])
        self.assertEqual("PROJECT", unbound["todo"]["scope_kind"])
        self.assertIsNone(unbound["todo"]["node_ref"])

    def test_stale_revision_conflicts(self):
        todo = self.make_todo()
        feature = self.make_feature("feature_bind_stale")
        status, result = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"] + 5,
            "node_ref": feature["feature_id"],
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", result["error_code"])

    def test_nonexistent_feature_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "node_ref": "feature_does_not_exist",
        })
        self.assertEqual(404, status)
        self.assertEqual("FEATURE_NODE_NOT_FOUND", result["error_code"])

    def test_feature_from_a_different_project_is_rejected(self):
        other_root = Path(self.fixture.temp.name) / "OTHER_BIND_NODE"
        if not other_root.exists():
            other_root.mkdir()
            (other_root / "REPOSITORY_MANIFEST.md").write_text("# OTHER\n", encoding="utf-8")
            self.server.store.register_project({
                "project_id": "OTHER_BIND_NODE",
                "project_root": str(other_root),
            })
        other_feature, _ = self.server.store.create_feature_node("OTHER_BIND_NODE", {
            "idempotency_key": "other-feature-bind-node",
            "title": "다른 프로젝트 노드",
            "intent_text": "x",
            "created_by_role": "USER",
        })
        todo = self.make_todo()
        status, result = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "node_ref": other_feature["feature_id"],
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_NODE_BINDING_PROJECT_MISMATCH", result["error_code"])

    def test_goal_scope_conflict_is_rejected(self):
        todo = self.make_todo()
        goal = self.server.store.create_goal("TEST", {
            "title": "프로젝트 Goal",
            "description": "project scoped",
            "owner": "Project Master",
            "state": "DESIGNING",
            "sort_order": 0,
            "scope_kind": "PROJECT",
        })
        bound_goal = self.server.store.set_todo_goal_binding(todo["todo_id"], {
            "expected_revision": todo["revision"],
            "goal_id": goal["goal_id"],
        })
        feature = self.make_feature("feature_goal_conflict")
        status, result = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": bound_goal["revision"],
            "node_ref": feature["feature_id"],
        })
        self.assertEqual(409, status)
        self.assertEqual("TODO_NODE_BINDING_GOAL_SCOPE_CONFLICT", result["error_code"])

    def _bind_node_contract(self):
        status, catalog = self.request("GET", "/v1/actions", None)
        self.assertEqual(200, status)
        contracts = {c["action_id"]: c for c in catalog["registry"]["contracts"]}
        self.assertIn("todo.bind_node", contracts)
        return contracts["todo.bind_node"]

    def test_catalog_publishes_an_accurate_request_schema(self):
        contract = self._bind_node_contract()
        schema = contract["metadata"]["request_schema"]
        self.assertEqual({"todo_id", "expected_revision"}, set(schema["required"]))
        self.assertEqual(
            {"todo_id", "expected_revision", "node_ref"}, set(schema["properties"])
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ", contract["metadata"]["replay"]
        )

    def test_call_built_from_only_catalog_required_fields_succeeds(self):
        schema = self._bind_node_contract()["metadata"]["request_schema"]
        todo = self.make_todo()
        values = {"todo_id": todo["todo_id"], "expected_revision": todo["revision"]}
        request = {field: values[field] for field in schema["required"]}
        status, result = self.act("todo.bind_node", request)
        self.assertEqual(200, status, result)
        self.assertEqual("TODO_NODE_UNBOUND", result["status"])

    def test_field_missing_from_catalog_required_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.bind_node", {"todo_id": todo["todo_id"]})
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_field_not_in_catalog_properties_is_rejected(self):
        todo = self.make_todo()
        status, result = self.act("todo.bind_node", {
            "todo_id": todo["todo_id"],
            "expected_revision": todo["revision"],
            "extra_field": "nope",
        })
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])


if __name__ == "__main__":
    unittest.main()
