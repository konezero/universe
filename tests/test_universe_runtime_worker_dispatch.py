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
from universe_app.terminal_host import TerminalHostError  # noqa: E402
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

    def test_task_frame_roles_run_from_repository_with_bounded_tool_permissions(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)
        qa_request = {
            **self.request,
            "context_pack": {"semantic_role": "QA_REVIEWER"},
        }
        permission = {
            "options": [
                {"kind": "allow_once", "optionId": "allow-once"},
                {"kind": "reject_once", "optionId": "reject-once"},
            ]
        }

        self.assertEqual(self.root.resolve(), dispatcher._worker_cwd(qa_request))
        self.assertEqual(900, dispatcher._response_timeout_seconds_for(qa_request))
        self.assertEqual(
            "allow-once",
            dispatcher._task_frame_permission(qa_request, permission),
        )
        prompt = dispatcher._worker_prompt(qa_request)
        self.assertIn("run tests, builds, and validation commands", prompt)
        self.assertIn("Do not modify files", prompt)

        review_request = {
            **self.request,
            "context_pack": {"semantic_role": "SECURITY_REVIEWER"},
        }
        self.assertEqual(self.root.resolve(), dispatcher._worker_cwd(review_request))
        self.assertEqual(
            "reject-once",
            dispatcher._task_frame_permission(review_request, permission),
        )

        read_permission = {
            "tool_call": {"toolName": "Read", "input": {"file_path": __file__}},
            "options": permission["options"],
        }
        self.assertEqual(
            "allow-once",
            dispatcher._task_frame_permission(review_request, read_permission),
        )

        target = str(self.root / "tools" / "app.py")
        implement_request = {
            **self.request,
            "repository_write_scope": "BOUNDED",
            "mutation_scope": {
                "operations": ["CREATE", "MODIFY"],
                "targets": [target],
            },
            "context_pack": {"semantic_role": "IMPLEMENTER"},
        }
        write_permission = {
            "tool_call": {
                "toolName": "Write",
                "path": target,
                "input": {"file_path": target, "content": "updated"},
            },
            "options": permission["options"],
        }
        self.assertEqual(
            "allow-once",
            dispatcher._task_frame_permission(implement_request, write_permission),
        )
        write_permission["tool_call"]["path"] = str(self.root / "outside.py")
        write_permission["tool_call"]["input"]["file_path"] = str(
            self.root / "outside.py"
        )
        self.assertEqual(
            "reject-once",
            dispatcher._task_frame_permission(implement_request, write_permission),
        )

        codex_file_permission = {
            "tool_call": {
                "title": "item/fileChange/requestApproval",
                "cwd": str(self.root),
                "grantRoot": None,
                "fileChanges": [
                    {"path": "tools/app.py", "type": "update"},
                ],
            },
            "options": permission["options"],
        }
        self.assertEqual(
            "allow-once",
            dispatcher._task_frame_permission(
                implement_request, codex_file_permission
            ),
        )
        codex_file_permission["tool_call"]["fileChanges"] = [
            {"path": "tools/outside.py", "type": "update"},
        ]
        self.assertEqual(
            "reject-once",
            dispatcher._task_frame_permission(
                implement_request, codex_file_permission
            ),
        )
        codex_file_permission["tool_call"]["fileChanges"] = [
            {
                "path": "tools/app.py",
                "type": "update",
                "move_path": "tools/moved.py",
            },
        ]
        self.assertEqual(
            "reject-once",
            dispatcher._task_frame_permission(
                implement_request, codex_file_permission
            ),
        )

    def test_task_frame_system_prompt_follows_validated_task_scope(self) -> None:
        prompt = RuntimeWorkerDispatcher._system_prompt("TASK_FRAME_RUNTIME")

        self.assertIn("Perform the assigned task", prompt)
        self.assertIn(
            "Runtime-validated purpose, scope, and output contract", prompt
        )
        self.assertIn("declared mutation scope", prompt)
        self.assertIn("Host gateway", prompt)
        self.assertNotIn("Do not create or modify files", prompt)

    def test_codex_task_frame_requests_ephemeral_provider_session(self) -> None:
        observed: list[tuple[bool, str, float | None]] = []

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

        self.assertEqual([(True, "test-model", None)], observed)
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("NOT_PERSISTED", result["provider_durable_chat_state"])

    def test_codex_task_frame_uses_exact_managed_host_coordinate(self) -> None:
        terminal_host = object()
        observed: list[dict[str, object]] = []

        class FakeCodexSession:
            def __init__(self, *, session_observer, **kwargs) -> None:
                observed.append(dict(kwargs))
                self.session_ref = "codex-app-server:managed-1"
                session_observer("managed-1")

        request = {
            **self.request,
            "effort": "MAX",
            "supervisor_transport": {
                "terminal_host": terminal_host,
                "project_id": "universe",
                "mode": "MASTER",
                "supervisor_session_id": "session-worker-1",
                "session_anchor_ref": "session-anchor-worker-1",
            },
        }
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
            result = dispatcher._invoke_codex(request)

        self.assertIs(terminal_host, observed[0]["terminal_host"])
        self.assertEqual("session-worker-1", observed[0]["supervisor_session_id"])
        self.assertEqual("session-anchor-worker-1", observed[0]["session_anchor_ref"])
        self.assertEqual("MAX", observed[0]["effort"])
        self.assertTrue(result["universe_coordinate_persisted"])
        self.assertEqual("session-worker-1", result["host_session_ref"])

    def test_dispatcher_resolves_one_host_coordinate_per_declared_turn(self) -> None:
        terminal_host = object()
        observed: list[tuple[str, str, str]] = []

        def resolve(provider, frame_id, turn_id):
            observed.append((provider, frame_id, turn_id))
            return {
                "supervisor_session_id": "session-worker-2",
                "session_anchor_ref": "session-anchor-worker-2",
            }

        dispatcher = RuntimeWorkerDispatcher(
            self.root,
            terminal_host=terminal_host,
            project_id="universe",
            mode="MASTER",
            worker_host_coordinate_resolver=resolve,
        )
        transport = dispatcher._supervisor_transport(
            "CLAUDE",
            {"frame_id": "frame-1", "turn_id": "implement"},
        )

        self.assertEqual([("CLAUDE", "frame-1", "implement")], observed)
        self.assertIs(terminal_host, transport["terminal_host"])
        self.assertEqual("session-worker-2", transport["supervisor_session_id"])
        self.assertEqual("session-anchor-worker-2", transport["session_anchor_ref"])

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
        observed_timeouts: list[float | None] = []

        class FakeGrokSession:
            def __init__(
                self, *, model, response_timeout_seconds, session_observer, **_kwargs
            ) -> None:
                self.model = model
                observed_timeouts.append(response_timeout_seconds)
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

        self.assertEqual([None], observed_timeouts)
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("UNKNOWN", result["provider_durable_chat_state"])

    def test_claude_task_frame_disables_session_persistence(self) -> None:
        observed: list[tuple[bool, bool, int | None, float, str, object]] = []
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
                    "result_mode": "STRUCTURED_JSON",
                    "output_contract": {"json_schema": schema},
                }
            )

        self.assertEqual([(True, False, None, 90.0, "test-model", schema)], observed)
        self.assertEqual("CLAUDE_CODE_CLI_ADAPTER", result["runtime_provider"])
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("NOT_PERSISTED", result["provider_durable_chat_state"])
        self.assertEqual("default", result["permission_mode"])

    def test_claude_task_frame_uses_managed_stream_host(self) -> None:
        terminal_host = object()
        observed: list[dict[str, object]] = []

        class FakeBroker:
            def start(self):
                return self

            def write_mcp_config(self, path):
                return path

            @staticmethod
            def provider_environment(environment):
                return dict(environment)

            @staticmethod
            def wait_for_registration():
                return True

            @staticmethod
            def close():
                return None

        class FakeClaudeResidentSession:
            def __init__(self, *, session_observer, **kwargs) -> None:
                observed.append(dict(kwargs))
                self.session_id = "managed-claude-1"
                self.session_ref = "claude-code:managed-claude-1"
                session_observer(self.session_id)

        request = {
            **self.request,
            "effort": "HIGH",
            "result_mode": "STRUCTURED_JSON",
            "output_contract": {"json_schema": {"type": "object"}},
            "supervisor_transport": {
                "terminal_host": terminal_host,
                "project_id": "universe",
                "mode": "MASTER",
                "supervisor_session_id": "session-claude-worker",
                "session_anchor_ref": "session-anchor-claude-worker",
            },
        }
        dispatcher = RuntimeWorkerDispatcher(
            self.root,
            project_id="universe",
            mode="MASTER",
        )
        with (
            patch(
                "universe_runtime_worker_dispatch._resolve_claude",
                return_value=(self.root / "claude.exe", {}, "test-model"),
            ),
            patch(
                "universe_runtime_worker_dispatch.ClaudePermissionBroker",
                return_value=FakeBroker(),
            ),
            patch(
                "universe_runtime_worker_dispatch.ClaudeResidentSession",
                FakeClaudeResidentSession,
            ),
            patch(
                "universe_runtime_worker_dispatch.UniverseAcpGateway",
                FakeGateway,
            ),
        ):
            result = dispatcher._invoke_claude(request)

        self.assertIs(terminal_host, observed[0]["terminal_host"])
        self.assertEqual("HIGH", observed[0]["effort"])
        self.assertEqual({"type": "object"}, observed[0]["json_schema"])
        self.assertIn("--no-session-persistence", observed[0]["extra_arguments"])
        self.assertIsNone(observed[0]["turn_timeout_seconds"])
        self.assertEqual("CLAUDE_CODE_STREAM_ADAPTER", result["runtime_provider"])
        self.assertTrue(result["universe_coordinate_persisted"])
        self.assertEqual("session-claude-worker", result["host_session_ref"])

    def test_managed_claude_preserves_rust_host_launch_failure_detail(self) -> None:
        class FakeBroker:
            def start(self):
                return self

            def write_mcp_config(self, path):
                return path

            @staticmethod
            def provider_environment(environment):
                return dict(environment)

            @staticmethod
            def wait_for_registration():
                return True

            @staticmethod
            def close():
                return None

        class FailingClaudeResidentSession:
            def __init__(self, **_kwargs) -> None:
                raise TerminalHostError(
                    "TERMINAL_SPAWN_FAILED",
                    "[WinError 206] The filename or extension is too long",
                )

        request = {
            **self.request,
            "supervisor_transport": {
                "terminal_host": object(),
                "project_id": "universe",
                "mode": "MASTER",
                "supervisor_session_id": "session-claude-worker",
                "session_anchor_ref": "session-anchor-claude-worker",
            },
        }
        dispatcher = RuntimeWorkerDispatcher(self.root)
        with (
            patch(
                "universe_runtime_worker_dispatch._resolve_claude",
                return_value=(self.root / "claude.exe", {}, "test-model"),
            ),
            patch(
                "universe_runtime_worker_dispatch.ClaudePermissionBroker",
                return_value=FakeBroker(),
            ),
            patch(
                "universe_runtime_worker_dispatch.ClaudeResidentSession",
                FailingClaudeResidentSession,
            ),
        ):
            with self.assertRaises(WorkerDispatchError) as captured:
                dispatcher._invoke_claude(request)

        self.assertEqual("WORKER_TRANSPORT_FAILED", captured.exception.code)
        self.assertEqual("RUST_HOST", captured.exception.stage)
        self.assertEqual(
            "TERMINAL_SPAWN_FAILED:[WinError 206] The filename or extension is too long",
            captured.exception.reason,
        )

    def test_managed_codex_preserves_rust_host_transport_failure_detail(self) -> None:
        class FailingCodexAppServerSession:
            def __init__(self, **_kwargs) -> None:
                raise TerminalHostError(
                    "PTY_SUPERVISOR_UNAVAILABLE",
                    "Remote end closed connection without response",
                )

        request = {
            **self.request,
            "supervisor_transport": {
                "terminal_host": object(),
                "project_id": "universe",
                "mode": "MASTER",
                "supervisor_session_id": "session-codex-worker",
                "session_anchor_ref": "session-anchor-codex-worker",
            },
        }
        dispatcher = RuntimeWorkerDispatcher(self.root)
        with (
            patch(
                "universe_runtime_worker_dispatch._resolve_codex",
                return_value=(self.root / "codex.exe", {}, "test-model"),
            ),
            patch(
                "universe_runtime_worker_dispatch.CodexAppServerSession",
                FailingCodexAppServerSession,
            ),
        ):
            with self.assertRaises(WorkerDispatchError) as captured:
                dispatcher._invoke_codex(request)

        self.assertEqual("WORKER_TRANSPORT_FAILED", captured.exception.code)
        self.assertEqual("RUST_HOST", captured.exception.stage)
        self.assertEqual(
            "PTY_SUPERVISOR_UNAVAILABLE:Remote end closed connection without response",
            captured.exception.reason,
        )

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

    def test_invoke_structured_provider_validates_complete_object(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)
        request = {
            **self.request,
            "result_mode": "STRUCTURED_JSON",
            "output_contract": {
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["summary"],
                    "properties": {"summary": {"type": "string"}},
                }
            },
        }
        worker = {
            "status": "COMPLETED",
            "result": {"text": '{"summary":"bounded"}'},
            "result_receipt_ref": "receipt://structured",
        }
        with patch.object(dispatcher, "_invoke_provider", return_value=worker):
            result = dispatcher.invoke_structured_provider("GROK", request)

        self.assertEqual({"summary": "bounded"}, result["structured_result"])

    def test_invoke_structured_provider_rejects_narration_and_missing_fields(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)
        request = {
            **self.request,
            "result_mode": "STRUCTURED_JSON",
            "output_contract": {
                "json_schema": {
                    "type": "object",
                    "required": ["summary"],
                    "properties": {"summary": {"type": "string"}},
                }
            },
        }
        for text, reason in (
            ('I will comply. {"summary":"bounded"}', "WORKER_RESULT_JSON_INVALID"),
            ('{"other":"value"}', "WORKER_RESULT_SCHEMA_REQUIRED_FIELD_MISSING"),
        ):
            with self.subTest(text=text):
                with patch.object(
                    dispatcher,
                    "_invoke_provider",
                    return_value={"status": "COMPLETED", "result": {"text": text}},
                ):
                    with self.assertRaises(WorkerDispatchError) as captured:
                        dispatcher.invoke_structured_provider("GROK", request)
                self.assertEqual("WORKER_STRUCTURED_RESULT_INVALID", captured.exception.code)
                self.assertEqual(reason, captured.exception.reason)

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

    def test_task_frame_ignores_legacy_turn_budget(self) -> None:
        dispatcher = RuntimeWorkerDispatcher(self.root)
        normalized = dispatcher._normalize_request(
            {**self._dispatch_request(), "max_turns": 16}
        )
        self.assertNotIn("max_turns", normalized)

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
