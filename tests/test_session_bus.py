from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
            cwd=str(ROOT),
            provider="GROK",
        )
        self.gcs = self.host.create(
            project_id="gcs",
            mode="MASTER",
            cwd=str(ROOT),
            provider="CODEX",
        )

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

    def test_notify_header_writes_pointer_only(self) -> None:
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
        self.assertEqual(1, len(writes))
        text = writes[0].decode("utf-8")
        self.assertIn("[session-bus] 1 unread", text)
        self.assertIn(posted["message_id"], text)
        self.assertNotIn("secret brief", text)
        self.assertEqual(
            writes[0],
            format_header(posted["messages"][0]),
        )

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
            cwd=str(ROOT),
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
            writes = b"".join(self.pty(terminal_id).writes).decode("utf-8")
            self.assertIn("[session-bus] 1 unread", writes)
            self.assertIn(room_id, writes)
            self.assertNotIn("debate the bus", writes)
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
            cwd=str(ROOT),
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


if __name__ == "__main__":
    unittest.main()
