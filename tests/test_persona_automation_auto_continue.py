"""Regression tests for persona automation auto-continuation after RUN_COMPLETED.

2026-09-17 operator finding: a node Master's bounded automation run stopped
for good the instant persona.automation.complete succeeded, even when
READY/IN_PROGRESS Todos remained in its own node scope -- nothing re-queued
the next bounded cycle, so the Master just sat idle (observed live across
multiple nodes in the same session, one idle for about an hour) until an
operator manually called persona.automation.start again. Fixed in
tools/universe_server.py::_continue_persona_automation_if_work_remains,
invoked from the persona.automation.complete handler. The existing "stop at
the next review gate" safety property is unchanged: this only starts the
*next* bounded cycle, it never skips a review.
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


class PersonaAutomationAutoContinueTests(unittest.TestCase):
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
        body = dict(request)
        if action_id in {
            "persona.create", "persona.assign", "persona.automation.start",
            "persona.automation.complete", "todo.state",
        }:
            body.setdefault("request_id", f"auto-continue-{action_id.replace('.', '-')}-{uuid.uuid4().hex}")
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": body})

    def register(self, mode, session_id):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": "TEST", "mode": mode, "provider": "CODEX",
        })
        return material["session_anchor_ref"]

    def make_feature_node(self, key):
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": key, "title": key, "intent_text": "테스트용 노드",
            "created_by_role": "USER",
        })
        return feature["feature_id"]

    def make_persona(self, title="node lead"):
        status, result = self.act("persona.create", {"title": title, "body": "담당 노드 범위 안의 작업만 다룬다."})
        self.assertEqual(200, status, result)
        return result["persona"]

    def _run_one_bounded_cycle_to_reviewed(self, anchor, node_ref, label):
        """Drive a fresh run through start/tick/plan/dispatch via the real
        HTTP action layer (the code path this fix lives on), then attach and
        verdict the Reviewer at the store level -- same shortcut
        test_persona_automation.py already uses for this exact choreography;
        driving a live Worker/Reviewer PTY session is out of scope here."""

        reviewer_persona = self.make_persona(f"{label} independent reviewer")
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor,
            "scope": f"node bounded {label}", "instruction": "work only within the owned node",
            "worker_persona_id": reviewer_persona["persona_id"],
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, tick = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor, "tick_id": f"{label}-tick",
        })
        self.assertEqual(200, status, tick)
        status, decided = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor, "decision_id": f"{label}-decision",
        })
        self.assertEqual(200, status, decided)
        self.assertEqual("EXECUTE", decided["decision"]["kind"])
        status, dispatched = self.act("persona.automation.dispatch", {
            "run_id": run["run_id"], "owner_ref": anchor, "dispatch_id": f"{label}-dispatch",
            "title": "bounded task", "instruction": "do the bounded task",
            "completion_conditions": ["evidence"],
        })
        self.assertEqual(201, status, dispatched)
        assignment_row = dispatched["dispatch"]
        self.server.persona_automation.record_master_completion({
            "run_id": run["run_id"], "dispatch_id": assignment_row["dispatch_id"],
            "assignment_revision": assignment_row["assignment_revision"],
            "source_message_id": assignment_row["message_id"],
            "result_ref": f"{label}-result", "body_text_utf8_sha256": "c" * 64,
            "completed_at": "2026-09-17T00:00:00Z",
        })
        reviewer_assignment = {
            "assignment_id": f"{label}-reviewer-assignment",
            "project_id": "TEST", "node_ref": node_ref, "todo_id": decided["decision"]["target"]["todo_id"],
            "task_frame_id": None, "worker_role": "REVIEWER",
            "session_anchor_ref": f"{label}-reviewer-anchor",
            "assignment_revision": 1, "assigned_by_session_anchor_ref": anchor,
        }
        self.server.persona_automation.attach_master_reviewer(
            {
                "run_id": run["run_id"], "dispatch_id": assignment_row["dispatch_id"],
                "assignment_revision": assignment_row["assignment_revision"],
                "source_message_id": assignment_row["message_id"],
                "result_ref": f"{label}-result", "body_text_utf8_sha256": "c" * 64,
                "result_text": "direct Master result",
            },
            create_reviewer=lambda spec: {"assignment": reviewer_assignment},
        )
        self.server.persona_automation.record_reviewer_verdict({
            "run_id": run["run_id"], "dispatch_id": assignment_row["dispatch_id"],
            "reviewer_assignment_id": reviewer_assignment["assignment_id"],
            "reviewer_assignment_revision": 1,
            "reviewer_anchor_ref": reviewer_assignment["session_anchor_ref"],
            "worker_result_ref": f"{label}-result", "outcome": "PASS",
            "acceptance_status": "VERIFIED_EVIDENCE", "evidence_refs": [f"review:{label}"],
        })
        return run

    def test_completed_run_starts_a_new_cycle_when_executable_work_remains(self):
        anchor = self.register("MASTER", "auto-continue-remaining")
        persona = self.make_persona()
        node_ref = self.make_feature_node("auto-continue-remaining-node")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref,
            "title": "First bounded Todo", "detail": "first", "priority": "P0",
            "state": "READY", "source_kind": "MASTER", "sort_order": 0,
        })
        second_todo = self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref,
            "title": "Second bounded Todo (still READY)", "detail": "second",
            "priority": "P1", "state": "READY", "source_kind": "MASTER", "sort_order": 1,
        })
        run = self._run_one_bounded_cycle_to_reviewed(anchor, node_ref, "remaining")
        status, completed = self.act("persona.automation.complete", {
            "run_id": run["run_id"], "complete": True,
        })
        self.assertEqual(200, status, completed)
        self.assertEqual("COMPLETED", completed["run"]["state"])
        continuation = completed["continuation"]
        self.assertEqual("PERSONA_AUTOMATION_RUN_STARTED", continuation.get("status"))
        new_run = continuation["run"]
        self.assertNotEqual(run["run_id"], new_run["run_id"])
        self.assertEqual(node_ref, new_run["node_ref"])
        self.assertEqual("RUNNING", new_run["state"])
        # The second bounded Todo is untouched by this fix -- the new run's
        # own plan step (unchanged) decides whether/which Todo to pick next.
        self.assertEqual("READY", self.server.store.get_todo(second_todo["todo_id"])["state"])

    def test_completed_run_stays_idle_when_no_executable_work_remains(self):
        anchor = self.register("MASTER", "auto-continue-empty")
        persona = self.make_persona()
        node_ref = self.make_feature_node("auto-continue-empty-node")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        only_todo = self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref,
            "title": "Only bounded Todo", "detail": "only", "priority": "P0",
            "state": "READY", "source_kind": "MASTER", "sort_order": 0,
        })
        run = self._run_one_bounded_cycle_to_reviewed(anchor, node_ref, "empty")
        # Planning/dispatch moves the selected Todo READY -> IN_PROGRESS via
        # the typed todo.state Action, so the fixture must re-read the
        # authoritative revision before marking it DONE.  Passing the create
        # response's revision here intentionally exercised a stale CAS input.
        current_todo = self.server.store.get_todo(only_todo["todo_id"])
        status, marked_done = self.act("todo.state", {
            "todo_id": only_todo["todo_id"], "project_id": "TEST",
            "expected_revision": current_todo["revision"], "state": "DONE",
            "validation": {"status": "PASSED", "evidence_ref": "test:auto-continue-empty"},
        })
        self.assertEqual(200, status, marked_done)
        status, completed = self.act("persona.automation.complete", {
            "run_id": run["run_id"], "complete": True,
        })
        self.assertEqual(200, status, completed)
        self.assertEqual("COMPLETED", completed["run"]["state"])
        continuation = completed["continuation"]
        self.assertEqual("PERSONA_AUTOMATION_CONTINUATION_NO_EXECUTABLE_TODO", continuation.get("status"))

    def test_node_has_executable_todo_helper_matches_ready_and_in_progress_only(self):
        node_ref = self.make_feature_node("executable-helper-node")
        self.assertFalse(self.server._persona_automation_node_has_executable_todo("TEST", node_ref))
        self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref,
            "title": "Backlog only", "detail": "x", "priority": "P2",
            "state": "BACKLOG", "source_kind": "MASTER", "sort_order": 0,
        })
        self.assertFalse(self.server._persona_automation_node_has_executable_todo("TEST", node_ref))
        self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref,
            "title": "Ready one", "detail": "x", "priority": "P2",
            "state": "READY", "source_kind": "MASTER", "sort_order": 1,
        })
        self.assertTrue(self.server._persona_automation_node_has_executable_todo("TEST", node_ref))

    def test_project_wide_run_is_never_auto_continued(self):
        # A CONDUCTOR's automation is project-wide (node_ref is None); this
        # fix must stay out of that scheduler entirely, same as the existing
        # review-driven re-enqueue path already does.
        result = self.server._continue_persona_automation_if_work_remains({
            "run_id": "conductor-run-x", "project_id": "TEST", "node_ref": None,
            "session_anchor_ref": "some-anchor", "revision": 1,
        })
        self.assertEqual("PERSONA_AUTOMATION_CONTINUATION_NOT_APPLICABLE", result.get("status"))

    def test_run_missing_owner_anchor_is_never_auto_continued(self):
        result = self.server._continue_persona_automation_if_work_remains({
            "run_id": "run-no-anchor", "project_id": "TEST", "node_ref": "feature_x",
            "session_anchor_ref": "", "revision": 1,
        })
        self.assertEqual("PERSONA_AUTOMATION_CONTINUATION_NOT_APPLICABLE", result.get("status"))


if __name__ == "__main__":
    unittest.main()
