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
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_codex_task_frame_requests_ephemeral_provider_session(self) -> None:
        observed: list[tuple[bool, str]] = []

        class FakeCodexSession:
            def __init__(self, *, ephemeral, model, session_observer, **_kwargs) -> None:
                observed.append((ephemeral, model))
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

        self.assertEqual([(True, "test-model")], observed)
        self.assertEqual("EPHEMERAL", result["session_persistence"])
        self.assertEqual("UNKNOWN", result["persistent_session_ref"])
        self.assertFalse(result["universe_coordinate_persisted"])
        self.assertEqual("NOT_PERSISTED", result["provider_durable_chat_state"])

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
        observed: list[tuple[bool, int, str, object]] = []
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        }

        class FakeClaudeSession:
            def __init__(
                self, *, ephemeral, max_turns, model, json_schema, **_kwargs
            ) -> None:
                observed.append((ephemeral, max_turns, model, json_schema))
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

        self.assertEqual([(True, 5, "test-model", schema)], observed)
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


if __name__ == "__main__":
    unittest.main()
