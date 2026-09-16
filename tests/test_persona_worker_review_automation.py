"""Focused contract tests for Persona WORKER_REVIEW orchestration."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from persona_automation import PersonaAutomationError, PersonaAutomationStore  # noqa: E402
from universe_server import UniverseHTTPServer  # noqa: E402


class PersonaWorkerReviewAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersonaAutomationStore(Path(self.tmp.name) / "automation.sqlite3")
        self.assignment = {
            "session_anchor_ref": "master-anchor",
            "project_id": "project-worker-review",
            "persona_id": "persona-master",
            "persona_revision": 4,
            "assignment_revision": 2,
            "state": "ACTIVE",
            "node_ref": "feature-worker-review",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _prepared_run(self):
        run = self.store.start_run(
            {
                "project_id": self.assignment["project_id"],
                "session_anchor_ref": self.assignment["session_anchor_ref"],
                "scope": "feature-worker-review",
                "instruction": "run one bounded Worker task",
                "request_id": "worker-review-start",
                "execution_mode": "WORKER_REVIEW",
                "worker_provider": "CODEX",
                "worker_model_ref": "gpt-5.6-luna",
            },
            self.assignment,
        )["run"]
        self.store.claim_tick(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "tick_id": "worker-review-tick",
            }
        )
        self.store.record_decision(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "decision_id": "worker-review-decision",
                "kind": "EXECUTE",
                "rationale": "the exact node Todo is in scope",
                "evidence_refs": ["todo:scope"],
                "target": {
                    "todo_id": "todo_worker_review",
                    "node_ref": self.assignment["node_ref"],
                },
            }
        )
        return run

    def test_worker_result_creates_distinct_reviewer_and_pass_completes(self):
        run = self._prepared_run()
        worker_assignment = {
            "assignment_id": "task_worker_implementer",
            "project_id": self.assignment["project_id"],
            "node_ref": self.assignment["node_ref"],
            "todo_id": "todo_worker_review",
            "task_frame_id": None,
            "worker_role": "IMPLEMENTER",
            "session_anchor_ref": "worker-anchor",
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": self.assignment["session_anchor_ref"],
        }
        dispatch = self.store.dispatch_work(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "dispatch_id": "worker-review-dispatch",
                "title": "bounded worker task",
                "instruction": "inspect the fixture without changing source",
                "completion_conditions": ["result evidence"],
            },
            lambda _project_id, _value: self.fail("WORKER_REVIEW must not enqueue Master work"),
            create_worker=lambda _spec: {"assignment": worker_assignment},
        )
        self.assertEqual("WORKER_ASSIGNED", dispatch["dispatch"]["state"])
        self.assertEqual("todo_worker_review", dispatch["dispatch"]["todo_id"])

        reviewer_assignment = {
            **worker_assignment,
            "assignment_id": "task_worker_reviewer",
            "worker_role": "REVIEWER",
            "session_anchor_ref": "reviewer-anchor",
        }
        result = self.store.record_worker_result(
            {
                "run_id": run["run_id"],
                "dispatch_id": "worker-review-dispatch",
                "worker_assignment_id": worker_assignment["assignment_id"],
                "worker_assignment_revision": 1,
                "worker_anchor_ref": worker_assignment["session_anchor_ref"],
                "result_ref": "worker-result-1",
                "outcome": "SUCCEEDED",
                "evidence_refs": ["fixture:result"],
                "result_text": "bounded fixture completed",
            },
            create_reviewer=lambda spec: self.assertEqual(
                "worker-result-1", spec["worker_result"]["result_ref"]
            ) or {"assignment": reviewer_assignment},
        )
        self.assertEqual("reviewer-anchor", result["reviewer"]["assignment"]["session_anchor_ref"])
        self.assertEqual("REVIEWER_ASSIGNED", result["run"]["current_assignment"]["state"])
        self.assertEqual("reviewer-anchor", result["run"]["current_reviewer"]["assignment"]["session_anchor_ref"])

        verdict = self.store.record_reviewer_verdict(
            {
                "run_id": run["run_id"],
                "dispatch_id": "worker-review-dispatch",
                "reviewer_assignment_id": reviewer_assignment["assignment_id"],
                "reviewer_assignment_revision": 1,
                "reviewer_anchor_ref": reviewer_assignment["session_anchor_ref"],
                "worker_result_ref": "worker-result-1",
                "outcome": "PASS",
                "acceptance_status": "VERIFIED_EVIDENCE",
                "evidence_refs": ["review:independent-pass"],
            }
        )
        self.assertEqual("PASS", verdict["review"]["outcome"])
        completed = self.store.complete_run(
            {"run_id": run["run_id"], "request_id": "worker-review-complete", "complete": True}
        )
        self.assertEqual("COMPLETED", completed["run"]["state"])

        reopened = PersonaAutomationStore(self.store.database_path)
        durable = reopened.get_run(run["run_id"])
        self.assertEqual("WORKER_REVIEW", durable["execution_mode"])
        self.assertEqual("worker-result-1", durable["current_worker"]["result"]["result_ref"])
        self.assertEqual("reviewer-anchor", durable["current_reviewer"]["assignment"]["session_anchor_ref"])
        self.assertEqual("PASS", durable["current_review"]["outcome"])

    def test_lineage_and_todo_gate_reject_unbound_verdicts(self):
        run = self._prepared_run()
        assignment = {
            "assignment_id": "task_worker_implementer",
            "project_id": self.assignment["project_id"],
            "node_ref": self.assignment["node_ref"],
            "todo_id": "todo_worker_review",
            "task_frame_id": None,
            "worker_role": "IMPLEMENTER",
            "session_anchor_ref": "worker-anchor",
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": self.assignment["session_anchor_ref"],
        }
        dispatch = self.store.dispatch_work(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "dispatch_id": "worker-review-dispatch",
                "title": "bounded worker task",
                "instruction": "inspect",
                "completion_conditions": ["evidence"],
            },
            lambda _project_id, _value: None,
            create_worker=lambda _spec: {"assignment": assignment},
        )
        with self.assertRaisesRegex(PersonaAutomationError, "exact session_anchor_ref lineage"):
            self.store.record_worker_result(
                {
                    "run_id": run["run_id"],
                    "dispatch_id": dispatch["dispatch"]["dispatch_id"],
                    "worker_assignment_id": assignment["assignment_id"],
                    "worker_assignment_revision": 1,
                    "worker_anchor_ref": "wrong-worker-anchor",
                    "result_ref": "wrong-result",
                    "outcome": "SUCCEEDED",
                    "evidence_refs": ["fixture"],
                },
                create_reviewer=lambda _spec: {"assignment": {}},
            )
        self.assertIsNotNone(
            self.store.todo_completion_gate(
                self.assignment["project_id"], "todo_worker_review", self.assignment["node_ref"]
            )
        )

    def test_server_dispatch_uses_typed_worker_adapter(self):
        calls = []

        class Automation:
            def get_run(self, _run_id):
                return {
                    "run_id": "run-1",
                    "project_id": "project-worker-review",
                    "execution_mode": "WORKER_REVIEW",
                }

            def dispatch_work(self, value, _enqueue, *, create_worker):
                calls.append(create_worker({"worker_role": "IMPLEMENTER", "todo_id": "todo_worker_review"}))
                return {"status": "PERSONA_AUTOMATION_WORKER_DISPATCHED", "dispatch": value}

        server = SimpleNamespace(
            persona_automation=Automation(),
            store=SimpleNamespace(create_master_message=lambda *_args: None),
            _persona_actor=UniverseHTTPServer._persona_actor,
            _create_persona_automation_worker=lambda spec, _context: {"assignment": dict(spec)},
            _wake_live_master_sessions=lambda *_args, **_kwargs: self.fail("Worker dispatch must not wake a Master queue message"),
        )
        result = UniverseHTTPServer._handle_persona_automation_action(
            server,
            {
                "run_id": "run-1",
                "owner_ref": "master-anchor",
                "dispatch_id": "dispatch-1",
                "title": "bounded task",
                "instruction": "inspect",
                "completion_conditions": ["evidence"],
            },
            {"action_id": "persona.automation.dispatch", "actor": {"kind": "USER"}},
        )
        self.assertEqual("PERSONA_AUTOMATION_WORKER_DISPATCHED", result["status"])
        self.assertEqual("IMPLEMENTER", calls[0]["assignment"]["worker_role"])


if __name__ == "__main__":
    unittest.main()
