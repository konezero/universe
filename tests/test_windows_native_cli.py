from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_runtime_worker_dispatch import RuntimeWorkerDispatcher  # noqa: E402
from windows_native_cli import (  # noqa: E402
    NATIVE_CLI_CONTRACT,
    NativeCliError,
    NativeCliRequest,
    NativeCliResult,
    run_native_cli,
)


def completed(stdout: str) -> NativeCliResult:
    return NativeCliResult(
        contract=NATIVE_CLI_CONTRACT,
        status="COMPLETED",
        return_code=0,
        duration_ms=1.0,
        stdout=stdout,
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )


class WindowsNativeCliTests(unittest.TestCase):
    def test_exact_argv_boundaries_survive_native_runner(self) -> None:
        expected = [
            "plain",
            "two words",
            "",
            'embedded "quote"',
            '{"key":"value with spaces"}',
            "line one\nline two",
            r"C:\path with spaces\file.txt",
            "한글 인수",
        ]
        script = (
            "import json,sys;"
            "sys.stdout.buffer.write("
            "json.dumps(sys.argv[1:],ensure_ascii=True).encode('utf-8'))"
        )
        result = run_native_cli(
            NativeCliRequest(
                executable=Path(sys.executable),
                arguments=("-c", script, *expected),
                timeout_seconds=10,
            )
        )
        self.assertEqual("COMPLETED", result.status)
        self.assertEqual(expected, json.loads(result.stdout))

    def test_shell_entrypoints_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = Path(temp) / "unsafe.cmd"
            command.write_text("@echo off\n", encoding="ascii")
            with self.assertRaisesRegex(NativeCliError, "shell and batch"):
                run_native_cli(NativeCliRequest(executable=command))

    def test_grok_dispatch_uses_prompt_file_and_structured_argv(self) -> None:
        native_requests: list[NativeCliRequest] = []
        task_frame_payloads: list[tuple[str, dict[str, object]]] = []
        observed_prompt = ""

        def native_runner(request: NativeCliRequest) -> NativeCliResult:
            nonlocal observed_prompt
            native_requests.append(request)
            if request.arguments == ("--version",):
                return completed("grok 0.2.112")
            arguments = list(request.arguments)
            prompt_path = Path(arguments[arguments.index("--prompt-file") + 1])
            observed_prompt = prompt_path.read_text(encoding="utf-8")
            self.assertNotIn("-p", arguments)
            self.assertIn("--json-schema", arguments)
            return completed(
                json.dumps(
                    {
                        "structuredOutput": {
                            "schema": "example.output.v1",
                            "value": "bounded",
                        },
                        "sessionId": "grok-session",
                        "requestId": "grok-request",
                        "stopReason": "EndTurn",
                    }
                )
            )

        def post(
            _: str,
            __: str,
            path: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            task_frame_payloads.append((path, payload))
            if path == "/v1/task-frame/worker-result":
                return {"status": "TASK_FRAME_RESULT_RECORDED"}
            operation = payload["operation"]
            assert isinstance(operation, dict)
            if operation["operation"] == "worker_invocation_plan":
                return {
                    "status": "TASK_FRAME_OPERATION_APPLIED",
                    "output": {
                        "status": "WORKER_INVOCATION_READY",
                        "worker_invocation": {
                            "provider": "GROK",
                            "model": "grok-build",
                            "input_bundle": {
                                "boss_allocation": {
                                    "skill_bindings": [
                                        {"skill_binding_digest": "a" * 64}
                                    ]
                                }
                            },
                        },
                    },
                }
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {"status": "TURN_CLAIMED"},
            }

        request = {
            "schema": "universe.task-frame-worker-dispatch-request.v1",
            "provider": "GROK",
            "endpoint": "http://127.0.0.1:17777",
            "token": "transient",
            "session_id": "session-1",
            "frame_id": "frame-1",
            "turn_id": "turn-1",
            "invoker_actor_ref": "universe-runtime-host",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "context_pack": {"summary": "첫 줄\nsecond line"},
            "output_contract": {
                "json_schema": {
                    "type": "object",
                    "required": ["schema", "value"],
                }
            },
            "max_turns": 1,
            "result_mode": "STRUCTURED_JSON",
        }
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "grok.exe"
            executable.write_bytes(b"MZ")
            with patch(
                "universe_runtime_worker_dispatch._resolve_grok",
                return_value=(executable, {"GROK_HOME": temp}),
            ):
                result = RuntimeWorkerDispatcher(
                    ROOT,
                    native_runner=native_runner,
                    post=post,
                ).dispatch(request)

        self.assertEqual("TASK_FRAME_RESULT_RECORDED", result["status"])
        self.assertEqual(
            {"schema": "example.output.v1", "value": "bounded"},
            result["structured_result"],
        )
        self.assertIn('"summary":"첫 줄\\nsecond line"', observed_prompt)
        self.assertEqual(2, len(native_requests))
        worker_result = next(
            payload
            for path, payload in task_frame_payloads
            if path == "/v1/task-frame/worker-result"
        )
        envelope = worker_result["envelope"]
        assert isinstance(envelope, dict)
        self.assertIsInstance(envelope["result"], dict)
        self.assertNotIn("structuredOutput", json.dumps(envelope))
        self.assertEqual(1, len(envelope["skill_run_observations"]))


if __name__ == "__main__":
    unittest.main()
