from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def _test_anchor(name: str) -> str:
    """Distinct Session Anchor per terminal; anchors are not shared."""
    return f"session_anchor_{name}"
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.session_bus import (  # noqa: E402
    SessionBus,
    SessionBusError,
    fanout_meeting_bus,
    format_header,
)
from universe_app.terminal_host import TerminalHost  # noqa: E402
from universe_multi_room import MultiRoomStore  # noqa: E402
from universe_pty_supervisor import Server  # noqa: E402
import json
import urllib.error
import urllib.request


class FakePty:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.size = (120, 32)
        self.pid = 4242
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read(self, timeout: float = 0.2) -> bytes:
        del timeout
        time.sleep(0.01)
        return b""

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def close(self) -> None:
        self.closed = True


class SessionBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        self.universe = self.host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=_test_anchor("t1"),
            provider="GROK",
        )
        self.gcs = self.host.create(
            project_id="gcs",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=_test_anchor("t2"),
            provider="CODEX",
        )

    def tearDown(self) -> None:
        for item in self.host.list_sessions():
            self.host.close(item["terminal_id"])

    def pty(self, terminal_id: str) -> FakePty:
        return self.host.get(terminal_id).backend

    def test_coordinate_post_inbox_ack(self) -> None:
        posted = self.host.bus.post(
            self.host,
            {
                "to": {"project_id": "universe", "mode": "MASTER", "provider": "GROK"},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "hello grok",
            },
        )
        self.assertEqual("CREATED", posted["status"])
        inbox = self.host.bus.inbox(
            self.host,
            project_id="universe",
            mode="MASTER",
            provider="GROK",
        )
        self.assertEqual(1, len(inbox["messages"]))
        self.assertEqual("hello grok", inbox["messages"][0]["body_text"])
        self.assertEqual([], self.pty(self.universe["terminal_id"]).writes)
        acked = self.host.bus.ack(
            posted["message_id"], self.universe["terminal_id"]
        )
        self.assertEqual("OK", acked["status"])
        empty = self.host.bus.inbox(
            self.host,
            terminal_id=self.universe["terminal_id"],
        )
        self.assertEqual([], empty["messages"])

    def test_notify_none_does_not_write_stdin(self) -> None:
        self.host.bus.post(
            self.host,
            {
                "to": {"terminal_id": self.universe["terminal_id"]},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "secret brief",
                "notify": "NONE",
            },
        )
        self.assertEqual([], self.pty(self.universe["terminal_id"]).writes)

    def test_notify_header_emits_display_pointer_without_cli_stdin(self) -> None:
        display = self.host.subscribe(self.universe["terminal_id"])
        posted = self.host.bus.post(
            self.host,
            {
                "to": {"terminal_id": self.universe["terminal_id"]},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "secret brief",
                "notify": "HEADER",
            },
        )
        writes = self.pty(self.universe["terminal_id"]).writes
        self.assertEqual([], writes)
        text = (display.get(timeout=0.5) or b"").decode("utf-8")
        self.assertIn("[session-bus] 1 unread", text)
        self.assertIn(posted["message_id"], text)
        self.assertNotIn("secret brief", text)
        self.assertEqual(
            text.encode("utf-8"),
            format_header(posted["messages"][0]),
        )

    def test_hook_claim_binds_one_instruction_to_anchor_and_dispatches_once(self) -> None:
        posted = self.host.bus.post(
            self.host,
            {
                "to": {"terminal_id": self.universe["terminal_id"]},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "kind": "INSTRUCTION",
                "body_text": "Prepare the documentation.",
            },
        )

        claim = self.host.bus.claim_instruction(
            self.host,
            terminal_id=self.universe["terminal_id"],
            session_anchor_ref="session_anchor_target_1",
        )
        self.assertEqual(posted["message_id"], claim["message_id"])
        self.assertEqual("CLAIMED", claim["delivery_state"])
        self.assertEqual("session_anchor_target_1", claim["session_anchor_ref"])
        self.assertIsNone(
            self.host.bus.claim_instruction(
                self.host,
                terminal_id=self.universe["terminal_id"],
                session_anchor_ref="session_anchor_target_1",
            )
        )

        completed = self.host.bus.complete_instruction_claim(
            terminal_id=self.universe["terminal_id"],
            message_id=posted["message_id"],
            session_anchor_ref="session_anchor_target_1",
        )
        self.assertEqual("DISPATCHED", completed["delivery_state"])
        self.assertEqual(0, self.host.bus.unread_count(self.universe["terminal_id"]))

    def test_ui_instruction_carries_verified_direct_user_provenance(self) -> None:
        posted = self.host.bus.post(
            self.host,
            {
                "to": {"terminal_id": self.universe["terminal_id"]},
                "from": {"project_id": "universe", "mode": "CONDUCTOR", "provider": "UI"},
                "kind": "INSTRUCTION",
                "body_text": "Implement the requested change.",
            },
        )

        claim = self.host.bus.claim_instruction(
            self.host,
            terminal_id=self.universe["terminal_id"],
            session_anchor_ref="session_anchor_direct_user_1",
        )

        self.assertEqual("DIRECT_USER_INSTRUCTION", posted["messages"][0]["provenance"]["kind"])
        self.assertTrue(claim["provenance"]["user_authorized"])
        self.assertEqual("UNIVERSE_UI", claim["provenance"]["verified_by"])

    def test_mailbox_uses_host_write_without_supervisor_bus_routes(self) -> None:
        class WriteOnlyHost:
            def __init__(self, inner: TerminalHost) -> None:
                self.inner = inner

            def list_sessions(self):
                return self.inner.list_sessions()

            def get(self, terminal_id: str):
                return self.inner.get(terminal_id)

            def write(self, terminal_id: str, data: bytes) -> None:
                self.inner.write(terminal_id, data)

        mailbox = SessionBus()
        posted = mailbox.post(
            WriteOnlyHost(self.host),
            {
                "to": {"terminal_id": self.universe["terminal_id"]},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "no supervisor /v1/bus",
                "notify": "HEADER",
            },
        )
        self.assertEqual("CREATED", posted["status"])
        writes = self.pty(self.universe["terminal_id"]).writes
        self.assertEqual(1, len(writes))
        self.assertIn(posted["message_id"].encode("utf-8"), writes[0])
        self.assertNotIn(b"no supervisor /v1/bus", writes[0])

    def test_missing_target(self) -> None:
        with self.assertRaises(SessionBusError) as ctx:
            self.host.bus.post(
                self.host,
                {
                    "to": {
                        "project_id": "missing",
                        "mode": "MASTER",
                        "provider": "GROK",
                    },
                    "body_text": "nope",
                },
            )
        self.assertEqual("BUS_TARGET_NOT_FOUND", ctx.exception.code)

    def test_meeting_fanout_copies_inbox_without_body_stdin(self) -> None:
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        rooms = MultiRoomStore(str(Path(temp.name) / "rooms.sqlite3"))
        created = rooms.create_meeting_room(
            {
                "title": "API debate",
                "project_id": "universe",
                "models": [
                    {"provider": "GROK", "display_name": "Grok"},
                    {"provider": "CODEX", "display_name": "Codex"},
                ],
            }
        )
        room_id = created["room"]["room_id"]
        grok2 = self.host.create(
            project_id="universe",
            mode="CONDUCTOR",
            cwd=str(ROOT), session_anchor_ref=_test_anchor("t3"),
            provider="CLAUDE",
        )
        result = fanout_meeting_bus(
            rooms=rooms,
            host=self.host,
            value={
                "to": {"room_id": room_id},
                "from": {"project_id": "universe", "mode": "MASTER", "provider": "UI"},
                "kind": "INSTRUCTION",
                "body_text": "debate the bus",
                "notify": "HEADER",
            },
        )
        self.assertEqual("CREATED", result["status"])
        self.assertEqual(room_id, result["room_id"])
        self.assertTrue(result["room_message"]["message_id"])
        providers = {
            item["to"]["provider"] for item in result["messages"]
        }
        self.assertEqual({"GROK", "CODEX"}, providers)
        for terminal_id in (
            self.universe["terminal_id"],
            self.gcs["terminal_id"],
        ):
            self.assertEqual([], self.pty(terminal_id).writes)
            display = self.host.subscribe(terminal_id)
            replay = (display.get(timeout=0.5) or b"").decode("utf-8")
            self.assertIn("[session-bus] 1 unread", replay)
            self.assertIn(room_id, replay)
            self.assertNotIn("debate the bus", replay)
        self.assertEqual([], self.pty(grok2["terminal_id"]).writes)
        grok_inbox = self.host.bus.inbox(
            self.host,
            terminal_id=self.universe["terminal_id"],
            room_id=room_id,
        )
        self.assertEqual(1, len(grok_inbox["messages"]))
        self.assertEqual("debate the bus", grok_inbox["messages"][0]["body_text"])


class SessionBusHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        self.server = Server("sup-token", host=host)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host_name, port = self.server.server_address[:2]
        self.base = f"http://{host_name}:{port}"
        self.host = host
        created = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=_test_anchor("t4"),
            provider="GROK",
        )
        self.terminal_id = created["terminal_id"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        token: str = "sup-token",
    ) -> tuple[int, dict]:
        body = None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_http_post_and_inbox(self) -> None:
        status, posted = self.request(
            "POST",
            "/v1/bus/messages",
            {
                "to": {"terminal_id": self.terminal_id},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "ping",
            },
        )
        self.assertEqual(201, status)
        _status, inbox = self.request(
            "GET",
            f"/v1/bus/inbox?terminal_id={self.terminal_id}",
        )
        self.assertEqual(1, len(inbox["messages"]))
        ack_status, _acked = self.request(
            "POST",
            f"/v1/bus/messages/{posted['message_id']}/ack",
            {"terminal_id": self.terminal_id},
        )
        self.assertEqual(200, ack_status)

    def test_http_requires_bearer(self) -> None:
        status, payload = self.request(
            "GET",
            "/v1/bus/directory",
            token="wrong",
        )
        self.assertEqual(401, status)
        self.assertEqual("TOKEN_REQUIRED", payload.get("error_code"))

    def test_http_rejects_room_id(self) -> None:
        status, payload = self.request(
            "POST",
            "/v1/bus/messages",
            {
                "to": {"room_id": "room_abc"},
                "body_text": "nope",
            },
        )
        self.assertEqual(400, status)
        self.assertEqual("BUS_ROOM_REQUIRES_UNIVERSE", payload.get("error_code"))


