from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from claude_channel_broker import (  # noqa: E402
    ClaudeChannelBroker,
    ClaudeChannelError,
    session_lookup_path,
)
import claude_channel_mcp  # noqa: E402
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
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.dict(os.environ, {"UNIVERSE_DATA_DIR": directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_broker(self, terminal_id: str = "term_channel_001") -> ClaudeChannelBroker:
        broker = ClaudeChannelBroker(
            terminal_id=terminal_id,
            project_id="universe",
            mode="CONDUCTOR",
            provider="CLAUDE",
            supervisor_session_id="supervisor_channel_001",
        )
        self.addCleanup(broker.close)
        return broker.start()

    def test_bootstrap_is_single_use_and_push_poll_is_authenticated(self) -> None:
        broker = self.make_broker()
        lookup_path = session_lookup_path(broker.token.terminal_id)
        lookup = json.loads(lookup_path.read_text(encoding="utf-8"))
        bootstrap = lookup["bootstrap_token"]
        registered = broker.exchange_bootstrap(bootstrap)
        self.assertTrue(lookup_path.is_file())

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
        broker = self.make_broker(terminal_id="term_channel_002")
        lookup = json.loads(
            session_lookup_path(broker.token.terminal_id).read_text(encoding="utf-8")
        )
        broker.exchange_bootstrap(lookup["bootstrap_token"])
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

    def test_authenticated_result_callback_is_idempotent(self) -> None:
        broker = self.make_broker(terminal_id="term_channel_result_001")
        lookup = json.loads(
            session_lookup_path(broker.token.terminal_id).read_text(encoding="utf-8")
        )
        broker.exchange_bootstrap(lookup["bootstrap_token"])
        observed: list[dict[str, str]] = []
        broker.push(
            {
                "message_id": "msg_channel_result_001",
                "session_anchor_ref": "CONDUCTOR-CURRENT-RESULT-001",
                "content": "Complete and reply.",
            },
            on_result=lambda result: observed.append(dict(result)),
        )
        result = broker.submit_result(
            {
                "message_id": "msg_channel_result_001",
                "body_text": "Completed through the Channel.",
                "outcome": "COMPLETED",
                "result_ref": "artifact://channel-result-001",
            }
        )
        self.assertEqual("ACCEPTED", result["status"])
        self.assertEqual("CONDUCTOR-CURRENT-RESULT-001", observed[0]["session_anchor_ref"])
        duplicate = broker.submit_result(
            {
                "message_id": "msg_channel_result_001",
                "body_text": "Completed through the Channel.",
                "outcome": "COMPLETED",
                "result_ref": "artifact://channel-result-001",
            }
        )
        self.assertEqual("DUPLICATE", duplicate["status"])
        self.assertEqual(1, len(observed))


class ClaudeChannelMcpToolTests(unittest.TestCase):
    def test_initialize_requires_in_thread_result_reply(self) -> None:
        initialized = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        )
        self.assertIn(
            "call universe_channel_reply exactly once",
            initialized["result"]["instructions"],
        )

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

    def test_reply_tool_posts_result_to_authenticated_broker(self) -> None:
        listed = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertIn("universe_channel_reply", names)
        with patch.object(claude_channel_mcp, "_SESSION_TOKEN", "session-token"), patch.object(
            claude_channel_mcp,
            "_post",
            return_value={"status": "ACCEPTED", "message_id": "msg_1"},
        ) as post:
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "universe_channel_reply",
                        "arguments": {
                            "message_id": "msg_1",
                            "body_text": "done",
                            "outcome": "COMPLETED",
                        },
                    },
                }
            )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual("/v1/claude-channel/result", post.call_args.args[0])


class ClaudeChannelTerminalHostTests(unittest.TestCase):
    def test_claude_terminal_keeps_pty_and_adds_channel_transport(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch(
            "universe_app.terminal_host.resolve_cli_executable",
            return_value="claude",
        ), patch(
            "universe_app.terminal_host.ensure_local_channel_server_registered"
        ), patch.dict(os.environ, {"UNIVERSE_DATA_DIR": directory.name}):
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
