from __future__ import annotations

import json
from pathlib import Path
import subprocess
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
    open_native_cli,
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
    def test_windows_runner_hides_console_processes(self) -> None:
        observed: dict[str, object] = {}

        def runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            observed.update(kwargs)
            return subprocess.CompletedProcess(command, 0, b"ok", b"")

        result = run_native_cli(
            NativeCliRequest(
                executable=Path(sys.executable),
                arguments=("--version",),
            ),
            runner=runner,
        )

        self.assertEqual("COMPLETED", result.status)
        self.assertEqual(
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            observed["creationflags"],
        )

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

    def test_persistent_process_uses_same_native_argv_boundary(self) -> None:
        observed: dict[str, object] = {}
        sentinel = object()

        def opener(command: list[str], **kwargs: object):
            observed["command"] = command
            observed.update(kwargs)
            return sentinel

        process = open_native_cli(
            NativeCliRequest(
                executable=Path(sys.executable),
                arguments=("app-server", "--listen", "stdio://"),
                cwd=ROOT,
                environment={"UNIVERSE_TEST": "1"},
            ),
            opener=opener,
        )

        self.assertIs(sentinel, process)
        self.assertEqual(
            [
                str(Path(sys.executable).resolve()),
                "app-server",
                "--listen",
                "stdio://",
            ],
            observed["command"],
        )
        self.assertFalse(observed["shell"])
        self.assertEqual("1", observed["env"]["UNIVERSE_TEST"])

    def test_shell_entrypoints_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = Path(temp) / "unsafe.cmd"
            command.write_text("@echo off\n", encoding="ascii")
            with self.assertRaisesRegex(NativeCliError, "shell and batch"):
                run_native_cli(NativeCliRequest(executable=command))

    def test_grok_dispatch_uses_acp_inside_task_frame(self) -> None:
        native_requests: list[NativeCliRequest] = []
        task_frame_payloads: list[tuple[str, dict[str, object]]] = []
        observed_prompt = ""

        def native_runner(request: NativeCliRequest) -> NativeCliResult:
            native_requests.append(request)
            if request.arguments == ("--version",):
                return completed("grok 0.2.112")
            raise AssertionError("provider execution must use ACP, not native CLI")

        class FakeGrokAcpSession:
            def __init__(self, *, session_observer, **_kwargs) -> None:
                self.session_ref = "grok-acp:grok-session"
                session_observer("grok-session")

            def prompt(self, text: str, _on_delta) -> str:
                nonlocal observed_prompt
                observed_prompt = text
                return json.dumps(
                    {
                        "schema": "example.output.v1",
                        "value": "bounded",
                    }
                )

            def close(self) -> None:
                return None

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
            with (
                patch(
                    "universe_runtime_worker_dispatch._resolve_grok",
                    return_value=(executable, {"GROK_HOME": temp}),
                ),
                patch(
                    "universe_runtime_worker_dispatch.GrokAcpSession",
                    FakeGrokAcpSession,
                ),
            ):
                result = RuntimeWorkerDispatcher(
                    ROOT,
                    native_runner=native_runner,
                    post=post,
                ).dispatch(request)

        self.assertEqual("TASK_FRAME_RESULT_RECORDED", result["status"])
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual(
            {"schema": "example.output.v1", "value": "bounded"},
            result["structured_result"],
        )
        self.assertIn('"summary":"첫 줄\\nsecond line"', observed_prompt)
        self.assertEqual(1, len(native_requests))
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
