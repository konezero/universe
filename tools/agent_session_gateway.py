from __future__ import annotations

from dataclasses import dataclass
import json
import os
import queue
import subprocess  # nosec B404
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from windows_native_cli import NativeCliRequest, NativeCliResult, open_native_cli, run_native_cli


AGENT_SESSION_SCHEMA = "universe.agent-session.v1"
PERMISSION_REQUEST_SCHEMA = "universe.agent-permission-request.v1"
PERMISSION_DECISION_SCHEMA = "universe.agent-permission-decision.v1"
PERMISSION_KINDS = frozenset(
    {"allow_once", "allow_always", "reject_once", "reject_always"}
)
GROK_PERMISSION_MODE = "default"
CODEX_APPROVAL_POLICY = "on-request"
CLAUDE_PERMISSION_MODE = "plan"


class AgentSessionError(RuntimeError):
    pass


def cli_auto_approve_status(
    provider: str,
) -> str:
    normalized = str(provider).strip().upper()
    if normalized == "GROK":
        return "OFF" if GROK_PERMISSION_MODE == "default" else "UNKNOWN"
    if normalized == "CODEX":
        return "OFF" if CODEX_APPROVAL_POLICY == "on-request" else "UNKNOWN"
    if normalized == "CLAUDE":
        return "OFF" if CLAUDE_PERMISSION_MODE == "plan" else "UNKNOWN"
    return "UNKNOWN"


class PermissionRequester(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> str | None: ...


class AgentSession(Protocol):
    @property
    def session_ref(self) -> str: ...

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str: ...

    def close(self) -> None: ...


@dataclass
class _PendingResponse:
    event: threading.Event
    result: Any = None
    error: Any = None


def normalize_permission_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentSessionError("AGENT_PERMISSION_REQUEST_INVALID")
    required = {
        "request_id",
        "provider",
        "session_id",
        "tool_call",
        "options",
    }
    fields = frozenset(value)
    if fields not in {frozenset(required), frozenset({*required, "schema"})}:
        raise AgentSessionError("AGENT_PERMISSION_REQUEST_INVALID")
    if value.get("schema") not in {None, PERMISSION_REQUEST_SCHEMA}:
        raise AgentSessionError("AGENT_PERMISSION_REQUEST_INVALID")
    request_id = _text(value["request_id"], "request_id")
    provider = _text(value["provider"], "provider").upper()
    session_id = _text(value["session_id"], "session_id")
    tool_call = value["tool_call"]
    if not isinstance(tool_call, Mapping):
        raise AgentSessionError("AGENT_PERMISSION_TOOL_CALL_INVALID")
    options = value["options"]
    if not isinstance(options, list) or not options:
        raise AgentSessionError("AGENT_PERMISSION_OPTIONS_INVALID")
    normalized_options: list[dict[str, str]] = []
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping):
            raise AgentSessionError("AGENT_PERMISSION_OPTION_INVALID")
        option_id = _text(option.get("optionId"), "option.optionId")
        name = _text(option.get("name"), "option.name")
        kind = _text(option.get("kind"), "option.kind")
        if kind not in PERMISSION_KINDS or option_id in seen:
            raise AgentSessionError("AGENT_PERMISSION_OPTION_INVALID")
        seen.add(option_id)
        normalized_options.append({"optionId": option_id, "name": name, "kind": kind})
    return {
        "schema": PERMISSION_REQUEST_SCHEMA,
        "request_id": request_id,
        "provider": provider,
        "session_id": session_id,
        "tool_call": _json_object(tool_call),
        "options": normalized_options,
    }


