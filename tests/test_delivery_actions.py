from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from host_profile import HostProfileStore  # noqa: E402
from universe_app.session_bus import SessionBus  # noqa: E402
from universe_server import create_server  # noqa: E402


class DeliveryActionHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project_root = root / "project"
        project_root.mkdir()
        (project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# Delivery Action fixture\n", encoding="utf-8"
        )
        self.token = "delivery-action-test-token"
        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token=self.token,
            auto_start_project_masters=False,
            auto_start_goal_scheduler=False,
            host_profile=HostProfileStore(root / "host.json"),
            service_state_path=root / "service.json",
            remote_gateway_state_path=root / "gateway.json",
            remote_connector_state_path=root / "connector.json",
            remote_connector_config_path=root / "connector-config.json",
        )
        self.server.store.register_project(
            {"project_id": "GCS", "project_root": str(project_root)}
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = Request(
            self.endpoint + "/v1/actions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_master_complete_action_preserves_unicode_and_replays_by_digest(self) -> None:
        queued, _ = self.server.store.create_master_message(
            "GCS",
            {
                "title": "Unicode delivery",
                "instruction": "Complete the bounded Action test",
                "idempotency_key": "delivery-action-master-1",
                "metadata": {
                    "reply_anchor_ref": "anchor-conductor",
                    "reply_terminal_id": "term-conductor",
                },
            },
        )
        claimed = self.server.store.claim_master_message(
            "GCS",
            provider="CODEX",
            terminal_id="term-owner",
            session_anchor_ref="anchor-owner",
        )
        self.assertIsNotNone(claimed)
        body = '  한글😀\n"quoted" \\path\n- **markdown**  '
        action = {
            "action_id": "master.complete",
            "request": {
                "message_id": queued["message_id"],
                "provider": "CODEX",
                "body_text": body,
                "result_ref": "artifact://delivery-action/1",
                "terminal_id": "term-owner",
                "session_anchor_ref": "anchor-owner",
            }
        }
        status, first = self.request(action)
        self.assertEqual(200, status, first)
        self.assertEqual("MASTER_MESSAGE_COMPLETED", first["status"])
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.assertEqual(digest, first["body_text_utf8_sha256"])
        stored = self.server.store.get_master_message(queued["message_id"])
        self.assertEqual(body, stored["completion_request"]["body_text"])
        self.assertEqual(digest, stored["completion_request"]["body_text_utf8_sha256"])
        self.assertEqual(
            first["action_request_digest"],
            stored["completion_receipt"]["action_request_digest"],
        )

        status, replay = self.request(action)
        self.assertEqual(200, status, replay)
        self.assertEqual(first["message"]["completion_result"]["message_id"], replay["message"]["completion_result"]["message_id"])
        conflict = {
            "action_id": "master.complete",
            "request": {
                **action["request"],
                "body_text": body + "changed",
            }
        }
        status, result = self.request(conflict)
        self.assertEqual(409, status, result)
        self.assertEqual("MASTER_RESULT_CONFLICT", result["error_code"])

    def test_persona_automation_master_complete_records_server_review_pending_not_self_inbox(self) -> None:
        assignment = {
            "session_anchor_ref": "anchor-automation-master",
            "project_id": "GCS",
            "persona_id": "persona-automation-master",
            "persona_revision": 1,
            "assignment_revision": 1,
            "state": "ACTIVE",
        }
        started = self.server.persona_automation.start_run({
            "project_id": "GCS", "session_anchor_ref": assignment["session_anchor_ref"],
            "scope": "exact completion routing regression", "instruction": "run one bounded test",
            "request_id": "automation-completion-routing", "idempotency_key": "automation-completion-routing",
        }, assignment)
        run = started["run"]
        self.server.persona_automation.claim_tick({
            "run_id": run["run_id"], "owner_ref": assignment["session_anchor_ref"],
            "tick_id": "completion-routing-tick",
        })
        self.server.persona_automation.record_decision({
            "run_id": run["run_id"], "owner_ref": assignment["session_anchor_ref"],
            "decision_id": "completion-routing-decision", "kind": "EXECUTE",
            "rationale": "the test has exact scope", "evidence_refs": ["test:delivery"],
        })
        dispatched = self.server.persona_automation.dispatch_work({
            "run_id": run["run_id"], "owner_ref": assignment["session_anchor_ref"],
            "dispatch_id": "completion-routing-dispatch", "title": "completion routing",
            "instruction": "complete with a result", "completion_conditions": ["review required"],
        }, self.server.store.create_master_message)
        message_id = dispatched["message"]["message_id"]
        claimed = self.server.store.claim_master_message(
            "GCS", provider="CODEX", terminal_id="term-automation-master",
            session_anchor_ref=assignment["session_anchor_ref"],
        )
        self.assertEqual(message_id, claimed["message_id"])
        status, completed = self.request({
            "action_id": "master.complete",
            "request": {
                "message_id": message_id, "provider": "CODEX",
                "terminal_id": "term-automation-master",
                "session_anchor_ref": assignment["session_anchor_ref"],
                "body_text": "finished bounded work", "result_ref": "test://automation/result",
            },
        })
        self.assertEqual(200, status, completed)
        self.assertEqual("PERSONA_AUTOMATION_MASTER_RESULT_RECORDED", completed["automation_result"]["status"])
        self.assertEqual([], completed["result_delivery"]["message_ids"])
        self.assertNotIn("completion_result", completed["message"])
        current = self.server.persona_automation.get_run(run["run_id"])
        self.assertEqual("WAITING", current["state"])
        self.assertEqual("RESULT_READY_FOR_REVIEW", current["current_assignment"]["state"])
        self.assertIn("Independent Reviewer", current["next_condition"])

    def test_session_bus_reply_action_preserves_exact_body_and_rejects_changed_replay(self) -> None:
        terminal_id = "term-bus-action"
        anchor = "session_anchor_bus_action"
        posted = self.server.session_bus.deliver_to_terminal(
            object(),
            terminal={
                "terminal_id": terminal_id,
                "session_anchor_ref": anchor,
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
            },
            source={
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "UI",
                "session_anchor_ref": "anchor-ui",
            },
            to={"project_id": "GCS", "mode": "MASTER", "provider": "CODEX"},
            kind="INSTRUCTION",
            notify="NONE",
            body="work",
        )
        self.server.session_bus.transition(
            posted["message_id"],
            state="ACCEPTED",
            terminal_id=terminal_id,
            session_anchor_ref=anchor,
        )
        self.server.session_bus.transition(
            posted["message_id"],
            state="STARTED",
            terminal_id=terminal_id,
            session_anchor_ref=anchor,
        )
        body = '  한글😀\n"quoted" \\path\n- **markdown**  '
        request = {
            "action_id": "session-bus.reply",
            "request": {
                "message_id": posted["message_id"],
                "terminal_id": terminal_id,
                "session_anchor_ref": anchor,
                "body_text": body,
                "result_ref": "artifact://delivery-action/bus",
            }
        }
        status, first = self.request(request)
        self.assertEqual(201, status, first)
        self.assertEqual("REPLIED", first["status"])
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.assertEqual(digest, first["body_text_utf8_sha256"])
        self.assertEqual(body, first["result"]["body_text"])
        self.assertEqual(digest, first["result"]["lifecycle"]["body_text_utf8_sha256"])
        self.assertEqual(
            first["action_request_digest"],
            first["result"]["lifecycle"]["action_request_digest"],
        )

        restarted = SessionBus(database_path=self.server.store.database_path)
        persisted = restarted._messages[first["result"]["message_id"]]
        self.assertEqual(body, persisted["body_text"])
        self.assertEqual(digest, persisted["lifecycle"]["body_text_utf8_sha256"])
        status, replay = self.request(request)
        self.assertEqual(201, status, replay)
        self.assertEqual(first["result"]["message_id"], replay["result"]["message_id"])
        changed = {
            "action_id": "session-bus.reply",
            "request": {**request["request"], "body_text": body + "changed"},
        }
        status, result = self.request(changed)
        self.assertEqual(409, status, result)
        self.assertEqual("BUS_REPLY_CONFLICT", result["error_code"])


if __name__ == "__main__":
    unittest.main()
