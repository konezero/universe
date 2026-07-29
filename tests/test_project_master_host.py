from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_master_bridge import MASTER_BRIDGE_ENVELOPE_SCHEMA  # noqa: E402
from project_master_host import (  # noqa: E402
    GrokProjectMasterRuntime,
    LiveProjectMasterBridgeHost,
    ProjectMasterConversationWorker,
    ProjectMasterSessionStore,
    ResidentProjectMasterHostManager,
)
from windows_native_cli import NativeCliResult  # noqa: E402


class FakeProvider:
    session_ref = "fake-provider:session"

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def reply(self, message: Mapping[str, Any]) -> str:
        self.messages.append(dict(message))
        return "Project Master answer"


class StreamingFakeProvider(FakeProvider):
    def reply_stream(self, message: Mapping[str, Any], on_delta) -> str:
        self.messages.append(dict(message))
        on_delta("Project ")
        on_delta("Master answer")
        return "Project Master answer"


class ProjectMasterHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".ai" / "master" / "inbox").mkdir(parents=True)
        self.state = ProjectMasterSessionStore(self.root / "state.sqlite", "GCS")
        self.provider = FakeProvider()
        self.replies: list[dict[str, Any]] = []
        self.streams: list[dict[str, Any]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_live_bridge_invokes_provider_and_posts_reply_once(self) -> None:
        worker = self._worker()
        host = LiveProjectMasterBridgeHost(
            self.root,
            "bridge-token",
            ".ai/master/inbox",
            worker,
        )
        worker.start()
        try:
            first = host.record(self._envelope())
            repeated = host.record(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual("RECORDED", first["status"])
        self.assertEqual("ALREADY_RECORDED", repeated["status"])
        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual(1, len(self.replies))
        self.assertEqual("Project Master answer", self.replies[0]["body"])
        self.assertEqual("COMPLETE", self.state.state(first["message_id"]))

    def test_pending_message_is_recovered_after_host_restart(self) -> None:
        self.assertTrue(self.state.register(self._envelope()))
        worker = self._worker()
        worker.start()
        try:
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))

    def test_grok_runtime_creates_then_resumes_the_same_session(self) -> None:
        requests = []

        def runner(request):
            requests.append(request)
            return NativeCliResult(
                contract="universe.windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(
                    {
                        "sessionId": runtime.session_id,
                        "requestId": f"request-{len(requests)}",
                        "stopReason": "EndTurn",
                        "text": f"answer-{len(requests)}",
                    }
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        runtime = GrokProjectMasterRuntime(
            self.root,
            "GCS",
            self.state,
            native_runner=runner,
        )
        first = runtime.reply(self._envelope()["message"])
        second = runtime.reply(self._envelope()["message"])

        self.assertEqual("answer-1", first)
        self.assertEqual("answer-2", second)
        self.assertIn("--session-id", requests[0].arguments)
        self.assertNotIn("--resume", requests[0].arguments)
        self.assertIn("--resume", requests[1].arguments)
        self.assertNotIn("--session-id", requests[1].arguments)
        self.assertEqual(self.root.resolve(), requests[0].cwd)
        self.assertIn("read-only", requests[0].arguments)

    def test_streaming_provider_emits_started_deltas_and_completed(self) -> None:
        self.provider = StreamingFakeProvider()
        worker = self._worker()
        worker.start()
        try:
            worker.submit(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(
            ["STARTED", "DELTA", "DELTA", "COMPLETED"],
            [item["event"] for item in self.streams],
        )
        self.assertEqual(
            ["Project ", "Master answer"],
            [item["delta"] for item in self.streams if item["event"] == "DELTA"],
        )
        self.assertEqual("Project Master answer", self.replies[0]["body"])

    def test_resident_manager_starts_one_host_per_project(self) -> None:
        registrations: list[dict[str, Any]] = []

        def register(project_id, value):
            registrations.append({"project_id": project_id, **dict(value)})
            return {"project_id": project_id, **dict(value)}, len(registrations) == 1

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=lambda _root, _project_id, _store: FakeProvider(),
            )
            try:
                first = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                second = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                self.assertTrue(manager.is_resident("GCS"))
            finally:
                manager.close()

        self.assertEqual("STARTED", first["status"])
        self.assertEqual("RESIDENT", second["status"])
        self.assertEqual(1, len(registrations))
        self.assertNotIn(registrations[0]["credential_env"], os.environ)

    def _worker(self) -> ProjectMasterConversationWorker:
        def post_reply(**values):
            self.replies.append(values)
            return {"status": "PROJECT_MASTER_REPLY_RECORDED"}

        def post_stream(**values):
            self.streams.append(values)
            return {"status": "PROJECT_MASTER_STREAM_EVENT_ACCEPTED"}

        return ProjectMasterConversationWorker(
            provider=self.provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            reply_poster=post_reply,
            stream_poster=post_stream,
        )

    @classmethod
    def _envelope(cls) -> dict[str, Any]:
        return {
            "schema": MASTER_BRIDGE_ENVELOPE_SCHEMA,
            "bridge_id": "bridge_1234567890abcdef1234",
            "project_id": "GCS",
            "master_session_ref": "grok-cli:test-session",
            "message": {
                "schema": "universe.project-room-message.v1",
                "message_id": cls._message_id(),
                "project_id": "GCS",
                "idempotency_key": "room-message-live-001",
                "kind": "QUESTION",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "What should the Master review next?",
                "content_digest": "0" * 64,
                "delivery_state": "DELIVERED_TO_MASTER",
                "created_at": "2026-07-30T00:00:00Z",
            },
        }

    @staticmethod
    def _message_id() -> str:
        return "room_1234567890abcdef1234567890abcdef"


if __name__ == "__main__":
    unittest.main()