class SessionBusDurabilityTests(unittest.TestCase):
    """Regression: inbox messages must survive across SessionBus instances."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temporary.name) / "bus_durable.sqlite3"
        self.host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        self.terminal = self.host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref="anchor_target_durable",
            provider="GROK",
        )
        self.terminal_id = self.terminal["terminal_id"]

    def tearDown(self) -> None:
        for item in self.host.list_sessions():
            self.host.close(item["terminal_id"])
        self.temporary.cleanup()

    def test_messages_survive_bus_restart(self) -> None:
        bus_a = SessionBus(database_path=self.db_path)
        posted = bus_a.post(
            self.host,
            {
                "to": {"terminal_id": self.terminal_id},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "durable payload",
            },
        )
        self.assertEqual("CREATED", posted["status"])
        message_id = posted["message_id"]

        bus_b = SessionBus(database_path=self.db_path)
        inbox = bus_b.inbox(self.host, terminal_id=self.terminal_id)
        self.assertEqual(1, len(inbox["messages"]))
        self.assertEqual(message_id, inbox["messages"][0]["message_id"])
        self.assertEqual("durable payload", inbox["messages"][0]["body_text"])

    def test_instruction_claim_state_survives_restart(self) -> None:
        bus_a = SessionBus(database_path=self.db_path)
        bus_a.post(
            self.host,
            {
                "to": {"terminal_id": self.terminal_id},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "kind": "INSTRUCTION",
                "body_text": "durable instruction",
            },
        )
        claim = bus_a.claim_instruction(
            self.host,
            terminal_id=self.terminal_id,
            session_anchor_ref="anchor_durable_test",
        )
        self.assertEqual("CLAIMED", claim["delivery_state"])

        bus_b = SessionBus(database_path=self.db_path)
        inbox = bus_b.inbox(self.host, terminal_id=self.terminal_id)
        self.assertEqual(1, len(inbox["messages"]))
        self.assertEqual("CLAIMED", inbox["messages"][0]["delivery_state"])
        self.assertEqual("anchor_durable_test", inbox["messages"][0]["session_anchor_ref"])

    def test_ack_hides_inbox_but_preserves_activity_durably(self) -> None:
        bus_a = SessionBus(database_path=self.db_path)
        posted = bus_a.post(
            self.host,
            {
                "to": {"terminal_id": self.terminal_id},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "ephemeral message",
            },
        )
        bus_a.ack(posted["message_id"], self.terminal_id)

        bus_b = SessionBus(database_path=self.db_path)
        inbox = bus_b.inbox(self.host, terminal_id=self.terminal_id)
        self.assertEqual(0, len(inbox["messages"]))
        activity = bus_b.inbox(
            self.host,
            terminal_id=self.terminal_id,
            projection="ACTIVITY",
        )
        self.assertEqual(1, len(activity["messages"]))
        self.assertEqual("DONE", activity["messages"][0]["lifecycle_state"])

    def test_projection_rules_are_filterable_and_emit_normalized_context(self) -> None:
        bus = SessionBus(database_path=self.db_path)
        anchor_ref = "anchor_projection_rules"
        terminal = self.host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT),
            session_anchor_ref=anchor_ref,
            provider="GROK",
        )
        self.terminal_id = terminal["terminal_id"]
        task_frame_ref = "task-frame://projection-rules"
        source = {
            "project_id": "gcs",
            "mode": "MASTER",
            "provider": "CODEX",
            "node_ref": "gcs",
            "task_frame_ref": task_frame_ref,
        }
        decision = bus.post(
            self.host,
            {
                "to": {
                    "terminal_id": self.terminal_id,
                    "session_anchor_ref": anchor_ref,
                    "node_ref": "universe",
                },
                "from": source,
                "kind": "NOTE",
                "projection_state": "DECISION_NEEDED",
                "body_text": "operator decision required",
            },
        )
        completed = bus.post(
            self.host,
            {
                "to": {
                    "terminal_id": self.terminal_id,
                    "session_anchor_ref": anchor_ref,
                    "node_ref": "universe",
                },
                "from": source,
                "kind": "INSTRUCTION",
                "body_text": "completed without a reply event",
            },
        )
        for state in ("ACCEPTED", "STARTED", "COMPLETED"):
            bus.transition(
                completed["message_id"],
                state=state,
                terminal_id=self.terminal_id,
                session_anchor_ref=anchor_ref,
                result_ref=(
                    "artifact://projection/completed"
                    if state == "COMPLETED"
                    else ""
                ),
            )

        restarted = SessionBus(database_path=self.db_path)
        inbox = restarted.inbox(
            self.host, session_anchor_ref=anchor_ref, projection="INBOX"
        )
        results = restarted.inbox(
            self.host, session_anchor_ref=anchor_ref, projection="RESULTS"
        )
        decision_results = restarted.inbox(
            self.host,
            session_anchor_ref=anchor_ref,
            projection="RESULTS",
            event_kind="NOTE",
            lifecycle_state="DECISION_NEEDED",
            task_frame_ref=task_frame_ref,
            node_ref="universe",
        )
        self.assertEqual([decision["message_id"]], [item["message_id"] for item in inbox["messages"]])
        self.assertEqual(2, len(results["messages"]))
        self.assertEqual([decision["message_id"]], [item["message_id"] for item in decision_results["messages"]])
        context = next(
            item["event_context"]
            for item in results["messages"]
            if item["message_id"] == completed["message_id"]
        )
        self.assertEqual(completed["message_id"], context["source_event_id"])
        self.assertEqual(anchor_ref, context["session_anchor_ref"])
        self.assertEqual(task_frame_ref, context["task_frame_ref"])
        self.assertEqual("universe", context["node_ref"])
        self.assertEqual(["artifact://projection/completed"], context["artifact_refs"])
        activity = restarted.inbox(
            self.host, session_anchor_ref=anchor_ref, projection="ACTIVITY"
        )["messages"]
        order = [
            (item["updated_at"], item["created_at"], item["message_id"])
            for item in activity
        ]
        self.assertEqual(sorted(order), order)
        restarted.ack(decision["message_id"], self.terminal_id)
        resolved_results = restarted.inbox(
            self.host, session_anchor_ref=anchor_ref, projection="RESULTS"
        )["messages"]
        self.assertEqual(
            [completed["message_id"]],
            [item["message_id"] for item in resolved_results],
        )

    def test_anchor_inbox_survives_terminal_detach_and_restart(self) -> None:
        bus_a = SessionBus(database_path=self.db_path)
        posted = bus_a.post(
            self.host,
            {
                "to": {
                    "terminal_id": self.terminal_id,
                    "session_anchor_ref": "anchor_target_durable",
                },
                "from": {"session_anchor_ref": "anchor_source_durable"},
                "body_text": "survive the live terminal handle",
            },
        )
        bus_a.drop_terminal(self.terminal_id)

        bus_b = SessionBus(database_path=self.db_path)
        inbox = bus_b.inbox(
            self.host,
            session_anchor_ref="anchor_target_durable",
        )
        self.assertEqual([posted["message_id"]], [item["message_id"] for item in inbox["messages"]])
        self.assertEqual(
            "survive the live terminal handle",
            inbox["messages"][0]["body_text"],
        )

    def test_instruction_result_is_durable_event_in_same_thread(self) -> None:
        bus = SessionBus(database_path=self.db_path)
        posted = bus.post(
            self.host,
            {
                "to": {
                    "terminal_id": self.terminal_id,
                    "session_anchor_ref": "anchor_worker",
                },
                "from": {
                    "terminal_id": self.terminal_id,
                    "session_anchor_ref": "anchor_conductor",
                },
                "kind": "INSTRUCTION",
                "body_text": "perform durable work",
                "thread_id": "thread_result_001",
            },
        )
        claimed = bus.claim_instruction(
            self.host,
            terminal_id=self.terminal_id,
            session_anchor_ref="anchor_worker",
        )
        self.assertEqual("ACCEPTED", claimed["lifecycle_state"])
        started = bus.complete_instruction_claim(
            terminal_id=self.terminal_id,
            message_id=posted["message_id"],
            session_anchor_ref="anchor_worker",
        )
        self.assertEqual("STARTED", started["lifecycle_state"])
        completed = bus.transition(
            posted["message_id"],
            state="COMPLETED",
            terminal_id=self.terminal_id,
            session_anchor_ref="anchor_worker",
            result_ref="provider-session://chat/message",
        )
        self.assertEqual("COMPLETED", completed["lifecycle_state"])
        replied = bus.reply(
            posted["message_id"],
            terminal_id=self.terminal_id,
            session_anchor_ref="anchor_worker",
            body_text="durable result body",
            result_ref="provider-session://chat/message",
        )
        self.assertEqual("thread_result_001", replied["result"]["thread_id"])
        self.assertEqual(posted["message_id"], replied["result"]["in_reply_to"])

        bus_restarted = SessionBus(database_path=self.db_path)
        activity = bus_restarted.inbox(
            self.host,
            session_anchor_ref="anchor_worker",
            projection="ACTIVITY",
        )
        results = bus_restarted.inbox(
            self.host,
            session_anchor_ref="anchor_conductor",
            projection="RESULTS",
        )
        self.assertEqual("REPLIED", activity["messages"][0]["lifecycle_state"])
        self.assertEqual(1, len(results["messages"]))
        self.assertEqual("RESULT", results["messages"][0]["kind"])
        self.assertEqual("durable result body", results["messages"][0]["body_text"])

    def test_provider_restart_rebinds_same_anchor_and_replies_after_service_restart(self) -> None:
        bus_a = SessionBus(database_path=self.db_path)
        posted = bus_a.post(
            self.host,
            {
                "to": {
                    "terminal_id": self.terminal_id,
                    "session_anchor_ref": "anchor_restart_worker",
                },
                "from": {"provider": "UI"},
                "kind": "INSTRUCTION",
                "body_text": "survive provider and service restart",
            },
        )
        bus_a.claim_instruction(
            self.host,
            terminal_id=self.terminal_id,
            session_anchor_ref="anchor_restart_worker",
        )
        bus_a.complete_instruction_claim(
            terminal_id=self.terminal_id,
            message_id=posted["message_id"],
            session_anchor_ref="anchor_restart_worker",
        )
        replacement = self.host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=_test_anchor("t6"),
            provider="CODEX",
        )

        bus_b = SessionBus(database_path=self.db_path)
        replied = bus_b.reply(
            posted["message_id"],
            terminal_id=replacement["terminal_id"],
            session_anchor_ref="anchor_restart_worker",
            body_text="restart-safe result",
            result_ref="provider-session://replacement/result",
        )
        self.assertEqual("REPLIED", replied["status"])
        results = SessionBus(database_path=self.db_path).inbox(
            self.host,
            session_anchor_ref="anchor_restart_worker",
            projection="RESULTS",
        )
        self.assertEqual(1, len(results["messages"]))
        self.assertEqual("restart-safe result", results["messages"][0]["body_text"])
        self.assertEqual(
            replacement["terminal_id"],
            results["messages"][0]["terminal_id"],
        )

    def test_thread_id_preserved_across_restart(self) -> None:
        bus_a = SessionBus(database_path=self.db_path)
        posted = bus_a.post(
            self.host,
            {
                "to": {"terminal_id": self.terminal_id},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "thread root",
                "thread_id": "thread_durable_001",
            },
        )
        self.assertEqual("thread_durable_001", posted["thread_id"])

        bus_b = SessionBus(database_path=self.db_path)
        inbox = bus_b.inbox(self.host, terminal_id=self.terminal_id)
        self.assertEqual("thread_durable_001", inbox["messages"][0]["thread_id"])

    def test_ram_only_bus_still_works_without_database(self) -> None:
        bus = SessionBus()
        posted = bus.post(
            self.host,
            {
                "to": {"terminal_id": self.terminal_id},
                "from": {"project_id": "gcs", "mode": "MASTER", "provider": "CODEX"},
                "body_text": "ram only",
            },
        )
        inbox = bus.inbox(self.host, terminal_id=self.terminal_id)
        self.assertEqual(1, len(inbox["messages"]))
        self.assertEqual("ram only", inbox["messages"][0]["body_text"])


if __name__ == "__main__":
    unittest.main()
