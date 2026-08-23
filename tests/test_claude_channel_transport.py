from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from claude_channel_broker import (  # noqa: E402
    CHANNEL_BOOTSTRAP_ENVIRONMENT,
    ClaudeChannelBroker,
    ClaudeChannelError,
)
from claude_channel_mcp import handle_message  # noqa: E402
from universe_app.terminal_host import TerminalHost  # noqa: E402


class FakePty:
    pid = 4242

    def write(self, _data: bytes) -> None:
        return

    def read(self, timeout: float = 0.2) -> bytes:
        del timeout
        return b""

    def close(self) -> None:
        return


class ClaudeChannelBrokerTests(unittest.TestCase):
    def make_broker(self) -> ClaudeChannelBroker:
        broker = ClaudeChannelBroker(
            terminal_id="term_channel_001",
            project_id="universe",
            mode="CONDUCTOR",
            provider="CLAUDE",
            supervisor_session_id="supervisor_channel_001",
        )
        self.addCleanup(broker.close)
        return broker.start()

    def test_bootstrap_is_single_use_and_push_poll_is_authenticated(self) -> None:
        broker = self.make_broker()
        with tempfile.TemporaryDirectory() as directory:
            config_path = broker.write_mcp_config(Path(directory) / "mcp.json")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            environment = config["mcpServers"]["universe_channel"]["env"]
            bootstrap = environment[CHANNEL_BOOTSTRAP_ENVIRONMENT]
            registered = broker.exchange_bootstrap(bootstrap)
            self.assertTrue(config_path.is_file())

        self.assertEqual("REGISTERED", registered["status"])
        self.assertEqual("READY", "READY" if broker.registered else "PENDING")
        self.assertEqual(
            {"status": "DENIED", "reason": "BOOTSTRAP_ALREADY_USED"},
            broker.exchange_bootstrap(bootstrap),
        )
        queued = broker.push(
            {
                "message_id": "msg_channel_001",
                "session_anchor_ref": "CONDUCTOR-CURRENT-001",
                "content": "Run the bounded task.",
                "meta": {"sender_id": "UNIVERSE_UI", "kind": "INSTRUCTION"},
            }
        )
        self.assertEqual("QUEUED", queued["status"])
        event = broker.poll(timeout_seconds=0.1)
        self.assertEqual("EVENT", event["status"])
        self.assertEqual("Run the bounded task.", event["event"]["content"])
        self.assertEqual(
            "UNIVERSE_UI", event["event"]["meta"]["sender_id"]
        )

        duplicate = broker.push(
            {
                "message_id": "msg_channel_001",
                "session_anchor_ref": "CONDUCTOR-CURRENT-001",
                "content": "Run the bounded task.",
            }
        )
        self.assertEqual("DUPLICATE", duplicate["status"])

    def test_channel_rejects_non_loopback_and_invalid_metadata(self) -> None:
        with self.assertRaises(ClaudeChannelError):
            ClaudeChannelBroker(
                terminal_id="term_channel_002",
                project_id="universe",
                mode="CONDUCTOR",
                host="0.0.0.0",
            )

        broker = self.make_broker()
        with tempfile.TemporaryDirectory() as directory:
            config_path = broker.write_mcp_config(Path(directory) / "mcp.json")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            bootstrap = config["mcpServers"]["universe_channel"]["env"][
                CHANNEL_BOOTSTRAP_ENVIRONMENT
            ]
        broker.exchange_bootstrap(bootstrap)
        with self.assertRaisesRegex(ClaudeChannelError, "META_KEY"):
            broker.push(
                {
                    "message_id": "msg_channel_002",
                    "session_anchor_ref": "CONDUCTOR-CURRENT-002",
                    "content": "invalid metadata key",
                    "meta": {"not-valid": "x"},
                }
            )
        with self.assertRaisesRegex(ClaudeChannelError, "SENDER_INVALID"):
            broker.push(
                {
                    "message_id": "msg_channel_003",
                    "session_anchor_ref": "CONDUCTOR-CURRENT-003",
                    "content": "invalid sender",
                    "meta": {"sender_id": "REMOTE_UNVERIFIED"},
                }
            )


class ClaudeChannelMcpToolTests(unittest.TestCase):
    def test_status_tool_is_discoverable_and_read_only(self) -> None:
        listed = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool = listed["result"]["tools"][0]
        self.assertEqual("universe_channel_status", tool["name"])
        self.assertEqual({}, tool["inputSchema"]["properties"])

        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "universe_channel_status", "arguments": {}},
            }
        )
        status = response["result"]["structuredContent"]
        self.assertEqual("universe_channel", status["server"])
        self.assertEqual("CLAUDE_CODE_CHANNEL", status["transport"])
        self.assertFalse(status["writable"])


class ClaudeChannelTerminalHostTests(unittest.TestCase):
    def test_claude_terminal_keeps_pty_and_adds_channel_transport(self) -> None:
        with patch(
            "universe_app.terminal_host.resolve_cli_executable",
            return_value="claude",
        ):
            host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
            created = host.create(
                project_id="universe",
                mode="CONDUCTOR",
                cwd=str(ROOT),
                provider="CLAUDE",
                supervisor_session_id="supervisor_channel_003",
            )
        self.addCleanup(host.close, created["terminal_id"])
        self.assertEqual("LIVE", created["state"])
        self.assertEqual("CLAUDE_CODE_CHANNEL", created["automation_transport"])
        self.assertFalse(created["channel_registered"])
        self.assertEqual("PENDING", host.channel_state(created["terminal_id"]))


if __name__ == "__main__":
    unittest.main()