class JsonRpcStdioProcess:
    def __init__(
        self,
        *,
        executable: Path,
        arguments: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        request_handler: Callable[[str, Mapping[str, Any]], Any],
        notification_handler: Callable[[str, Mapping[str, Any]], None],
    ) -> None:
        self._process = open_native_cli(
            NativeCliRequest(
                executable=executable,
                arguments=arguments,
                cwd=cwd,
                timeout_seconds=300,
                output_encoding="utf-8",
                environment={
                    str(key): str(value) for key, value in environment.items()
                },
            )
        )
        self._request_handler = request_handler
        self._notification_handler = notification_handler
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _PendingResponse] = {}
        self._next_id = 1
        self._stderr: queue.Queue[str] = queue.Queue(maxsize=100)
        self._closed = threading.Event()
        self._reader = threading.Thread(
            target=self._read_stdout,
            name="universe-agent-jsonrpc-reader",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="universe-agent-jsonrpc-stderr",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()

    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> Any:
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingResponse(threading.Event())
            self._pending[request_id] = pending
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": _text(method, "method"),
                "params": _json_object(params),
            }
        )
        if not pending.event.wait(max(1.0, float(timeout_seconds))):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise AgentSessionError(f"AGENT_RPC_TIMEOUT:{method}")
        if pending.error is not None:
            raise AgentSessionError(
                f"AGENT_RPC_ERROR:{method}:{_error_text(pending.error)}"
            )
        return pending.result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": _text(method, "method"),
        }
        if params is not None:
            message["params"] = _json_object(params)
        self._send(message)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.error = {"message": "agent process closed"}
            item.event.set()
        self._reader.join(timeout=1)
        self._stderr_reader.join(timeout=1)

    def _send(self, message: Mapping[str, Any]) -> None:
        if self._closed.is_set() or self._process.poll() is not None:
            raise AgentSessionError("AGENT_PROCESS_UNAVAILABLE" + self._stderr_detail())
        stdin = self._process.stdin
        if stdin is None:
            raise AgentSessionError("AGENT_PROCESS_STDIN_UNAVAILABLE")
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._write_lock:
            stdin.write(encoded + "\n")
            stdin.flush()

    def _read_stdout(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            self._closed.set()
            return
        try:
            for line in stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, Mapping):
                    continue
                if "method" in message and "id" in message:
                    threading.Thread(
                        target=self._handle_server_request,
                        args=(dict(message),),
                        daemon=True,
                    ).start()
                    continue
                if "method" in message:
                    params = message.get("params")
                    if isinstance(params, Mapping):
                        self._notification_handler(
                            str(message["method"]),
                            dict(params),
                        )
                    continue
                request_id = message.get("id")
                if not isinstance(request_id, int):
                    continue
                with self._pending_lock:
                    pending = self._pending.pop(request_id, None)
                if pending is None:
                    continue
                pending.result = message.get("result")
                pending.error = message.get("error")
                pending.event.set()
        finally:
            self._closed.set()
            with self._pending_lock:
                pending_responses = list(self._pending.values())
                self._pending.clear()
            for item in pending_responses:
                item.error = {"message": "agent stdout closed"}
                item.event.set()

    def _read_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        for line in stderr:
            if self._stderr.full():
                try:
                    self._stderr.get_nowait()
                except queue.Empty:
                    pass
            self._stderr.put_nowait(line.rstrip())

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params")
        try:
            if not isinstance(params, Mapping):
                raise AgentSessionError("AGENT_SERVER_REQUEST_PARAMS_INVALID")
            result = self._request_handler(method, dict(params))
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as error:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32001,
                        "message": f"{type(error).__name__}: {error}",
                    },
                }
            )

    def _stderr_detail(self) -> str:
        parts: list[str] = []
        while len(parts) < 5:
            try:
                parts.append(self._stderr.get_nowait())
            except queue.Empty:
                break
        return f":{' | '.join(parts)}" if parts else ""


class UniverseAcpGateway:
    def __init__(self, session: AgentSession) -> None:
        self.session = session

    @property
    def session_ref(self) -> str:
        return self.session.session_ref

    def reply_stream(
        self,
        message: str,
        on_delta: Callable[[str], None],
    ) -> str:
        return self.session.prompt(message, on_delta)

    def close(self) -> None:
        self.session.close()


