"""The Host ports speak the server's real routes and bodies over loopback HTTP."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from task_frame_host import DIRECTIVE_SCHEMA, parse_directive  # noqa: E402
from task_frame_host_transport import (  # noqa: E402
    HttpBusPort,
    HttpRoomPort,
    HttpTodoPort,
    TransportError,
)


class Stub(BaseHTTPRequestHandler):
    calls: list = []
    todos: list = []
    room_state = "OPEN"
    fail = False

    def log_message(self, *args):
        pass

    def _reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        Stub.calls.append(("GET", self.path, None))
        if Stub.fail:
            return self._reply(500, {})
        if self.path.startswith("/v1/rooms/room_1/events"):
            message = {"author_role": "MASTER", "body_text": json.dumps({"schema": DIRECTIVE_SCHEMA, "directive": "DONE"})}
            return self._reply(200, {"events": [{"room_sequence": 7, "message": message}]})
        if self.path == "/v1/rooms/room_1":
            return self._reply(200, {"room": {"state": Stub.room_state}})
        if self.path == "/v1/todos":
            return self._reply(200, {"todos": Stub.todos})
        self._reply(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.calls.append(("POST", self.path, body))
        self._reply(200 if not Stub.fail else 500, {})


class TransportTests(unittest.TestCase):
    def setUp(self):
        Stub.calls, Stub.todos, Stub.room_state, Stub.fail = [], [], "OPEN", False
        self.server = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_room_port_uses_the_boss_room_routes(self):
        room = HttpRoomPort(self.base, "room_1", author_binding_id="b1")
        room.post_report(body_text="done", severity="INFO", idempotency_key="k1")
        room.call_master(reason="stuck")
        self.assertEqual(
            ("POST", "/v1/rooms/room_1/worker-report",
             {"body_text": "done", "severity": "INFO", "idempotency_key": "k1", "author_binding_id": "b1"}),
            Stub.calls[0],
        )
        self.assertEqual(("POST", "/v1/rooms/room_1/call-master", {"reason": "stuck"}), Stub.calls[1])
        events = room.events_after(3)
        self.assertEqual("/v1/rooms/room_1/events?after_sequence=3&limit=200", Stub.calls[2][1])
        # What the server returns is exactly what the Host's directive parser accepts.
        self.assertEqual("DONE", parse_directive(events[0])["directive"])
        self.assertTrue(room.is_open())
        Stub.room_state = "CLOSED"
        self.assertFalse(room.is_open())

    def test_bus_port_posts_a_master_addressed_notice(self):
        bus = HttpBusPort(self.base, to={"mode": "MASTER", "terminal_id": "t1"},
                          sender={"provider": "UNIVERSE"}, thread_id="thr")
        bus.notify(idempotency_key="k", body_text="WORKER done", payload={"status": "COMPLETED"})
        method, path, body = Stub.calls[0]
        self.assertEqual("/v1/session-bus/messages", path)
        self.assertEqual({"mode": "MASTER", "terminal_id": "t1"}, body["to"])
        self.assertEqual("k", body["idempotency_key"])
        self.assertEqual("thr", body["thread_id"])
        self.assertIn('"status": "COMPLETED"', body["body_text"])

    def test_a_clean_host_exit_is_a_note_so_it_cannot_block_the_masters_queue(self):
        bus = HttpBusPort(self.base, to={"mode": "MASTER"}, sender={"provider": "UNIVERSE"}, thread_id="thr")
        bus.notify(idempotency_key="k1", body_text="exited", payload={"role": "HOST", "status": "EXITED"})
        bus.notify(idempotency_key="k2", body_text="idle", payload={"role": "HOST", "status": "EXITED_FAILED"})
        bus.notify(idempotency_key="k3", body_text="done", payload={"role": "WORKER", "status": "COMPLETED"})
        self.assertEqual(["NOTE", "INSTRUCTION", "INSTRUCTION"], [call[2]["kind"] for call in Stub.calls])

    def test_todo_port_reports_state_and_treats_an_absent_todo_as_archived(self):
        Stub.todos = [{"todo_id": "todo_1", "state": "IN_PROGRESS", "archived_at": None}]
        self.assertEqual(("IN_PROGRESS", False), HttpTodoPort(self.base, "todo_1").state())
        self.assertEqual(("", True), HttpTodoPort(self.base, "todo_gone").state())

    def test_server_errors_raise_transport_error_for_the_host_to_survive(self):
        Stub.fail = True
        with self.assertRaises(TransportError):
            HttpRoomPort(self.base, "room_1").call_master(reason="x")
        with self.assertRaises(TransportError):
            HttpTodoPort(self.base, "todo_1").state()
        dead = HttpRoomPort("http://127.0.0.1:1", "room_1")
        with self.assertRaises(TransportError):
            dead.is_open()


if __name__ == "__main__":
    unittest.main()
