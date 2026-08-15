from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from agent_session_gateway import (  # noqa: E402
    AgentSessionError,
    ClaudeCodeSession,
    CodexAppServerSession,
    GrokAcpSession,
    GitTrace2Observer,
    build_platform_approval_evidence,
    cli_auto_approve_status,
    normalize_permission_request,
)
from windows_native_cli import NativeCliResult  # noqa: E402


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
        if "agent" in self.arguments and self.arguments[-1] == "stdio":
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

    def test_git_trace2_observer_emits_terminal_commit_and_push_once(self) -> None:
        observer = GitTrace2Observer(self.root)
        environment = observer.environment({"EXISTING": "value"})
        self.assertEqual("value", environment["EXISTING"])
        self.assertEqual(str(observer.path), environment["GIT_TRACE2_EVENT"])
        observer.path.write_text(
            "\n".join(
                json.dumps(record)
                for record in (
                    {"event": "cmd_name", "sid": "commit-1", "name": "commit"},
                    {"event": "exit", "sid": "commit-1", "code": 0},
                    {"event": "atexit", "sid": "commit-1", "code": 0},
                    {"event": "cmd_name", "sid": "push-1", "name": "push"},
                    {"event": "exit", "sid": "push-1", "code": 1},
                )
            )
            + "\n",
            encoding="utf-8",
        )

        milestones = observer.drain_work_statuses()

        self.assertEqual(["COMMIT", "PUSH"], [item["operation"] for item in milestones])
        self.assertEqual(["COMPLETED", "FAILED"], [item["state"] for item in milestones])
        self.assertEqual([], observer.drain_work_statuses())
        self.assertNotIn("argv", json.dumps(milestones))
        observer.close()
        self.assertFalse(observer.path.exists())

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

    def test_platform_approval_evidence_is_provider_neutral(self) -> None:
        request = normalize_permission_request(
            {
                "request_id": "permission_codex_001",
                "provider": "CODEX",
                "session_id": "thread-001",
                "tool_call": {"command": "python secret-operation.py"},
                "options": [
                    {
                        "optionId": "accept",
                        "name": "Allow once",
                        "kind": "allow_once",
                    }
                ],
            }
        )

        evidence = build_platform_approval_evidence(request, "accept")

        self.assertEqual(
            "ai-career.platform-approval-evidence.v1", evidence["schema"]
        )
        self.assertEqual(
            "platform-approval://CODEX/permission_codex_001",
            evidence["evidence_ref"],
        )
        self.assertEqual("ALLOW_ONCE", evidence["decision"])
        self.assertNotIn("secret-operation.py", evidence)

    def test_cli_auto_approve_status_uses_effective_universe_policy(self) -> None:
        self.assertEqual("OFF", cli_auto_approve_status("GROK"))
        self.assertEqual("OFF", cli_auto_approve_status("CODEX"))
        self.assertEqual("OFF", cli_auto_approve_status("CLAUDE"))

    def test_claude_print_session_resumes_and_observes_session_id(self) -> None:
        requests = []

        def runner(request):
            requests.append(request)
            self.assertIn("System\n\nQuestion", request.stdin_path.read_text("utf-8"))
            return NativeCliResult(
                contract="test",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(
                    {
                        "result": "Claude answer",
                        "session_id": "claude-session-new",
                        "is_error": False,
                    }
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        sessions: list[str] = []
        session = ClaudeCodeSession(
            executable=self.root / "claude.exe",
            cwd=self.root,
            environment={},
            system_prompt="System",
            session_id="claude-session-old",
            model="opus",
            permission_requester=lambda _request: None,
            session_observer=sessions.append,
            native_runner=runner,
        )
        deltas: list[str] = []
        answer = session.prompt("Question", deltas.append)

        self.assertEqual("Claude answer", answer)
        self.assertEqual(["Claude answer"], deltas)
        self.assertEqual(["claude-session-new"], sessions)
        self.assertIn("--resume", requests[0].arguments)
        self.assertIn("claude-session-old", requests[0].arguments)
        self.assertEqual(
            "opus",
            requests[0].arguments[requests[0].arguments.index("--model") + 1],
        )
        self.assertEqual(
            "Read,Glob,Grep",
            requests[0].arguments[requests[0].arguments.index("--tools") + 1],
        )

    def test_claude_ephemeral_session_disables_tools_and_persistence(self) -> None:
        requests = []

        def runner(request):
            requests.append(request)
            return NativeCliResult(
                contract="test",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(
                    {
                        "result": "Bounded result",
                        "session_id": "ephemeral-session",
                        "is_error": False,
                    }
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        sessions: list[str] = []
        session = ClaudeCodeSession(
            executable=self.root / "claude.exe",
            cwd=self.root,
            environment={},
            system_prompt="System",
            session_id="must-not-resume",
            permission_requester=lambda _request: None,
            session_observer=sessions.append,
            ephemeral=True,
            native_runner=runner,
        )
        session.prompt("Question", lambda _delta: None)

        arguments = requests[0].arguments
        self.assertNotIn("--resume", arguments)
        self.assertNotIn("--bare", arguments)
        self.assertIn("--no-session-persistence", arguments)
        self.assertEqual("default", arguments[arguments.index("--model") + 1])
        self.assertEqual("", arguments[arguments.index("--tools") + 1])
        self.assertEqual([], sessions)

    def test_claude_structured_session_binds_schema_and_uses_structured_output(
        self,
    ) -> None:
        requests = []
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        }

        def runner(request):
            requests.append(request)
            return NativeCliResult(
                contract="test",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(
                    {
                        "result": "This prose must not be consumed.",
                        "structured_output": {"value": "bounded"},
                        "session_id": "ephemeral-structured-session",
                        "is_error": False,
                    }
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        session = ClaudeCodeSession(
            executable=self.root / "claude.exe",
            cwd=self.root,
            environment={},
            system_prompt="System",
            session_id=None,
            permission_requester=lambda _request: None,
            session_observer=lambda _session_id: None,
            ephemeral=True,
            json_schema=schema,
            native_runner=runner,
        )
        deltas: list[str] = []
        answer = session.prompt("Question", deltas.append)

        self.assertEqual('{"value":"bounded"}', answer)
        self.assertEqual(['{"value":"bounded"}'], deltas)
        arguments = requests[0].arguments
        self.assertEqual(
            schema,
            json.loads(arguments[arguments.index("--json-schema") + 1]),
        )

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
                "--model",
                "default",
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

    def test_grok_bootstraps_once_and_passes_effort(self) -> None:
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
                model="grok-4",
                effort="MAX",
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            session.prompt("First", lambda _delta: None)
            session.prompt("Second", lambda _delta: None)
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        self.assertIn("--reasoning-effort", transport.arguments)
        self.assertEqual(
            "max",
            transport.arguments[transport.arguments.index("--reasoning-effort") + 1],
        )
        prompts = [
            params["prompt"][0]["text"]
            for method, params in transport.requests
            if method == "session/prompt"
        ]
        self.assertEqual(["System\n\nFirst", "Second"], prompts)

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

    def test_grok_dead_session_is_replaced(self) -> None:
        class DeadSessionTransport(FakeJsonRpcTransport):
            def request(
                self,
                method: str,
                params: Mapping[str, Any],
                *,
                timeout_seconds: float = 300,
            ) -> Any:
                if method == "session/load":
                    self.requests.append((method, dict(params)))
                    raise AgentSessionError("SESSION_NOT_FOUND")
                return super().request(
                    method,
                    params,
                    timeout_seconds=timeout_seconds,
                )

        sessions: list[str] = []
        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            DeadSessionTransport,
        ):
            session = GrokAcpSession(
                executable=self.root / "grok.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="dead-grok-session",
                permission_requester=lambda _request: None,
                session_observer=sessions.append,
            )
            session.close()

        methods = [
            method for method, _params in FakeJsonRpcTransport.instances[0].requests
        ]
        self.assertEqual(["grok-session-001"], sessions)
        self.assertIn("session/load", methods)
        self.assertIn("session/new", methods)

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
        self.assertNotIn('model="default"', transport.arguments)
        self.assertEqual("CODEX", selected[0]["provider"])
        self.assertEqual("allow_once", selected[0]["options"][0]["kind"])
        self.assertTrue(
            session.last_platform_approval_evidence["evidence_ref"].startswith(
                "platform-approval://CODEX/permission_"
            )
        )
        start = next(
            params
            for method, params in transport.requests
            if method == "thread/start"
        )
        self.assertFalse(start["ephemeral"])
        self.assertTrue(transport.closed)

    def test_codex_new_thread_uses_developer_bootstrap_and_passes_effort(self) -> None:
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
                model="gpt-test",
                effort="MAX",
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            session.prompt("First", lambda _delta: None)
            session.prompt("Second", lambda _delta: None)
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        self.assertIn('model_reasoning_effort="max"', transport.arguments)
        started = next(
            params for method, params in transport.requests if method == "thread/start"
        )
        self.assertEqual("System", started["developerInstructions"])
        prompts = [
            params["input"][0]["text"]
            for method, params in transport.requests
            if method == "turn/start"
        ]
        self.assertEqual(["First", "Second"], prompts)

    def test_codex_resumed_thread_bootstraps_first_turn_only(self) -> None:
        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            FakeJsonRpcTransport,
        ):
            session = CodexAppServerSession(
                executable=self.root / "codex.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="codex-thread-existing",
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            session.prompt("First", lambda _delta: None)
            session.prompt("Second", lambda _delta: None)
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        prompts = [
            params["input"][0]["text"]
            for method, params in transport.requests
            if method == "turn/start"
        ]
        self.assertEqual(["System\n\nFirst", "Second"], prompts)

    def test_codex_additional_permissions_preserve_profile_and_scope(self) -> None:
        selected: list[dict[str, Any]] = []
        requested = {
            "network": {"enabled": True},
            "fileSystem": {
                "entries": [
                    {
                        "access": "write",
                        "path": {"type": "path", "path": str(self.root)},
                    }
                ]
            },
        }
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
                    selected.append(dict(request)) or "grantForSession"
                ),
                session_observer=lambda _session_id: None,
            )
            params = {
                "threadId": "codex-thread-001",
                "turnId": "turn-001",
                "itemId": "item-001",
                "startedAtMs": 1,
                "cwd": str(self.root),
                "environmentId": "local",
                "reason": "Need bounded access",
                "permissions": requested,
            }
            granted = session._handle_request(
                "item/permissions/requestApproval",
                params,
            )
            session.permission_requester = lambda _request: None
            denied = session._handle_request(
                "item/permissions/requestApproval",
                params,
            )
            session.close()

        self.assertEqual(
            {"permissions": requested, "scope": "session"},
            granted,
        )
        self.assertEqual({"permissions": {}, "scope": "turn"}, denied)
        self.assertEqual(
            requested,
            selected[0]["tool_call"]["requestedPermissions"],
        )
        self.assertEqual(
            ["grantForTurn", "grantForSession", "decline"],
            [option["optionId"] for option in selected[0]["options"]],
        )

    def test_codex_uses_completed_agent_message_when_delta_is_absent(self) -> None:
        class CompletedItemOnlyTransport(FakeJsonRpcTransport):
            def request(
                self,
                method: str,
                params: Mapping[str, Any],
                *,
                timeout_seconds: float = 300,
            ) -> Any:
                if method != "turn/start":
                    return super().request(
                        method,
                        params,
                        timeout_seconds=timeout_seconds,
                    )
                self.requests.append((method, dict(params)))
                turn_id = "codex-turn-completed-item"
                self.notification_handler(
                    "item/completed",
                    {
                        "threadId": params["threadId"],
                        "turnId": turn_id,
                        "completedAtMs": 1,
                        "item": {
                            "id": "item-final",
                            "type": "agentMessage",
                            "text": "Final Codex answer",
                        },
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

        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            CompletedItemOnlyTransport,
        ):
            session = CodexAppServerSession(
                executable=self.root / "codex.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id=None,
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            deltas: list[str] = []
            answer = session.prompt("Question", deltas.append)
            session.close()

        self.assertEqual("Final Codex answer", answer)
        self.assertEqual(["Final Codex answer"], deltas)

    def test_codex_ephemeral_session_never_resumes_or_persists_thread(self) -> None:
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
                session_id="stored-thread",
                permission_requester=lambda _request: None,
                session_observer=sessions.append,
                ephemeral=True,
            )
            session.close()

        transport = FakeJsonRpcTransport.instances[0]
        methods = [method for method, _params in transport.requests]
        self.assertNotIn("thread/resume", methods)
        start = next(
            params
            for method, params in transport.requests
            if method == "thread/start"
        )
        self.assertTrue(start["ephemeral"])
        self.assertEqual(["codex-thread-001"], sessions)

    def test_codex_dead_session_is_replaced(self) -> None:
        class DeadThreadTransport(FakeJsonRpcTransport):
            def request(
                self,
                method: str,
                params: Mapping[str, Any],
                *,
                timeout_seconds: float = 300,
            ) -> Any:
                if method == "thread/resume":
                    self.requests.append((method, dict(params)))
                    raise AgentSessionError("THREAD_NOT_FOUND")
                return super().request(
                    method,
                    params,
                    timeout_seconds=timeout_seconds,
                )

        sessions: list[str] = []
        with patch(
            "agent_session_gateway.JsonRpcStdioProcess",
            DeadThreadTransport,
        ):
            session = CodexAppServerSession(
                executable=self.root / "codex.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="dead-codex-thread",
                permission_requester=lambda _request: None,
                session_observer=sessions.append,
            )
            session.close()

        methods = [
            method for method, _params in FakeJsonRpcTransport.instances[0].requests
        ]
        self.assertEqual(["codex-thread-001"], sessions)
        self.assertIn("thread/resume", methods)
        self.assertIn("thread/start", methods)

    def test_grok_rebinds_loaded_session_before_changing_cwd(self) -> None:
        target = self.root / "target"
        target.mkdir()
        with patch("agent_session_gateway.JsonRpcStdioProcess", FakeJsonRpcTransport):
            session = GrokAcpSession(
                executable=self.root / "grok.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="grok-existing",
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            rebound = session.rebind_working_directory(target)
            session.close()
        self.assertEqual(str(target.resolve()), rebound)
        self.assertEqual(target.resolve(), session.cwd)
        method, params = FakeJsonRpcTransport.instances[0].requests[-1]
        self.assertEqual("session/load", method)
        self.assertEqual("grok-existing", params["sessionId"])
        self.assertEqual(str(target.resolve()), params["cwd"])

    def test_grok_failed_rebind_preserves_previous_cwd(self) -> None:
        class FailedRebindTransport(FakeJsonRpcTransport):
            def request(self, method, params, *, timeout_seconds=300):
                if method == "session/load" and params.get("cwd", "").endswith("target"):
                    self.requests.append((method, dict(params)))
                    return {}
                return super().request(method, params, timeout_seconds=timeout_seconds)

        target = self.root / "target"
        target.mkdir()
        with patch("agent_session_gateway.JsonRpcStdioProcess", FailedRebindTransport):
            session = GrokAcpSession(
                executable=self.root / "grok.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="grok-existing",
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            with self.assertRaisesRegex(AgentSessionError, "GROK_ACP_CWD_REBIND_FAILED"):
                session.rebind_working_directory(target)
            self.assertEqual(self.root.resolve(), session.cwd)
            session.close()

    def test_codex_rebinds_resumed_thread_before_changing_cwd(self) -> None:
        target = self.root / "target"
        target.mkdir()
        with patch("agent_session_gateway.JsonRpcStdioProcess", FakeJsonRpcTransport):
            session = CodexAppServerSession(
                executable=self.root / "codex.exe",
                cwd=self.root,
                environment={},
                system_prompt="System",
                session_id="codex-existing",
                permission_requester=lambda _request: None,
                session_observer=lambda _session_id: None,
            )
            rebound = session.rebind_working_directory(target)
            session.close()
        self.assertEqual(str(target.resolve()), rebound)
        self.assertEqual(target.resolve(), session.cwd)
        method, params = FakeJsonRpcTransport.instances[0].requests[-1]
        self.assertEqual("thread/resume", method)
        self.assertEqual("codex-existing", params["threadId"])
        self.assertEqual(str(target.resolve()), params["cwd"])


if __name__ == "__main__":
    unittest.main()
