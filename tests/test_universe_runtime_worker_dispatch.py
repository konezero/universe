from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_runtime_worker_dispatch import (  # noqa: E402
    RuntimeWorkerDispatcher,
    WorkerDispatchError,
)
from worker_failure_evidence import WorkerFailureEvidenceStore  # noqa: E402


class FakeGateway:
    def __init__(self, session) -> None:
        self.session = session
        self.session_ref = session.session_ref

    def reply_stream(self, _prompt, _on_delta) -> str:
        return "bounded result"

    def close(self) -> None:
        return


class RuntimeWorkerDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.request = {
            "task_frame_id": "task-frame-1",
            "turn_id": "turn-1",
            "worker_run_ref": "worker-run-1",
            "context_pack": {"goal": "review"},
            "output_contract": {"type": "text"},
            "result_mode": "REDACTED",
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "model": "test-model",
            "response_timeout_seconds": 90,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_codex_task_frame_requests_ephemeral_provider_session(self) -> None:
        observed: list[tuple[bool, str, float]] = []

        class FakeCodexSession:
            def __init__(
                self, *, ephemeral, model, response_timeout_seconds, session_observer, **_kwargs
            ) -> None:
                observed.append((ephemeral, model, response_timeout_seconds))
                self.session_ref = "codex-app-server:ephemeral-1"
                session_observer("ephemeral-1")

        dispatcher = RuntimeWorkerDispatcher(self.root)
        with (
            patch(
                "universe_runtime_worker_dispatch._resolve_codex",
                return_value=(self.root / "codex.exe", {}, "test-model"),
            ),
            patch(
                "universe_runtime_worker_dispatch.CodexAppServerSession",
                FakeCodexSession,
            ),
            patch(
                "universe_runtime_worker_dispatch.UniverseAcpGateway",
                FakeGateway,
            ),
        ):
            result = dispatcher._invoke_codex(self.request)

        self.assertEqual([(True, "test-model", 90.0)], observed)
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("NOT_PERSISTED", result["provider_durable_chat_state"])

    def test_dispatch_normalizes_runtime_provider_aliases(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)

        self.assertEqual(
            "CODEX",
            dispatcher._normalize_request(
                {**self._dispatch_request(), "provider": "openai"}
            )["provider"],
        )
        self.assertEqual(
            "CLAUDE",
            dispatcher._normalize_request(
                {**self._dispatch_request(), "provider": "anthropic"}
            )["provider"],
        )
        self.assertEqual(
            "GROK",
            dispatcher._normalize_request(
                {**self._dispatch_request(), "provider": "xai"}
            )["provider"],
        )

    def test_grok_task_frame_does_not_publish_persistent_session_ref(self) -> None:
        class FakeGrokSession:
            def __init__(self, *, model, session_observer, **_kwargs) -> None:
                self.model = model
                self.session_ref = "grok-acp:ephemeral-1"
                session_observer("ephemeral-1")

        dispatcher = RuntimeWorkerDispatcher(self.root)
        with (
            patch(
                "universe_runtime_worker_dispatch._resolve_grok",
                return_value=(self.root / "grok.exe", {}, "test-model"),
            ),
            patch(
                "universe_runtime_worker_dispatch.GrokAcpSession",
                FakeGrokSession,
            ),
            patch(
                "universe_runtime_worker_dispatch.UniverseAcpGateway",
                FakeGateway,
            ),
        ):
            result = dispatcher._invoke_grok(self.request)

        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("UNKNOWN", result["provider_durable_chat_state"])

