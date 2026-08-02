from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from claude_permission_bridge import ClaudePermissionBridge  # noqa: E402
from claude_permission_broker import (  # noqa: E402
    BROKER_PATH,
    TOKEN_HEADER,
    ClaudePermissionBroker,
    ClaudePermissionBrokerError,
)
import claude_permission_mcp as mcp  # noqa: E402


def _bridge(decision) -> ClaudePermissionBridge:
    def requester(_request: Mapping[str, Any]) -> str | None:
        return decision

    return ClaudePermissionBridge(
        session_ref="claude-code:session-1", permission_requester=requester
    )


def _prompt(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_name": "Write",
        "input": {"file_path": "C:/tmp/a.txt", "content": "hi"},
        "tool_use_id": "toolu_1",
    }
    payload.update(overrides)
    return payload


class BrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brokers: list[ClaudePermissionBroker] = []

    def tearDown(self) -> None:
        for broker in self.brokers:
            broker.close()
        self.temp.cleanup()

    def _broker(self, decision, **overrides) -> ClaudePermissionBroker:
        broker = ClaudePermissionBroker(
            bridge=_bridge(decision), target="universe/MASTER", **overrides
        )
        self.brokers.append(broker)
        return broker

    def test_endpoint_is_loopback_only(self) -> None:
        broker = self._broker("allow-once")
        self.assertTrue(broker.endpoint.startswith("http://127.0.0.1:"))

    def test_non_loopback_host_is_refused(self) -> None:
        with self.assertRaises(ClaudePermissionBrokerError):
            ClaudePermissionBroker(bridge=_bridge("allow-once"), host="0.0.0.0")

    def test_token_is_bound_and_not_the_service_token(self) -> None:
        broker = self._broker("allow-once")
        identity = broker.token.identity()

        self.assertEqual("CLAUDE", identity["provider"])
        self.assertEqual("claude-code:session-1", identity["session_ref"])
        self.assertEqual("universe/MASTER", identity["target"])
        self.assertIsInstance(identity["process_id"], int)
        # The identity view must never leak the secret.
        self.assertNotIn(broker.token.value, json.dumps(identity))
        self.assertGreaterEqual(len(broker.token.value), 32)

    def test_valid_token_is_accepted(self) -> None:
        broker = self._broker("allow-once")
        result = broker.handle_payload(
            _prompt(), presented_token=broker.token.value
        )
        self.assertEqual("allow", result["behavior"])

    def test_wrong_token_fails_closed(self) -> None:
        broker = self._broker("allow-once")
        result = broker.handle_payload(_prompt(), presented_token="nope")

        self.assertEqual("deny", result["behavior"])
        self.assertIn("TOKEN_INVALID", result["message"])

    def test_revoked_token_fails_closed(self) -> None:
        broker = self._broker("allow-once")
        token = broker.token.value
        broker.token.revoke()
        result = broker.handle_payload(_prompt(), presented_token=token)

        self.assertEqual("deny", result["behavior"])
        self.assertIn("TOKEN_INVALID", result["message"])

    def test_duplicate_request_fails_closed(self) -> None:
        broker = self._broker("allow-once")
        first = broker.handle_payload(_prompt(), presented_token=broker.token.value)
        second = broker.handle_payload(_prompt(), presented_token=broker.token.value)

        self.assertEqual("allow", first["behavior"])
        self.assertEqual("deny", second["behavior"])
        self.assertIn("DUPLICATE_REQUEST", second["message"])

    def test_session_mismatch_fails_closed(self) -> None:
        broker = self._broker("allow-once")
        result = broker.handle_payload(
            _prompt(session_ref="claude-code:other"),
            presented_token=broker.token.value,
        )

        self.assertEqual("deny", result["behavior"])
        self.assertIn("SESSION_MISMATCH", result["message"])

    def test_closed_broker_fails_closed_and_revokes(self) -> None:
        broker = self._broker("allow-once")
        token = broker.token.value
        broker.close()

        self.assertTrue(broker.token.revoked)
        result = broker.handle_payload(_prompt(), presented_token=token)
        self.assertEqual("deny", result["behavior"])

    def test_mcp_config_keeps_token_out_of_argv(self) -> None:
        broker = self._broker("allow-once")
        config = broker.mcp_config()
        server = config["mcpServers"]["universe_permission"]

        self.assertNotIn(broker.token.value, json.dumps(server["args"]))
        self.assertNotIn(broker.token.value, server["command"])
        self.assertEqual(
            broker.token.value, server["env"]["UNIVERSE_CLAUDE_PERMISSION_TOKEN"]
        )
        self.assertTrue(
            server["env"]["UNIVERSE_CLAUDE_PERMISSION_ENDPOINT"].startswith(
                "http://127.0.0.1:"
            )
        )
        self.assertTrue(str(server["args"][0]).endswith("claude_permission_mcp.py"))

    def test_token_reaches_mcp_child_only_not_claude(self) -> None:
        """Claude must not be able to read the capability that gates it."""
        broker = self._broker("allow-once")
        token = broker.token.value
        server = broker.mcp_config()["mcpServers"]["universe_permission"]

        # MCP child process environment: token present.
        self.assertEqual(token, server["env"]["UNIVERSE_CLAUDE_PERMISSION_TOKEN"])

        # Claude process environment: token absent, even if a caller tries.
        provider_env = broker.provider_environment(
            {
                "PATH": "C:/bin",
                "UNIVERSE_CLAUDE_PERMISSION_TOKEN": token,
                "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": broker.endpoint,
            }
        )
        self.assertEqual({"PATH": "C:/bin"}, provider_env)
        self.assertNotIn(token, json.dumps(provider_env))

    def test_token_absent_from_universe_process_environment(self) -> None:
        import os

        broker = self._broker("allow-once")
        self.assertNotIn("UNIVERSE_CLAUDE_PERMISSION_TOKEN", os.environ)
        self.assertNotIn(broker.token.value, json.dumps(dict(os.environ)))

    def test_token_absent_from_argv_stdout_stderr_and_identity(self) -> None:
        broker = self._broker("allow-once")
        token = broker.token.value
        server = broker.mcp_config()["mcpServers"]["universe_permission"]

        self.assertNotIn(token, json.dumps(server["args"]))
        self.assertNotIn(token, server["command"])
        self.assertNotIn(token, json.dumps(broker.token.identity()))
        # A denial message must never echo the capability either.
        result = broker.handle_payload(_prompt(), presented_token="wrong")
        self.assertNotIn(token, json.dumps(result))

    def test_token_unusable_after_close(self) -> None:
        broker = self._broker("allow-once")
        token = broker.token.value
        self.assertEqual(
            "allow",
            broker.handle_payload(_prompt(), presented_token=token)["behavior"],
        )

        broker.close()
        after = broker.handle_payload(
            _prompt(tool_use_id="toolu_after"), presented_token=token
        )
        self.assertEqual("deny", after["behavior"])
        self.assertTrue(broker.token.revoked)
        self.assertFalse(broker.token.matches(token))

    def test_write_mcp_config_round_trips(self) -> None:
        broker = self._broker("allow-once")
        path = broker.write_mcp_config(self.root / "nested" / "mcp.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))

        self.assertIn("universe_permission", loaded["mcpServers"])

    def test_http_round_trip_allows_and_denies(self) -> None:
        broker = self._broker("allow-once").start()
        url = f"{broker.endpoint}{BROKER_PATH}"

        def post(payload: dict[str, Any], token: str) -> dict[str, Any]:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json", TOKEN_HEADER: token},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        allowed = post(_prompt(), broker.token.value)
        self.assertEqual("allow", allowed["behavior"])

        rejected = post(_prompt(tool_use_id="toolu_2"), "bad-token")
        self.assertEqual("deny", rejected["behavior"])


class McpServerTests(unittest.TestCase):
    def test_initialize_reports_tool_capability(self) -> None:
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

        self.assertEqual(1, response["id"])
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual("universe_permission", response["result"]["serverInfo"]["name"])

    def test_tools_list_exposes_only_approve(self) -> None:
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = response["result"]["tools"]

        self.assertEqual(1, len(tools))
        self.assertEqual("approve", tools[0]["name"])

    def test_notification_returns_nothing(self) -> None:
        self.assertIsNone(
            mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_tools_call_forwards_and_returns_decision(self) -> None:
        seen: list[Mapping[str, Any]] = []

        def asker(arguments):
            seen.append(arguments)
            return {"behavior": "allow", "updatedInput": dict(arguments["input"])}

        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "approve", "arguments": _prompt()},
            },
            asker=asker,
        )
        decision = json.loads(response["result"]["content"][0]["text"])

        self.assertEqual("allow", decision["behavior"])
        self.assertEqual("Write", seen[0]["tool_name"])

    def test_unknown_tool_denies(self) -> None:
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "something_else", "arguments": {}},
            }
        )
        decision = json.loads(response["result"]["content"][0]["text"])

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("TOOL_UNKNOWN", decision["message"])

    def test_unconfigured_transport_denies(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            decision = mcp.ask_universe(_prompt())

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("UNCONFIGURED", decision["message"])

    def test_non_loopback_endpoint_denies(self) -> None:
        environment = {
            "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": "http://example.com/approve",
            "UNIVERSE_CLAUDE_PERMISSION_TOKEN": "t",
        }
        with patch.dict("os.environ", environment, clear=True):
            decision = mcp.ask_universe(_prompt())

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("NOT_LOOPBACK", decision["message"])

    def test_broker_unreachable_denies(self) -> None:
        environment = {
            "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": "http://127.0.0.1:1/approve",
            "UNIVERSE_CLAUDE_PERMISSION_TOKEN": "t",
        }

        def boom(*_args, **_kwargs):
            raise OSError("refused")

        with patch.dict("os.environ", environment, clear=True):
            decision = mcp.ask_universe(_prompt(), opener=boom)

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("UNREACHABLE", decision["message"])

    def test_end_to_end_mcp_to_broker(self) -> None:
        broker = ClaudePermissionBroker(
            bridge=_bridge("reject-once"), target="universe/MASTER"
        ).start()
        try:
            environment = {
                "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": f"{broker.endpoint}{BROKER_PATH}",
                "UNIVERSE_CLAUDE_PERMISSION_TOKEN": broker.token.value,
            }
            with patch.dict("os.environ", environment, clear=True):
                response = mcp.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": "approve", "arguments": _prompt()},
                    }
                )
            decision = json.loads(response["result"]["content"][0]["text"])
        finally:
            broker.close()

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("rejected", decision["message"])


if __name__ == "__main__":
    unittest.main()