class GrokAcpSession:
    def __init__(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        system_prompt: str,
        session_id: str | None,
        permission_requester: PermissionRequester,
        session_observer: Callable[[str], None],
    ) -> None:
        self.cwd = cwd
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.permission_requester = permission_requester
        self.session_observer = session_observer
        self._active_delta: Callable[[str], None] | None = None
        self._transport = JsonRpcStdioProcess(
            executable=executable,
            arguments=(
                "--no-auto-update",
                "--permission-mode",
                GROK_PERMISSION_MODE,
                "agent",
                "stdio",
            ),
            cwd=cwd,
            environment=environment,
            request_handler=self._handle_request,
            notification_handler=self._handle_notification,
        )
        try:
            self._initialize()
        except Exception:
            self._transport.close()
            raise

    @property
    def session_ref(self) -> str:
        return (
            f"grok-acp:{self.session_id}"
            if self.session_id
            else "grok-acp:initializing"
        )

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str:
        if not self.session_id:
            raise AgentSessionError("GROK_ACP_SESSION_UNAVAILABLE")
        parts: list[str] = []

        def receive(delta: str) -> None:
            parts.append(delta)
            on_delta(delta)

        self._active_delta = receive
        try:
            result = self._transport.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": f"{self.system_prompt}\n\n{text}",
                        }
                    ],
                },
                timeout_seconds=300,
            )
        finally:
            self._active_delta = None
        if not isinstance(result, Mapping):
            raise AgentSessionError("GROK_ACP_PROMPT_RESULT_INVALID")
        output = "".join(parts).strip()
        if not output:
            raise AgentSessionError("GROK_ACP_RESPONSE_MISSING")
        return output

    def close(self) -> None:
        self._transport.close()

    def _initialize(self) -> None:
        result = self._transport.request(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {}},
            timeout_seconds=30,
        )
        if not isinstance(result, Mapping):
            raise AgentSessionError("GROK_ACP_INITIALIZE_INVALID")
        auth_methods = {
            str(item.get("id"))
            for item in result.get("authMethods", [])
            if isinstance(item, Mapping)
        }
        if "cached_token" not in auth_methods:
            raise AgentSessionError("GROK_ACP_CACHED_AUTH_UNAVAILABLE")
        self._transport.request(
            "authenticate",
            {"methodId": "cached_token", "_meta": {"headless": True}},
            timeout_seconds=30,
        )
        capabilities = result.get("agentCapabilities")
        can_load = isinstance(capabilities, Mapping) and bool(
            capabilities.get("loadSession")
        )
        session_result: Any = None
        if self.session_id and can_load:
            try:
                session_result = self._transport.request(
                    "session/load",
                    {
                        "sessionId": self.session_id,
                        "cwd": str(self.cwd),
                        "mcpServers": [],
                    },
                    timeout_seconds=30,
                )
                if not isinstance(session_result, Mapping):
                    raise AgentSessionError("GROK_ACP_SESSION_LOAD_INVALID")
                self.session_observer(self.session_id)
                return
            except AgentSessionError:
                session_result = None
        if session_result is None:
            session_result = self._transport.request(
                "session/new",
                {"cwd": str(self.cwd), "mcpServers": []},
                timeout_seconds=30,
            )
        if not isinstance(session_result, Mapping):
            raise AgentSessionError("GROK_ACP_SESSION_INVALID")
        session_id = _text(session_result.get("sessionId"), "sessionId")
        self.session_id = session_id
        self.session_observer(session_id)

    def _handle_notification(self, method: str, params: Mapping[str, Any]) -> None:
        if method != "session/update" or self._active_delta is None:
            return
        update = params.get("update")
        if not isinstance(update, Mapping):
            return
        if update.get("sessionUpdate") != "agent_message_chunk":
            return
        content = update.get("content")
        if isinstance(content, Mapping):
            delta = content.get("text")
            if isinstance(delta, str) and delta:
                self._active_delta(delta)

    def _handle_request(self, method: str, params: Mapping[str, Any]) -> Any:
        if method != "session/request_permission":
            raise AgentSessionError("GROK_ACP_REQUEST_UNSUPPORTED")
        request = normalize_permission_request(
            {
                "request_id": f"permission_{uuid4().hex}",
                "provider": "GROK",
                "session_id": params.get("sessionId"),
                "tool_call": params.get("toolCall"),
                "options": params.get("options"),
            }
        )
        option_id = self.permission_requester(request)
        if option_id is None:
            return {"outcome": {"outcome": "cancelled"}}
        if option_id not in {option["optionId"] for option in request["options"]}:
            raise AgentSessionError("AGENT_PERMISSION_OPTION_UNKNOWN")
        return {
            "outcome": {
                "outcome": "selected",
                "optionId": option_id,
            }
        }


