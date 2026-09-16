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
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures  # noqa: E402
from universe_app.terminal_host import persona_native_queue_message_id  # noqa: E402


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


class FleetWorkerSessionStartTests(unittest.TestCase):
    """2026-09-16: the real blocker -- Fleet could bind a Worker/Reviewer
    assignment, but the live project had no eligible Worker/Reviewer
    terminal, and generic session.new only creates Project Master or
    Conductor. fleet.worker-session-start closes this by reusing
    create_cli_terminal (the same real Host-launch route session.new uses)
    with mode="WORKER", then recording the durable Fleet assignment only
    once that terminal actually exists.

    The real OS-level CLI process spawn is mocked (same boundary the
    existing persona-native-queue test in test_persona_assignment.py uses)
    -- everything else (Supervisor anchor-before-spawn resolution, the
    exact kwargs create_cli_terminal passes to the Host, the assignment
    write and its CAS/lineage checks) runs for real.
    """

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

    def make_todo(self, title="Worker pilot todo"):
        todo = self.server.store.create_todo({
            "project_id": "TEST", "scope_kind": "PROJECT", "title": title, "detail": "d",
            "priority": "P2", "state": "READY", "source_kind": "USER", "sort_order": 0,
        })
        return todo["todo_id"]

    def fake_worker_host(self):
        fake_host = Mock()
        fake_host.find_live.return_value = None

        def fake_create(**kwargs):
            # Echo back the exact Session Anchor create_cli_terminal already
            # resolved via the Supervisor (anchor-before-spawn) -- the real
            # Host launch confirms receipt for that anchor, never invents a
            # different one.
            return {
                "terminal_id": "term_worker_pilot",
                "state": "LIVE",
                "provider": kwargs.get("provider"),
                "mode": kwargs.get("mode"),
                "session_anchor_ref": kwargs["session_anchor_ref"],
                "host_reused_existing": False,
            }

        fake_host.create.side_effect = fake_create
        return fake_host

    def test_session_start_creates_a_real_terminal_and_records_the_assignment_only_after_receipt(self):
        master = self.register("MASTER", "fleet-worker-pilot-master")
        todo_id = self.make_todo()
        fake_host = self.fake_worker_host()
        original_host = self.server.terminal_host
        self.server.terminal_host = fake_host
        try:
            status, result = self.act("fleet.worker-session-start", {
                "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
                "assigned_by_session_anchor_ref": master, "provider": "CODEX",
            })
        finally:
            self.server.terminal_host = original_host
        self.assertEqual(200, status, result)
        self.assertEqual("FLEET_WORKER_SESSION_STARTED", result["status"])
        terminal = result["terminal"]
        assignment = result["assignment"]
        self.assertEqual("LIVE", terminal["state"])
        self.assertEqual("WORKER", terminal["mode"])
        self.assertTrue(terminal["session_anchor_ref"])
        self.assertEqual(terminal["session_anchor_ref"], assignment["session_anchor_ref"])
        self.assertEqual("ACTIVE", assignment["state"])
        self.assertEqual("IMPLEMENTER", assignment["worker_role"])
        fake_host.create.assert_called_once()

        # The durable row is real and readable back, exactly like a manually
        # bound assignment.
        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_id,
        })
        self.assertEqual(200, status, listed)
        self.assertEqual(1, len(listed["assignments"]))
        self.assertEqual(assignment["assignment_id"], listed["assignments"][0]["assignment_id"])

    def test_session_start_supports_reviewer_role_as_a_worker_session_not_a_new_authority(self):
        master = self.register("MASTER", "fleet-worker-pilot-reviewer-master")
        todo_id = self.make_todo()
        fake_host = self.fake_worker_host()
        original_host = self.server.terminal_host
        self.server.terminal_host = fake_host
        try:
            status, result = self.act("fleet.worker-session-start", {
                "project_id": "TEST", "todo_id": todo_id, "worker_role": "REVIEWER",
                "assigned_by_session_anchor_ref": master, "provider": "CODEX",
            })
        finally:
            self.server.terminal_host = original_host
        self.assertEqual(200, status, result)
        # The session itself is mode=WORKER -- REVIEWER is the assignment's
        # role, never a distinct session mode/authority.
        self.assertEqual("WORKER", result["terminal"]["mode"])
        self.assertEqual("REVIEWER", result["assignment"]["worker_role"])

    def test_session_start_pins_selected_persona_to_worker_anchor_and_keeps_queue_receipt_pending(self):
        master = self.register("MASTER", "fleet-worker-pilot-persona-master")
        todo_id = self.make_todo("Worker persona delivery")
        status, persona_result = self.act("persona.create", {
            "title": "Worker persona",
            "body": 'Exact worker framing with a quote "and a newline\nmarker".',
            "request_id": "fleet-worker-persona-test-create",
        })
        self.assertEqual(200, status, persona_result)
        persona = persona_result["persona"]
        fake_host = self.fake_worker_host()
        def queue_persona(_terminal_id, _body, *, message_id):
            return {
                "status": "PERSONA_NATIVE_QUEUE_ACCEPTED",
                "message_id": message_id,
                "delivery": {
                    "message_id": message_id,
                    "phase": "NATIVE_QUEUED",
                    "queued_submission_id": "worker-persona-submission",
                },
            }

        fake_host.deliver_persona_native_queue.side_effect = queue_persona
        original_host = self.server.terminal_host
        self.server.terminal_host = fake_host
        try:
            status, result = self.act("fleet.worker-session-start", {
                "project_id": "TEST", "todo_id": todo_id,
                "worker_role": "IMPLEMENTER",
                "assigned_by_session_anchor_ref": master,
                "provider": "CODEX", "persona_id": persona["persona_id"],
            })
        finally:
            self.server.terminal_host = original_host
        self.assertEqual(200, status, result)
        self.assertEqual("NATIVE_QUEUED", result["persona_delivery"]["status"])
        self.assertEqual("PENDING_PROVIDER_PHASE", result["persona_delivery"]["application"])
        worker_anchor = result["assignment"]["session_anchor_ref"]
        self.assertEqual(worker_anchor, result["persona_assignment"]["session_anchor_ref"])
        self.assertEqual(persona["persona_id"], result["persona_assignment"]["persona_id"])
        expected_message_id = persona_native_queue_message_id(
            persona["body"],
            session_anchor_ref=worker_anchor,
            persona_id=persona["persona_id"],
            persona_revision=persona["revision"],
            assignment_revision=result["persona_assignment"]["assignment_revision"],
        )
        fake_host.deliver_persona_native_queue.assert_called_once_with(
            "term_worker_pilot", persona["body"], message_id=expected_message_id
        )
        persisted = self.server.store.read_persona_assignment(worker_anchor)
        self.assertEqual("NATIVE_QUEUED", persisted["delivery_status"])
        self.assertIsNone(persisted["applied_at"])
        self.assertEqual(expected_message_id, persisted["queued_message_id"])

    def test_session_start_cleans_persona_assignment_when_late_delivery_binding_fails(self):
        master = self.register("MASTER", "fleet-worker-pilot-persona-cleanup-master")
        todo_id = self.make_todo("Worker persona cleanup")
        status, persona_result = self.act("persona.create", {
            "title": "Worker cleanup persona",
            "body": "The late delivery binding will fail in this fixture.",
            "request_id": "fleet-worker-persona-cleanup-create",
        })
        self.assertEqual(200, status, persona_result)
        persona = persona_result["persona"]
        before = {
            row["session_anchor_ref"]
            for row in self.server.store.list_persona_assignments("TEST")
        }
        fake_host = self.fake_worker_host()
        original_host = self.server.terminal_host
        self.server.terminal_host = fake_host
        try:
            with patch.object(
                self.server,
                "_deliver_persona_to_existing_terminal",
                side_effect=RuntimeError("late delivery binding failure"),
            ):
                status, result = self.act("fleet.worker-session-start", {
                    "project_id": "TEST", "todo_id": todo_id,
                    "worker_role": "IMPLEMENTER",
                    "assigned_by_session_anchor_ref": master,
                    "provider": "CODEX", "persona_id": persona["persona_id"],
                })
        finally:
            self.server.terminal_host = original_host
        self.assertEqual(500, status, result)
        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_id,
        })
        self.assertEqual(200, status, listed)
        self.assertEqual([], [row for row in listed["assignments"] if row["state"] == "ACTIVE"])
        after = self.server.store.list_persona_assignments("TEST")
        new_rows = [row for row in after if row["session_anchor_ref"] not in before]
        self.assertEqual(1, len(new_rows))
        self.assertEqual("UNASSIGNED", new_rows[0]["state"])

    def test_session_start_rejects_a_non_master_assigner(self):
        not_master = self.register("CONDUCTOR", "fleet-worker-pilot-non-master")
        todo_id = self.make_todo()
        status, result = self.act("fleet.worker-session-start", {
            "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
            "assigned_by_session_anchor_ref": not_master,
        })
        self.assertEqual(409, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_MASTER_REQUIRED", result["error_code"])

    def test_session_start_requires_a_todo_or_task_frame_scope(self):
        master = self.register("MASTER", "fleet-worker-pilot-scope-master")
        status, result = self.act("fleet.worker-session-start", {
            "project_id": "TEST", "worker_role": "IMPLEMENTER",
            "assigned_by_session_anchor_ref": master,
        })
        self.assertEqual(400, status, result)
        self.assertEqual("TASK_WORKER_ASSIGNMENT_SCOPE_REQUIRED", result["error_code"])

    def test_a_failed_host_launch_never_records_a_durable_assignment(self):
        master = self.register("MASTER", "fleet-worker-pilot-failure-master")
        todo_id = self.make_todo()
        fake_host = Mock()
        fake_host.find_live.return_value = None
        from universe_app.terminal_host import TerminalHostError
        fake_host.create.side_effect = TerminalHostError(
            "TERMINAL_PROVIDER_LAUNCH_FAILED", "the provider CLI failed to start"
        )
        original_host = self.server.terminal_host
        self.server.terminal_host = fake_host
        try:
            status, result = self.act("fleet.worker-session-start", {
                "project_id": "TEST", "todo_id": todo_id, "worker_role": "IMPLEMENTER",
                "assigned_by_session_anchor_ref": master, "provider": "CODEX",
            })
        finally:
            self.server.terminal_host = original_host
        self.assertEqual(409, status, result)
        self.assertEqual("TERMINAL_PROVIDER_LAUNCH_FAILED", result["error_code"])
        status, listed = self.act("fleet.worker-assignments-list", {
            "project_id": "TEST", "todo_id": todo_id,
        })
        self.assertEqual(200, status, listed)
        self.assertEqual([], listed["assignments"])


if __name__ == "__main__":
    unittest.main()
