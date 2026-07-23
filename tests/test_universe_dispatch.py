from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_dispatch import (  # noqa: E402
    DispatchError,
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
        with self.assertRaisesRegex(DispatchError, "below .ai/inbox"):
            LocalInboxConnector(
                self.root,
                ".ai/inbox/../core",
            ).deliver(envelope)

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


if __name__ == "__main__":
    unittest.main()
