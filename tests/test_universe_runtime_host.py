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


class UniverseRuntimeHostLayoutTests(unittest.TestCase):
    def test_provider_workers_are_owned_by_universe_runtime_host(self) -> None:
        expected = (
            "tools/universe_runtime_host_dispatch.ps1",
            "tools/universe_runtime_host_grok_adapter.json",
            "tools/universe_runtime_host_grok_invoke.ps1",
            "tools/universe_runtime_host_codex_worker.ps1",
        )
        for relative in expected:
            self.assertTrue((ROOT / relative).is_file(), relative)

        dispatcher = (ROOT / expected[0]).read_text(encoding="utf-8")
        self.assertIn("Join-Path $PSScriptRoot '..'", dispatcher)
        self.assertIn("tools\\universe_runtime_host_grok_invoke.ps1", dispatcher)
        self.assertIn("tools\\universe_runtime_host_codex_worker.ps1", dispatcher)
        self.assertNotIn("tools\\runtime_host\\", dispatcher)
        self.assertIn("LOCALAPPDATA", dispatcher)
        self.assertIn("worker_run_ref", dispatcher)
        self.assertIn("result_receipt_ref", dispatcher)
        self.assertIn("skill_run_observations", dispatcher)
        self.assertIn("validation_state = 'NOT_RUN'", dispatcher)
        self.assertNotIn("host_invocation_receipt_ref", dispatcher)
        self.assertNotIn("host_result_evidence_ref", dispatcher)

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

    def test_write_scope_is_rejected_before_dispatch(self) -> None:
        request = self.request()
        request["repository_write_scope"] = "PROJECT"
        with self.assertRaisesRegex(RuntimeHostError, "read-only"):
            redacted_invocation_record(request)

    def test_capability_and_invocation_are_normalized(self) -> None:
        calls: list[list[str]] = []
        dispatched_request: dict[str, object] = {}

        def runner(command: list[str], **_: object) -> SimpleNamespace:
            calls.append(command)
            if "-CapabilityOnly" in command:
                return SimpleNamespace(
                    stdout='{"provider":"GROK","status":"AVAILABLE"}',
                    returncode=0,
                )
            request_path = Path(command[command.index("-RequestPath") + 1])
            dispatched_request.update(
                json.loads(request_path.read_text(encoding="utf-8"))
            )
            return SimpleNamespace(
                stdout=(
                    '{"status":"TASK_FRAME_RESULT_RECORDED",'
                    '"worker_id":"grok-1",'
                    '"result_receipt_ref":"result-1",'
                    '"skill_run_observation_count":2}'
                ),
                returncode=0,
            )

        host = UniverseRuntimeHost(ROOT, runner=runner)
        self.assertEqual("AVAILABLE", host.provider_capability("GROK")["status"])
        result = host.invoke_read_only(self.request())
        self.assertEqual("TASK_FRAME_RESULT_RECORDED", result["status"])
        self.assertFalse(result["repository_write"])
        self.assertEqual("result-1", result["result_receipt_ref"])
        self.assertEqual(2, result["skill_run_observation_count"])
        self.assertNotIn("worker_run_ref", result)
        self.assertEqual(2, len(calls))
        self.assertEqual(
            "universe.task-frame-worker-dispatch-request.v1",
            dispatched_request["schema"],
        )

    def test_dispatcher_without_json_reports_transport_failure(self) -> None:
        def runner(_: list[str], **__: object) -> SimpleNamespace:
            return SimpleNamespace(stdout="", stderr="adapter failed", returncode=4)

        host = UniverseRuntimeHost(ROOT, runner=runner)
        with self.assertRaises(RuntimeHostError) as captured:
            host.provider_capability("GROK")
        self.assertEqual("RUNTIME_HOST_TRANSPORT_FAILED", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
