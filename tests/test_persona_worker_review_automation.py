"""Focused contract tests for Persona WORKER_REVIEW orchestration."""

from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from types import SimpleNamespace

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from persona_automation import PersonaAutomationError, PersonaAutomationStore  # noqa: E402
from task_frame_lineage import TaskFrameLineageError  # noqa: E402
from universe_server import UniverseHTTPServer  # noqa: E402
from universe_server import UniverseStore  # noqa: E402


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

    def test_task_frame_assignment_is_worker_role_without_worker_mode(self):
        persistent = UniverseStore._task_worker_assignment_row(
            {
                "assignment_id": "fleet-1",
                "project_id": "project-worker-review",
                "node_ref": "feature-worker-review",
                "todo_id": "todo-worker",
                "task_frame_id": "frame-fleet",
                "worker_role": "REVIEWER",
                "session_anchor_ref": "worker-anchor",
                "persona_id": None,
                "state": "ACTIVE",
                "assignment_revision": 1,
                "assigned_by_session_anchor_ref": "master-anchor",
                "ended_reason": None,
                "created_at": "now",
                "updated_at": "now",
                "ended_at": None,
            }
        )
        self.assertEqual("FLEET_SESSION", persistent["execution_shape"])
        self.assertEqual("WORKER", persistent["runtime_role"])
        self.assertEqual("REVIEW", persistent["persona_task_kind"])

        ephemeral = UniverseStore._task_worker_assignment_row(
            {
                "assignment_id": "task-frame-1",
                "project_id": "project-worker-review",
                "node_ref": "feature-worker-review",
                "todo_id": "todo-worker",
                "task_frame_id": "frame-task",
                "worker_role": "IMPLEMENTER",
                "session_anchor_ref": "master-anchor",
                "persona_id": None,
                "state": "ACTIVE",
                "assignment_revision": 1,
                "assigned_by_session_anchor_ref": "master-anchor",
                "ended_reason": None,
                "created_at": "now",
                "updated_at": "now",
                "ended_at": None,
            }
        )
        self.assertEqual("TASK_FRAME", ephemeral["execution_shape"])
        self.assertEqual("WORKER", ephemeral["runtime_role"])
        self.assertEqual("IMPLEMENTATION", ephemeral["persona_task_kind"])

    def test_automation_worker_mints_task_frame_under_master_anchor(self):
        calls = []

        class Lineage:
            def get_task_frame(self, _frame_id):
                raise TaskFrameLineageError(
                    "TASK_FRAME_NOT_FOUND", "not created", status=404
                )

            def create_task_frame(self, **kwargs):
                calls.append(("frame", kwargs))
                return {
                    "frame_ref": kwargs["frame_ref"],
                    "origin_session_anchor_ref": kwargs["origin_session_anchor_ref"],
                    "target_session_anchor_ref": kwargs["target_session_anchor_ref"],
                }, True

        class Store:
            def assign_task_worker(self, value, _actor):
                calls.append(("assignment", dict(value)))
                return {
                    "assignment": {
                        "assignment_id": "task-frame-assignment",
                        "project_id": value["project_id"],
                        "node_ref": value["node_ref"],
                        "todo_id": value["todo_id"],
                        "task_frame_id": value["task_frame_id"],
                        "worker_role": value["worker_role"],
                        "session_anchor_ref": value["session_anchor_ref"],
                        "persona_id": value.get("persona_id"),
                        "state": "ACTIVE",
                        "assignment_revision": 1,
                        "assigned_by_session_anchor_ref": value[
                            "assigned_by_session_anchor_ref"
                        ],
                    }
                }

        server = SimpleNamespace(
            store=Store(),
            task_frame_lineage=Lineage(),
            _validate_persona_assignment_anchor=lambda _project, _anchor: None,
            _anchor_modes=lambda _anchor: {"MASTER"},
            _persona_actor=UniverseHTTPServer._persona_actor,
            _record_worker_assignment_event=lambda **kwargs: calls.append(
                ("event", kwargs)
            ),
        )
        result = UniverseHTTPServer._create_persona_automation_task_frame_worker(
            server,
            {
                "run_id": "run-task-frame",
                "dispatch_id": "dispatch-task-frame",
                "project_id": "project-worker-review",
                "node_ref": "feature-worker-review",
                "todo_id": "todo-worker",
                "worker_role": "REVIEWER",
                "assigned_by_session_anchor_ref": "master-anchor",
                "provider": "CODEX",
                "model_ref": "gpt-5.6-luna",
            },
            {"actor": {"kind": "USER", "actor_ref": "master-anchor"}},
        )
        assignment = result["assignment"]
        self.assertEqual("TASK_FRAME", result["execution_shape"])
        self.assertEqual("TASK_FRAME", assignment["execution_shape"])
        self.assertEqual("WORKER", assignment["runtime_role"])
        self.assertEqual("REVIEW", assignment["persona_task_kind"])
        self.assertEqual("master-anchor", assignment["session_anchor_ref"])
        self.assertEqual("master-anchor", assignment["assigned_by_session_anchor_ref"])
        self.assertEqual("frame", calls[0][0])
        self.assertEqual("assignment", calls[1][0])

    def test_master_reviewer_task_frame_records_provider_verdict(self):
        run = self.store.start_run(
            {
                "project_id": self.assignment["project_id"],
                "session_anchor_ref": self.assignment["session_anchor_ref"],
                "scope": "master direct review",
                "instruction": "review one bounded Master result",
                "request_id": "master-direct-start",
                "execution_mode": "MASTER_DIRECT",
                "worker_persona_id": "persona-reviewer",
                "worker_provider": "CODEX",
                "worker_model_ref": "gpt-5.6-luna",
            },
            self.assignment,
        )["run"]
        self.store.claim_tick(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "tick_id": "master-direct-tick",
            }
        )
        self.store.record_decision(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "decision_id": "master-direct-decision",
                "kind": "EXECUTE",
                "rationale": "exact Todo is ready",
                "evidence_refs": ["todo:master"],
                "target": {"todo_id": "todo-master-review", "node_ref": self.assignment["node_ref"]},
            }
        )
        dispatched = self.store.dispatch_work(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "dispatch_id": "master-direct-dispatch",
                "title": "bounded Master result",
                "instruction": "complete the bounded result",
                "completion_conditions": ["result evidence"],
            },
            lambda _project_id, value: (
                {
                    "message_id": "master-message-1",
                    "target_session_anchor_ref": self.assignment["session_anchor_ref"],
                },
                True,
            ),
        )
        assignment = dispatched["dispatch"]
        completed = self.store.record_master_completion(
            {
                "run_id": run["run_id"],
                "dispatch_id": assignment["dispatch_id"],
                "assignment_revision": assignment["assignment_revision"],
                "source_message_id": assignment["message_id"],
                "result_ref": "master-result-1",
                "body_text_utf8_sha256": "digest-master-result",
                "completed_at": "2026-09-18T00:00:00Z",
                "result_text": "bounded Master result",
            }
        )
        reviewer_assignment = {
            "assignment_id": "task-frame-reviewer",
            "project_id": self.assignment["project_id"],
            "node_ref": self.assignment["node_ref"],
            "todo_id": assignment["todo_id"],
            "task_frame_id": "frame-master-review",
            "worker_role": "REVIEWER",
            "session_anchor_ref": self.assignment["session_anchor_ref"],
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": self.assignment["session_anchor_ref"],
            "execution_shape": "TASK_FRAME",
        }
        attached = self.store.attach_master_reviewer(
            {
                "run_id": run["run_id"],
                "dispatch_id": assignment["dispatch_id"],
                "assignment_revision": assignment["assignment_revision"],
                "source_message_id": assignment["message_id"],
                "result_ref": "master-result-1",
                "body_text_utf8_sha256": "digest-master-result",
                "result_text": "bounded Master result",
            },
            create_reviewer=lambda spec: (
                spec.__setitem__("task_frame_id", "frame-master-review")
                or {"assignment": reviewer_assignment}
            ),
        )
        self.assertEqual("PERSONA_AUTOMATION_MASTER_REVIEWER_ASSIGNED", attached["status"])
        run_with_reviewer = attached["run"]

        class Runtime:
            def provider_capability(self, provider):
                return {"provider": provider, "status": "AVAILABLE", "model": "gpt-5.6-luna"}

            def invoke_structured_task(self, **_kwargs):
                return {
                    "result_receipt_ref": "receipt-reviewer",
                    "model_ref": "provider://CODEX/model/gpt-5.6-luna",
                    "task_frame_result_status": "TASK_COMPLETED",
                    "terminal_result_verified": True,
                    "structured_result": {
                        "verdict": "PASS",
                        "evidence_refs": ["review:bounded"],
                        "note": "result is independently verified",
                        "next_action": "continue",
                    },
                }

        lineage_results = []
        cycle_appends = []

        class Lineage:
            def attach_result(self, **kwargs):
                lineage_results.append(dict(kwargs))
                return {"result_ref": kwargs["result_ref"]}, True

        server = SimpleNamespace(
            runtime_host=Runtime(),
            persona_automation=self.store,
            task_frame_lineage=Lineage(),
            _persona_automation_runtime_binding=lambda _run: {
                "endpoint": "http://127.0.0.1:1",
                "token": "opaque",
                "session_id": "master-runtime",
                "origin_anchor_ref": self.assignment["session_anchor_ref"],
                "origin_frame_id": "current",
                "parent_actor_ref": "master-runtime",
                "parent_evidence_ref": "anchor://master",
            },
            _create_persona_review_followup_todo=lambda _result: {"todo": None},
            _enqueue_persona_automation_driver=lambda _run, **_kwargs: {"status": "QUEUED"},
        )
        server._append_task_frame_cycle = (
            lambda run, frame_id, anchor, status, **kwargs: cycle_appends.append(
                (frame_id, anchor, status, kwargs)
            )
        )
        result = UniverseHTTPServer._run_persona_automation_task_frame(
            server,
            run_with_reviewer,
            worker_role="REVIEWER",
            current=run_with_reviewer["current_reviewer"],
        )
        self.assertEqual("PERSONA_AUTOMATION_REVIEW_RECORDED", result["status"])
        self.assertEqual("PASS", result["review"]["outcome"])
        self.assertEqual("TASK_FRAME", result["provider_execution"]["execution_shape"])
        self.assertEqual("task-frame-result://frame-master-review/reviewer/1", lineage_results[0]["result_ref"])
        self.assertEqual("frame-master-review", lineage_results[0]["frame_ref"])
        # A collected result is appended to the origin Session Anchor's own history.
        self.assertEqual(1, len(cycle_appends))
        frame_id, anchor, status, extra = cycle_appends[0]
        self.assertEqual("frame-master-review", frame_id)
        self.assertEqual(self.assignment["session_anchor_ref"], anchor)
        self.assertEqual("COMPLETED", status)
        self.assertEqual(
            "task-frame-result://frame-master-review/reviewer/1", extra["result_ref"]
        )
        self.assertEqual(64, len(extra["result_digest"]))

    def test_legacy_master_reviewer_repair_is_cas_pinned_and_upgrades_to_task_frame(self):
        run = self.store.start_run(
            {
                "project_id": self.assignment["project_id"],
                "session_anchor_ref": self.assignment["session_anchor_ref"],
                "scope": "legacy repair",
                "instruction": "repair one historical reviewer assignment",
                "request_id": "legacy-repair-start",
                "execution_mode": "MASTER_DIRECT",
                "worker_persona_id": "persona-reviewer",
                "worker_provider": "CODEX",
                "worker_model_ref": "gpt-5.6-luna",
            },
            self.assignment,
        )["run"]
        self.store.claim_tick({
            "run_id": run["run_id"],
            "owner_ref": self.assignment["session_anchor_ref"],
            "tick_id": "legacy-repair-tick",
        })
        self.store.record_decision({
            "run_id": run["run_id"],
            "owner_ref": self.assignment["session_anchor_ref"],
            "decision_id": "legacy-repair-decision",
            "kind": "EXECUTE",
            "rationale": "repair the exact historical reviewer",
            "evidence_refs": ["fixture:legacy-reviewer"],
            "target": {"todo_id": "todo-legacy-repair", "node_ref": self.assignment["node_ref"]},
        })
        dispatched = self.store.dispatch_work(
            {
                "run_id": run["run_id"],
                "owner_ref": self.assignment["session_anchor_ref"],
                "dispatch_id": "legacy-repair-dispatch",
                "title": "legacy result",
                "instruction": "record a historical Master result",
                "completion_conditions": ["result evidence"],
            },
            lambda _project_id, _value: ({"message_id": "legacy-master-message"}, True),
        )
        assignment = dispatched["dispatch"]
        completed = self.store.record_master_completion({
            "run_id": run["run_id"],
            "dispatch_id": assignment["dispatch_id"],
            "assignment_revision": assignment["assignment_revision"],
            "source_message_id": assignment["message_id"],
            "result_ref": "legacy-master-result",
            "body_text_utf8_sha256": "legacy-digest",
            "completed_at": "2026-09-18T00:00:00Z",
            "result_text": "historical result",
        })
        old_assignment = {
            "assignment_id": "legacy-fleet-reviewer",
            "project_id": self.assignment["project_id"],
            "node_ref": self.assignment["node_ref"],
            "todo_id": assignment["todo_id"],
            "task_frame_id": None,
            "worker_role": "REVIEWER",
            "session_anchor_ref": "legacy-reviewer-anchor",
            "assignment_revision": 1,
            "assigned_by_session_anchor_ref": self.assignment["session_anchor_ref"],
            "execution_shape": "FLEET_SESSION",
        }
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE persona_automation_run SET current_reviewer_json = ? WHERE run_id = ?",
                (json.dumps({
                    "state": "ASSIGNED",
                    "assignment": old_assignment,
                    "worker_result_ref": "legacy-master-result",
                    "source_role": "MASTER",
                    "master_result": {"result_ref": "legacy-master-result", "result_text": "historical result"},
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), run["run_id"]),
            )
        current = self.store.get_run(run["run_id"])
        prepared = self.store.prepare_legacy_reviewer_repair({
            "run_id": run["run_id"],
            "request_id": "legacy-repair-request",
            "expected_revision": current["revision"],
            "legacy_reviewer_assignment_id": old_assignment["assignment_id"],
            "expected_reviewer_assignment_revision": 1,
        })
        self.assertEqual("PERSONA_AUTOMATION_LEGACY_REVIEWER_REPAIR_RESERVED", prepared["status"])
        repaired_assignment = {
            **old_assignment,
            "task_frame_id": "frame-legacy-repair",
            "session_anchor_ref": self.assignment["session_anchor_ref"],
            "execution_shape": "TASK_FRAME",
        }
        attached = self.store.attach_master_reviewer(
            {
                "run_id": run["run_id"],
                "dispatch_id": assignment["dispatch_id"],
                "assignment_revision": assignment["assignment_revision"],
                "source_message_id": assignment["message_id"],
                "result_ref": "legacy-master-result",
                "body_text_utf8_sha256": "legacy-digest",
                "result_text": "historical result",
            },
            create_reviewer=lambda spec: (
                spec.__setitem__("task_frame_id", "frame-legacy-repair")
                or {"assignment": repaired_assignment}
            ),
        )
        self.assertEqual("PERSONA_AUTOMATION_MASTER_REVIEWER_ASSIGNED", attached["status"])
        self.assertEqual("TASK_FRAME", attached["run"]["current_reviewer"]["assignment"]["execution_shape"])
        self.assertEqual(self.assignment["session_anchor_ref"], attached["run"]["current_reviewer"]["assignment"]["session_anchor_ref"])
        self.assertEqual("frame-legacy-repair", attached["run"]["current_assignment"]["task_frame_id"])
        replay = self.store.prepare_legacy_reviewer_repair({
            "run_id": run["run_id"],
            "request_id": "legacy-repair-request",
            "expected_revision": current["revision"],
            "legacy_reviewer_assignment_id": old_assignment["assignment_id"],
            "expected_reviewer_assignment_revision": 1,
        })
        self.assertEqual("PERSONA_AUTOMATION_LEGACY_REVIEWER_REPAIR_REPLAYED", replay["status"])

    def test_worker_review_mode_is_retired_for_new_runs(self):
        # Worker and Reviewer are roles in a Task Frame the Master launches, not a
        # mode of the run: the server no longer starts or reviews Workers itself.
        with self.assertRaises(PersonaAutomationError) as raised:
            self.store.start_run(
                {
                    "project_id": self.assignment["project_id"],
                    "session_anchor_ref": self.assignment["session_anchor_ref"],
                    "scope": "feature-worker-review",
                    "instruction": "run one bounded Worker task",
                    "request_id": "worker-review-retired",
                    "execution_mode": "WORKER_REVIEW",
                },
                self.assignment,
            )
        self.assertEqual("PERSONA_AUTOMATION_EXECUTION_MODE_RETIRED", raised.exception.code)
        self.assertIn("launch-frame", raised.exception.detail)
        started = self.store.start_run(
            {
                "project_id": self.assignment["project_id"],
                "session_anchor_ref": self.assignment["session_anchor_ref"],
                "scope": "feature-worker-review",
                "instruction": "direct run",
                "request_id": "master-direct-still-starts",
            },
            self.assignment,
        )["run"]
        self.assertEqual("MASTER_DIRECT", started["execution_mode"])

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
