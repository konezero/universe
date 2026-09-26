"""Structured plan items linked to existing nodes, Todos and results."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from universe_plan_item_actions import _EVENT_TODO_ID, PlanItemActions, PlanItemError  # noqa: E402
from universe_project_drafts import FIELDS, ProjectDrafts  # noqa: E402
from universe_server import UniverseStore  # noqa: E402
from universe_todo_actions import TodoActions  # noqa: E402

USER = {"kind": "USER", "actor_ref": "operator:test"}


def item(**overrides):
    value = {
        "kind": "WORK", "label": "P1", "title": "Link plan items to nodes",
        "purpose": "Keep plan -> node -> Todo -> result traceable",
        "work": "Store references as rows", "done_criteria": "Round trip is navigable",
        "source": {"kind": "MANUAL"}, "node_refs": [], "todo_refs": [], "depends_on": [],
        "state": "ACTIVE",
    }
    value.update(overrides)
    return value


class PlanItemStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = UniverseStore(root / "store.sqlite3")
        for project_id in ("TEST", "OTHER"):
            (root / project_id).mkdir()
            (root / project_id / "REPOSITORY_MANIFEST.md").write_text("# fixture\n", encoding="utf-8")
            self.store.register_project({"project_id": project_id, "project_root": str(root / project_id)})
        self.node = self.feature("TEST", "owner-node")
        self.other_node = self.feature("OTHER", "foreign-node")
        self.done_todo = self.todo("TEST", self.node, "DONE")
        self.open_todo = self.todo("TEST", self.node, "READY")
        self.foreign_todo = self.todo("OTHER", self.other_node, "READY")
        self.actions = PlanItemActions(self.store)

    def feature(self, project_id, key):
        feature, _ = self.store.create_feature_node(project_id, {
            "idempotency_key": key, "title": key, "intent_text": "fixture node", "created_by_role": "USER"})
        return feature["feature_id"]

    def todo(self, project_id, node_ref, state):
        return self.store.create_todo({
            "project_id": project_id, "scope_kind": "NODE", "node_ref": node_ref, "title": f"{state} work",
            "detail": "d", "priority": "P2", "state": state, "source_kind": "USER", "sort_order": 0})["todo_id"]

    def save(self, expected=0, request_id=None, plan_item_id="plan_p1", project_id="TEST", **overrides):
        return self.actions.save({
            "plan_item_id": plan_item_id, "project_id": project_id, "expected_revision": expected,
            "request_id": request_id or "req_" + uuid.uuid4().hex[:16], "item": item(**overrides)}, USER)

    def test_links_existing_node_and_todos_and_traces_results_both_ways(self):
        saved = self.save(node_refs=[self.node], todo_refs=[self.done_todo, self.open_todo])
        self.assertEqual("PLAN_ITEM_SAVED", saved["status"])
        self.assertEqual(1, saved["plan_item"]["revision"])
        self.assertEqual({"added": [self.node], "removed": []}, saved["link_changes"]["node_refs"])
        with self.store._connection() as connection:
            connection.execute(
                "INSERT INTO work_loop_result_fanout(fanout_id, project_id, source_kind, source_id, outcome, fanout_digest, fanout_json, created_at)"
                " VALUES ('fanout_fixture', 'TEST', 'TODO', ?, 'COMPLETED', 'digest_fixture', '{}', '2026-09-25T00:00:00Z')",
                (self.done_todo,))
        trace = self.actions.trace({"plan_item_id": "plan_p1"})
        self.assertEqual(["LINKED"], [node["status"] for node in trace["nodes"]])
        self.assertEqual("plan_p1", trace["nodes"][0]["plan_items"][0]["plan_item_id"])
        by_id = {todo["todo_id"]: todo for todo in trace["todos"]}
        self.assertEqual(["fanout_fixture"], [r["fanout_id"] for r in by_id[self.done_todo]["results"]])
        self.assertTrue(by_id[self.done_todo]["node_in_plan_item"])
        self.assertEqual("plan_p1", by_id[self.open_todo]["plan_items"][0]["plan_item_id"])
        # One DONE Todo out of two is partial; nothing is aggregated to the project.
        self.assertEqual({"status": "PARTIAL", "linked_todos": 2, "done_todos": 1, "missing_targets": 0,
                          "scope": "PLAN_ITEM_ONLY", "project_completion": "NOT_AGGREGATED"}, trace["completion"])
        listed = self.actions.list({"project_id": "TEST", "node_ref": self.node})
        self.assertEqual(["plan_p1"], [entry["plan_item_id"] for entry in listed["plan_items"]])
        todo_read = TodoActions(self.store).read({"todo_id": self.open_todo})
        self.assertEqual(["plan_p1"], [entry["plan_item_id"] for entry in todo_read["plan_items"]])

    def test_todo_state_evidence_uses_indexed_lookup_by_todo(self):
        self.save(todo_refs=[self.done_todo])
        events = [("evt_done", {"todo_id": self.done_todo, "state": "DONE", "evidence_ref": "ref://done"}),
                  ("evt_other", {"todo_id": self.open_todo, "state": "DONE", "evidence_ref": "ref://other"}),
                  ("evt_ready", {"todo_id": self.done_todo, "state": "READY"}),
                  ("evt_list", [self.done_todo]), ("evt_bad", "{not json")]
        with self.store._connection() as connection:
            for event_id, payload in events:
                connection.execute(
                    "INSERT INTO project_event(event_id, project_id, event_type, payload_json, created_at)"
                    " VALUES (?, 'TEST', 'TODO_ACTION_APPLIED', ?, '2026-09-25T00:00:00Z')",
                    (event_id, payload if isinstance(payload, str) else json.dumps(payload)))
            plan = " ".join(str(row[-1]) for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT event_id FROM project_event WHERE project_id=? AND event_type='TODO_ACTION_APPLIED'"
                f" AND ({_EVENT_TODO_ID})=?"
                " ORDER BY created_at, event_id", ("TEST", self.done_todo)))
        self.assertIn("project_event_todo_action_todo", plan)
        results = self.actions.trace({"plan_item_id": "plan_p1"})["todos"][0]["results"]
        self.assertEqual([("TODO_STATE_EVIDENCE", "evt_done", "DONE", "ref://done")],
                         [(r["kind"], r["event_id"], r["state"], r["evidence_ref"]) for r in results])

    def test_change_and_remove_links_keep_history_and_reverse_index_current(self):
        self.save(node_refs=[self.node], todo_refs=[self.done_todo])
        changed = self.save(expected=1, todo_refs=[self.open_todo], node_refs=[])
        self.assertEqual({"added": [self.open_todo], "removed": [self.done_todo]}, changed["link_changes"]["todo_refs"])
        self.assertEqual({"added": [], "removed": [self.node]}, changed["link_changes"]["node_refs"])
        self.assertEqual([], self.actions.list({"project_id": "TEST", "todo_id": self.done_todo})["plan_items"])
        self.assertEqual([], TodoActions(self.store).read({"todo_id": self.done_todo})["plan_items"])
        with self.store._connection() as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM project_plan_item_revision").fetchone()[0])

    def test_missing_foreign_duplicate_and_stale_links_are_rejected_without_a_write(self):
        cases = [
            ({"node_refs": ["feature_missing"]}, "PLAN_ITEM_LINK_TARGET_NOT_FOUND"),
            ({"node_refs": [self.other_node]}, "PLAN_ITEM_LINK_PROJECT_MISMATCH"),
            ({"todo_refs": [self.foreign_todo]}, "PLAN_ITEM_LINK_PROJECT_MISMATCH"),
            ({"todo_refs": [self.open_todo, self.open_todo]}, "PLAN_ITEM_LINK_DUPLICATE"),
            ({"depends_on": ["plan_p1"]}, "PLAN_ITEM_DEPENDENCY_INVALID"),
            ({"depends_on": ["plan_missing"]}, "PLAN_ITEM_LINK_TARGET_NOT_FOUND"),
            ({"source": {"kind": "PROJECT_DRAFT", "draft_id": "draft_none", "draft_revision": 1}}, "PLAN_ITEM_SOURCE_NOT_FOUND"),
        ]
        for overrides, code in cases:
            with self.subTest(code=code), self.assertRaises(PlanItemError) as raised:
                self.save(**overrides)
            self.assertEqual(code, raised.exception.code)
        with self.store._connection() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM project_plan_item_revision").fetchone()[0])
        self.save()
        with self.assertRaises(PlanItemError) as raised:
            self.save(expected=0)
        self.assertEqual("PLAN_ITEM_REVISION_CONFLICT", raised.exception.code)
        with self.assertRaises(PlanItemError) as raised:
            self.save(expected=1, project_id="OTHER")
        self.assertEqual("PLAN_ITEM_SCOPE_CONFLICT", raised.exception.code)

    def test_exact_replay_returns_original_and_changed_replay_conflicts(self):
        first = self.save(request_id="req_replay_1", todo_refs=[self.open_todo])
        replay = self.save(request_id="req_replay_1", todo_refs=[self.open_todo])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["plan_item"], replay["plan_item"])
        with self.assertRaises(PlanItemError) as raised:
            self.save(request_id="req_replay_1", todo_refs=[])
        self.assertEqual("PLAN_ITEM_REPLAY_CONFLICT", raised.exception.code)

    def test_draft_source_dependency_cycle_and_missing_target_drift(self):
        drafts = ProjectDrafts(self.store._connection)
        draft = drafts.save({"draft_id": "draft_plan", "project_id": "TEST", "expected_revision": 0,
                             "request_id": "draft_save_1", "fields": dict.fromkeys(FIELDS, "")}, USER)
        foreign = drafts.save({"draft_id": "draft_foreign", "project_id": "OTHER", "expected_revision": 0,
                               "request_id": "draft_save_2", "fields": dict.fromkeys(FIELDS, "")}, USER)
        with self.assertRaises(PlanItemError) as raised:
            self.save(source={"kind": "PROJECT_DRAFT", "draft_id": "draft_foreign", "draft_revision": foreign["revision"]})
        self.assertEqual("PLAN_ITEM_SOURCE_PROJECT_MISMATCH", raised.exception.code)
        source = {"kind": "PROJECT_DRAFT", "draft_id": "draft_plan", "draft_revision": draft["revision"], "section": "capabilities"}
        self.save(source=source, kind="VALIDATION", label="V3")
        self.save(plan_item_id="plan_p2", depends_on=["plan_p1"])
        with self.assertRaises(PlanItemError) as raised:
            self.save(expected=1, source=source, depends_on=["plan_p2"])
        self.assertEqual("PLAN_ITEM_DEPENDENCY_INVALID", raised.exception.code)
        drafts.save({"draft_id": "draft_plan", "project_id": "TEST", "expected_revision": 1,
                     "request_id": "draft_save_3", "fields": dict.fromkeys(FIELDS, "x")}, USER)
        trace = self.actions.trace({"plan_item_id": "plan_p1"})
        self.assertEqual({"draft_id": "draft_plan", "linked_revision": 1, "current_revision": 2, "stale": True}, trace["source_draft"])
        self.assertEqual(["plan_p2"], [entry["plan_item_id"] for entry in trace["dependents"]])
        self.assertEqual("NO_TODOS", trace["completion"]["status"])
        # A linked target removed later is reported, not silently counted.
        self.save(plan_item_id="plan_p3", todo_refs=[self.open_todo])
        with self.store._connection() as connection:
            connection.execute("DELETE FROM project_todo WHERE todo_id = ?", (self.open_todo,))
        drift = self.actions.trace({"plan_item_id": "plan_p3"})
        self.assertEqual("MISSING", drift["todos"][0]["status"])
        self.assertEqual({"status": "NO_TODOS", "missing_targets": 1}, {k: drift["completion"][k] for k in ("status", "missing_targets")})

    def test_actor_and_schema_are_enforced(self):
        with self.assertRaises(PlanItemError) as raised:
            self.actions.save({"plan_item_id": "plan_x", "project_id": "TEST", "expected_revision": 0,
                               "request_id": "req_actor_1", "item": item()}, {"kind": "SESSION"})
        self.assertEqual("ACTION_ACTOR_RESOLUTION_FAILED", raised.exception.code)
        for overrides in ({"kind": "GOAL"}, {"title": " "}, {"source": {"kind": "DOCUMENT"}}, {"extra": 1}):
            with self.subTest(overrides=overrides), self.assertRaises(PlanItemError):
                self.save(**overrides)
        with self.assertRaises(PlanItemError) as raised:
            self.actions.read({"plan_item_id": "plan_absent"})
        self.assertEqual(("PLAN_ITEM_NOT_FOUND", 404), (raised.exception.code, raised.exception.status))


import test_memory_candidates_and_delegations as fixtures  # noqa: E402


class PlanItemActionApiTests(unittest.TestCase):
    """UI and LLM clients use the same POST /v1/actions contract."""

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

    def test_round_trip_through_actions_and_registry(self):
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": "plan-api-node", "title": "Functional Node ownership", "intent_text": "fixture",
            "created_by_role": "USER"})
        todo = self.server.store.create_todo({
            "project_id": "TEST", "scope_kind": "NODE", "node_ref": feature["feature_id"], "title": "detail todo",
            "detail": "d", "priority": "P2", "state": "READY", "source_kind": "USER", "sort_order": 0})
        body = {"plan_item_id": "plan_api_p1", "project_id": "TEST", "expected_revision": 0,
                "request_id": "plan_api_" + uuid.uuid4().hex[:12],
                "item": item(node_refs=[feature["feature_id"]], todo_refs=[todo["todo_id"]])}
        status, saved = self.act("plan.item.save", body)
        self.assertEqual(200, status, saved)
        self.assertEqual("PLAN_ITEM_SAVED", saved["status"])
        status, stale = self.act("plan.item.save", {**body, "request_id": "plan_api_" + uuid.uuid4().hex[:12]})
        self.assertEqual(409, status, stale)
        self.assertIn("PLAN_ITEM_REVISION_CONFLICT", json.dumps(stale))
        status, trace = self.act("plan.item.trace", {"plan_item_id": "plan_api_p1"})
        self.assertEqual(200, status, trace)
        self.assertEqual(feature["feature_id"], trace["nodes"][0]["node_ref"])
        self.assertEqual("NOT_STARTED", trace["completion"]["status"])
        status, listed = self.act("plan.item.list", {"project_id": "TEST", "todo_id": todo["todo_id"]})
        self.assertEqual(200, status, listed)
        self.assertEqual(["plan_api_p1"], [entry["plan_item_id"] for entry in listed["plan_items"]])
        status, todo_read = self.act("todo.read", {"todo_id": todo["todo_id"]})
        self.assertEqual(200, status, todo_read)
        self.assertEqual(["plan_api_p1"], [entry["plan_item_id"] for entry in todo_read["plan_items"]])
        status, node_read = self.request("GET", f"/v1/feature-nodes/{feature['feature_id']}")
        self.assertEqual(200, status, node_read)
        self.assertEqual(feature["feature_id"], node_read["feature"]["feature_id"])
        self.assertEqual(["plan_api_p1"], [entry["plan_item_id"] for entry in node_read["plan_items"]])
        self.assertEqual({"plan_item_id", "revision", "kind", "label", "title", "state"}, set(node_read["plan_items"][0]))
        registry = self.server.action_registry_catalog()["registry"]
        self.assertIn("plan.item.save", json.dumps(registry))


if __name__ == "__main__":
    unittest.main()
