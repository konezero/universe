from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_master_bridge import (  # noqa: E402
    MASTER_BRIDGE_ENVELOPE_SCHEMA,
    ProjectMasterBridgeError,
    ProjectMasterBridgeHost,
    ProjectMasterBridgeHttpServer,
    post_master_reply,
)


class ProjectMasterBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".ai" / "inbox" / "MASTER").mkdir(parents=True)
        self.token = "bridge-token"
        self.host = ProjectMasterBridgeHost(self.root, self.token)
        self.server = ProjectMasterBridgeHttpServer(("127.0.0.1", 0), self.host)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def test_authenticated_message_is_recorded_idempotently_in_master_inbox(self) -> None:
        envelope = self._envelope()

        status, first = self._post_to_host(envelope, self.token)
        repeat_status, repeated = self._post_to_host(envelope, self.token)

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("RECORDED", first["status"])
        self.assertEqual(HTTPStatus.OK, repeat_status)
        self.assertEqual("ALREADY_RECORDED", repeated["status"])
        target = self.root / first["target_ref"]
        self.assertEqual(envelope, json.loads(target.read_text(encoding="utf-8")))

    def test_plain_host_rejects_live_conversation_delivery(self) -> None:
        with self.assertRaises(HTTPError) as rejected:
            self._post_to_host(
                self._envelope(),
                self.token,
                path="/v1/project-master/messages",
            )

        self.assertEqual(HTTPStatus.BAD_REQUEST, rejected.exception.code)
        payload = json.loads(rejected.exception.read().decode("utf-8"))
        rejected.exception.close()
        self.assertEqual(
            "MASTER_CONVERSATION_HANDLER_UNAVAILABLE",
            payload["error_code"],
        )
        self.assertEqual(
            [],
            list((self.root / ".ai" / "inbox" / "MASTER").glob("*.json")),
        )

    def test_host_rejects_bad_auth_and_path_like_message_id(self) -> None:
        with self.assertRaises(HTTPError) as denied:
            self._post_to_host(self._envelope(), "wrong-token")
        self.assertEqual(HTTPStatus.FORBIDDEN, denied.exception.code)
        denied.exception.close()

        invalid = self._envelope()
        invalid["message"]["message_id"] = "room_../outside"
        with self.assertRaises(HTTPError) as rejected:
            self._post_to_host(invalid, self.token)
        self.assertEqual(HTTPStatus.BAD_REQUEST, rejected.exception.code)
        rejected.exception.close()
        self.assertFalse((self.root / "outside").exists())

    def test_host_rejects_message_from_a_different_project(self) -> None:
        envelope = self._envelope()
        envelope["message"]["project_id"] = "OTHER"

        with self.assertRaises(HTTPError) as rejected:
            self._post_to_host(envelope, self.token)

        self.assertEqual(HTTPStatus.BAD_REQUEST, rejected.exception.code)
        rejected.exception.close()

    def test_host_accepts_project_specific_master_inbox(self) -> None:
        alternate = self.root / ".ai" / "master" / "inbox"
        alternate.mkdir(parents=True)
        host = ProjectMasterBridgeHost(
            self.root,
            self.token,
            ".ai/master/inbox",
        )

        receipt = host.record_inbox_dispatch(self._envelope())

        self.assertEqual("RECORDED", receipt["status"])
        self.assertEqual(
            alternate / f"universe-room-{receipt['message_id']}.json",
            self.root / receipt["target_ref"],
        )

    def test_host_rejects_inbox_ref_outside_project(self) -> None:
        with self.assertRaisesRegex(
            ProjectMasterBridgeError,
            "MASTER_INBOX_REF_INVALID",
        ):
            ProjectMasterBridgeHost(
                self.root,
                self.token,
                "../outside",
            )

    def test_extended_host_creates_approved_task_frame_at_dedicated_route(self) -> None:
        request = {
            "primary_proposal": {"proposal_id": "task_proposal_001"},
            "governance_approval": {"status": "APPROVED"},
            "source_work": {"scope_kind": "PROJECT_SOURCE_WORK"},
            "task_frame": {"frame_id": "gcs-bootstrap-frame-001"},
        }
        owner = self

        class ReadyHost(ProjectMasterBridgeHost):
            def create_approved_descendant_task_frame(
                self, received: Any
            ) -> dict[str, Any]:
                owner.assertEqual(request, received)
                return {"status": "APPROVED_DESCENDANT_TASK_FRAME_READY"}

        host = ReadyHost(self.root, self.token)
        server = ProjectMasterBridgeHttpServer(("127.0.0.1", 0), host)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        try:
            payload = json.dumps(request).encode("utf-8")
            http_request = Request(
                endpoint + "/v1/project-master/task-frames/approved",
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(http_request) as response:  # nosec B310
                status = HTTPStatus(response.status)
                result = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("APPROVED_DESCENDANT_TASK_FRAME_READY", result["status"])

    def test_extended_host_runs_approved_task_frame_at_dedicated_route(self) -> None:
        request = {
            "task_frame_id": "gcs-bootstrap-frame-001",
            "primary_proposal_id": "task_proposal_001",
            "primary_proposal_digest": "a" * 64,
            "approval_evidence_ref": "universe://governance/decision/001",
        }
        owner = self

        class CompletedHost(ProjectMasterBridgeHost):
            def run_approved_descendant_task_frame(
                self, received: Any
            ) -> dict[str, Any]:
                owner.assertEqual(request, received)
                return {"status": "APPROVED_DESCENDANT_TASK_FRAME_COMPLETED"}

        host = CompletedHost(self.root, self.token)
        server = ProjectMasterBridgeHttpServer(("127.0.0.1", 0), host)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        try:
            http_request = Request(
                endpoint + "/v1/project-master/task-frames/run",
                data=json.dumps(request).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(http_request) as response:  # nosec B310
                status = HTTPStatus(response.status)
                result = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("APPROVED_DESCENDANT_TASK_FRAME_COMPLETED", result["status"])

    def test_reply_posts_bound_payload_to_loopback_universe_endpoint(self) -> None:
        captured: dict[str, Any] = {}

        class UniverseHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                captured["path"] = self.path
                captured["token"] = self.headers.get("X-Universe-Bridge-Token")
                length = int(self.headers["Content-Length"])
                captured["payload"] = json.loads(self.rfile.read(length))
                body = b'{"status":"PROJECT_MASTER_REPLY_RECORDED"}'
                self.send_response(HTTPStatus.CREATED)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        universe = ThreadingHTTPServer(("127.0.0.1", 0), UniverseHandler)
        thread = threading.Thread(target=universe.serve_forever, daemon=True)
        thread.start()
        try:
            response = post_master_reply(
                universe_endpoint=f"http://127.0.0.1:{universe.server_port}",
                project_id="GCS",
                bridge_id="bridge_1234567890abcdef1234",
                in_reply_to="room_1234567890abcdef1234567890abcdef",
                kind="STATUS",
                body="Master received the review request.",
                idempotency_key="master-reply-001",
                bridge_token=self.token,
            )
        finally:
            universe.shutdown()
            universe.server_close()
            thread.join(timeout=5)

        self.assertEqual("PROJECT_MASTER_REPLY_RECORDED", response["status"])
        self.assertEqual("/v1/projects/GCS/master-bridge/replies", captured["path"])
        self.assertEqual(self.token, captured["token"])
        payload = cast(dict[str, str], captured["payload"])
        self.assertEqual("bridge_1234567890abcdef1234", payload["bridge_id"])
        self.assertEqual("STATUS", payload["kind"])

    def test_reply_rejects_non_loopback_before_network(self) -> None:
        with self.assertRaisesRegex(ProjectMasterBridgeError, "ENDPOINT"):
            post_master_reply(
                universe_endpoint="https://example.com",
                project_id="GCS",
                bridge_id="bridge_1234567890abcdef1234",
                in_reply_to="room_1234567890abcdef1234567890abcdef",
                kind="STATUS",
                body="No network request.",
                idempotency_key="master-reply-002",
                bridge_token=self.token,
            )

    def _post_to_host(
        self,
        envelope: dict[str, Any],
        token: str,
        *,
        path: str = "/v1/project-master/inbox-dispatches",
    ) -> tuple[HTTPStatus, dict[str, Any]]:
        request = Request(
            self.endpoint + path,
            data=json.dumps(envelope).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request) as response:  # nosec B310
            return HTTPStatus(response.status), json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _envelope() -> dict[str, Any]:
        return {
            "schema": MASTER_BRIDGE_ENVELOPE_SCHEMA,
            "bridge_id": "bridge_1234567890abcdef1234",
            "project_id": "GCS",
            "master_session_ref": "opaque-project-master-session",
            "message": {
                "schema": "universe.project-room-message.v1",
                "message_id": "room_1234567890abcdef1234567890abcdef",
                "project_id": "GCS",
                "idempotency_key": "room-message-001",
                "kind": "QUESTION",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "What should the Master review next?",
                "content_digest": "0" * 64,
                "delivery_state": "RECORDED",
                "created_at": "2026-07-28T00:00:00Z",
            },
        }


if __name__ == "__main__":
    unittest.main()