    def test_claude_task_frame_disables_session_persistence(self) -> None:
        observed: list[tuple[bool, bool, int, float, str, object]] = []
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        }

        class FakeClaudeSession:
            def __init__(
                self, *, ephemeral, allow_read_only_tools, max_turns, response_timeout_seconds, model, json_schema, **_kwargs
            ) -> None:
                observed.append(
                    (
                        ephemeral,
                        allow_read_only_tools,
                        max_turns,
                        response_timeout_seconds,
                        model,
                        json_schema,
                    )
                )
                self.session_ref = "claude-code:ephemeral-1"

        dispatcher = RuntimeWorkerDispatcher(self.root)
        with (
            patch(
                "universe_runtime_worker_dispatch._resolve_claude",
                return_value=(self.root / "claude.exe", {}, "test-model"),
            ),
            patch(
                "universe_runtime_worker_dispatch.ClaudeCodeSession",
                FakeClaudeSession,
            ),
            patch(
                "universe_runtime_worker_dispatch.UniverseAcpGateway",
                FakeGateway,
            ),
        ):
            result = dispatcher._invoke_claude(
                {
                    **self.request,
                    "max_turns": 5,
                    "result_mode": "STRUCTURED_JSON",
                    "output_contract": {"json_schema": schema},
                }
            )

        self.assertEqual([(True, True, 5, 90.0, "test-model", schema)], observed)
        self.assertEqual("CLAUDE_CODE_CLI_ADAPTER", result["runtime_provider"])
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("NOT_PERSISTED", result["provider_durable_chat_state"])

    def test_claude_structured_task_frame_requires_json_schema(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)
        with patch(
            "universe_runtime_worker_dispatch._resolve_claude",
            return_value=(self.root / "claude.exe", {}, "test-model"),
        ):
            with self.assertRaises(WorkerDispatchError) as captured:
                dispatcher._invoke_claude(
                    {**self.request, "result_mode": "STRUCTURED_JSON"}
                )

        self.assertEqual("WORKER_OUTPUT_SCHEMA_REQUIRED", captured.exception.code)
        self.assertEqual("CLAUDE_JSON_SCHEMA_REQUIRED", captured.exception.reason)

    @staticmethod
    def _dispatch_request() -> dict[str, object]:
        return {
            "schema": "universe.task-frame-worker-dispatch-request.v1",
            "provider": "CODEX",
            "endpoint": "http://127.0.0.1:17777",
            "token": "transient",
            "session_id": "session-1",
            "frame_id": "frame-1",
            "turn_id": "turn-1",
            "invoker_actor_ref": "universe-runtime-host",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "context_pack": {"goal": "review"},
            "output_contract": {"type": "text"},
            "max_turns": 1,
            "response_timeout_seconds": 90,
            "result_mode": "REDACTED",
        }

    def test_dispatch_claims_before_starting_provider(self) -> None:
        events: list[str] = []
        observed_timeouts: list[float] = []

        def post(_endpoint, _token, path, payload):
            if path == "/v1/task-frame/worker-result":
                events.append("result")
                return {"status": "TASK_FRAME_RESULT_RECORDED"}
            operation = payload["operation"]["operation"]
            events.append(operation)
            if operation == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "input_bundle": {},
                        },
                    },
                }
            operation_payload = payload["operation"]
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "TURN_CLAIMED",
                    "turn": {
                        "turn_id": operation_payload["turn_id"],
                        "state": "CLAIMED",
                        "claimed_by": operation_payload["worker_id"],
                    },
                },
            }

        class Dispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, request):
                self.assert_claimed(request)
                events.append("provider")
                observed_timeouts.append(request["response_timeout_seconds"])
                return {
                    "status": "COMPLETED",
                    "worker_id": "provider-worker-1",
                    "worker_run_ref": request["worker_run_ref"],
                    "result_receipt_ref": "result://worker-1",
                    "result": {"text": "done"},
                    "session_persistence": "EPHEMERAL",
                    "persistent_session_ref": "UNKNOWN",
                    "universe_coordinate_persisted": False,
                }

            @staticmethod
            def assert_claimed(_request):
                if events[-1] != "claim_turn":
                    raise AssertionError("provider started before the turn was claimed")

        result = Dispatcher(self.root, post=post).dispatch(self._dispatch_request())

        self.assertEqual(
            ["worker_invocation_plan", "claim_turn", "provider", "result"], events
        )
        self.assertTrue(result["worker_id"].startswith("universe-runtime-worker:"))
        self.assertEqual("provider-worker-1", result["provider_worker_ref"])
        self.assertEqual([240], observed_timeouts)

    def test_read_only_worker_allows_extended_review_turn_budget(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)
        normalized = dispatcher._normalize_request(
            {**self._dispatch_request(), "max_turns": 16}
        )
        self.assertEqual(16, normalized["max_turns"])

        target = str(self.root / "tools" / "app.py")
        with self.assertRaises(WorkerDispatchError) as captured:
            dispatcher._normalize_request(
                {
                    **self._dispatch_request(),
                    "repository_write_scope": "BOUNDED",
                    "mutation_scope": {
                        "operations": ["MODIFY"],
                        "targets": [target],
                    },
                    "max_turns": 16,
                }
            )
        self.assertEqual("WORKER_TRANSPORT_FAILED", captured.exception.code)
        self.assertEqual("MAX_TURNS_INVALID", captured.exception.reason)

    def test_read_only_review_records_fail_conclusion_without_mutation_receipt(self) -> None:
        result = {
            "outcome": "SUCCEEDED",
            "summary": "A correctness defect was found.",
            "evidence_refs": ["source://reviewed-file"],
            "validation": [
                {
                    "plane": "independent-review",
                    "state": "FAIL",
                    "evidence_refs": ["source://reviewed-file:42"],
                }
            ],
        }
        RuntimeWorkerDispatcher._validate_structured_result(
            result,
            {
                "schema": "universe.task-frame-child-result.v1",
                "mutation_evidence_required": False,
            },
        )

        with self.assertRaises(WorkerDispatchError) as captured:
            RuntimeWorkerDispatcher._validate_structured_result(
                {**result, "mutation_evidence_refs": ["mutation://receipt"]},
                {
                    "schema": "universe.task-frame-child-result.v1",
                    "mutation_evidence_required": True,
                },
            )
        self.assertEqual("WORKER_STRUCTURED_RESULT_INVALID", captured.exception.code)
        self.assertEqual("WORKER_VALIDATION_FAILED", captured.exception.reason)

    def test_dispatch_forwards_validated_bounded_mutation_scope(self) -> None:
        observed_scope: dict[str, object] = {}
        target = str(self.root / "tools" / "app.py")

        def post(_endpoint, _token, path, payload):
            if path == "/v1/task-frame/worker-result":
                return {"status": "TASK_FRAME_RESULT_RECORDED"}
            operation = payload["operation"]
            if operation["operation"] == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "input_bundle": {},
                        },
                    },
                }
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "TURN_CLAIMED",
                    "turn": {
                        "turn_id": operation["turn_id"],
                        "state": "CLAIMED",
                        "claimed_by": operation["worker_id"],
                    },
                },
            }

        class Dispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, request):
                observed_scope.update(
                    repository_write_scope=request["repository_write_scope"],
                    mutation_scope=request["mutation_scope"],
                    response_timeout_seconds=request["response_timeout_seconds"],
                )
                return {
                    "status": "COMPLETED",
                    "worker_id": "provider-worker-1",
                    "worker_run_ref": request["worker_run_ref"],
                    "result_receipt_ref": "result://worker-1",
                    "result": {
                        "text": (
                            '{"outcome":"SUCCEEDED","summary":"done",'
                            '"evidence_refs":["result://worker-1"],'
                            '"validation":[{"plane":"focused-tests","state":"PASS",'
                            '"evidence_refs":["test://focused"]}],'
                            '"mutation_evidence_refs":["mutation://receipt-1"]}'
                        )
                    },
                    "session_persistence": "EPHEMERAL",
                    "persistent_session_ref": "UNKNOWN",
                    "universe_coordinate_persisted": False,
                }

        request = {
            **self._dispatch_request(),
            "repository_write_scope": "BOUNDED",
            "mutation_scope": {"operations": ["MODIFY"], "targets": [target]},
            "result_mode": "STRUCTURED_JSON",
            "output_contract": {
                "schema": "universe.task-frame-child-result.v1",
                "mutation_evidence_required": True,
            },
        }
        result = Dispatcher(self.root, post=post).dispatch(request)

        self.assertEqual("BOUNDED", observed_scope["repository_write_scope"])
        self.assertEqual(
            {"operations": ["MODIFY"], "targets": [target]},
            observed_scope["mutation_scope"],
        )
        self.assertEqual(90, observed_scope["response_timeout_seconds"])
        self.assertEqual("TASK_FRAME_RESULT_RECORDED", result["status"])

    def test_structured_refusal_recovers_claim_before_result_submission(self) -> None:
        events: list[str] = []

        def post(_endpoint, _token, path, payload):
            if path == "/v1/task-frame/worker-result":
                self.fail("refused child result must not be submitted")
            operation = payload["operation"]
            events.append(operation["operation"])
            if operation["operation"] == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "input_bundle": {},
                        },
                    },
                }
            if operation["operation"] == "claim_turn":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "TURN_CLAIMED",
                        "turn": {
                            "turn_id": operation["turn_id"],
                            "state": "CLAIMED",
                            "claimed_by": operation["worker_id"],
                        },
                    },
                }
            self.assertEqual("worker_initialization_failed", operation["operation"])
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {"status": "WORKER_INITIALIZATION_FAILURE_RECORDED"},
            }

        class Dispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, request):
                return {
                    "status": "COMPLETED",
                    "worker_id": "provider-worker-1",
                    "worker_run_ref": request["worker_run_ref"],
                    "result_receipt_ref": "result://worker-1",
                    "result": {
                        "text": (
                            '{"outcome":"REFUSED","summary":"no",'
                            '"evidence_refs":["result://worker-1"],'
                            '"validation":[{"plane":"focused-tests","state":"NOT_RUN",'
                            '"evidence_refs":["test://not-run"]}]}'
                        )
                    },
                    "session_persistence": "EPHEMERAL",
                    "persistent_session_ref": "UNKNOWN",
                    "universe_coordinate_persisted": False,
                }

        request = {
            **self._dispatch_request(),
            "result_mode": "STRUCTURED_JSON",
            "output_contract": {
                "schema": "universe.task-frame-child-result.v1",
                "mutation_evidence_required": False,
            },
        }
        store = WorkerFailureEvidenceStore(self.root / "worker-failures.sqlite3")
        with self.assertRaises(WorkerDispatchError) as captured:
            Dispatcher(
                self.root,
                post=post,
                failure_evidence_store=store,
            ).dispatch(request)

        self.assertEqual("WORKER_STRUCTURED_RESULT_INVALID", captured.exception.code)
        self.assertEqual("WORKER_OUTCOME_NOT_SUCCEEDED", captured.exception.reason)
        self.assertEqual(
            "WORKER_INITIALIZATION_FAILURE_RECORDED",
            captured.exception.recovery_status,
        )
        self.assertEqual(
            ["worker_invocation_plan", "claim_turn", "worker_initialization_failed"],
            events,
        )

    def test_root_boss_can_defer_terminal_result_until_children_finish(self) -> None:
        events: list[str] = []

        def post(_endpoint, _token, path, payload):
            if path == "/v1/task-frame/worker-result":
                events.append("result")
                return {"status": "TASK_COMPLETED"}
            operation = payload["operation"]["operation"]
            events.append(operation)
            if operation == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "role": "BOSS",
                            "input_bundle": {},
                        },
                    },
                }
            claimed = payload["operation"]
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "TURN_CLAIMED",
                    "turn": {
                        "turn_id": claimed["turn_id"],
                        "state": "CLAIMED",
                        "claimed_by": claimed["worker_id"],
                    },
                },
            }

        class Dispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, request):
                events.append("provider")
                return {
                    "status": "COMPLETED",
                    "worker_id": "provider-boss-1",
                    "worker_run_ref": request["worker_run_ref"],
                    "result_receipt_ref": "result://boss-1",
                    "result": {"text": "{}"},
                    "session_persistence": "EPHEMERAL",
                    "persistent_session_ref": "UNKNOWN",
                    "universe_coordinate_persisted": False,
                }

        request = {**self._dispatch_request(), "defer_terminal_result": True}
        dispatcher = Dispatcher(self.root, post=post)
        captured = dispatcher.dispatch(request)

        self.assertEqual("WORKER_OUTPUT_CAPTURED", captured["status"])
        self.assertEqual(
            ["worker_invocation_plan", "claim_turn", "provider"], events
        )
        completed = dispatcher.record_captured_result(
            request, captured["worker_envelope"]
        )
        self.assertEqual("TASK_COMPLETED", completed["status"])
        self.assertEqual(
            ["worker_invocation_plan", "claim_turn", "provider", "result"], events
        )

    def test_dispatch_uses_model_declared_by_task_frame(self) -> None:
        observed_models: list[str] = []

        def post(_endpoint, _token, path, payload):
            if path == "/v1/task-frame/worker-result":
                return {"status": "TASK_FRAME_RESULT_RECORDED"}
            operation = payload["operation"]
            if operation["operation"] == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "node-master-selected-model",
                            "input_bundle": {},
                        },
                    },
                }
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "TURN_CLAIMED",
                    "turn": {
                        "turn_id": operation["turn_id"],
                        "state": "CLAIMED",
                        "claimed_by": operation["worker_id"],
                    },
                },
            }

        class Dispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "host-default-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, request):
                observed_models.append(request["model"])
                return {
                    "status": "COMPLETED",
                    "worker_id": "provider-worker-1",
                    "worker_run_ref": request["worker_run_ref"],
                    "result_receipt_ref": "result://worker-1",
                    "result": {"text": "done"},
                    "session_persistence": "EPHEMERAL",
                    "persistent_session_ref": "UNKNOWN",
                    "universe_coordinate_persisted": False,
                }

        result = Dispatcher(self.root, post=post).dispatch(self._dispatch_request())

        self.assertEqual(["node-master-selected-model"], observed_models)
        self.assertEqual(
            "provider://CODEX/model/node-master-selected-model",
            result["model_ref"],
        )

    def test_dispatch_rejects_claimed_turn_identity_mismatch(self) -> None:
        provider_started = False

        def post(_endpoint, _token, _path, payload):
            operation = payload["operation"]
            if operation["operation"] == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "input_bundle": {},
                        },
                    },
                }
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "TURN_CLAIMED",
                    "turn": {
                        "turn_id": operation["turn_id"],
                        "state": "CLAIMED",
                        "claimed_by": "different-worker",
                    },
                },
            }

        class Dispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, _request):
                nonlocal provider_started
                provider_started = True
                raise AssertionError("provider must not start for a mismatched claim")

        with self.assertRaises(WorkerDispatchError) as captured:
            Dispatcher(self.root, post=post).dispatch(self._dispatch_request())

        self.assertEqual("WORKER_CLAIM_EVIDENCE_MISMATCH", captured.exception.code)
        self.assertFalse(provider_started)

    def test_provider_failure_is_persisted_before_claim_recovery(self) -> None:
        store = WorkerFailureEvidenceStore(self.root / "universe.sqlite3")
        observed_ref = ""

        def post(_endpoint, _token, _path, payload):
            nonlocal observed_ref
            operation = payload["operation"]["operation"]
            if operation == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "input_bundle": {},
                        },
                    },
                }
            if operation == "claim_turn":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "TURN_CLAIMED",
                        "turn": {
                            "turn_id": payload["operation"]["turn_id"],
                            "state": "CLAIMED",
                            "claimed_by": payload["operation"]["worker_id"],
                        },
                    },
                }
            self.assertEqual("worker_initialization_failed", operation)
            observed_ref = payload["operation"]["host_evidence_ref"]
            self.assertIsNotNone(store.get(observed_ref))
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "WORKER_INITIALIZATION_FAILURE_RECORDED"
                },
            }

        class FailingDispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, _request):
                raise WorkerDispatchError(
                    "TASK_FRAME_INITIALIZATION_FAILED",
                    "WORKER_ADAPTER",
                    "approval request failed",
                )

        dispatcher = FailingDispatcher(
            self.root,
            post=post,
            failure_evidence_store=store,
        )
        with self.assertRaises(WorkerDispatchError) as captured:
            dispatcher.dispatch(self._dispatch_request())

        self.assertEqual("TASK_FRAME_INITIALIZATION_FAILED", captured.exception.code)
        self.assertTrue(observed_ref)
        self.assertEqual(
            "WORKER_INITIALIZATION_FAILURE_RECORDED",
            captured.exception.recovery_status,
        )
        evidence = store.get(observed_ref)
        assert evidence is not None
        self.assertEqual("session-1", evidence["session_id"])
        self.assertEqual("frame-1", evidence["frame_id"])
        self.assertEqual("turn-1", evidence["turn_id"])

    def test_recovery_rejection_preserves_durable_evidence_ref(self) -> None:
        store = WorkerFailureEvidenceStore(self.root / "universe.sqlite3")

        def post(_endpoint, _token, _path, payload):
            operation = payload["operation"]["operation"]
            if operation == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "CODEX",
                            "model": "test-model",
                            "input_bundle": {},
                        },
                    },
                }
            if operation == "claim_turn":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "TURN_CLAIMED",
                        "turn": {
                            "turn_id": payload["operation"]["turn_id"],
                            "state": "CLAIMED",
                            "claimed_by": payload["operation"]["worker_id"],
                        },
                    },
                }
            return {
                "status": "TASK_FRAME_OPERATION_REJECTED",
                "output": {"status": "TASK_FRAME_OPERATION_UNKNOWN"},
            }

        class FailingDispatcher(RuntimeWorkerDispatcher):
            def provider_capability(self, _provider):
                return {
                    "status": "AVAILABLE",
                    "provider": "CODEX",
                    "model": "test-model",
                    "capability_evidence_ref": "host://codex/test",
                }

            def _invoke_provider(self, _provider, _request):
                raise WorkerDispatchError(
                    "TASK_FRAME_INITIALIZATION_FAILED",
                    "WORKER_ADAPTER",
                    "approval request failed",
                )

        with self.assertRaises(WorkerDispatchError) as captured:
            FailingDispatcher(
                self.root,
                post=post,
                failure_evidence_store=store,
            ).dispatch(self._dispatch_request())

        self.assertEqual(
            "WORKER_INITIALIZATION_RECOVERY_FAILED", captured.exception.code
        )
        self.assertEqual(
            "TASK_FRAME_OPERATION_UNKNOWN", captured.exception.recovery_status
        )
        self.assertIsNotNone(store.get(captured.exception.host_evidence_ref))


if __name__ == "__main__":
    unittest.main()
