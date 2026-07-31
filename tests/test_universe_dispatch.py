from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_dispatch import (  # noqa: E402
    DispatchError,
    HttpProjectMasterBridge,
    HttpProjectWakeAdapter,
    LocalInboxConnector,
    normalize_dispatch_request,
    normalize_result_packet,
    transition_event,
)


class UniverseDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inbox = self.root / ".ai" / "inbox" / "MASTER"
        self.inbox.mkdir(parents=True)
        self.request = {
            "idempotency_key": "user-request-001",
            "title": "Implement broker adapter",
            "instruction": "Add the bounded broker adapter and tests.",
            "constraints": ["Do not change order execution."],
            "expected_output": {"tests": "passing"},
            "requested_mode": "MASTER",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_dispatch_id_is_stable_for_the_same_idempotent_request(self) -> None:
        first = normalize_dispatch_request("GCS", self.request)
        second = normalize_dispatch_request("GCS", self.request)

        self.assertEqual(first["dispatch_id"], second["dispatch_id"])
        self.assertEqual(first["content_digest"], second["content_digest"])
        self.assertEqual("QUEUED", first["status"])

    def test_local_inbox_delivery_is_append_only_and_idempotent(self) -> None:
        envelope = normalize_dispatch_request("GCS", self.request)
        connector = LocalInboxConnector(self.root, ".ai/inbox/MASTER")

        first = connector.deliver(envelope)
        second = connector.deliver(envelope)

        self.assertEqual("DELIVERED", first["status"])
        self.assertEqual("ALREADY_DELIVERED", second["status"])
        stored = json.loads(
            (self.inbox / f"{envelope['dispatch_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(envelope["content_digest"], stored["content_digest"])

    def test_local_inbox_must_already_exist_under_ai_inbox(self) -> None:
        envelope = normalize_dispatch_request("GCS", self.request)
        with self.assertRaisesRegex(DispatchError, "MASTER_INBOX_UNAVAILABLE"):
            LocalInboxConnector(
                self.root,
                ".ai/inbox/NOT_CREATED",
            ).deliver(envelope)
        with self.assertRaisesRegex(DispatchError, "relative project path|inbox_ref"):
            LocalInboxConnector(
                self.root,
                ".ai/inbox/../core",
            ).deliver(envelope)

    def test_local_inbox_accepts_project_owned_master_inbox(self) -> None:
        alternate = self.root / ".ai" / "master" / "inbox"
        alternate.mkdir(parents=True)
        envelope = normalize_dispatch_request(
            "GCS",
            {**self.request, "inbox_ref": ".ai/master/inbox"},
        )
        connector = LocalInboxConnector(self.root, ".ai/master/inbox")

        receipt = connector.deliver(envelope)

        self.assertEqual("DELIVERED", receipt["status"])
        self.assertEqual(
            f".ai/master/inbox/{envelope['dispatch_id']}.json",
            receipt["target_ref"],
        )
        self.assertTrue((alternate / f"{envelope['dispatch_id']}.json").is_file())

    def test_lifecycle_requires_ordered_transitions_and_started_result(self) -> None:
        envelope = normalize_dispatch_request("GCS", self.request)
        delivered = transition_event(
            dispatch_id=envelope["dispatch_id"],
            project_id="GCS",
            current_status="QUEUED",
            next_status="DELIVERED",
            evidence_ref="delivery:sha256",
        )
        self.assertEqual("DELIVERED", delivered["status"])
        with self.assertRaisesRegex(DispatchError, "invalid"):
            transition_event(
                dispatch_id=envelope["dispatch_id"],
                project_id="GCS",
                current_status="QUEUED",
                next_status="STARTED",
                evidence_ref="invalid-jump",
            )
        envelope["status"] = "STARTED"
        packet = normalize_result_packet(
            dispatch=envelope,
            value={
                "status": "COMPLETED",
                "summary": "Broker adapter completed.",
                "evidence_refs": ["commit:abc", "test:pytest"],
                "outputs": {"changed": ["src/broker.py"]},
            },
        )
        self.assertEqual("COMPLETED", packet["status"])
        self.assertEqual(64, len(packet["result_digest"]))

    def test_wake_adapter_rejects_non_loopback_endpoint_before_network(self) -> None:
        envelope = normalize_dispatch_request("GCS", self.request)
        with self.assertRaisesRegex(DispatchError, "loopback"):
            HttpProjectWakeAdapter(
                "https://example.com",
                "secret",
            ).wake(envelope)

    def test_master_bridge_rejects_non_loopback_and_missing_credential(self) -> None:
        bridge = HttpProjectMasterBridge(
            endpoint="https://example.com",
            credential_env="UNIVERSE_TEST_MASTER_TOKEN",
        )
        with self.assertRaisesRegex(DispatchError, "loopback"):
            bridge.validate()

        bridge = HttpProjectMasterBridge(
            endpoint="http://127.0.0.1:9010",
            credential_env="UNIVERSE_TEST_MASTER_TOKEN",
        )
        previous = os.environ.pop("UNIVERSE_TEST_MASTER_TOKEN", None)
        try:
            with self.assertRaisesRegex(DispatchError, "CREDENTIAL_UNAVAILABLE"):
                bridge.deliver(
                    bridge={
                        "bridge_id": "bridge_123",
                        "project_id": "GCS",
                        "master_session_ref": "host-session-opaque",
                    },
                    message={"message_id": "room_123"},
                )
        finally:
            if previous is not None:
                os.environ["UNIVERSE_TEST_MASTER_TOKEN"] = previous

    def test_master_bridge_delivers_a_bound_room_envelope(self) -> None:
        captured: dict[str, object] = {}

        class BridgeHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                captured["path"] = self.path
                captured["authorization"] = self.headers.get("Authorization")
                length = int(self.headers["Content-Length"])
                captured["payload"] = json.loads(self.rfile.read(length))
                body = b'{"status":"accepted"}'
                self.send_response(HTTPStatus.ACCEPTED)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        host_text = host.decode("ascii") if isinstance(host, bytes) else host
        previous = os.environ.get("UNIVERSE_TEST_MASTER_TOKEN")
        os.environ["UNIVERSE_TEST_MASTER_TOKEN"] = "bridge-test-token"
        try:
            receipt = HttpProjectMasterBridge(
                endpoint=f"http://{host_text}:{port}",
                credential_env="UNIVERSE_TEST_MASTER_TOKEN",
            ).deliver(
                bridge={
                    "bridge_id": "bridge_123",
                    "project_id": "GCS",
                    "master_session_ref": "host-session-opaque",
                },
                message={"message_id": "room_123", "body": "Review the route."},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if previous is None:
                os.environ.pop("UNIVERSE_TEST_MASTER_TOKEN", None)
            else:
                os.environ["UNIVERSE_TEST_MASTER_TOKEN"] = previous

        self.assertEqual("DELIVERED", receipt["status"])
        self.assertEqual("/v1/project-master/messages", captured["path"])
        self.assertEqual("Bearer bridge-test-token", captured["authorization"])
        payload = cast(dict[str, Any], captured["payload"])
        self.assertEqual("bridge_123", payload["bridge_id"])
        self.assertEqual("host-session-opaque", payload["master_session_ref"])


if __name__ == "__main__":
    unittest.main()
