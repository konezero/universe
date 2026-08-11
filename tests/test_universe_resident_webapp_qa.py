from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.resident_webapp_qa import (  # noqa: E402
    ResidentWebappQaError,
    load_resident_service_state,
    redacted_state_summary,
    run_resident_qa,
)


class ResidentWebappQaTests(unittest.TestCase):
    def write_state(self, root: Path, **overrides) -> Path:
        value = {
            "schema": "universe.local-service-state.v1",
            "endpoint": "http://127.0.0.1:45678",
            "token": "must-never-leak",
            "database": str(root / "universe.sqlite3"),
            "pid": 1234,
            "universe": {"universe_id": "universe-test"},
            **overrides,
        }
        path = root / "server.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_state_summary_is_redacted_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_state(Path(temp))
            state = load_resident_service_state(path)
            summary = redacted_state_summary(state)
            raw = json.dumps(summary)
            self.assertNotIn("must-never-leak", raw)
            self.assertNotIn(str(Path(temp)), raw)
            self.assertEqual("universe.sqlite3", summary["database_name"])
            self.assertTrue(summary["token_present"])

    def test_non_loopback_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_state(
                Path(temp), endpoint="http://192.0.2.1:45678"
            )
            with self.assertRaises(ResidentWebappQaError) as raised:
                load_resident_service_state(path)
            self.assertEqual("RESIDENT_ENDPOINT_FORBIDDEN", raised.exception.code)

    def test_missing_state_fails_without_starting_or_stopping_a_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = run_resident_qa(
                root / "missing.json",
                artifacts_dir=root / "artifacts",
                browser=False,
            )
            self.assertEqual("FAIL", report.overall)
            self.assertEqual("RESIDENT_NOT_OWNED", report.ownership)
            self.assertEqual("NOT_STOPPED", report.cleanup)
            self.assertFalse((root / "artifacts").exists())

    def test_running_loopback_service_is_observed_but_not_stopped(self) -> None:
        universe_id = "resident-qa-universe"
        responses = {
            "/health": {"status": "READY", "universe": {"universe_id": universe_id}},
            "/v1/projects": {"status": "PROJECTS_COLLECTED", "projects": []},
            "/v1/todos": {"status": "TODOS_COLLECTED", "todos": []},
            "/v1/bench/skills": {"status": "SKILL_BENCH_COLLECTED", "items": []},
            "/v1/session-observer/chat-rooms": {
                "status": "PROVIDER_CHAT_CATALOG_COLLECTED",
                "rooms": [],
            },
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                payload = responses.get(self.path)
                if payload is None:
                    self.send_error(404)
                    return
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path = self.write_state(
                    root,
                    endpoint=f"http://127.0.0.1:{server.server_port}",
                    pid=os.getpid(),
                    universe={"universe_id": universe_id},
                )
                report = run_resident_qa(
                    path,
                    artifacts_dir=root / "artifacts",
                    browser=False,
                )
                self.assertEqual("PASS", report.overall)
                self.assertEqual("RESIDENT_NOT_OWNED", report.ownership)
                self.assertEqual("NOT_STOPPED", report.cleanup)
                self.assertTrue(thread.is_alive())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
