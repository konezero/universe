from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from agent_session_gateway import (  # noqa: E402
    CodexAppServerSession,
    GrokAcpSession,
    cli_auto_approve_status,
    normalize_permission_request,
)


class FakeJsonRpcTransport:
    instances: list["FakeJsonRpcTransport"] = []

    def __init__(
        self,
        *,
        arguments,
        request_handler,
        notification_handler,
        **_kwargs,
    ) -> None:
        self.arguments = tuple(arguments)
        self.request_handler = request_handler
        self.notification_handler = notification_handler
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, dict[str, Any] | None]] = []
        self.closed = False
        self.instances.append(self)

    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> Any:
        del timeout_seconds
        self.requests.append((method, dict(params)))
        if self.arguments[-2:] == ("agent", "stdio"):
            if method == "initialize":
                return {
                    "authMethods": [{"id": "cached_token"}],
                    "agentCapabilities": {"loadSession": True},
                }
            if method in {"authenticate", "session/load"}:
                if method == "session/load":
                    if params["sessionId"] == "grok-load-empty":
                        return {}
                    return {"sessionId": params["sessionId"]}
                return {}
            if method == "session/new":
                return {"sessionId": "grok-session-001"}
            if method == "session/prompt":
                self.notification_handler(
                    "session/update",
                    {
                        "sessionId": params["sessionId"],
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "Grok answer"},
                        },
                    },
                )
                return {"stopReason": "end_turn"}
        if method == "initialize":
            return {"serverInfo": {"name": "codex"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "thread/start":
            return {"thread": {"id": "codex-thread-001"}}
        if method == "turn/start":
            turn_id = "codex-turn-001"
            self.notification_handler(
                "item/agentMessage/delta",
                {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "itemId": "item-001",
                    "delta": "Codex answer",
                },
            )
            self.notification_handler(
                "turn/completed",
                {
                    "threadId": params["threadId"],
                    "turn": {"id": turn_id, "status": "completed"},
                },
            )
            return {"turn": {"id": turn_id}}
        raise AssertionError(f"unexpected method: {method}")

    def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.notifications.append((method, dict(params) if params else None))

    def close(self) -> None:
        self.closed = True


class AgentSessionGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        FakeJsonRpcTransport.instances.clear()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_permission_request_uses_acp_option_contract(self) -> None:
        request = normalize_permission_request(
            {
                "request_id": "permission_001",
                "provider": "grok",
                "session_id": "session-001",
                "tool_call": {"toolCallId": "tool-001"},
                "options": [
                    {
                        "optionId": "allow-once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            }
        )

        self.assertEqual("universe.agent-permission-request.v1", request["schema"])
        self.assertEqual("GROK", request["provider"])
        self.assertEqual("allow_once", request["options"][0]["kind"])

    def test_cli_auto_approve_status_uses_effective_universe_policy(self) -> None:
        self.assertEqual("OFF", cli_auto_approve_status("GROK"))
        self.assertEqual("OFF", cli_auto_approve_status("CODEX"))

    def test_grok_runs_as_acp_session_and_forwards_permission(self) -> None:
        selected: list[dict[str, Any]] = []
        sessions: list[str] = []
        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            FakeJsonRpcTransport,
        ):
            session = GrokAcpSession(
                executable=self.root / "grok.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="grok-session-existing",
                permission_requester=lambda request: (
                    selected.append(dict(request)) or "allow-once"
                ),
                session_observer=sessions.append,
            )
            deltas: list[str] = []
            answer = session.prompt("Question", deltas.append)
            permission = session._handle_request(
                "session/request_permission",
                {
                    "sessionId": sessions[0],
                    "toolCall": {"toolCallId": "tool-001", "title": "Read"},
                    "options": [
                        {
                            "optionId": "allow-once",
                            "name": "Allow once",
                            "kind": "allow_once",
                        },
                        {
                            "optionId": "reject-once",
                            "name": "Reject",
                            "kind": "reject_once",
                        },
                    ],
                },
            )
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        self.assertEqual(
            (
                "--no-auto-update",
                "--permission-mode",
                "default",
                "agent",
                "stdio",
            ),
            transport.arguments,
        )
        self.assertEqual("Grok answer", answer)
        self.assertEqual(["Grok answer"], deltas)
        self.assertEqual("selected", permission["outcome"]["outcome"])
        self.assertEqual("allow-once", permission["outcome"]["optionId"])
        self.assertEqual("GROK", selected[0]["provider"])
        self.assertTrue(transport.closed)

    def test_grok_load_keeps_requested_session_when_response_is_empty(self) -> None:
        sessions: list[str] = []
        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            FakeJsonRpcTransport,
        ):
            session = GrokAcpSession(
                executable=self.root / "grok.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="grok-load-empty",
                permission_requester=lambda _request: None,
                session_observer=sessions.append,
            )
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        self.assertEqual(["grok-load-empty"], sessions)
        self.assertNotIn(
            "session/new",
            [method for method, _params in transport.requests],
        )
        self.assertTrue(transport.closed)

    def test_grok_initialization_failure_closes_transport(self) -> None:
        class InvalidSessionTransport(FakeJsonRpcTransport):
            def request(
                self,
                method: str,
                params: Mapping[str, Any],
                *,
                timeout_seconds: float = 300,
            ) -> Any:
                if method == "session/new":
                    self.requests.append((method, dict(params)))
                    return {}
                return super().request(
                    method,
                    params,
                    timeout_seconds=timeout_seconds,
                )

        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            InvalidSessionTransport,
        ):
            with self.assertRaisesRegex(Exception, "SESSIONID_REQUIRED"):
                GrokAcpSession(
                    executable=self.root / "grok.exe",
                    cwd=self.root,
                    environment={},
                    system_prompt="System",
                    session_id=None,
                    permission_requester=lambda _request: None,
                    session_observer=lambda _session_id: None,
                )

        self.assertTrue(FakeJsonRpcTransport.instances[0].closed)

    def test_codex_app_server_is_normalized_to_acp_permission_options(self) -> None:
        selected: list[dict[str, Any]] = []
        sessions: list[str] = []
        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            FakeJsonRpcTransport,
        ):
            session = CodexAppServerSession(
                executable=self.root / "codex.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id=None,
                permission_requester=lambda request: (
                    selected.append(dict(request)) or "accept"
                ),
                session_observer=sessions.append,
            )
            deltas: list[str] = []
            answer = session.prompt("Question", deltas.append)
            decision = session._handle_request(
                "item/commandExecution/requestApproval",
                {
                    "threadId": sessions[0],
                    "turnId": "turn-001",
                    "itemId": "item-001",
                    "startedAtMs": 1,
                    "command": "git status",
                    "cwd": str(self.root),
                    "availableDecisions": ["accept", "decline"],
                },
            )
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        self.assertEqual(
            ("app-server", "--listen", "stdio://"),
            transport.arguments,
        )
        self.assertIn(("initialized", None), transport.notifications)
        self.assertEqual("Codex answer", answer)
        self.assertEqual(["Codex answer"], deltas)
        self.assertEqual({"decision": "accept"}, decision)
        self.assertEqual("CODEX", selected[0]["provider"])
        self.assertEqual("allow_once", selected[0]["options"][0]["kind"])
        self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
