from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from session_broker_host import (  # noqa: E402
    SessionBrokerClient,
    SessionBrokerService,
    _BrokerHTTPServer,
)


class _Store:
    def __init__(self) -> None:
        self.observed: list[tuple[str, str]] = []

    def observe_provider_session(self, provider: str, session_ref: str) -> None:
        self.observed.append((provider, session_ref))


class _Host:
    def __init__(self) -> None:
        self.store = _Store()
        self.prepared: list[tuple[str, str]] = []
        self.messages: list[dict[str, object]] = []
        self.closed = False

    def prepare(self, provider: str, **kwargs: str) -> dict[str, object]:
        self.prepared.append((provider, kwargs.get("session_action", "")))
        return {"provider": provider, "resident": True}

    def reply(self, provider: str, message: dict[str, object]) -> dict[str, str]:
        self.messages.append(dict(message))
        return {"text": f"{provider}:{message['body']}"}

    def status(self) -> dict[str, object]:
        return {"resident": not self.closed}

    def active_provider_session_ref(self) -> str:
        return "fresh-provider-session"

    def close(self) -> None:
        self.closed = True


def _descriptor(*, session_ref: str = "claude-session-1") -> dict[str, str]:
    return {
        "chat_key": "provider_chat_1",
        "provider": "CLAUDE",
        "provider_session_ref": session_ref,
        "project_id": "universe",
        "node": "universe",
        "mode": "MASTER",
        "repository_root": str(ROOT),
        "current_anchor_ref": "MASTER-CURRENT",
        "model_ref": "",
    }


class SessionBrokerServiceTests(unittest.TestCase):
    def test_default_hosts_use_per_chat_session_databases(self) -> None:
        with TemporaryDirectory() as directory, patch(
            "session_broker_host.ResidentModeSessionHost"
        ) as host_type:
            service = SessionBrokerService(Path(directory) / "broker.sqlite3")
            first = service._default_host(_descriptor())
            second_descriptor = _descriptor()
            second_descriptor["chat_key"] = "provider_chat_2"
            second = service._default_host(second_descriptor)

            self.assertIs(first, host_type.return_value)
            self.assertIs(second, host_type.return_value)
            first_database = host_type.call_args_list[0].args[3]
            second_database = host_type.call_args_list[1].args[3]
            self.assertNotEqual(first_database, second_database)
            self.assertEqual(first_database.parent, second_database.parent)

    def test_broker_owns_and_reuses_resumed_provider_host(self) -> None:
        with TemporaryDirectory() as directory:
            hosts: list[_Host] = []

            def factory(_descriptor: object) -> _Host:
                host = _Host()
                hosts.append(host)
                return host

            service = SessionBrokerService(
                Path(directory) / "broker.sqlite3", host_factory=factory
            )
            first = service.turn(
                {"descriptor": _descriptor(), "body": "one", "message_id": "m1"}
            )
            second = service.turn(
                {"descriptor": _descriptor(), "body": "two", "message_id": "m2"}
            )

            self.assertEqual("CLAUDE:one", first["body"])
            self.assertEqual("CLAUDE:two", second["body"])
            self.assertEqual(1, len(hosts))
            self.assertEqual([("CLAUDE", "claude-session-1")], hosts[0].store.observed)
            self.assertEqual("BROKER_IPC", hosts[0].messages[0]["runtime_context"]["conversation_transport"])
            self.assertEqual("LIVE", service.snapshot()["sessions"][0]["runtime_state"])
            service.close()
            self.assertTrue(hosts[0].closed)

    def test_changed_resume_coordinate_replaces_resident_host(self) -> None:
        with TemporaryDirectory() as directory:
            hosts: list[_Host] = []

            def factory(_descriptor: object) -> _Host:
                host = _Host()
                hosts.append(host)
                return host

            service = SessionBrokerService(
                Path(directory) / "broker.sqlite3", host_factory=factory
            )
            service.turn(
                {"descriptor": _descriptor(), "body": "one", "message_id": "m1"}
            )
            service.turn(
                {
                    "descriptor": _descriptor(session_ref="claude-session-2"),
                    "body": "two",
                    "message_id": "m2",
                }
            )
            self.assertEqual(2, len(hosts))
            self.assertTrue(hosts[0].closed)

    def test_fresh_session_is_owned_until_explicit_archive(self) -> None:
        with TemporaryDirectory() as directory:
            host = _Host()
            service = SessionBrokerService(
                Path(directory) / "broker.sqlite3", host_factory=lambda _descriptor: host
            )
            descriptor = _descriptor()
            descriptor.pop("provider_session_ref")

            created = service.create_session({"descriptor": descriptor})
            archived = service.archive_session(descriptor["chat_key"])

            self.assertEqual("SESSION_BROKER_SESSION_CREATED", created["status"])
            self.assertEqual("fresh-provider-session", created["provider_session_ref"])
            self.assertEqual([("CLAUDE", "NEW")], host.prepared)
            self.assertEqual("SESSION_BROKER_SESSION_ARCHIVED", archived["status"])
            self.assertTrue(host.closed)


class SessionBrokerHTTPTests(unittest.TestCase):
    def test_client_uses_authenticated_broker_ipc(self) -> None:
        with TemporaryDirectory() as directory:
            host = _Host()
            service = SessionBrokerService(
                Path(directory) / "broker.sqlite3", host_factory=lambda _descriptor: host
            )
            token = "test-token"
            server = _BrokerHTTPServer(("127.0.0.1", 0), service, token)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "endpoint": f"http://127.0.0.1:{server.server_address[1]}",
                        "token": token,
                    }
                ),
                encoding="utf-8",
            )
            try:
                client = SessionBrokerClient(
                    state_path, Path(directory) / "broker.sqlite3", timeout=5.0
                )
                result = client.turn(_descriptor(), "hello", "message-1")
                self.assertEqual("SESSION_BROKER_TURN_COMPLETED", result["status"])
                self.assertEqual("CLAUDE:hello", result["body"])

                fresh = _descriptor()
                fresh["chat_key"] = "meeting_chat_1"
                fresh.pop("provider_session_ref")
                created = client.create_session(fresh)
                self.assertEqual("SESSION_BROKER_SESSION_CREATED", created["status"])
                archived = client.archive_session("meeting_chat_1")
                self.assertEqual("SESSION_BROKER_SESSION_ARCHIVED", archived["status"])
            finally:
                server.shutdown()
                server.server_close()
                service.close()
                thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
