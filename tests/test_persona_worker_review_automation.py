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
            _mark_dispatched_todo_in_progress=lambda **_kwargs: {"status": "NOT_RUN"},
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

    def test_worker_instruction_receipt_is_bound_and_replay_safe(self):
        run = self._prepared_run()
        worker_assignment = {
            "assignment_id": "task_worker_instruction",
            "project_id": self.assignment["project_id"],
            "node_ref": self.assignment["node_ref"],
            "todo_id": "todo_worker_review",
            "task_frame_id": None,
            "worker_role": "IMPLEMENTER",
            "session_anchor_ref": "worker-instruction-anchor",
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": self.assignment["session_anchor_ref"],
        }
        self.store.dispatch_work(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "dispatch_id": "worker-instruction-dispatch",
                "title": "bounded task",
                "instruction": "inspect fixture",
                "completion_conditions": ["result evidence"],
            },
            lambda _project_id, _value: self.fail("WORKER_REVIEW must not enqueue Master work"),
            create_worker=lambda _spec: {"assignment": worker_assignment},
        )
        receipt = {
            "run_id": run["run_id"],
            "dispatch_id": "worker-instruction-dispatch",
            "worker_role": "IMPLEMENTER",
            "worker_assignment_id": worker_assignment["assignment_id"],
            "worker_assignment_revision": 1,
            "worker_anchor_ref": worker_assignment["session_anchor_ref"],
            "message_id": "msg_worker_instruction",
            "idempotency_key": "persona-worker-instruction-test",
        }
        first = self.store.record_worker_instruction(receipt)
        self.assertEqual(
            "PERSONA_AUTOMATION_WORKER_INSTRUCTION_RECORDED", first["status"]
        )
        self.assertEqual(
            "msg_worker_instruction",
            self.store.get_run(run["run_id"])["current_worker"]["instruction_message_id"],
        )
        replay = self.store.record_worker_instruction(receipt)
        self.assertEqual(
            "PERSONA_AUTOMATION_WORKER_INSTRUCTION_REPLAYED", replay["status"]
        )
        with self.assertRaisesRegex(PersonaAutomationError, "different instruction message"):
            self.store.record_worker_instruction({**receipt, "message_id": "msg_other"})

    def test_precreated_claim_stays_claimed_until_transport_completion(self):
        run = self._prepared_run()
        worker_assignment = {
            "assignment_id": "task_worker_claim",
            "project_id": self.assignment["project_id"],
            "node_ref": self.assignment["node_ref"],
            "todo_id": "todo_worker_review",
            "task_frame_id": None,
            "worker_role": "IMPLEMENTER",
            "session_anchor_ref": "worker-claim-anchor",
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": self.assignment["session_anchor_ref"],
        }
        precreated = {
            "message_id": "msg_precreated_claim",
            "terminal_id": "term-worker-claim",
            "claim_state": "CLAIMED",
            "metadata": {
                "run_id": run["run_id"],
                "dispatch_id": "worker-claim-dispatch",
                "worker_role": "IMPLEMENTER",
                "worker_assignment_id": worker_assignment["assignment_id"],
                "worker_assignment_revision": 1,
                "worker_anchor_ref": worker_assignment["session_anchor_ref"],
                "idempotency_key": "persona-automation-claim",
            },
        }
        self.store.dispatch_work(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "dispatch_id": "worker-claim-dispatch",
                "title": "bounded task",
                "instruction": "inspect fixture",
                "completion_conditions": ["result evidence"],
            },
            lambda _project_id, _value: self.fail("WORKER_REVIEW must not enqueue Master work"),
            create_worker=lambda _spec: {
                "assignment": worker_assignment,
                "automation_instruction": precreated,
            },
        )
        receipt = {
            **precreated["metadata"],
            "message_id": precreated["message_id"],
        }
        recorded = self.store.record_worker_instruction(receipt)
        self.assertEqual(
            "CLAIMED",
            self.store.get_run(run["run_id"])["current_worker"]["precreated_instruction"]["claim_state"],
        )
        marked = self.store.mark_worker_instruction_claim_state(
            run_id=run["run_id"],
            worker_role="IMPLEMENTER",
            message_id=precreated["message_id"],
            expected_revision=recorded["run"]["revision"],
        )
        self.assertEqual("DISPATCHED", marked["claim_state"])
        replay = self.store.mark_worker_instruction_claim_state(
            run_id=run["run_id"],
            worker_role="IMPLEMENTER",
            message_id=precreated["message_id"],
            expected_revision=marked["run"]["revision"],
        )
        self.assertEqual("PERSONA_AUTOMATION_WORKER_CLAIM_STATE_REPLAYED", replay["status"])

    def test_session_bus_worker_reply_routes_to_typed_result_action(self):
        calls = []

        class Automation:
            def record_worker_route_failure(self, _value):
                self.failed = True

        server = SimpleNamespace(
            persona_automation=Automation(),
            resolve_action_context=lambda action_id, source: {
                "action_id": action_id,
                "source": source,
                "actor": {"kind": "USER", "actor_ref": "server://loopback-user"},
            },
            _handle_persona_automation_action=lambda request, context: calls.append((request, context))
            or {"run": {"current_reviewer": None}},
            _ensure_persona_automation_worker_instruction=lambda *_args, **_kwargs: None,
        )
        original = {
            "message_id": "msg_worker_instruction",
            "to": {"mode": "WORKER", "project_id": "project-worker-review"},
            "lifecycle": {
                "persona_automation": {
                    "schema": "universe.persona-automation-session-bus.v1",
                    "run_id": "run-worker-reply",
                    "dispatch_id": "dispatch-worker-reply",
                    "worker_role": "IMPLEMENTER",
                    "worker_assignment_id": "task_worker_reply",
                    "worker_assignment_revision": 1,
                    "worker_anchor_ref": "worker-reply-anchor",
                }
            },
        }
        result = {
            "message_id": "msg_worker_result",
            "body_text": "bounded result",
            "lifecycle_state": "COMPLETED",
            "lifecycle": {"result_ref": "worker-result-ref"},
        }
        UniverseHTTPServer._observe_persona_automation_result(server, original, result)
        self.assertEqual(1, len(calls))
        request, context = calls[0]
        self.assertEqual("persona.automation.worker-result", context["action_id"])
        self.assertEqual("USER", context["actor"]["kind"])
        self.assertEqual("SESSION_BUS_RESULT_OBSERVER", context["source"])
        self.assertEqual("run-worker-reply", request["run_id"])
        self.assertEqual("task_worker_reply", request["worker_assignment_id"])
        self.assertEqual("worker-result-ref", request["result_ref"])
        self.assertEqual("SUCCEEDED", request["outcome"])

    def test_combined_native_persona_task_preserves_worker_reply_lineage(self):
        server = UniverseHTTPServer.__new__(UniverseHTTPServer)
        parts = server._persona_automation_task_message(
            task={
                "run_id": "run-combined",
                "dispatch_id": "dispatch-combined",
                "worker_role": "IMPLEMENTER",
                "provider": "CODEX",
                "node_ref": "feature-worker-review",
                "assigned_by_session_anchor_ref": "master-anchor",
                "instruction": "inspect the non-programming fixture without editing source",
                "completion_conditions": ["return the fixture result"],
            },
            assignment={
                "assignment_id": "task-worker-combined",
                "assignment_revision": 3,
                "todo_id": "todo_worker_review",
                "task_frame_id": "frame-worker-review",
            },
            project_id="project-worker-review",
            session_anchor_ref="worker-combined-anchor",
            terminal_id="term-combined",
        )
        self.assertIn("without editing source", parts["body_text"])
        self.assertEqual(
            "task-worker-combined",
            parts["automation_context"]["worker_assignment_id"],
        )
        self.assertEqual(
            "persona-automation:run-combined:dispatch-combined:IMPLEMENTER:task-worker-combined:3",
            parts["idempotency_key"],
        )
        self.assertEqual("worker-combined-anchor", parts["to"]["session_anchor_ref"])

    def test_worker_persona_delivery_claims_bus_task_before_combined_native_queue(self):
        calls = {}

        class Store:
            def resolve_active_persona_prompt(self, _anchor):
                return (
                    'Persona "exact"\n다음 줄',
                    {
                        "persona_id": "persona-worker",
                        "persona_revision": 2,
                        "assignment_revision": 4,
                    },
                )

            def record_persona_queued(self, *args):
                calls["queued_args"] = args
                return True

        class Host:
            def deliver_persona_native_queue(self, _terminal_id, _text, **kwargs):
                calls["queue_kwargs"] = kwargs
                return {
                    "status": "PERSONA_NATIVE_QUEUE_ACCEPTED",
                    "message_id": kwargs["message_id"],
                    "delivery": {
                        "message_id": kwargs["message_id"],
                        "phase": "NATIVE_QUEUED",
                        "queued_submission_id": "queue-submission",
                    },
                }

        class Bus:
            def post(self, _host, payload):
                calls["posted"] = payload
                return {"messages": [{"message_id": "msg-combined-worker"}]}

            def claim_instruction(self, _host, **kwargs):
                calls["claim"] = kwargs
                return {"message_id": kwargs["message_id"]}

            def release_instruction_claim(self, **kwargs):
                calls["released"] = kwargs

        server = SimpleNamespace(
            store=Store(),
            terminal_host=Host(),
            session_bus=Bus(),
            _session_anchor_terminal_host=lambda: Host(),
            _persona_automation_task_message=lambda **kwargs: UniverseHTTPServer._persona_automation_task_message(None, **kwargs),
        )
        result = UniverseHTTPServer._deliver_persona_to_existing_terminal(
            server,
            project_id="project-worker-review",
            terminal={"terminal_id": "term-worker", "provider": "CODEX"},
            session_anchor_ref="worker-combined-anchor",
            automation_task={
                "run_id": "run-combined",
                "dispatch_id": "dispatch-combined",
                "worker_role": "IMPLEMENTER",
                "worker_assignment_id": "task-worker-combined",
                "worker_assignment_revision": 3,
                "todo_id": "todo-worker-combined",
                "task_frame_id": "frame-worker-combined",
                "provider": "CODEX",
                "node_ref": "feature-worker-review",
                "assigned_by_session_anchor_ref": "master-anchor",
                "instruction": "inspect the fixture",
                "completion_conditions": ["return evidence"],
            },
        )
        self.assertEqual("NATIVE_QUEUED", result["status"])
        self.assertEqual("msg-combined-worker", result["automation_instruction"]["message_id"])
        self.assertEqual("CLAIMED", result["automation_instruction"]["claim_state"])
        self.assertIn("inspect the fixture", calls["queue_kwargs"]["continuation_text"])
        self.assertEqual("worker-combined-anchor", calls["claim"]["session_anchor_ref"])
        self.assertIn("todo-worker-combined", calls["posted"]["body_text"])
        self.assertIn("frame-worker-combined", calls["posted"]["body_text"])


if __name__ == "__main__":
    unittest.main()
