from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from host_profile import HostProfileStore  # noqa: E402
from universe_app.provider_session_service import ProviderSessionError  # noqa: E402
from universe_server import create_server  # noqa: E402


CHAT_KEY = "provider_chat_abcdef0123456789abcdef01"


class _CoordinateStore:
    def __init__(self) -> None:
        self.observed: list[tuple[str, str]] = []

    def observe_provider_session(self, provider: str, session_ref: str) -> None:
        self.observed.append((provider, session_ref))


class _Host:
    def __init__(
        self,
        requester: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        self.requester = requester
        self.store = _CoordinateStore()
        self.closed = False
        self.block_started = threading.Event()
        self.block_release = threading.Event()

    def set_permission_requester(
        self, requester: Callable[[Mapping[str, Any]], str | None]
    ) -> None:
        self.requester = requester

    def prepare(self, _provider: str) -> Mapping[str, Any]:
        return self.status()

    def status(self) -> Mapping[str, Any]:
        return {
            "connection_state": "REUSED",
            "resident": True,
            "requested_mode": "MASTER",
            "last_provider": "CODEX",
            "last_session_ref": "secret-provider-session",
            "model_ref": "luna",
        }

    def reply_stream(
        self,
        _provider: str,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]:
        self.last_message = dict(message)
        if message["body"] == "block":
            on_delta("before cancel ")
            self.block_started.set()
            self.block_release.wait(2)
            on_delta("after cancel")
            return {"text": "after cancel result", "session_ref": "secret-provider-session"}
        on_delta("direct ")
        on_delta("reply")
        return {"text": "direct reply", "session_ref": "secret-provider-session"}

    def close(self) -> None:
        self.closed = True


class ProviderSessionHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.hosts: list[_Host] = []

        def host_factory(
            _descriptor: Mapping[str, Any],
            requester: Callable[[Mapping[str, Any]], str | None],
        ) -> _Host:
            host = _Host(requester)
            self.hosts.append(host)
            return host

        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token="provider-http-token",
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            provider_session_host_factory=host_factory,
            host_profile=HostProfileStore(root / "host.json"),
            service_state_path=root / "server.json",
            remote_gateway_state_path=root / "remote-gateway.json",
            remote_connector_state_path=root / "remote-connector.json",
            remote_connector_config_path=root / "remote-connector-config.json",
        )
        self.server.provider_sessions.resolver = self._resolve
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    @staticmethod
    def _resolve(chat_key: str) -> Mapping[str, Any]:
        return {
            "chat_key": chat_key,
            "provider": "CODEX",
            "provider_session_ref": "secret-provider-session",
            "project_id": "universe",
            "node": "universe",
            "mode": "MASTER",
            "repository_root": str(ROOT),
            "current_anchor_ref": "MASTER-CURRENT-HTTP",
            "alias": "Universe Master",
            "model_ref": "luna",
            "session_kind": "CHAT",
            "identity_state": "VERIFIED",
        }

    def request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        data = None
        headers = {"Authorization": "Bearer provider-http-token"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.endpoint + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            with error:
                return int(error.code), json.loads(error.read().decode("utf-8"))

    def test_direct_http_turn_never_uses_room_queue_or_exposes_secret(self) -> None:
        status, accepted = self.request(
            "POST",
            f"/v1/provider-sessions/{CHAT_KEY}/messages",
            {"body": "hello", "idempotency_key": "http-turn-1"},
        )
        self.assertEqual(HTTPStatus.ACCEPTED, status)
        self.assertEqual("PROVIDER_SESSION_INPUT_ACCEPTED", accepted["status"])
        self.assertFalse(accepted["room_queue_used"])
        self.assertTrue(self.server.provider_sessions.wait_idle(CHAT_KEY))

        status, snapshot = self.request("GET", f"/v1/provider-sessions/{CHAT_KEY}")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("direct reply", snapshot["messages"][-1]["body"])
        self.assertFalse(snapshot["room_queue_used"])
        self.assertEqual(
            "PROVIDER_NATIVE_DIRECT",
            self.hosts[0].last_message["runtime_context"]["conversation_transport"],
        )
        public = json.dumps(snapshot)
        self.assertNotIn("secret-provider-session", public)
        self.assertNotIn("repository_root", public)

    def test_cancel_endpoint_suppresses_late_provider_result(self) -> None:
        status, accepted = self.request(
            "POST",
            f"/v1/provider-sessions/{CHAT_KEY}/messages",
            {"body": "block", "idempotency_key": "cancel-http-turn"},
        )
        self.assertEqual(HTTPStatus.ACCEPTED, status)
        self.assertTrue(self.hosts[0].block_started.wait(1))

        status, cancelled = self.request(
            "POST", f"/v1/provider-sessions/{CHAT_KEY}/cancel", {}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROVIDER_SESSION_CANCELLATION_REQUESTED", cancelled["status"])
        self.hosts[0].block_release.set()
        self.assertTrue(self.server.provider_sessions.wait_idle(CHAT_KEY))

        status, snapshot = self.request("GET", f"/v1/provider-sessions/{CHAT_KEY}")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("CANCELLED", snapshot["messages"][-1]["state"])
        self.assertEqual("before cancel ", snapshot["messages"][-1]["body"])

    def test_unavailable_target_is_a_bounded_conflict(self) -> None:
        def unavailable(_chat_key: str) -> Mapping[str, Any]:
            raise ProviderSessionError(
                "PROVIDER_SESSION_NOT_ATTACHED", "not attached", HTTPStatus.CONFLICT
            )

        self.server.provider_sessions.resolver = unavailable
        status, result = self.request("GET", f"/v1/provider-sessions/{CHAT_KEY}")
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("PROVIDER_SESSION_NOT_ATTACHED", result["error_code"])

    def test_stream_cursor_precedes_snapshot_so_connect_events_are_not_lost(self) -> None:
        original_snapshot = self.server.provider_sessions.snapshot

        def snapshot_with_event(chat_key: str) -> dict[str, Any]:
            snapshot = original_snapshot(chat_key)
            self.server.provider_sessions.events.publish(
                chat_key,
                {"type": "INJECTED_DURING_SNAPSHOT"},
            )
            return snapshot

        self.server.provider_sessions.snapshot = snapshot_with_event
        request = Request(
            self.endpoint + f"/v1/provider-sessions/{CHAT_KEY}/stream",
            method="GET",
            headers={"Authorization": "Bearer provider-http-token"},
        )
        with urlopen(request, timeout=3) as response:
            observed: list[str] = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                line = response.readline().decode("utf-8", errors="replace")
                if not line:
                    break
                observed.append(line)
                if "INJECTED_DURING_SNAPSHOT" in line:
                    break
        self.assertIn("INJECTED_DURING_SNAPSHOT", "".join(observed))


if __name__ == "__main__":
    unittest.main()
