from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_runtime_host import (  # noqa: E402
    RuntimeHostError,
    UniverseRuntimeHost,
    redacted_invocation_record,
)
from universe_runtime_worker_dispatch import WorkerDispatchError  # noqa: E402


class FakeWorkerDispatcher:
    def __init__(
        self,
        *,
        response: dict[str, object] | None = None,
        error: WorkerDispatchError | None = None,
    ) -> None:
        self.response = response or {}
        self.error = error
        self.capability_calls: list[str] = []
        self.dispatch_calls: list[dict[str, object]] = []

    def provider_capability(self, provider: str) -> dict[str, str]:
        self.capability_calls.append(provider)
        return {
            "provider": provider,
            "status": "AVAILABLE",
            "cli_auto_approve": "ON",
        }

    def dispatch(self, request: dict[str, object]) -> dict[str, object]:
        self.dispatch_calls.append(request)
        if self.error is not None:
            raise self.error
        return dict(self.response)


class UniverseRuntimeHostLayoutTests(unittest.TestCase):
    def test_provider_workers_are_owned_by_universe_runtime_host(self) -> None:
        expected = (
            "tools/universe_runtime_host_dispatch.ps1",
            "tools/universe_runtime_host_grok_adapter.json",
            "tools/universe_runtime_host_grok_invoke.ps1",
            "tools/universe_runtime_host_codex_worker.ps1",
            "tools/agent_session_gateway.py",
            "tools/universe_runtime_worker_dispatch.py",
            "tools/windows_native_cli.py",
        )
        for relative in expected:
            self.assertTrue((ROOT / relative).is_file(), relative)

        legacy_dispatcher = (ROOT / expected[0]).read_text(encoding="utf-8")
        self.assertIn("WINDOWS_NATIVE_CLI_ROUTE_REQUIRED", legacy_dispatcher)
        self.assertNotIn("powershell.exe", legacy_dispatcher)

        gateway = (ROOT / expected[4]).read_text(encoding="utf-8")
        self.assertIn("class UniverseAcpGateway", gateway)
        self.assertIn("class GrokAcpSession", gateway)
        self.assertIn("class CodexAppServerSession", gateway)

        dispatcher = (ROOT / expected[5]).read_text(encoding="utf-8")
        self.assertIn("RuntimeWorkerDispatcher", dispatcher)
        self.assertIn("GrokAcpSession", dispatcher)
        self.assertIn("CodexAppServerSession", dispatcher)
        self.assertNotIn('"--prompt-file"', dispatcher)
        self.assertNotIn('"exec",', dispatcher)
        self.assertIn("skill_run_observations", dispatcher)
        self.assertIn('"validation_state": "NOT_RUN"', dispatcher)
        self.assertNotIn("shell=True", dispatcher)

        native_runner = (ROOT / expected[6]).read_text(encoding="utf-8")
        self.assertIn("shell=False", native_runner)
        self.assertIn("NativeCliRequest", native_runner)
        host = (ROOT / "tools/universe_runtime_host.py").read_text(encoding="utf-8")
        self.assertIn("RuntimeWorkerDispatcher", host)
        self.assertNotIn('"powershell.exe"', host)

    def test_installed_codex_adapter_does_not_declare_universe_worker(self) -> None:
        adapter = json.loads(
            (ROOT / ".ai/adapters/codex/adapter.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("task_frame_worker", adapter)

        removed = (
            ".ai/adapters/worker-dispatch.ps1",
            ".ai/adapters/worker-dispatch.md",
            ".ai/adapters/grok/adapter.json",
            ".ai/adapters/grok/invoke.ps1",
            ".ai/adapters/codex/worker.ps1",
        )
        for relative in removed:
            self.assertFalse((ROOT / relative).exists(), relative)


class UniverseRuntimeHostTests(unittest.TestCase):
    @staticmethod
    def request() -> dict[str, object]:
        return {
            "schema": "universe.runtime-worker-invocation-request.v1",
            "invocation_id": "invoke-001",
            "provider": "GROK",
            "endpoint": "http://127.0.0.1:17777",
            "token": "transient-token-must-not-persist",
            "session_id": "session-001",
            "frame_id": "frame-001",
            "turn_id": "turn-001",
            "invoker_actor_ref": "universe-runtime-host",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "context_pack": {"summary": "read only"},
            "output_contract": {"kind": "review"},
            "max_turns": 1,
        }

    def test_redacted_record_excludes_endpoint_token_and_bodies(self) -> None:
        record = redacted_invocation_record(self.request())
        encoded = json.dumps(record, sort_keys=True)
        self.assertNotIn("transient-token", encoded)
        self.assertNotIn("127.0.0.1:17777", encoded)
        self.assertNotIn("read only", encoded)
        self.assertIn("context_pack_digest", record)
        self.assertEqual("REDACTED", record["result_mode"])

    def test_write_scope_is_rejected_before_dispatch(self) -> None:
        request = self.request()
        request["repository_write_scope"] = "PROJECT"
        with self.assertRaisesRegex(RuntimeHostError, "read-only"):
            redacted_invocation_record(request)

    def test_capability_and_invocation_are_normalized(self) -> None:
        dispatcher = FakeWorkerDispatcher(
            response={
                "status": "TASK_FRAME_RESULT_RECORDED",
                "worker_id": "grok-1",
                "result_receipt_ref": "result-1",
                "skill_run_observation_count": 2,
                "result": {"text": "bounded reply"},
            }
        )
        host = UniverseRuntimeHost(ROOT, worker_dispatcher=dispatcher)
        capability = host.provider_capability("GROK")
        self.assertEqual("AVAILABLE", capability["status"])
        self.assertEqual("ON", capability["cli_auto_approve"])
        result = host.invoke_read_only(self.request())
        self.assertEqual("TASK_FRAME_RESULT_RECORDED", result["status"])
        self.assertFalse(result["repository_write"])
        self.assertEqual("result-1", result["result_receipt_ref"])
        self.assertEqual(2, result["skill_run_observation_count"])
        self.assertEqual({"text": "bounded reply"}, result["result"])
        self.assertNotIn("worker_run_ref", result)
        self.assertEqual(["GROK"], dispatcher.capability_calls)
        self.assertEqual(1, len(dispatcher.dispatch_calls))
        self.assertEqual(
            "universe.task-frame-worker-dispatch-request.v1",
            dispatcher.dispatch_calls[0]["schema"],
        )
        self.assertEqual("REDACTED", dispatcher.dispatch_calls[0]["result_mode"])

    def test_structured_invocation_returns_only_parsed_result(self) -> None:
        dispatcher = FakeWorkerDispatcher(
            response={
                "status": "TURN_COMPLETED",
                "provider": "GROK",
                "model_ref": "provider://GROK/model/grok-build",
                "worker_id": "grok-structured-1",
                "result_receipt_ref": "result-structured-1",
                "structured_result": {
                    "schema": "example.output.v1",
                    "value": "bounded",
                },
            }
        )

        request = self.request()
        request["result_mode"] = "STRUCTURED_JSON"
        result = UniverseRuntimeHost(
            ROOT, worker_dispatcher=dispatcher
        ).invoke_structured(request)
        self.assertEqual(
            {"schema": "example.output.v1", "value": "bounded"},
            result["structured_result"],
        )
        self.assertEqual(
            "provider://GROK/model/grok-build",
            result["model_ref"],
        )
        self.assertEqual("STRUCTURED_JSON", dispatcher.dispatch_calls[0]["result_mode"])

    def test_planning_proposal_is_built_by_installed_task_frame_cli(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_: object) -> SimpleNamespace:
            commands.append(command)
            if "-CapabilityOnly" in command:
                return SimpleNamespace(
                    stdout='{"provider":"GROK","status":"AVAILABLE"}',
                    returncode=0,
                )
            request_path = Path(command[command.index("--request") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            proposal = {
                "schema": "ai-career.task-frame-execution-proposal.v2",
                "status": "TASK_FRAME_EXECUTION_PROPOSED",
                "proposal_id": "task_frame_proposal_test",
                "plan_digest": "a" * 64,
                "approval_required": True,
                "execution_plan": request["execution_plan"],
                "authority_created": False,
                "task_frame_started": False,
            }
            return SimpleNamespace(
                stdout=json.dumps({"execution_proposal": proposal}),
                returncode=0,
            )

        host = UniverseRuntimeHost(
            ROOT,
            runner=runner,
            worker_dispatcher=FakeWorkerDispatcher(),
        )
        result = host.build_planning_proposal(
            runtime_binding={
                "session_id": "session-001",
                "origin_anchor_ref": "anchor-001",
                "origin_frame_id": "current",
                "parent_actor_ref": "universe-conductor",
            },
            refinement_request={"request_id": "refinementreq_001"},
            provider="GROK",
            run_id="planningrun_001",
        )
        self.assertEqual("GROK", result["provider"])
        self.assertEqual("planning-boss", result["turn_id"])
        self.assertEqual(
            "NONE",
            result["execution_proposal"]["execution_plan"]["repository_write_scope"],
        )
        self.assertEqual(1, len(commands))
        self.assertIn("task-frame", commands[0])

    def test_conductor_message_uses_read_only_task_frame(self) -> None:
        def runner(command: list[str], **_: object) -> SimpleNamespace:
            request_path = Path(command[command.index("--request") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            proposal = {
                "schema": "ai-career.task-frame-execution-proposal.v2",
                "status": "TASK_FRAME_EXECUTION_PROPOSED",
                "proposal_id": "task_frame_proposal_conductor",
                "plan_digest": "b" * 64,
                "approval_required": True,
                "execution_plan": request["execution_plan"],
                "authority_created": False,
                "task_frame_started": False,
            }
            return SimpleNamespace(
                stdout=json.dumps({"execution_proposal": proposal}),
                returncode=0,
            )

        dispatcher = FakeWorkerDispatcher(
            response={
                "status": "TURN_COMPLETED",
                "worker_id": "grok-conductor-1",
                "result_receipt_ref": "result-conductor-1",
                "result": {
                    "reply": "유니버스 응답",
                    "action": {"kind": "NONE"},
                },
                "structured_result": {
                    "reply": "유니버스 응답",
                    "action": {"kind": "NONE"},
                },
            }
        )
        host = UniverseRuntimeHost(
            ROOT,
            runner=runner,
            worker_dispatcher=dispatcher,
        )
        operations: list[dict[str, object]] = []

        def post_runtime(
            _: str, __: str, path: str, payload: dict[str, object]
        ) -> dict[str, object]:
            operations.append({"path": path, "payload": payload})
            if path == "/v1/task-frame/create":
                return {"status": "TASK_FRAME_HOST_ACTIVE"}
            if path == "/v1/task-frame/close":
                return {"status": "TASK_FRAME_CLOSED"}
            operation = payload["operation"]
            if operation["operation"] == "declare_turns":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {"status": "TASK_TURNS_DECLARED"},
                }
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {"status": "RESULT_PACKET_BUILT"},
            }

        host._post_runtime = post_runtime  # type: ignore[method-assign]
        result = host.invoke_conductor_message(
            runtime_binding={
                "endpoint": "http://127.0.0.1:17777",
                "token": "transient-token",
                "session_id": "session-001",
                "origin_anchor_ref": "anchor-001",
                "origin_frame_id": "current",
                "parent_actor_ref": "universe-conductor",
                "parent_evidence_ref": "host://parent/current",
            },
            message={
                "message_id": "conductor_001",
                "body": "현재 위험을 알려줘.",
                "ui_context": {"selected_project_id": "GCS"},
                "available_projects": [{"project_id": "GCS", "summary": "Trading"}],
            },
            history=[
                {
                    "sender": "USER",
                    "kind": "QUESTION",
                    "body": "이전 질문",
                }
            ],
            provider="GROK",
        )
        self.assertEqual(
            {"reply": "유니버스 응답", "action": {"kind": "NONE"}},
            result["structured_result"],
        )
        self.assertEqual(1, len(dispatcher.dispatch_calls))
        request = dispatcher.dispatch_calls[0]
        self.assertEqual("NONE", request["repository_write_scope"])
        self.assertEqual([], request["mutation_scope"]["operations"])
        self.assertEqual("CONDUCTOR", request["context_pack"]["mode"])
        self.assertEqual("STRUCTURED_JSON", request["result_mode"])
        self.assertEqual(
            "GCS", request["context_pack"]["ui_context"]["selected_project_id"]
        )
        self.assertEqual(
            "GCS", request["context_pack"]["available_projects"][0]["project_id"]
        )
        self.assertIn(
            "FRESH_PROJECT_DRAFT",
            request["output_contract"]["action_kinds"],
        )
        self.assertEqual(
            [
                "project",
                "kind",
                "goal",
                "target_users",
                "technologies",
                "constraints",
            ],
            request["output_contract"]["fresh_project_contract"]["required"],
        )
        self.assertEqual(
            "FRESH_PROJECT_DRAFT",
            request["output_contract"]["fresh_project_contract"]["example"]["kind"],
        )
        self.assertEqual(
            "intent",
            request["output_contract"]["fresh_project_contract"]["action_field"],
        )
        self.assertEqual(
            [
                "/v1/task-frame/create",
                "/v1/task-frame/operation",
                "/v1/task-frame/operation",
                "/v1/task-frame/close",
            ],
            [operation["path"] for operation in operations],
        )

    def test_dispatcher_json_failure_preserves_stage_and_reason(self) -> None:
        request = self.request()
        request["result_mode"] = "STRUCTURED_JSON"
        dispatcher = FakeWorkerDispatcher(
            error=WorkerDispatchError(
                "WORKER_STRUCTURED_RESULT_INVALID",
                "WORKER_ADAPTER",
                "WORKER_RESULT_JSON_INVALID",
            )
        )
        with self.assertRaises(RuntimeHostError) as captured:
            UniverseRuntimeHost(ROOT, worker_dispatcher=dispatcher).invoke_structured(
                request
            )
        self.assertEqual(
            "WORKER_STRUCTURED_RESULT_INVALID",
            captured.exception.code,
        )
        self.assertEqual(
            "WORKER_ADAPTER: WORKER_RESULT_JSON_INVALID",
            captured.exception.detail,
        )


if __name__ == "__main__":
    unittest.main()
