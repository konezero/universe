"""Bounded durable Persona Conductor run contract (P2-P4).

These tests use an isolated SQLite file and a fake Master queue callback.  They
verify the server boundary without starting a provider or a self-pinging loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from persona_automation import PersonaAutomationError, PersonaAutomationStore  # noqa: E402
from universe_server import UniverseHTTPServer  # noqa: E402


class PersonaAutomationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersonaAutomationStore(Path(self.tmp.name) / "automation.sqlite3")
        self.assignment = {
            "session_anchor_ref": "session_anchor_conductor_test",
            "project_id": "project_test",
            "persona_id": "persona_lead",
            "persona_revision": 3,
            "assignment_revision": 2,
            "state": "ACTIVE",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, key="start-1"):
        result = self.store.start_run(
            {
                "project_id": "project_test",
                "session_anchor_ref": self.assignment["session_anchor_ref"],
                "scope": "one bounded regression slice",
                "instruction": "inspect the existing implementation and report evidence",
                "request_id": key,
                "idempotency_key": key,
                "goal_ref": "goal-test",
                "goal_version": "7",
                "budget": {"max_dispatches": 1},
            },
            self.assignment,
        )
        return result["run"]

    def test_start_is_assignment_pinned_and_idempotent(self):
        run = self.start()
        self.assertEqual("RUNNING", run["state"])
        self.assertEqual("persona_lead", run["persona_id"])
        replay = self.store.start_run(
            {
                "project_id": "project_test",
                "session_anchor_ref": self.assignment["session_anchor_ref"],
                "scope": "one bounded regression slice",
                "instruction": "inspect the existing implementation and report evidence",
                "request_id": "start-1",
                "idempotency_key": "start-1",
                "goal_ref": "goal-test",
                "goal_version": "7",
                "budget": {"max_dispatches": 1},
            },
            self.assignment,
        )
        self.assertEqual("PERSONA_AUTOMATION_RUN_REPLAYED", replay["status"])
        with self.assertRaisesRegex(PersonaAutomationError, "active persona automation run"):
            self.start("start-2")

    def test_driver_receipt_is_durable_and_replay_safe(self):
        run = self.start()
        message = {
            "message_id": "master_driver_1",
            "target_session_anchor_ref": self.assignment["session_anchor_ref"],
            "node_ref": "feature_test",
            "created": True,
        }
        first = self.store.record_driver_message(
            run["run_id"], driver_key="initial-v1", message=message
        )
        self.assertEqual("PERSONA_AUTOMATION_DRIVER_ENQUEUED", first["status"])
        replay = self.store.record_driver_message(
            run["run_id"], driver_key="initial-v1", message=message
        )
        self.assertEqual("PERSONA_AUTOMATION_DRIVER_REPLAYED", replay["status"])
        self.assertEqual("master_driver_1", replay["driver"]["message_id"])
        with self.assertRaisesRegex(PersonaAutomationError, "idempotency key"):
            self.store.record_driver_message(
                run["run_id"],
                driver_key="initial-v1",
                message={**message, "message_id": "master_driver_other"},
            )

    def test_node_master_driver_is_exactly_targeted_and_woken_once(self):
        run = {
            "run_id": "persona_run_driver_test",
            "project_id": "project_test",
            "session_anchor_ref": self.assignment["session_anchor_ref"],
            "node_ref": "feature_test",
        }
        queued = []

        class Store:
            def create_master_message(self, project_id, value):
                queued.append((project_id, dict(value)))
                return {
                    "message_id": "master_driver_1",
                    "target_session_anchor_ref": value["target_session_anchor_ref"],
                    "node_ref": value["node_ref"],
                }, True

        recorded = []
        class Automation:
            def record_driver_message(self, run_id, *, driver_key, message):
                recorded.append((run_id, driver_key, dict(message)))
                return {"status": "PERSONA_AUTOMATION_DRIVER_ENQUEUED", "driver": dict(message)}

        wakes = []
        server = SimpleNamespace(
            store=Store(),
            persona_automation=Automation(),
            _wake_live_master_sessions=lambda project_id, *, reason: wakes.append((project_id, reason)),
        )
        result = UniverseHTTPServer._enqueue_persona_automation_driver(server, run)
        self.assertEqual("PERSONA_AUTOMATION_DRIVER_ENQUEUED", result["status"])
        self.assertEqual([("project_test", "PERSONA_AUTOMATION_CONTROL_QUEUED")], wakes)
        self.assertEqual(1, len(queued))
        request = queued[0][1]
        self.assertEqual(self.assignment["session_anchor_ref"], request["target_session_anchor_ref"])
        self.assertEqual("feature_test", request["node_ref"])
        self.assertIn("persona.automation.tick", request["instruction"])
        self.assertEqual("PERSONA_AUTOMATION_CONTROL", request["metadata"]["kind"])
        self.assertEqual("initial-v1", recorded[0][1])
        self.assertEqual(
            "persona-automation-driver:persona_run_driver_test:initial-v1",
            request["idempotency_key"],
        )
        UniverseHTTPServer._enqueue_persona_automation_driver(server, run, driver_key="kick-r3")
        self.assertEqual(2, len(queued))
        self.assertEqual(
            "persona-automation-driver:persona_run_driver_test:kick-r3",
            queued[1][1]["idempotency_key"],
        )

    def test_pause_resume_preserves_cursor_and_stopped_run_cannot_tick(self):
        run = self.start()
        tick = self.store.claim_tick({"run_id": run["run_id"], "owner_ref": self.assignment["session_anchor_ref"], "tick_id": "tick-1", "cursor": {"todo": "todo-1"}})
        self.assertEqual({"todo": "todo-1"}, tick["run"]["cursor"])
        paused = self.store.pause_run({"run_id": run["run_id"], "request_id": "pause-1"})
        self.assertEqual("PAUSED", paused["run"]["state"])
        resumed = self.store.resume_run({"run_id": run["run_id"], "request_id": "resume-1"})
        self.assertEqual({"todo": "todo-1"}, resumed["run"]["cursor"])
        stopped = self.store.stop_run({"run_id": run["run_id"], "request_id": "stop-1"})
        self.assertEqual("STOPPED", stopped["run"]["state"])
        with self.assertRaises(PersonaAutomationError):
            self.store.claim_tick({"run_id": run["run_id"], "owner_ref": self.assignment["session_anchor_ref"], "tick_id": "tick-2"})
        recovered = self.store.resume_run(
            {"run_id": run["run_id"], "request_id": "recover-1"},
            recover_stopped=True,
        )
        self.assertEqual("RUNNING", recovered["run"]["state"])

    def test_stale_lease_can_be_reclaimed_after_restart(self):
        run = self.start()
        self.store.claim_tick({"run_id": run["run_id"], "owner_ref": "old-owner", "tick_id": "tick-old", "lease_seconds": 90})
        expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
        with self.store._connection() as connection:
            connection.execute("UPDATE persona_automation_run SET lease_expires_at = ? WHERE run_id = ?", (expired, run["run_id"]))
        restarted = PersonaAutomationStore(self.store.database_path)
        reclaimed = restarted.claim_tick({"run_id": run["run_id"], "owner_ref": "new-owner", "tick_id": "tick-new"})
        self.assertTrue(reclaimed["event"]["takeover"])
        self.assertEqual("new-owner", reclaimed["run"]["lease"]["owner"])

    def test_decision_dispatch_review_requires_evidence_and_explicit_completion(self):
        run = self.start()
        owner = self.assignment["session_anchor_ref"]
        self.store.claim_tick({"run_id": run["run_id"], "owner_ref": owner, "tick_id": "tick-1"})
        decision = self.store.record_decision({
            "run_id": run["run_id"], "owner_ref": owner, "decision_id": "decision-1",
            "kind": "EXECUTE", "rationale": "the remaining TODO is inside the pinned scope",
            "evidence_refs": ["todo:todo-1", "plan:plan-7"], "target": {"todo_id": "todo-1"},
        })
        self.assertNotIn("keyword_score", decision["decision"])
        queued = []

        def enqueue(project_id, value):
            message = {"message_id": "master-message-1", "project_id": project_id, "idempotency_key": value["idempotency_key"]}
            queued.append(message)
            return message, True

        dispatch = self.store.dispatch_work({
            "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "dispatch-1",
            "title": "bounded TODO", "instruction": "implement the pinned TODO",
            "completion_conditions": ["tests pass", "result evidence is attached"],
        }, enqueue)
        self.assertEqual("PERSONA_AUTOMATION_WORK_DISPATCHED", dispatch["status"])
        self.assertEqual(1, len(queued))
        assignment = dispatch["dispatch"]
        review = self.store.record_review({
            "run_id": run["run_id"], "result_ref": "master-result-1", "outcome": "NOT_RUN",
            "evidence_refs": ["provider:quota"], "next_action": "retry after quota window",
            "dispatch_id": assignment["dispatch_id"],
            "assignment_revision": assignment["assignment_revision"],
            "source_message_id": assignment["message_id"],
        })
        self.assertEqual("NOT_RUN", review["review"]["outcome"])
        with self.assertRaisesRegex(PersonaAutomationError, "PASS review"):
            self.store.complete_run({"run_id": run["run_id"], "request_id": "complete-1", "complete": True})
        with self.assertRaisesRegex(PersonaAutomationError, "explicit verified acceptance"):
            self.store.record_review({
                "run_id": run["run_id"], "result_ref": "master-result-not-verified",
                "outcome": "PASS", "acceptance_status": "NOT_RUN", "evidence_refs": ["provider:quota"],
                "dispatch_id": assignment["dispatch_id"], "assignment_revision": assignment["assignment_revision"],
                "source_message_id": assignment["message_id"],
            })
        with self.assertRaisesRegex(PersonaAutomationError, "NOT_RUN UI"):
            self.store.record_review({
                "run_id": run["run_id"], "result_ref": "master-result-ui-not-run",
                "outcome": "PASS", "evidence_refs": ["ui:NOT_RUN"],
                "dispatch_id": assignment["dispatch_id"], "assignment_revision": assignment["assignment_revision"],
                "source_message_id": assignment["message_id"],
            })
        with self.assertRaisesRegex(PersonaAutomationError, "queue receipt"):
            self.store.record_review({
                "run_id": run["run_id"], "result_ref": "master-result-queue-only",
                "outcome": "PASS", "evidence_refs": ["delivery:NATIVE_QUEUED"],
                "dispatch_id": assignment["dispatch_id"], "assignment_revision": assignment["assignment_revision"],
                "source_message_id": assignment["message_id"],
            })
        self.store.record_review({"run_id": run["run_id"], "result_ref": "master-result-2", "outcome": "PASS", "evidence_refs": ["test:green"], "dispatch_id": assignment["dispatch_id"], "assignment_revision": assignment["assignment_revision"], "source_message_id": assignment["message_id"]})
        completed = self.store.complete_run({"run_id": run["run_id"], "request_id": "complete-2", "complete": True})
        self.assertEqual("COMPLETED", completed["run"]["state"])

    def test_master_completion_records_review_pending_without_session_reply_target(self):
        run = self.start("master-completion-route")
        owner = self.assignment["session_anchor_ref"]
        self.store.claim_tick({"run_id": run["run_id"], "owner_ref": owner, "tick_id": "completion-tick"})
        self.store.record_decision({
            "run_id": run["run_id"], "owner_ref": owner, "decision_id": "completion-decision",
            "kind": "EXECUTE", "rationale": "the Todo is inside scope",
            "evidence_refs": ["todo:exact"], "target": {"todo_id": "todo-exact"},
        })
        queued = []

        def enqueue(project_id, value):
            queued.append((project_id, dict(value)))
            return {"message_id": "master-completion-route-1"}, True

        dispatched = self.store.dispatch_work({
            "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "completion-dispatch",
            "title": "exact Todo", "instruction": "implement it",
            "completion_conditions": ["review verdict"],
        }, enqueue)
        metadata = queued[0][1]["metadata"]
        self.assertEqual("PERSONA_AUTOMATION_STORE", metadata["completion_route"])
        self.assertNotIn("reply_anchor_ref", metadata)
        self.assertNotIn("reply_terminal_id", metadata)
        assignment = dispatched["dispatch"]
        completion = {
            "run_id": run["run_id"], "dispatch_id": assignment["dispatch_id"],
            "assignment_revision": assignment["assignment_revision"],
            "source_message_id": assignment["message_id"], "result_ref": "result:exact",
            "body_text_utf8_sha256": "a" * 64,
            "completed_at": "2026-09-16T09:00:00Z",
        }
        recorded = self.store.record_master_completion(completion)
        self.assertEqual("PERSONA_AUTOMATION_MASTER_RESULT_RECORDED", recorded["status"])
        self.assertEqual("WAITING", recorded["run"]["state"])
        self.assertEqual("RESULT_READY_FOR_REVIEW", recorded["run"]["current_assignment"]["state"])
        self.assertIn("Independent Reviewer", recorded["run"]["next_condition"])
        replayed = self.store.record_master_completion(completion)
        self.assertEqual("PERSONA_AUTOMATION_MASTER_RESULT_REPLAYED", replayed["status"])
        with self.assertRaisesRegex(PersonaAutomationError, "differs"):
            self.store.record_master_completion({**completion, "result_ref": "result:changed"})

    def test_master_completion_creates_distinct_reviewer_worker_and_accepts_verdict(self):
        assignment = {**self.assignment, "node_ref": "feature_master_review"}
        run = self.store.start_run(
            {
                "project_id": "project_test",
                "session_anchor_ref": assignment["session_anchor_ref"],
                "scope": "one direct Master Todo",
                "instruction": "complete one bounded Todo",
                "request_id": "master-review-worker",
                "idempotency_key": "master-review-worker",
            },
            assignment,
        )["run"]
        owner = assignment["session_anchor_ref"]
        self.store.claim_tick({"run_id": run["run_id"], "owner_ref": owner, "tick_id": "master-review-tick"})
        self.store.record_decision({
            "run_id": run["run_id"], "owner_ref": owner,
            "decision_id": "master-review-decision", "kind": "EXECUTE",
            "rationale": "the exact node Todo is selected",
            "evidence_refs": ["todo:master-review"],
            "target": {"todo_id": "todo-master-review"},
        })
        queued = []
        dispatched = self.store.dispatch_work({
            "run_id": run["run_id"], "owner_ref": owner,
            "dispatch_id": "master-review-dispatch", "title": "direct Master work",
            "instruction": "complete the bounded Todo", "completion_conditions": ["evidence"],
        }, lambda project_id, value: (queued.append(dict(value)) or ({"message_id": "master-review-message", "project_id": project_id}, True)))
        assignment_row = dispatched["dispatch"]
        completion = self.store.record_master_completion({
            "run_id": run["run_id"], "dispatch_id": assignment_row["dispatch_id"],
            "assignment_revision": assignment_row["assignment_revision"],
            "source_message_id": assignment_row["message_id"],
            "result_ref": "master-result-review", "body_text_utf8_sha256": "c" * 64,
            "completed_at": "2026-09-17T00:00:00Z",
        })
        self.assertEqual("RESULT_READY_FOR_REVIEW", completion["run"]["current_assignment"]["state"])
        reviewer_assignment = {
            "assignment_id": "task_master_reviewer",
            "project_id": "project_test",
            "node_ref": "feature_master_review",
            "todo_id": "todo-master-review",
            "task_frame_id": None,
            "worker_role": "REVIEWER",
            "session_anchor_ref": "master-reviewer-anchor",
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": owner,
        }
        reviewer = self.store.attach_master_reviewer(
            {
                "run_id": run["run_id"],
                "dispatch_id": assignment_row["dispatch_id"],
                "assignment_revision": assignment_row["assignment_revision"],
                "source_message_id": assignment_row["message_id"],
                "result_ref": "master-result-review",
                "body_text_utf8_sha256": "c" * 64,
                "result_text": "direct Master result",
            },
            create_reviewer=lambda spec: self.assertEqual(
                "master-result-review", spec["worker_result_ref"]
            ) or {"assignment": reviewer_assignment},
        )
        self.assertEqual("PERSONA_AUTOMATION_MASTER_REVIEWER_ASSIGNED", reviewer["status"])
        self.assertEqual("REVIEWER_ASSIGNED", reviewer["run"]["current_assignment"]["state"])
        self.assertEqual("MASTER", reviewer["run"]["current_reviewer"]["source_role"])
        verdict = self.store.record_reviewer_verdict({
            "run_id": run["run_id"], "dispatch_id": assignment_row["dispatch_id"],
            "reviewer_assignment_id": reviewer_assignment["assignment_id"],
            "reviewer_assignment_revision": 1,
            "reviewer_anchor_ref": reviewer_assignment["session_anchor_ref"],
            "worker_result_ref": "master-result-review", "outcome": "PASS",
            "acceptance_status": "VERIFIED_EVIDENCE",
            "evidence_refs": ["review:master-result"],
        })
        self.assertEqual("PASS", verdict["review"]["outcome"])
        self.assertEqual("REVIEWED", verdict["run"]["current_assignment"]["state"])

    def test_legacy_self_reply_migration_derives_revision_only_from_exact_current_assignment(self):
        run = self.start("legacy-self-reply")
        owner = self.assignment["session_anchor_ref"]
        self.store.claim_tick({"run_id": run["run_id"], "owner_ref": owner, "tick_id": "legacy-tick"})
        self.store.record_decision({
            "run_id": run["run_id"], "owner_ref": owner, "decision_id": "legacy-decision",
            "kind": "EXECUTE", "rationale": "migrate one exact old completion",
            "evidence_refs": ["test:legacy"],
        })
        dispatch = self.store.dispatch_work({
            "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "legacy-dispatch",
            "title": "legacy", "instruction": "legacy", "completion_conditions": ["review"],
        }, lambda project_id, value: ({"message_id": "legacy-master-message"}, True))
        assignment = dispatch["dispatch"]
        recorded = self.store.record_master_completion({
            "run_id": run["run_id"], "dispatch_id": assignment["dispatch_id"],
            "source_message_id": assignment["message_id"], "result_ref": "legacy:result",
            "body_text_utf8_sha256": "b" * 64, "completed_at": "2026-09-16T09:10:00Z",
            "legacy_self_reply_migration": True,
        })
        self.assertEqual("LEGACY_SELF_REPLY_MIGRATION", recorded["result"]["route"])
        self.assertEqual(assignment["assignment_revision"], recorded["result"]["assignment_revision"])
        with self.assertRaisesRegex(PersonaAutomationError, "differs"):
            self.store.record_master_completion({
                "run_id": run["run_id"], "dispatch_id": "other-dispatch",
                "source_message_id": assignment["message_id"], "result_ref": "wrong",
                "body_text_utf8_sha256": "c" * 64, "completed_at": "2026-09-16T09:10:01Z",
                "legacy_self_reply_migration": True,
            })

    @staticmethod
    def _planner_stub(run, goals, todos):
        class Store:
            def list_project_goals(self, project_id):
                return [item for item in goals if item["project_id"] == project_id]

            def list_todos(self):
                return list(todos)

            def list_events(self, project_id, limit=100):
                return []

            def goal_work_plan_surface(self, goal_id):
                return {
                    "feature_goal_derivation": None,
                    "candidates": [],
                    "adoption": None,
                    "application": None,
                }

        class Automation:
            def get_run(self, run_id):
                return dict(run)

            def record_decision(self, value):
                return {"status": "PERSONA_AUTOMATION_DECISION_RECORDED", "decision": value}

        return SimpleNamespace(
            store=Store(),
            persona_automation=Automation(),
            multi_rooms=SimpleNamespace(list_bindings=lambda room_id: []),
        )

    def test_plan_selects_node_scoped_todo_without_goal_pin(self):
        run = {
            "run_id": "run-plan-node", "project_id": "project_test",
            "session_anchor_ref": "session_anchor_master_test",
            "scope": "node:feature-owned", "node_ref": "feature-owned",
            "goal_ref": None, "goal_version": None,
        }
        goals = [{"goal_id": "goal-other", "project_id": "project_test", "revision": 4, "state": "BLOCKED", "node_ref": "feature-other"}]
        todos = [
            {"todo_id": "todo-other", "project_id": "project_test", "node_ref": "feature-other", "goal_id": "goal-other", "state": "READY", "priority": "P0", "sort_order": 0, "source_kind": "USER"},
            {"todo_id": "todo-owned", "project_id": "project_test", "node_ref": "feature-owned", "goal_id": None, "state": "READY", "priority": "P1", "sort_order": 0, "source_kind": "USER"},
        ]
        server = self._planner_stub(run, goals, todos)
        planned = UniverseHTTPServer._persona_automation_plan(server, {
            "run_id": run["run_id"], "owner_ref": run["session_anchor_ref"], "decision_id": "plan-node",
        })
        self.assertEqual("EXECUTE", planned["decision"]["kind"])
        self.assertEqual("todo-owned", planned["decision"]["target"]["todo_id"])
        self.assertEqual("NODE_ASSIGNMENT_BOUND", planned["planning_context"]["selection"]["scope_alignment"])
        self.assertEqual("TODO_SCOPE_READY", planned["planning_context"]["selection"]["status"])
        self.assertEqual(["todo-owned"], [item["todo_id"] for item in planned["planning_context"]["scoped_todos"]])

    def test_plan_prefers_in_progress_todo_in_scope_over_goal_metadata(self):
        run = {
            "run_id": "run-plan-priority", "project_id": "project_test",
            "session_anchor_ref": "session_anchor_master_test",
            "scope": "node:feature-owned", "node_ref": "feature-owned",
            "goal_ref": None, "goal_version": None,
        }
        goals = [{"goal_id": "goal-context", "project_id": "project_test", "revision": 2, "state": "READY", "node_ref": "feature-owned"}]
        todos = [
            {"todo_id": "todo-ready", "project_id": "project_test", "node_ref": "feature-owned", "goal_id": "goal-context", "state": "READY", "priority": "P0", "sort_order": 0, "source_kind": "USER"},
            {"todo_id": "todo-progress", "project_id": "project_test", "node_ref": "feature-owned", "goal_id": "goal-context", "state": "IN_PROGRESS", "priority": "P2", "sort_order": 3, "source_kind": "MASTER"},
        ]
        server = self._planner_stub(run, goals, todos)
        planned = UniverseHTTPServer._persona_automation_plan(server, {
            "run_id": run["run_id"], "owner_ref": run["session_anchor_ref"], "decision_id": "plan-priority",
        })
        self.assertEqual("EXECUTE", planned["decision"]["kind"])
        self.assertEqual("todo-progress", planned["decision"]["target"]["todo_id"])
        self.assertEqual("goal-context", planned["decision"]["target"]["goal_id"])
        self.assertEqual(2, planned["planning_context"]["decision_basis"]["scoped_todo_count"])

    def test_plan_escalates_blocked_todo_without_goal_version_gate(self):
        run = {
            "run_id": "run-plan-blocked", "project_id": "project_test",
            "session_anchor_ref": "session_anchor_master_test",
            "scope": "node:feature-owned", "node_ref": "feature-owned",
            "goal_ref": None, "goal_version": None,
        }
        todos = [{"todo_id": "todo-blocked", "project_id": "project_test", "node_ref": "feature-owned", "goal_id": "goal-stale", "state": "BLOCKED", "priority": "P0", "sort_order": 0, "source_kind": "USER"}]
        server = self._planner_stub(run, [], todos)
        planned = UniverseHTTPServer._persona_automation_plan(server, {
            "run_id": run["run_id"], "owner_ref": run["session_anchor_ref"], "decision_id": "plan-blocked",
        })
        self.assertEqual("ESCALATE", planned["decision"]["kind"])
        self.assertEqual("todo-blocked", planned["decision"]["target"]["todo_id"])
        self.assertNotEqual("GOAL_VERSION_REQUIRED", planned["planning_context"]["selection"]["status"])

    def test_plan_rejects_explicit_scope_outside_bound_node(self):
        run = {
            "run_id": "run-plan-scope-mismatch", "project_id": "project_test",
            "session_anchor_ref": "session_anchor_master_test",
            "scope": "node:feature-owned", "node_ref": "feature-owned",
            "goal_ref": None, "goal_version": None,
        }
        todos = [{"todo_id": "todo-scoped", "project_id": "project_test", "node_ref": "feature-owned", "goal_id": None, "state": "READY", "priority": "P1", "sort_order": 0, "source_kind": "USER"}]
        server = self._planner_stub(run, [], todos)
        planned = UniverseHTTPServer._persona_automation_plan(server, {
            "run_id": run["run_id"], "owner_ref": run["session_anchor_ref"], "decision_id": "plan-scope-mismatch",
            "scope_ref": "feature-other",
        })
        self.assertEqual("WAIT", planned["decision"]["kind"])
        self.assertEqual("MISMATCH", planned["planning_context"]["selection"]["scope_alignment"])
        self.assertNotIn("todo-scoped", str(planned["decision"]["target"]))

    def test_plan_meeting_is_an_explicit_reviewable_work_plan_route(self):
        run = {
            "run_id": "run-plan-meeting",
            "project_id": "project_test",
            "session_anchor_ref": "session_anchor_conductor_test",
            "scope": "goal-design bounded planning",
            "goal_ref": "goal-design",
            "goal_version": "1",
        }
        goal = {"goal_id": "goal-design", "project_id": "project_test", "revision": 1, "state": "DESIGNING", "node_ref": None}

        class MeetingStore:
            def list_project_goals(self, project_id):
                return [goal]

            def list_todos(self):
                return []

            def goal_work_plan_surface(self, goal_id):
                return {
                    "feature_goal_derivation": {"feature_id": "feature-design"},
                    "candidates": [],
                    "adoption": None,
                    "application": None,
                }

            def get_feature_node(self, feature_id):
                return {"feature_id": feature_id, "meeting_room_id": "room-design"}

        class MeetingAutomation:
            def get_run(self, run_id):
                return dict(run)

            def record_decision(self, value):
                return {"status": "PERSONA_AUTOMATION_DECISION_RECORDED", "decision": value}

        bindings = [
            {"slot_role": "MODEL", "provider": "CODEX", "provider_session_ref": "codex-ref", "metadata": {"provider_chat_key": "codex-chat"}},
            {"slot_role": "MODEL", "provider": "CLAUDE", "provider_session_ref": "claude-ref", "metadata": {"provider_chat_key": "claude-chat"}},
        ]
        server = SimpleNamespace(
            store=MeetingStore(),
            persona_automation=MeetingAutomation(),
            multi_rooms=SimpleNamespace(list_bindings=lambda room_id: bindings),
        )
        planned = UniverseHTTPServer._persona_automation_plan(server, {
            "run_id": run["run_id"], "owner_ref": run["session_anchor_ref"], "decision_id": "plan-meeting",
            "goal_id": "goal-design", "goal_version": "1", "scope_ref": "goal-design",
            "work_owner_ref": run["session_anchor_ref"],
        })
        self.assertEqual("MEETING", planned["decision"]["kind"])
        meeting = planned["decision"]["meeting"]
        self.assertEqual("/v1/goals/goal-design/work-plan-runs", meeting["route"])
        self.assertEqual("NONE_UNTIL_EXPLICIT_ROUTE_CALL", meeting["provider_invocation"])
        self.assertEqual("GOAL_WORK_PLAN_CANDIDATES_READY", meeting["result_contract"])
        self.assertIn("review", meeting["review_gate"])
        self.assertTrue(meeting["request_body"]["run_id"].startswith("persona_work_plan_"))
        self.assertNotIn(":", meeting["request_body"]["run_id"])

    def test_work_plan_parser_adapts_reviewed_route_candidate(self):
        import json

        route_candidate = {
            "title": "Route B",
            "summary": "A bounded alternative route for review.",
            "specification": "A reviewable alternative implementation route.",
            "route": {
                "steps": [
                    {"step_id": "step-1", "title": "Frame the comparison", "summary": "Pin the comparison criteria.", "phase": "Design"},
                    {"step_id": "step-2", "title": "Review the route", "summary": "Compare the bounded alternatives.", "phase": "Review"},
                ],
                "dependencies": [{"from_step_id": "step-1", "to_step_id": "step-2", "kind": "PRECEDES"}],
                "branches": [],
                "architecture_decisions": [],
                "implementation_phases": [
                    {"title": "Design", "step_ids": ["step-1"]},
                    {"title": "Review", "step_ids": ["step-2"]},
                ],
                "risks": [],
                "acceptance_conditions": ["The comparison is ready for explicit review."],
                "estimates": {"effort": "UNKNOWN", "cost": "UNKNOWN", "quota": "UNKNOWN"},
                "evidence_refs": [],
            },
        }
        plan = UniverseHTTPServer._parse_goal_work_plan_output(json.dumps(route_candidate))
        self.assertEqual("universe.goal-work-plan.v1", plan["schema"])
        self.assertEqual(2, len(plan["milestones"]))
        self.assertEqual("Frame the comparison", plan["milestones"][0]["todos"][0]["title"])
        self.assertEqual("AUTO", plan["milestones"][1]["todos"][0]["priority"])
        compact_route = {
            "title": "Route C",
            "summary": "A compact reviewed route.",
            "route": [
                {
                    "title": "Pin the contract",
                    "description": "Record the bounded behavior.",
                    "acceptance": "The behavior is reviewable.",
                    "priority": "P1",
                }
            ],
        }
        compact_plan = UniverseHTTPServer._parse_goal_work_plan_output(json.dumps(compact_route))
        self.assertEqual("Route C", compact_plan["title"])
        self.assertEqual("Pin the contract", compact_plan["milestones"][0]["todos"][0]["title"])
        compact_route["route"][0] = {
            "step": "Pin the contract",
            "description": "Record the bounded behavior.",
            "acceptance": "The behavior is reviewable.",
        }
        step_plan = UniverseHTTPServer._parse_goal_work_plan_output(json.dumps(compact_route))
        self.assertEqual("Pin the contract", step_plan["milestones"][0]["todos"][0]["title"])
        detail_route = {
            "title": "Route D",
            "summary": "A provider-shaped route using detail.",
            "route": [
                {
                    "title": "Preserve evidence",
                    "detail": "Keep provider evidence attached to the candidate.",
                    "acceptance": "A reviewer can trace the candidate input.",
                }
            ],
        }
        detail_plan = UniverseHTTPServer._parse_goal_work_plan_output(json.dumps(detail_route))
        self.assertEqual(
            "Keep provider evidence attached to the candidate.",
            detail_plan["milestones"][0]["todos"][0]["detail"],
        )

    def test_judge_persists_luna_invocation_and_replays_without_provider_call(self):
        run = self.start("judge-execute")
        owner = self.assignment["session_anchor_ref"]
        self.store.claim_tick({
            "run_id": run["run_id"],
            "owner_ref": owner,
            "tick_id": "judge-tick",
            "lease_seconds": 600,
        })

        class GoalStore:
            def list_project_goals(self, project_id):
                return [{
                    "goal_id": "goal-test", "project_id": project_id,
                    "revision": 7, "state": "READY", "node_ref": None,
                }]

            def list_todos(self):
                return [{
                    "todo_id": "todo-judge", "project_id": "project_test",
                    "goal_id": "goal-test", "state": "READY", "priority": "P1",
                    "sort_order": 0, "source_kind": "USER",
                }]

            def goal_work_plan_surface(self, goal_id):
                return {
                    "feature_goal_derivation": None,
                    "candidates": [], "adoption": None, "application": None,
                }

        class FakeRuntime:
            def __init__(self):
                self.calls = 0

            def provider_capability(self, provider):
                return {"provider": provider, "status": "AVAILABLE", "model": "gpt-5.6-luna"}

            def invoke_structured_task(self, **kwargs):
                self.calls += 1
                return {
                    "status": "TASK_FRAME_RESULT_RECORDED",
                    "model_ref": "provider://CODEX/model/gpt-5.6-luna",
                    "worker_id": "worker-judge",
                    "worker_run_ref": "run-judge",
                    "result_receipt_ref": "receipt-judge",
                    "task_frame_result_status": "TASK_COMPLETED",
                    "terminal_result_verified": True,
                    "structured_result": {
                        "kind": "EXECUTE",
                        "rationale": "the supplied Todo is ready",
                        "evidence_refs": ["todo:todo-judge"],
                        "target": {"todo_id": "todo-judge", "state": "READY"},
                        "next_condition": "dispatch after review",
                    },
                }

        runtime = FakeRuntime()
        server = SimpleNamespace(
            store=GoalStore(),
            persona_automation=self.store,
            multi_rooms=SimpleNamespace(list_bindings=lambda room_id: []),
            runtime_host=runtime,
            _persona_automation_runtime_binding=lambda _run: {
                "endpoint": "http://127.0.0.1:1", "token": "opaque",
                "session_id": "session-judge", "origin_anchor_ref": owner,
                "origin_frame_id": "current", "parent_actor_ref": "persona-judge",
                "parent_evidence_ref": "anchor://judge", "binding_evidence_ref": "binding://judge",
            },
        )
        server._persona_automation_plan = lambda value, record=True: UniverseHTTPServer._persona_automation_plan(server, value, record=record)
        judged = UniverseHTTPServer._persona_automation_judge(server, {
            "run_id": run["run_id"], "owner_ref": owner,
            "decision_id": "judge-decision", "goal_id": "goal-test",
            "goal_version": "7", "scope_ref": "goal-test",
            "work_owner_ref": owner,
        })
        self.assertEqual("PERSONA_AUTOMATION_JUDGEMENT_RECORDED", judged["status"])
        self.assertEqual("EXECUTE", judged["decision"]["kind"])
        self.assertEqual("receipt-judge", judged["provider_invocation"]["result_receipt_ref"])
        self.assertEqual(owner, judged["provider_invocation"]["owner_ref"])
        self.assertEqual(1, runtime.calls)
        replay = UniverseHTTPServer._persona_automation_judge(server, {
            "run_id": run["run_id"], "owner_ref": owner,
            "decision_id": "judge-decision", "goal_id": "goal-test",
            "goal_version": "7", "scope_ref": "goal-test",
            "work_owner_ref": owner,
        })
        self.assertEqual("PERSONA_AUTOMATION_JUDGEMENT_REPLAYED", replay["status"])
        self.assertEqual(1, runtime.calls)

    def test_judge_executes_bounded_meeting_and_keeps_review_pending(self):
        run = self.store.start_run({
            "project_id": "project_test",
            "session_anchor_ref": self.assignment["session_anchor_ref"],
            "scope": "goal-design bounded planning",
            "instruction": "obtain a bounded design meeting result",
            "request_id": "judge-meeting",
            "idempotency_key": "judge-meeting",
            "goal_ref": "goal-design",
            "goal_version": "1",
        }, self.assignment)["run"]
        owner = self.assignment["session_anchor_ref"]
        self.store.claim_tick({
            "run_id": run["run_id"], "owner_ref": owner,
            "tick_id": "meeting-tick", "lease_seconds": 600,
        })

        class GoalStore:
            def list_project_goals(self, project_id):
                return [{
                    "goal_id": "goal-design", "project_id": project_id,
                    "revision": 1, "state": "DESIGNING", "node_ref": None,
                }]

            def list_todos(self):
                return []

            def goal_work_plan_surface(self, goal_id):
                return {
                    "feature_goal_derivation": {"feature_id": "feature-design"},
                    "candidates": [], "adoption": None, "application": None,
                }

            def get_feature_node(self, feature_id):
                return {"feature_id": feature_id, "meeting_room_id": "room-design"}

        class FakeRuntime:
            def provider_capability(self, provider):
                return {"provider": provider, "status": "AVAILABLE", "model": "gpt-5.6-luna"}

            def invoke_structured_task(self, **kwargs):
                return {
                    "status": "TASK_FRAME_RESULT_RECORDED",
                    "model_ref": "provider://CODEX/model/gpt-5.6-luna",
                    "worker_id": "worker-meeting", "worker_run_ref": "run-meeting",
                    "result_receipt_ref": "receipt-meeting",
                    "task_frame_result_status": "TASK_COMPLETED",
                    "terminal_result_verified": True,
                    "structured_result": {
                        "kind": "MEETING", "rationale": "design evidence needs review",
                        "evidence_refs": ["goal:goal-design"],
                        "target": {"goal_id": "goal-design"},
                        "next_condition": "review candidates",
                    },
                }

        meeting_calls = []
        bindings = [
            {"slot_role": "MODEL", "provider": "CODEX", "provider_session_ref": "codex", "metadata": {"provider_chat_key": "codex"}},
            {"slot_role": "MODEL", "provider": "CLAUDE", "provider_session_ref": "claude", "metadata": {"provider_chat_key": "claude"}},
        ]
        server = SimpleNamespace(
            store=GoalStore(), persona_automation=self.store,
            multi_rooms=SimpleNamespace(list_bindings=lambda room_id: bindings),
            runtime_host=FakeRuntime(),
            run_goal_work_plan_meeting=lambda goal_id, request: (
                meeting_calls.append((goal_id, request))
                or {"status": "GOAL_WORK_PLAN_CANDIDATES_READY", "candidates": [{"candidate_id": "candidate-1"}]}
            ),
            _persona_automation_runtime_binding=lambda _run: {
                "endpoint": "http://127.0.0.1:1", "token": "opaque",
                "session_id": "session-meeting", "origin_anchor_ref": owner,
                "origin_frame_id": "current", "parent_actor_ref": "persona-judge",
                "parent_evidence_ref": "anchor://meeting", "binding_evidence_ref": "binding://meeting",
            },
        )
        server._persona_automation_plan = lambda value, record=True: UniverseHTTPServer._persona_automation_plan(server, value, record=record)
        judged = UniverseHTTPServer._persona_automation_judge(server, {
            "run_id": run["run_id"], "owner_ref": owner,
            "decision_id": "meeting-decision", "goal_id": "goal-design",
            "goal_version": "1", "scope_ref": "goal-design",
            "work_owner_ref": owner, "max_turns": 4,
        })
        self.assertEqual("MEETING", judged["decision"]["kind"])
        self.assertEqual("GOAL_WORK_PLAN_CANDIDATES_READY", judged["meeting"]["status"])
        self.assertEqual(1, len(meeting_calls))
        self.assertEqual("WAITING", judged["run"]["state"])
        self.assertEqual("receipt-meeting", judged["provider_invocation"]["result_receipt_ref"])

    def _prepare_execute(self):
        run = self.start("prepare-execute")
        owner = self.assignment["session_anchor_ref"]
        self.store.claim_tick({"run_id": run["run_id"], "owner_ref": owner, "tick_id": "prepare-tick"})
        self.store.record_decision({
            "run_id": run["run_id"], "owner_ref": owner, "decision_id": "prepare-decision",
            "kind": "EXECUTE", "rationale": "bounded evidence is sufficient", "evidence_refs": ["todo:one"],
        })
        return run, owner

    def test_distinct_dispatch_cannot_overwrite_active_assignment(self):
        run, owner = self._prepare_execute()
        queued = {}

        def enqueue(project_id, value):
            message = queued.get(value["idempotency_key"])
            if message is None:
                message = {"message_id": "message-" + str(len(queued) + 1), "project_id": project_id, "idempotency_key": value["idempotency_key"]}
                queued[value["idempotency_key"]] = message
                return message, True
            return message, False

        first = self.store.dispatch_work({
            "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "dispatch-one",
            "title": "one", "instruction": "one", "completion_conditions": ["one"],
        }, enqueue)
        with self.assertRaisesRegex(PersonaAutomationError, "different bounded work assignment"):
            self.store.dispatch_work({
                "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "dispatch-two",
                "title": "two", "instruction": "two", "completion_conditions": ["two"],
            }, enqueue)
        self.assertEqual(first["dispatch"]["dispatch_id"], self.store.get_run(run["run_id"])["current_assignment"]["dispatch_id"])
        self.assertEqual(1, len(queued))

    def test_queue_acceptance_then_record_failure_retries_same_message(self):
        run, owner = self._prepare_execute()
        calls = []
        messages = {}

        def enqueue(project_id, value):
            calls.append(value["idempotency_key"])
            if value["idempotency_key"] not in messages:
                messages[value["idempotency_key"]] = {"message_id": "message-retry", "project_id": project_id, "idempotency_key": value["idempotency_key"]}
                return messages[value["idempotency_key"]], True
            return messages[value["idempotency_key"]], False

        original_event = self.store._event
        failed = {"value": False}

        def event_once(connection, run_id, event_type, idempotency_key, payload):
            if event_type == "WORK_DISPATCHED" and not failed["value"]:
                failed["value"] = True
                raise sqlite3.OperationalError("simulated run-record failure")
            return original_event(connection, run_id, event_type, idempotency_key, payload)

        self.store._event = event_once
        request = {
            "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "dispatch-retry",
            "title": "retry", "instruction": "retry", "completion_conditions": ["evidence"],
        }
        with self.assertRaises(sqlite3.OperationalError):
            self.store.dispatch_work(request, enqueue)
        replay = self.store.dispatch_work(request, enqueue)
        self.assertEqual("PERSONA_AUTOMATION_WORK_REPLAYED", replay["status"])
        self.assertEqual("message-retry", replay["dispatch"]["message_id"])
        self.assertEqual(2, len(calls))
        self.assertEqual(1, len(messages))

    def test_review_requires_current_assignment_provenance_and_replay_content(self):
        run, owner = self._prepare_execute()
        dispatch = self.store.dispatch_work({
            "run_id": run["run_id"], "owner_ref": owner, "dispatch_id": "dispatch-review",
            "title": "review", "instruction": "review", "completion_conditions": ["evidence"],
        }, lambda project_id, value: ({"message_id": "message-review", "project_id": project_id}, True))
        assignment = dispatch["dispatch"]
        with self.assertRaisesRegex(PersonaAutomationError, "not bound to the current assignment"):
            self.store.record_review({
                "run_id": run["run_id"], "result_ref": "wrong", "outcome": "PASS", "evidence_refs": ["test"],
                "dispatch_id": assignment["dispatch_id"], "assignment_revision": assignment["assignment_revision"], "source_message_id": "other-message",
            })
        request = {
            "run_id": run["run_id"], "result_ref": "result-review", "outcome": "PASS", "evidence_refs": ["test"],
            "note": "verified", "next_action": "complete", "dispatch_id": assignment["dispatch_id"],
            "assignment_revision": assignment["assignment_revision"], "source_message_id": assignment["message_id"],
        }
        recorded = self.store.record_review(request)
        self.assertEqual("PASS", recorded["review"]["outcome"])
        self.assertEqual("PERSONA_AUTOMATION_REVIEW_REPLAYED", self.store.record_review(request)["status"])
        with self.assertRaisesRegex(PersonaAutomationError, "different review content"):
            self.store.record_review({**request, "note": "changed"})


class PersonaAutomationActionIntegrationTests(unittest.TestCase):
    """Exercise the HTTP Action boundary with a real project and Anchor."""

    @classmethod
    def setUpClass(cls):
        import test_memory_candidates_and_delegations as fixtures

        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def act(self, action_id, request):
        body = dict(request)
        if action_id in {"persona.create", "persona.assign", "persona.automation.start", "persona.automation.pause", "persona.automation.resume", "persona.automation.stop", "persona.automation.complete"}:
            body.setdefault("request_id", f"integration-{action_id.replace('.', '-')}-{id(request)}")
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": body})

    def test_action_run_surface_and_master_queue_dispatch(self):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": "persona-automation-action-session",
            "node": "TEST",
            "mode": "CONDUCTOR",
            "provider": "CODEX",
        })
        anchor = material["session_anchor_ref"]
        status, persona_result = self.act("persona.create", {"title": "bounded lead", "body": "근거를 확인하고 범위 안의 작업만 배정한다."})
        self.assertEqual(200, status, persona_result)
        persona = persona_result["persona"]
        status, assignment = self.act("persona.assign", {
            "session_anchor_ref": anchor,
            "project_id": "TEST",
            "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"],
            "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, assignment)
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST",
            "session_anchor_ref": anchor,
            "scope": "one bounded test",
            "instruction": "inspect evidence and dispatch one bounded task",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, tick = self.act("persona.automation.tick", {"run_id": run["run_id"], "owner_ref": anchor, "tick_id": "integration-tick"})
        self.assertEqual(200, status, tick)
        status, planned = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor, "decision_id": "integration-plan",
        })
        self.assertEqual(200, status, planned)
        self.assertIn(planned["decision"]["kind"], {"WAIT", "EXECUTE", "MEETING", "ESCALATE"})
        self.assertIn("planning_context", planned)
        status, decision = self.act("persona.automation.decide", {
            "run_id": run["run_id"], "owner_ref": anchor, "decision_id": "integration-decision",
            "kind": "EXECUTE", "rationale": "the test is inside the declared scope", "evidence_refs": ["test:scope"],
        })
        self.assertEqual(200, status, decision)
        wakes = []
        original_wake = self.server._wake_live_master_sessions
        self.server._wake_live_master_sessions = lambda project_id, *, reason: wakes.append((project_id, reason)) or 1
        try:
            status, dispatched = self.act("persona.automation.dispatch", {
                "run_id": run["run_id"], "owner_ref": anchor, "dispatch_id": "integration-dispatch",
                "title": "bounded task", "instruction": "run the isolated check", "completion_conditions": ["result evidence"],
            })
        finally:
            self.server._wake_live_master_sessions = original_wake
        self.assertEqual(201, status, dispatched)
        self.assertEqual([("TEST", "PERSONA_AUTOMATION_WORK_DISPATCHED")], wakes)
        self.assertEqual(1, dispatched["woken_master_sessions"])
        status, surface = self.request("GET", "/v1/projects/TEST/persona-automation")
        self.assertEqual(200, status, surface)
        self.assertEqual(run["run_id"], surface["run"]["run_id"])
        self.assertEqual(dispatched["message"]["message_id"], surface["run"]["current_assignment"]["message_id"])
        status, stopped = self.act("persona.automation.stop", {
            "run_id": run["run_id"], "reason": "test cleanup",
        })
        self.assertEqual(200, status, stopped)
        self.assertEqual("STOPPED", stopped["run"]["state"])

    def test_non_passing_review_creates_one_priority_inheriting_followup_todo(self):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": "persona-review-followup-session",
            "node": "TEST",
            "mode": "CONDUCTOR",
            "provider": "CODEX",
        })
        anchor = material["session_anchor_ref"]
        status, persona_result = self.act("persona.create", {
            "title": "review follow-up lead", "body": "Record reviewed follow-up work."
        })
        self.assertEqual(200, status, persona_result)
        persona = persona_result["persona"]
        status, assignment = self.act("persona.assign", {
            "session_anchor_ref": anchor,
            "project_id": "TEST",
            "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"],
            "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, assignment)
        source = self.server.store.create_todo({
            "scope_kind": "PROJECT", "project_id": "TEST",
            "title": "Source Todo for review follow-up", "detail": "source",
            "priority": "P0", "state": "IN_PROGRESS", "source_kind": "MASTER",
            "sort_order": 41,
        })
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor,
            "scope": "review follow-up integration", "instruction": "review one Todo",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, _ = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor, "tick_id": "followup-tick",
        })
        self.assertEqual(200, status)
        status, _ = self.act("persona.automation.decide", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "decision_id": "followup-decision", "kind": "EXECUTE",
            "rationale": "the source Todo is in scope", "evidence_refs": ["test:source"],
            "target": {"todo_id": source["todo_id"]},
        })
        self.assertEqual(200, status)
        status, dispatched = self.act("persona.automation.dispatch", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "dispatch_id": "followup-dispatch", "title": "review source Todo",
            "instruction": "inspect source", "completion_conditions": ["review evidence"],
        })
        self.assertEqual(201, status, dispatched)
        review_request = {
            "run_id": run["run_id"], "result_ref": "followup-result",
            "outcome": "NEEDS_REVISION", "evidence_refs": ["test:review"],
            "next_action": "Add the missing regression coverage.",
            "dispatch_id": dispatched["dispatch"]["dispatch_id"],
            "assignment_revision": dispatched["dispatch"]["assignment_revision"],
            "source_message_id": dispatched["message"]["message_id"],
        }
        status, reviewed = self.act("persona.automation.review", review_request)
        self.assertEqual(200, status, reviewed)
        followup = reviewed["followup"]
        self.assertTrue(followup["todo_created"])
        self.assertEqual("P0", followup["todo"]["priority"])
        self.assertEqual("READY", followup["todo"]["state"])
        self.assertEqual("MASTER", followup["todo"]["source_kind"])
        self.assertIn(source["todo_id"], followup["todo"]["detail"])
        self.assertEqual(
            "PERSONA_AUTOMATION_FOLLOWUP_DRIVER_NOT_APPLICABLE",
            reviewed["driver"]["status"],
        )
        status, pause_rejected = self.act("persona.automation.pause", {
            "run_id": run["run_id"], "reason": "incorrect review gate pause",
        })
        self.assertEqual(409, status, pause_rejected)
        self.assertEqual(
            "PERSONA_AUTOMATION_REVIEW_FOLLOWUP_PENDING",
            pause_rejected["error_code"],
        )
        status, stop_rejected = self.act("persona.automation.stop", {
            "run_id": run["run_id"], "reason": "incorrect automatic stop",
        })
        self.assertEqual(409, status, stop_rejected)
        self.assertEqual(
            "PERSONA_AUTOMATION_REVIEW_FOLLOWUP_PENDING",
            stop_rejected["error_code"],
        )
        status, replayed = self.act("persona.automation.review", review_request)
        self.assertEqual(200, status, replayed)
        self.assertFalse(replayed["followup"]["todo_created"])
        self.assertEqual(followup["todo"]["todo_id"], replayed["followup"]["todo"]["todo_id"])
        status, _ = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor, "tick_id": "followup-plan-tick",
        })
        self.assertEqual(200, status)
        status, planned = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "decision_id": "followup-remediation-decision",
        })
        self.assertEqual(200, status, planned)
        self.assertEqual(followup["todo"]["todo_id"], planned["decision"]["target"]["todo_id"])
        status, stopped = self.act("persona.automation.stop", {
            "run_id": run["run_id"], "reason": "explicit operator stop", "force": True,
        })
        self.assertEqual(200, status, stopped)


if __name__ == "__main__":
    unittest.main()
