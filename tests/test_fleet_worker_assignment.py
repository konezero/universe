"""Fleet execution visibility (2026-09-16): a node Master's durable
Worker (IMPLEMENTER) / Reviewer bindings to an exact Todo/Task Frame.

Distinct from session_persona_assignment (single 1:1 node-owner slot) --
this table is append-only per assignment_id so multiple concurrent Workers,
retry/ended history, and cross-project/cross-todo isolation are all real,
verifiable acceptance scenarios rather than UI illusions built on
overwritten state.

Runs against a real in-process server/DB via the existing
MemoryCandidateApiTests fixture (project "TEST"), same pattern as
test_persona_node_master_automation.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures  # noqa: E402


class FleetWorkerAssignmentTests(unittest.TestCase):
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
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": dict(request)})

    def register(self, mode, session_id):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": "TEST", "mode": mode, "provider": "CODEX",
        })
        return material["session_anchor_ref"]

    def make_todo(self, title="Worker-bound todo"):
        todo = self.server.store.create_todo({
            "project_id": "TEST", "scope_kind": "PROJECT", "title": title, "detail": "d",
            "priority": "P2", "state": "READY", "source_kind": "USER", "sort_order": 0,
        })
        return todo["todo_id"]

    # -- assign / list -----------------------------------------------------

    def test_assign_creates_an_active_binding_readable_via_list(self):
        master = self.register("MASTER", "fleet-worker-assign-master")
        worker = self.register("CODEX", "fleet-worker-assign-worker")
        todo_id = self.make_todo()
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, result)
        assignment = result["assignment"]
        self.assertEqual("ACTIVE", assignment["state"])
        self.assertEqual(1, assignment["assignment_revision"])
        self.assertEqual("IMPLEMENTER", assignment["worker_role"])

        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_id,
        })
        self.assertEqual(200, status, listed)
        self.assertEqual(1, len(listed["assignments"]))
        self.assertEqual(assignment["assignment_id"], listed["assignments"][0]["assignment_id"])

    def test_multiple_worker_and_reviewer_roles_coexist_on_the_same_todo(self):
        master = self.register("MASTER", "fleet-worker-multi-master")
        worker_a = self.register("CODEX", "fleet-worker-multi-a")
        worker_b = self.register("CODEX", "fleet-worker-multi-b")
        reviewer = self.register("CODEX", "fleet-worker-multi-reviewer")
        todo_id = self.make_todo()
        for anchor, role in ((worker_a, "IMPLEMENTER"), (worker_b, "IMPLEMENTER"), (reviewer, "REVIEWER")):
            status, result = self.act("fleet.worker-assign", {
                "project_id": "TEST", "todo_id": todo_id, "worker_role": role,
                "session_anchor_ref": anchor, "assigned_by_session_anchor_ref": master,
            })
            self.assertEqual(200, status, result)
        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_id,
        })
        self.assertEqual(200, status, listed)
        active = [a for a in listed["assignments"] if a["state"] == "ACTIVE"]
        self.assertEqual(3, len(active))
        roles = sorted(a["worker_role"] for a in active)
        self.assertEqual(["IMPLEMENTER", "IMPLEMENTER", "REVIEWER"], roles)

    # -- unrelated same-project session exclusion ---------------------------

    def test_list_never_leaks_an_unrelated_same_project_todos_assignment(self):
        master = self.register("MASTER", "fleet-worker-isolation-master")
        worker = self.register("CODEX", "fleet-worker-isolation-worker")
        todo_a = self.make_todo("Todo A")
        todo_b = self.make_todo("Todo B")
        status, _ = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_a, "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status)
        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_b,
        })
        self.assertEqual(200, status, listed)
        self.assertEqual([], listed["assignments"])

    # -- retry / ended history -----------------------------------------------

    def test_reassigning_the_same_worker_ends_the_prior_row_and_preserves_history(self):
        master = self.register("MASTER", "fleet-worker-retry-master")
        worker = self.register("CODEX", "fleet-worker-retry-worker")
        todo_id = self.make_todo()
        status, first = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, first)
        status, second = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, second)
        self.assertNotEqual(
            first["assignment"]["assignment_id"], second["assignment"]["assignment_id"]
        )
        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_id,
        })
        self.assertEqual(200, status, listed)
        self.assertEqual(2, len(listed["assignments"]))
        by_id = {a["assignment_id"]: a for a in listed["assignments"]}
        self.assertEqual("ENDED", by_id[first["assignment"]["assignment_id"]]["state"])
        self.assertEqual("REASSIGNED", by_id[first["assignment"]["assignment_id"]]["ended_reason"])
        self.assertEqual("ACTIVE", by_id[second["assignment"]["assignment_id"]]["state"])

    def test_explicit_unassign_ends_the_row_with_a_reason_and_is_cas_guarded(self):
        master = self.register("MASTER", "fleet-worker-unassign-master")
        worker = self.register("CODEX", "fleet-worker-unassign-worker")
        todo_id = self.make_todo()
        status, assigned = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, assigned)
        assignment_id = assigned["assignment"]["assignment_id"]

        # Stale revision is rejected, not silently applied.
        status, stale = self.act("fleet.worker-unassign", {
            "task_worker_assignment_id": assignment_id, "expected_assignment_revision": 99, "reason": "done",
        })
        self.assertEqual(409, status, stale)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_REVISION_CONFLICT", stale["error_code"])

        status, ended = self.act("fleet.worker-unassign", {
            "task_worker_assignment_id": assignment_id, "expected_assignment_revision": 1, "reason": "task done",
        })
        self.assertEqual(200, status, ended)
        self.assertEqual("ENDED", ended["assignment"]["state"])
        self.assertEqual("task done", ended["assignment"]["ended_reason"])

        # Already-ended cannot be ended again.
        status, twice = self.act("fleet.worker-unassign", {
            "task_worker_assignment_id": assignment_id, "expected_assignment_revision": 2, "reason": "again",
        })
        self.assertEqual(409, status, twice)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_ALREADY_ENDED", twice["error_code"])

    def test_assignment_binds_an_existing_task_frame_to_its_target_worker_anchor(self):
        master = self.register("MASTER", "fleet-worker-frame-master")
        worker = self.register("CODEX", "fleet-worker-frame-worker")
        todo_id = self.make_todo("Task Frame Worker pilot")
        frame, created = self.server.task_frame_lineage.create_task_frame(
            frame_ref="fleet-worker-frame-pilot",
            origin_session_anchor_ref=master,
            target_session_anchor_ref=worker,
        )
        self.assertTrue(created)
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id,
            "task_frame_id": frame["frame_ref"], "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, result)
        self.assertEqual(todo_id, result["assignment"]["todo_id"])
        self.assertEqual(frame["frame_ref"], result["assignment"]["task_frame_id"])
        self.assertEqual(worker, result["assignment"]["session_anchor_ref"])

    def test_nonprogramming_reviewer_fixture_preserves_todo_and_frame_lineage(self):
        master = self.register("MASTER", "fleet-worker-nonprogramming-master")
        reviewer = self.register("CODEX", "fleet-worker-nonprogramming-reviewer")
        todo_id = self.make_todo("Review meeting agenda and accessibility notes")
        frame, created = self.server.task_frame_lineage.create_task_frame(
            frame_ref="fleet-worker-nonprogramming-review",
            origin_session_anchor_ref=master,
            target_session_anchor_ref=reviewer,
        )
        self.assertTrue(created)
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id,
            "task_frame_id": frame["frame_ref"], "worker_role": "REVIEWER",
            "session_anchor_ref": reviewer, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, result)
        assignment = result["assignment"]
        self.assertEqual("REVIEWER", assignment["worker_role"])
        self.assertEqual(todo_id, assignment["todo_id"])
        self.assertEqual(frame["frame_ref"], assignment["task_frame_id"])

    def test_assignment_rejects_task_frame_target_anchor_mismatch(self):
        master = self.register("MASTER", "fleet-worker-frame-mismatch-master")
        worker = self.register("CODEX", "fleet-worker-frame-mismatch-worker")
        other_worker = self.register("CODEX", "fleet-worker-frame-mismatch-other")
        frame, _ = self.server.task_frame_lineage.create_task_frame(
            frame_ref="fleet-worker-frame-mismatch",
            origin_session_anchor_ref=master,
            target_session_anchor_ref=other_worker,
        )
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "task_frame_id": frame["frame_ref"],
            "worker_role": "REVIEWER", "session_anchor_ref": worker,
            "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(409, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_FRAME_ANCHOR_MISMATCH", result["error_code"])

    def test_assignment_requires_a_master_assigner_and_worker_mode(self):
        conductor = self.register("CONDUCTOR", "fleet-worker-invalid-conductor")
        worker = self.register("CODEX", "fleet-worker-invalid-worker")
        todo_id = self.make_todo("Invalid assigner")
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id,
            "worker_role": "IMPLEMENTER", "session_anchor_ref": worker,
            "assigned_by_session_anchor_ref": conductor,
        })
        self.assertEqual(409, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_MASTER_REQUIRED", result["error_code"])

        master = self.register("MASTER", "fleet-worker-invalid-master")
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id,
            "worker_role": "IMPLEMENTER", "session_anchor_ref": master,
            "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(409, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_WORKER_MODE_INVALID", result["error_code"])

    def test_assignment_rejects_node_todo_lineage_mismatch(self):
        master = self.register("MASTER", "fleet-worker-node-lineage-master")
        worker = self.register("CODEX", "fleet-worker-node-lineage-worker")
        todo_id = self.make_todo("Node lineage")
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "node_ref": "missing-node",
            "worker_role": "IMPLEMENTER", "session_anchor_ref": worker,
            "assigned_by_session_anchor_ref": master,
        })
        self.assertGreaterEqual(status, 400)
        self.assertIn(result["error_code"], {
            "FEATURE_NOT_FOUND", "PERSONA_ASSIGNMENT_NODE_NOT_FOUND",
            "FEATURE_NODE_NOT_FOUND", "TASK_WORKER_ASSIGNMENT_NODE_TODO_MISMATCH",
        })

    # -- activity event producer ---------------------------------------------

    def test_assign_and_unassign_record_durable_activity_events(self):
        master = self.register("MASTER", "fleet-worker-activity-master")
        worker = self.register("CODEX", "fleet-worker-activity-worker")
        todo_id = self.make_todo()
        status, assigned = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "REVIEWER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(200, status, assigned)
        status, ended = self.act("fleet.worker-unassign", {
            "task_worker_assignment_id": assigned["assignment"]["assignment_id"],
            "expected_assignment_revision": 1, "reason": "review complete",
        })
        self.assertEqual(200, status, ended)

        events = self.server.store.list_events("TEST")
        matching = [e for e in events if e["event_type"] == "TASK_WORKER_ASSIGNMENT_CHANGED"
                    and e["payload"].get("assignment_id") == assigned["assignment"]["assignment_id"]]
        outcomes = sorted(e["payload"]["outcome"] for e in matching)
        self.assertEqual(["ASSIGNED", "ENDED"], outcomes)

    # -- input validation -----------------------------------------------------

    def test_assign_requires_at_least_one_of_todo_id_or_task_frame_id(self):
        master = self.register("MASTER", "fleet-worker-scope-master")
        worker = self.register("CODEX", "fleet-worker-scope-worker")
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "worker_role": "IMPLEMENTER",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(400, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_SCOPE_REQUIRED", result["error_code"])

    def test_assign_rejects_an_unrecognized_worker_role(self):
        master = self.register("MASTER", "fleet-worker-role-master")
        worker = self.register("CODEX", "fleet-worker-role-worker")
        todo_id = self.make_todo()
        status, result = self.act("fleet.worker-assign", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "SCOUT",
            "session_anchor_ref": worker, "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(400, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_ROLE_INVALID", result["error_code"])


if __name__ == "__main__":
    unittest.main()
