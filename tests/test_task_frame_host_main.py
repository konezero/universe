"""A launched Host is its own process, heartbeats, and exits when its Todo does."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from task_frame_host_main import launch, validate_spec  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    posts: list = []
    todo_state = "IN_PROGRESS"

    def log_message(self, *args):
        pass

    def _reply(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/v1/rooms/room_1/events"):
            return self._reply({"events": []})
        if self.path == "/v1/rooms/room_1":
            return self._reply({"room": {"state": "OPEN"}})
        if self.path == "/v1/todos":
            return self._reply({"todos": [{"todo_id": "todo_1", "state": Stub.todo_state, "archived_at": None}]})
        self._reply({})

    def do_POST(self):
        Stub.posts.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self._reply({})


class LauncherTests(unittest.TestCase):
    def test_spec_must_be_complete(self):
        with self.assertRaises(ValueError):
            validate_spec({"base_url": "x"})
        with self.assertRaises(ValueError):
            validate_spec({"base_url": "x", "room_id": "r", "task_frame_id": "f", "todo_id": "t",
                           "bus_to": {"a": 1}, "bus_from": {"b": 1}, "runner": "nocolon"})

    def test_detached_host_runs_a_role_heartbeats_and_exits_with_the_todo(self):
        Stub.posts, Stub.todo_state = [], "IN_PROGRESS"
        server = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        os.environ["PYTHONPATH"] = str(ROOT / "tests") + os.pathsep + str(ROOT / "tools")
        try:
            with tempfile.TemporaryDirectory() as temp:
                spec = {
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}",
                    "room_id": "room_1", "task_frame_id": "frame_1", "todo_id": "todo_1",
                    "bus_to": {"mode": "MASTER"}, "bus_from": {"provider": "UNIVERSE"},
                    "runner": "task_frame_host_fixture_runner:make",
                    "poll_interval_seconds": 0.2, "idle_timeout_seconds": 60,
                }
                heartbeat = Path(temp) / "hb.json"
                pid = launch(spec, Path(temp) / "spec.json", heartbeat)
                self.assertNotEqual(os.getpid(), pid)
                deadline = time.time() + 20
                while time.time() < deadline and not any("worker-report" in p for p, _ in Stub.posts):
                    time.sleep(0.2)
                self.assertTrue(any("worker-report" in p for p, _ in Stub.posts), "role result was reported")
                self.assertTrue(any(p == "/v1/session-bus/messages" for p, _ in Stub.posts), "Master was notified")
                live = json.loads(heartbeat.read_text(encoding="utf-8"))
                self.assertEqual(pid, live["pid"])
                self.assertEqual("WAITING", live["phase"])  # it did not exit after the result
                Stub.todo_state = "DONE"
                final = {}
                while time.time() < deadline:
                    final = json.loads(heartbeat.read_text(encoding="utf-8"))
                    if final["phase"] == "EXITED":
                        break
                    time.sleep(0.2)
                self.assertEqual("EXITED", final["phase"])
                self.assertEqual("TODO_DONE", final["exit_reason"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
