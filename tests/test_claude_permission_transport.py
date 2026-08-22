from __future__ import annotations

import json
import sys
import tempfile
import threading
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

    def test_pending_broker_identity_binds_with_provider_session(self) -> None:
        bridge = ClaudePermissionBridge(
            session_ref="claude-code:pending:GCS",
            permission_requester=lambda _request: "allow-once",
        )
        broker = ClaudePermissionBroker(bridge=bridge, target="GCS/MASTER")
        self.brokers.append(broker)
        broker.bind_session_ref("claude-code:vendor-session")
        result = broker.handle_payload(
            _prompt(session_ref="claude-code:vendor-session"),
            presented_token=broker.token.value,
        )
        self.assertEqual("allow", result["behavior"])
        self.assertEqual(
            "claude-code:vendor-session",
            broker.token.identity()["session_ref"],
        )

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
        # The session token is never written; only the one-time bootstrap is.
        self.assertNotIn(broker.token.value, json.dumps(server["env"]))
        self.assertNotIn("UNIVERSE_CLAUDE_PERMISSION_TOKEN", server["env"])
        self.assertTrue(server["env"]["UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"])
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

        # MCP child environment: a usable bootstrap, never the session token.
        self.assertTrue(server["env"]["UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"])
        self.assertNotIn(token, json.dumps(server["env"]))

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

    def test_bootstrap_registration_removes_config_file_and_private_directory(
        self,
    ) -> None:
        broker = self._broker("allow-once")
        config_root = self.root / "registration-cleanup"
        path = broker.write_mcp_config(config_root / "mcp.json")
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]

        self.assertTrue(path.is_file())
        self.assertEqual("REGISTERED", broker.exchange_bootstrap(bootstrap)["status"])
        self.assertFalse(path.exists())
        self.assertFalse(config_root.exists())

    def test_registration_timeout_invalidates_bootstrap_and_cleans_config(self) -> None:
        broker = self._broker("allow-once")
        config_root = self.root / "timeout-cleanup"
        path = broker.write_mcp_config(config_root / "mcp.json")
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]

        self.assertFalse(broker.wait_for_registration(0.01))
        self.assertFalse(path.exists())
        self.assertFalse(config_root.exists())
        self.assertEqual(
            "BOOTSTRAP_ALREADY_USED",
            broker.exchange_bootstrap(bootstrap)["reason"],
        )

    def test_close_cleans_config_file_and_private_directory(self) -> None:
        broker = self._broker("allow-once")
        config_root = self.root / "close-cleanup"
        path = broker.write_mcp_config(config_root / "mcp.json")

        broker.close()

        self.assertFalse(path.exists())
        self.assertFalse(config_root.exists())

    def test_close_retries_cleanup_after_windows_file_lock(self) -> None:
        broker = self._broker("allow-once")
        config_root = self.root / "locked-cleanup"
        path = broker.write_mcp_config(config_root / "mcp.json")
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]
        real_unlink = Path.unlink
        attempts = 0

        def locked_once(target: Path, *args, **kwargs) -> None:
            nonlocal attempts
            if target == path and attempts == 0:
                attempts += 1
                raise PermissionError("simulated Windows file lock")
            real_unlink(target, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=locked_once):
            self.assertEqual("REGISTERED", broker.exchange_bootstrap(bootstrap)["status"])
            self.assertTrue(path.exists())
            broker.close()

        self.assertEqual(1, attempts)
        self.assertFalse(path.exists())
        self.assertFalse(config_root.exists())

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