class CodexAppServerSession:
    def __init__(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        system_prompt: str,
        session_id: str | None,
        permission_requester: PermissionRequester,
        session_observer: Callable[[str], None],
        ephemeral: bool = False,
    ) -> None:
        self.cwd = cwd
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.permission_requester = permission_requester
        self.session_observer = session_observer
        self.ephemeral = bool(ephemeral)
        self._active_delta: Callable[[str], None] | None = None
        self._turn_events: dict[str, threading.Event] = {}
        self._completed_turns: set[str] = set()
        self._transport = JsonRpcStdioProcess(
            executable=executable,
            arguments=("app-server", "--listen", "stdio://"),
            cwd=cwd,
            environment=environment,
            request_handler=self._handle_request,
            notification_handler=self._handle_notification,
        )
        try:
            self._initialize()
        except Exception:
            self._transport.close()
            raise

    @property
    def session_ref(self) -> str:
        return (
            f"codex-app-server:{self.session_id}"
            if self.session_id
            else "codex-app-server:initializing"
        )

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str:
        if not self.session_id:
            raise AgentSessionError("CODEX_APP_SESSION_UNAVAILABLE")
        parts: list[str] = []

        def receive(delta: str) -> None:
            parts.append(delta)
            on_delta(delta)

        self._active_delta = receive
        try:
            result = self._transport.request(
                "turn/start",
                {
                    "threadId": self.session_id,
                    "input": [
                        {
                            "type": "text",
                            "text": f"{self.system_prompt}\n\n{text}",
                        }
                    ],
                    "approvalPolicy": CODEX_APPROVAL_POLICY,
                    "cwd": str(self.cwd),
                },
                timeout_seconds=30,
            )
            if not isinstance(result, Mapping) or not isinstance(
                result.get("turn"), Mapping
            ):
                raise AgentSessionError("CODEX_TURN_START_INVALID")
            turn_id = _text(result["turn"].get("id"), "turn.id")
            event = self._turn_events.setdefault(turn_id, threading.Event())
            if turn_id in self._completed_turns:
                event.set()
            if not event.wait(300):
                raise AgentSessionError("CODEX_TURN_TIMED_OUT")
        finally:
            self._active_delta = None
        output = "".join(parts).strip()
        if not output:
            raise AgentSessionError("CODEX_RESPONSE_MISSING")
        return output

    def close(self) -> None:
        self._transport.close()

    def _initialize(self) -> None:
        result = self._transport.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "universe",
                    "title": "Universe ACP Gateway",
                    "version": "1.0.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [],
                },
            },
            timeout_seconds=30,
        )
        if not isinstance(result, Mapping):
            raise AgentSessionError("CODEX_APP_INITIALIZE_INVALID")
        self._transport.notify("initialized")
        if self.session_id and not self.ephemeral:
            try:
                session_result = self._transport.request(
                    "thread/resume",
                    {
                        "threadId": self.session_id,
                        "cwd": str(self.cwd),
                        "approvalPolicy": CODEX_APPROVAL_POLICY,
                        "approvalsReviewer": "user",
                        "sandbox": "read-only",
                    },
                    timeout_seconds=30,
                )
            except AgentSessionError:
                session_result = None
        else:
            session_result = None
        if session_result is None:
            session_result = self._transport.request(
                "thread/start",
                {
                    "cwd": str(self.cwd),
                    "approvalPolicy": CODEX_APPROVAL_POLICY,
                    "approvalsReviewer": "user",
                    "sandbox": "read-only",
                    "developerInstructions": self.system_prompt,
                    "runtimeWorkspaceRoots": [str(self.cwd)],
                    "ephemeral": self.ephemeral,
                },
                timeout_seconds=30,
            )
        if not isinstance(session_result, Mapping) or not isinstance(
            session_result.get("thread"), Mapping
        ):
            raise AgentSessionError("CODEX_APP_THREAD_INVALID")
        session_id = _text(session_result["thread"].get("id"), "thread.id")
        self.session_id = session_id
        self.session_observer(session_id)

    def _handle_notification(self, method: str, params: Mapping[str, Any]) -> None:
        if method == "item/agentMessage/delta" and self._active_delta is not None:
            delta = params.get("delta")
            if isinstance(delta, str) and delta:
                self._active_delta(delta)
            return
        if method != "turn/completed":
            return
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            return
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            return
        self._completed_turns.add(turn_id)
        self._turn_events.setdefault(turn_id, threading.Event()).set()

    def _handle_request(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "item/commandExecution/requestApproval":
            return self._codex_approval(
                method,
                params,
                params.get("availableDecisions")
                or ["accept", "acceptForSession", "decline", "cancel"],
            )
        if method == "item/fileChange/requestApproval":
            return self._codex_approval(
                method,
                params,
                ["accept", "acceptForSession", "decline", "cancel"],
            )
        raise AgentSessionError("CODEX_APP_REQUEST_UNSUPPORTED")

    def _codex_approval(
        self,
        method: str,
        params: Mapping[str, Any],
        decisions: list[Any],
    ) -> dict[str, Any]:
        option_kinds = {
            "accept": "allow_once",
            "acceptForSession": "allow_always",
            "decline": "reject_once",
            "cancel": "reject_once",
        }
        option_names = {
            "accept": "Allow once",
            "acceptForSession": "Allow for session",
            "decline": "Reject",
            "cancel": "Reject and stop",
        }
        options = [
            {
                "optionId": decision,
                "name": option_names[decision],
                "kind": option_kinds[decision],
            }
            for decision in decisions
            if isinstance(decision, str) and decision in option_kinds
        ]
        request = normalize_permission_request(
            {
                "request_id": f"permission_{uuid4().hex}",
                "provider": "CODEX",
                "session_id": params.get("threadId"),
                "tool_call": {
                    "toolCallId": params.get("itemId"),
                    "title": method,
                    "command": params.get("command"),
                    "cwd": params.get("cwd"),
                    "reason": params.get("reason"),
                },
                "options": options,
            }
        )
        option_id = self.permission_requester(request)
        if option_id is None:
            option_id = "cancel"
        if option_id not in {option["optionId"] for option in request["options"]}:
            raise AgentSessionError("AGENT_PERMISSION_OPTION_UNKNOWN")
        return {"decision": option_id}


class ClaudeCodeSession:
    """Claude Code print-mode adapter with explicit session persistence."""

    def __init__(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        system_prompt: str,
        session_id: str | None,
        permission_requester: PermissionRequester,
        session_observer: Callable[[str], None],
        ephemeral: bool = False,
        max_turns: int = 8,
        native_runner: Callable[[NativeCliRequest], NativeCliResult] = run_native_cli,
    ) -> None:
        self.executable = executable
        self.cwd = cwd
        self.environment = dict(environment)
        self.system_prompt = system_prompt
        self._resume_session = bool(session_id) and not ephemeral
        self.session_id = (
            None
            if ephemeral
            else session_id or str(uuid4())
        )
        self.permission_requester = permission_requester
        self.session_observer = session_observer
        self.ephemeral = bool(ephemeral)
        self.max_turns = max(1, int(max_turns))
        self.native_runner = native_runner

    @property
    def session_ref(self) -> str:
        return (
            f"claude-code:{self.session_id}"
            if self.session_id
            else "claude-code:pending"
        )

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str:
        prompt_path = self._prompt_file(text)
        arguments = [
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            CLAUDE_PERMISSION_MODE,
            "--max-turns",
            str(self.max_turns),
            "--strict-mcp-config",
            "--tools",
            "" if self.ephemeral else "Read,Glob,Grep",
        ]
        if self.ephemeral:
            arguments.append("--no-session-persistence")
        elif self._resume_session and self.session_id:
            arguments.extend(("--resume", self.session_id))
        elif self.session_id:
            arguments.extend(("--session-id", self.session_id))
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=self.executable,
                    arguments=tuple(arguments),
                    cwd=self.cwd,
                    timeout_seconds=300,
                    output_encoding="utf-8",
                    environment=self.environment,
                    stdin_path=prompt_path,
                )
            )
        finally:
            prompt_path.unlink(missing_ok=True)
        if result.status != "COMPLETED" or result.return_code != 0:
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:500]
            raise AgentSessionError(f"CLAUDE_CODE_FAILED:{detail or result.status}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AgentSessionError("CLAUDE_CODE_RESPONSE_INVALID") from error
        if not isinstance(payload, Mapping) or payload.get("is_error") is True:
            raise AgentSessionError("CLAUDE_CODE_RESPONSE_INVALID")
        output = payload.get("result")
        if not isinstance(output, str) or not output.strip():
            raise AgentSessionError("CLAUDE_CODE_RESPONSE_MISSING")
        observed_session = payload.get("session_id")
        if isinstance(observed_session, str) and observed_session.strip():
            self.session_id = observed_session.strip()
            if not self.ephemeral:
                self.session_observer(self.session_id)
        on_delta(output)
        return output.strip()

    def close(self) -> None:
        return None

    def _prompt_file(self, text: str) -> Path:
        root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        root = root / "Universe" / "runtime-tmp"
        root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="claude-prompt-",
            suffix=".txt",
            dir=root,
        )
        path = Path(temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{self.system_prompt}\n\n{text}\n")
        return path


def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise AgentSessionError("AGENT_JSON_VALUE_INVALID") from error
    if not isinstance(decoded, dict):
        raise AgentSessionError("AGENT_JSON_OBJECT_REQUIRED")
    return decoded


def _error_text(value: Any) -> str:
    if isinstance(value, Mapping):
        message = value.get("message")
        if isinstance(message, str) and message:
            return message[:500]
    return str(value)[:500]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 4096:
        raise AgentSessionError(f"{field.upper()}_REQUIRED")
    return value.strip()