class BootstrapExchangeTests(unittest.TestCase):
    """The session token must never touch disk, argv, or the environment."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brokers: list[ClaudePermissionBroker] = []

    def tearDown(self) -> None:
        for broker in self.brokers:
            broker.close()
        self.temp.cleanup()

    def _broker(self, decision="allow-once") -> ClaudePermissionBroker:
        broker = ClaudePermissionBroker(
            bridge=_bridge(decision), target="universe/MASTER"
        )
        self.brokers.append(broker)
        return broker

    def test_mcp_config_contains_no_session_token(self) -> None:
        broker = self._broker()
        path = broker.write_mcp_config(self.root / "mcp.json")
        raw = path.read_text(encoding="utf-8")

        self.assertNotIn(broker.token.value, raw)
        self.assertNotIn("UNIVERSE_CLAUDE_PERMISSION_TOKEN", raw)
        # Only the one-time bootstrap credential is on disk.
        self.assertIn("UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP", raw)

    def test_first_bootstrap_exchange_succeeds(self) -> None:
        broker = self._broker()
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]
        self.assertFalse(broker.registered)

        result = broker.exchange_bootstrap(bootstrap)

        self.assertEqual("REGISTERED", result["status"])
        self.assertEqual(broker.token.value, result["session_token"])
        self.assertTrue(broker.registered)

    def test_bootstrap_replay_is_refused(self) -> None:
        broker = self._broker()
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]
        self.assertEqual("REGISTERED", broker.exchange_bootstrap(bootstrap)["status"])

        replay = broker.exchange_bootstrap(bootstrap)
        self.assertEqual("DENIED", replay["status"])
        self.assertEqual("BOOTSTRAP_ALREADY_USED", replay["reason"])
        self.assertNotIn("session_token", replay)

        # A config read after registration yields a dead credential.
        stale = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]
        self.assertEqual("", stale)

    def test_wrong_bootstrap_is_refused(self) -> None:
        broker = self._broker()
        result = broker.exchange_bootstrap("not-the-token")

        self.assertEqual("DENIED", result["status"])
        self.assertEqual("BOOTSTRAP_INVALID", result["reason"])
        self.assertFalse(broker.registered)

    def test_bootstrap_dead_after_close(self) -> None:
        broker = self._broker()
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]
        broker.close()

        result = broker.exchange_bootstrap(bootstrap)
        self.assertEqual("DENIED", result["status"])
        self.assertEqual("BROKER_STOPPED", result["reason"])

    def test_turn_is_refused_before_mcp_registers(self) -> None:
        from claude_resident_session import (
            ClaudeResidentError,
            ClaudeResidentSession,
        )

        class Process:
            def __init__(self, **_kwargs):
                self.alive = True
                self.sent: list[str] = []

            def send_user_message(self, text: str) -> None:
                self.sent.append(text)

            def stderr_detail(self) -> str:
                return ""

            def close(self) -> None:
                self.alive = False

        built: list[Process] = []

        def factory(**kwargs):
            process = Process(**kwargs)
            built.append(process)
            return process

        broker = self._broker()
        session = ClaudeResidentSession(
            executable=Path("claude.exe"),
            cwd=self.root,
            environment={},
            system_prompt="probe",
            session_id=None,
            session_observer=lambda _s: None,
            permission_ready=lambda: broker.wait_for_registration(0.1),
            process_factory=factory,
        )

        with self.assertRaises(ClaudeResidentError) as caught:
            session.send_message("go", lambda _d: None)
        self.assertIn("MCP_NOT_REGISTERED", str(caught.exception))
        # The prompt must never have been written.
        self.assertEqual([], built[0].sent)

    def test_provider_launch_failure_cleans_permission_config_and_broker(self) -> None:
        from claude_resident_session import ClaudeResidentSession

        broker = self._broker("allow-once")
        config_root = self.root / "launch-failure"
        path = broker.write_mcp_config(config_root / "mcp.json")

        def failing_factory(**_kwargs):
            raise RuntimeError("provider launch failed")

        session = ClaudeResidentSession(
            executable=Path("claude.exe"),
            cwd=self.root,
            environment={},
            system_prompt="probe",
            session_id=None,
            session_observer=lambda _s: None,
            permission_failure=broker.close,
            process_factory=failing_factory,
        )

        with self.assertRaisesRegex(RuntimeError, "provider launch failed"):
            session.send_message("go", lambda _d: None)

        self.assertFalse(path.exists())
        self.assertFalse(config_root.exists())
        self.assertTrue(broker.token.revoked)

    def test_mcp_client_registers_then_uses_memory_token(self) -> None:
        broker = self._broker().start()
        bootstrap = broker.mcp_config()["mcpServers"]["universe_permission"]["env"][
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
        ]
        environment = {
            "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": broker.endpoint,
            "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP": bootstrap,
        }
        with patch.dict("os.environ", environment, clear=True):
            mcp._SESSION_TOKEN = None
            try:
                self.assertTrue(mcp.register())
                # The bootstrap credential is gone from the environment.
                import os

                self.assertNotIn("UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP", os.environ)
                self.assertNotIn("UNIVERSE_CLAUDE_PERMISSION_TOKEN", os.environ)

                decision = mcp.ask_universe(_prompt())
                self.assertEqual("allow", decision["behavior"])
            finally:
                mcp._SESSION_TOKEN = None

    def test_mcp_client_denies_before_registration(self) -> None:
        broker = self._broker().start()
        with patch.dict(
            "os.environ",
            {"UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": broker.endpoint},
            clear=True,
        ):
            mcp._SESSION_TOKEN = None
            decision = mcp.ask_universe(_prompt())

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("NOT_REGISTERED", decision["message"])


class PendingPermissionShutdownTests(unittest.TestCase):
    """Test 9: a permission still waiting when the process stops must cancel."""

    def test_in_flight_request_is_cancelled_when_broker_closes(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow_requester(_request: Mapping[str, Any]) -> str | None:
            entered.set()
            release.wait(timeout=5)
            # The operator answered, but the service is gone by now.
            return "allow-once"

        bridge = ClaudePermissionBridge(
            session_ref="claude-code:session-1",
            permission_requester=slow_requester,
        )
        broker = ClaudePermissionBroker(bridge=bridge, target="universe/MASTER")
        results: list[Mapping[str, Any]] = []

        def call() -> None:
            results.append(
                broker.handle_payload(_prompt(), presented_token=broker.token.value)
            )

        worker = threading.Thread(target=call)
        worker.start()
        self.assertTrue(entered.wait(timeout=5))

        # Shut down while the approval is still pending.
        broker.close()
        release.set()
        worker.join(timeout=5)

        self.assertEqual(1, len(results))
        self.assertEqual("deny", results[0]["behavior"])

    def test_session_close_cancels_pending_turn(self) -> None:
        from claude_resident_session import ClaudeResidentSession

        class HangingProcess:
            def __init__(self, **_kwargs):
                self.alive = True
                self.closed = False

            def send_user_message(self, _text: str) -> None:
                return None  # never emits a result

            def stderr_detail(self) -> str:
                return ""

            def close(self) -> None:
                self.closed = True
                self.alive = False

        session = ClaudeResidentSession(
            executable=Path("claude.exe"),
            cwd=Path("."),
            environment={},
            system_prompt="probe",
            session_id=None,
            session_observer=lambda _s: None,
            turn_timeout_seconds=10.0,
            process_factory=HangingProcess,
        )
        errors: list[Exception] = []

        def turn() -> None:
            try:
                session.send_message("go", lambda _d: None)
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        worker = threading.Thread(target=turn)
        worker.start()
        threading.Event().wait(0.2)
        session.close()
        worker.join(timeout=5)

        self.assertEqual(1, len(errors))
        self.assertIn("CANCELLED", str(errors[0]))


class IndependentSessionTests(unittest.TestCase):
    """Test 11: Conductor and Project Master must not share a session."""

    def test_conductor_and_project_master_have_separate_tokens(self) -> None:
        conductor = ClaudePermissionBroker(
            bridge=_bridge("allow-once"), target="universe/CONDUCTOR"
        )
        master = ClaudePermissionBroker(
            bridge=ClaudePermissionBridge(
                session_ref="claude-code:gcs-master",
                permission_requester=lambda _r: "allow-once",
            ),
            target="GCS/MASTER",
        )
        try:
            self.assertNotEqual(conductor.token.value, master.token.value)
            self.assertNotEqual(conductor.endpoint, master.endpoint)
            self.assertEqual(
                "universe/CONDUCTOR", conductor.token.identity()["target"]
            )
            self.assertEqual("GCS/MASTER", master.token.identity()["target"])

            # A Conductor token must not authorize a Project Master prompt.
            crossed = master.handle_payload(
                _prompt(), presented_token=conductor.token.value
            )
            self.assertEqual("deny", crossed["behavior"])
            self.assertIn("TOKEN_INVALID", crossed["message"])
        finally:
            conductor.close()
            master.close()

    def test_closing_one_session_leaves_the_other_serving(self) -> None:
        conductor = ClaudePermissionBroker(
            bridge=_bridge("allow-once"), target="universe/CONDUCTOR"
        )
        master = ClaudePermissionBroker(
            bridge=ClaudePermissionBridge(
                session_ref="claude-code:gcs-master",
                permission_requester=lambda _r: "allow-once",
            ),
            target="GCS/MASTER",
        )
        try:
            conductor.close()
            still = master.handle_payload(
                {
                    "tool_name": "Write",
                    "input": {"file_path": "x"},
                    "tool_use_id": "t1",
                    "session_ref": "claude-code:gcs-master",
                },
                presented_token=master.token.value,
            )
            self.assertEqual("allow", still["behavior"])
        finally:
            master.close()


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
            "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": "http://127.0.0.1:1",
        }

        def boom(*_args, **_kwargs):
            raise OSError("refused")

        with patch.dict("os.environ", environment, clear=True):
            mcp._SESSION_TOKEN = "in-memory-token"
            try:
                decision = mcp.ask_universe(_prompt(), opener=boom)
            finally:
                mcp._SESSION_TOKEN = None

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("UNREACHABLE", decision["message"])

    def test_end_to_end_mcp_to_broker(self) -> None:
        broker = ClaudePermissionBroker(
            bridge=_bridge("reject-once"), target="universe/MASTER"
        ).start()
        try:
            environment = {
                "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": broker.endpoint,
                "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP": broker.mcp_config()[
                    "mcpServers"
                ]["universe_permission"]["env"][
                    "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
                ],
            }
            with patch.dict("os.environ", environment, clear=True):
                mcp._SESSION_TOKEN = None
                self.assertTrue(mcp.register())
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
            mcp._SESSION_TOKEN = None
            broker.close()

        self.assertEqual("deny", decision["behavior"])
        self.assertIn("rejected", decision["message"])


if __name__ == "__main__":
    unittest.main()
