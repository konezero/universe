from __future__ import annotations

from dataclasses import dataclass
import json
import os
import queue
import shutil
import subprocess  # nosec B404
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from process_identity import launched_process_identity
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
PROVIDER_EFFORTS = frozenset({"AUTO", "LOW", "MEDIUM", "HIGH", "MAX"})

# Claude Code routes permission prompts to the MCP tool named by
# --permission-prompt-tool. Verified against claude-code 2.1.212: the flag is
# accepted (it is hidden from --help), and in plan mode file edits and
# file-modifying shell commands are never auto-approved -- they reach the
# prompt tool instead.
CLAUDE_PERMISSION_TOOL_SERVER = "universe_permission"
CLAUDE_PERMISSION_TOOL_NAME = "approve"
CLAUDE_PERMISSION_PROMPT_TOOL = (
    f"mcp__{CLAUDE_PERMISSION_TOOL_SERVER}__{CLAUDE_PERMISSION_TOOL_NAME}"
)
# Read-only tools the resident session may pre-approve. Anything that writes,
# executes a shell, or reaches the network must go through the prompt tool.
CLAUDE_READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
# Argument vectors that would bypass or pre-approve permission checks.
CLAUDE_FORBIDDEN_ARGUMENTS = frozenset(
    {
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--yolo",
    }
)
# Permission modes that do not route write or shell execution to the prompt
# tool. ``plan`` and ``default`` both reach it; the modes below do not.
CLAUDE_FORBIDDEN_PERMISSION_MODES = frozenset(
    {"acceptEdits", "auto", "bypassPermissions", "dontAsk"}
)
PLATFORM_APPROVAL_EVIDENCE_SCHEMA = "ai-career.platform-approval-evidence.v1"


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


def permission_bridge_status(provider: str) -> dict[str, Any]:
    """Report whether a provider can surface permission requests to Universe.

    ``AVAILABLE`` means the adapter forwards provider permission requests
    through ``universe.agent-permission-request.v1``. ``UNAVAILABLE`` means the
    provider offers no such channel; the adapter must fail closed instead of
    synthesizing an approval.
    """

    normalized = str(provider).strip().upper()
    if normalized == "GROK":
        return {
            "provider": normalized,
            "status": "AVAILABLE",
            "transport": "session/request_permission",
            "option_kinds": sorted(PERMISSION_KINDS),
            "evidence_ref": "acp session/request_permission",
        }
    if normalized == "CODEX":
        return {
            "provider": normalized,
            "status": "AVAILABLE",
            "transport": "item/permissions/requestApproval",
            "option_kinds": ["allow_once", "allow_always", "reject_once"],
            "evidence_ref": "codex app-server approval requests",
        }
    if normalized == "CLAUDE":
        return {
            "provider": normalized,
            "status": "AVAILABLE",
            "transport": CLAUDE_PERMISSION_PROMPT_TOOL,
            "option_kinds": ["allow_once", "allow_always", "reject_once"],
            "evidence_ref": "claude-code --permission-prompt-tool mcp bridge",
        }
    return {
        "provider": normalized,
        "status": "UNKNOWN",
        "transport": "UNKNOWN",
        "option_kinds": [],
        "evidence_ref": "UNKNOWN",
    }


# Shared lifecycle states for every provider session. Codex, Grok, and Claude
# report the same vocabulary upward even though each adapter keeps its own
# transport internally.
SESSION_CONNECTING = "CONNECTING"
SESSION_READY = "READY"
SESSION_BUSY = "BUSY"
SESSION_WAITING_APPROVAL = "WAITING_APPROVAL"
SESSION_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
SESSION_FAILED = "FAILED"
SESSION_STOPPED = "STOPPED"
SESSION_STATES = frozenset(
    {
        SESSION_CONNECTING,
        SESSION_READY,
        SESSION_BUSY,
        SESSION_WAITING_APPROVAL,
        SESSION_QUOTA_EXHAUSTED,
        SESSION_FAILED,
        SESSION_STOPPED,
    }
)

# Who may hold a resident session, and who must stay bounded.
RESIDENT_ROLES = frozenset({"UNIVERSE_CONDUCTOR", "UNIVERSE_MASTER", "PROJECT_MASTER"})
EPHEMERAL_ROLES = frozenset({"TASK_FRAME_BOSS", "TASK_FRAME_WORKER", "PROBE"})


class PermissionRequester(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> str | None: ...


class AgentSession(Protocol):
    @property
    def session_ref(self) -> str: ...

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str: ...

    def set_permission_requester(self, requester: PermissionRequester) -> None: ...

    def drain_work_statuses(self) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class BoundedSession(Protocol):
    """The lifetime contract every provider adapter must expose.

    ``ephemeral`` is the single external switch that separates a bounded Task
    Frame worker from a resident Conductor or Master session. Each adapter
    keeps its own transport (app-server, ACP, stream-json) behind it.
    """

    ephemeral: bool
    session_id: str | None

    @property
    def session_ref(self) -> str: ...

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str: ...

    def close(self) -> None: ...


class GitTrace2Observer:
    """Collect terminal commit/push milestones from one provider process tree.

    Git writes JSONL to a session-scoped runtime file. Only the command name and
    exit code leave this observer; argv and repository paths stay out of UI
    events and the trace file is removed when the provider session closes.
    """

    schema = "universe.git-trace2-work-status.v1"
    _operations = {"commit": "COMMIT", "push": "PUSH"}

    def __init__(
        self,
        cwd: Path,
        *,
        metadata_reader: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        runtime_root = self.cwd / ".ai" / "runtime" / "tmp" / "git-trace2"
        try:
            runtime_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            runtime_root = Path(tempfile.gettempdir()) / "universe-git-trace2"
            runtime_root.mkdir(parents=True, exist_ok=True)
        self.path = runtime_root / f"provider-{uuid4().hex}.jsonl"
        self._offset = 0
        self._remainder = b""
        self._commands: dict[str, str] = {}
        self._emitted: set[str] = set()
        self._lock = threading.Lock()
        self._metadata_reader = metadata_reader or self._read_git_metadata

    def environment(self, base: Mapping[str, str]) -> dict[str, str]:
        environment = dict(base)
        environment["GIT_TRACE2_EVENT"] = str(self.path)
        environment["GIT_TRACE2_EVENT_NESTING"] = "20"
        environment["GIT_TRACE_REDACT"] = "1"
        return environment

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read_records()
            milestones: list[dict[str, Any]] = []
            for record in records:
                sid = str(record.get("sid") or "").strip()
                if not sid:
                    continue
                event = str(record.get("event") or "").casefold()
                if event == "cmd_name":
                    operation = self._operations.get(
                        str(record.get("name") or "").casefold()
                    )
                    if operation is not None:
                        self._commands[sid] = operation
                    continue
                if event not in {"exit", "atexit"} or sid in self._emitted:
                    continue
                operation = self._commands.get(sid)
                code = record.get("code")
                if operation is None or not isinstance(code, int):
                    continue
                self._emitted.add(sid)
                milestone = {
                    "schema": self.schema,
                    "operation": operation,
                    "state": "COMPLETED" if code == 0 else "FAILED",
                    "exit_code": code,
                    "source": "GIT_TRACE2",
                }
                if code == 0:
                    try:
                        metadata = self._metadata_reader(operation)
                    except Exception:
                        metadata = {}
                    if isinstance(metadata, Mapping):
                        milestone.update(
                            {
                                key: metadata[key]
                                for key in (
                                    "commit_sha",
                                    "short_sha",
                                    "commit_message",
                                    "branch",
                                    "remote",
                                    "changed_files",
                                )
                                if key in metadata
                            }
                        )
                milestones.append(milestone)
            return milestones

    def _read_git_metadata(self, operation: str) -> dict[str, Any]:
        executable = shutil.which("git.exe") or shutil.which("git")
        if not executable:
            return {}
        environment = {
            "GIT_TRACE2_EVENT": "0",
            "GIT_TRACE2_EVENT_NESTING": "0",
            "GIT_TRACE_REDACT": "1",
        }

        def read(*arguments: str, maximum: int = 4096) -> str:
            result = run_native_cli(
                NativeCliRequest(
                    executable=Path(executable),
                    arguments=tuple(arguments),
                    cwd=self.cwd,
                    timeout_seconds=5,
                    output_encoding="utf-8",
                    max_output_chars=maximum,
                    environment=environment,
                )
            )
            if result.status != "COMPLETED" or result.return_code != 0:
                return ""
            return result.stdout

        identity = read("show", "-s", "--format=%H%x00%s", "HEAD")
        commit_sha, separator, message = identity.strip().partition("\x00")
        if not separator or not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit_sha):
            return {}
        metadata: dict[str, Any] = {
            "commit_sha": commit_sha.lower(),
            "short_sha": commit_sha[:7].lower(),
            "commit_message": self._safe_git_label(message, 240) or "Commit",
        }
        changed = read(
            "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "HEAD",
            maximum=200_000,
        )
        metadata["changed_files"] = len(
            [line for line in changed.splitlines() if line.strip()]
        )
        branch = self._safe_git_label(read("branch", "--show-current"), 160)
        metadata["branch"] = branch or "DETACHED"
        if operation == "PUSH" and branch:
            remote = self._safe_git_label(
                read("config", "--get", f"branch.{branch}.remote"), 80
            )
            if remote and not any(marker in remote for marker in ("/", chr(92), "://")):
                metadata["remote"] = remote
        return metadata

    @staticmethod
    def _safe_git_label(value: Any, maximum: int) -> str:
        text = " ".join(str(value or "").split())
        return "".join(character for character in text if character.isprintable())[:maximum]

    def close(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)

    def _read_records(self) -> list[dict[str, Any]]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size < self._offset:
            self._offset = 0
            self._remainder = b""
        with self.path.open("rb") as stream:
            stream.seek(self._offset)
            chunk = stream.read()
        self._offset += len(chunk)
        if not chunk:
            return []
        lines = (self._remainder + chunk).split(b"\n")
        self._remainder = lines.pop()
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(record, Mapping):
                records.append(dict(record))
        return records


def worker_session_contract(session: Any) -> dict[str, Any]:
    """Describe one session's lifetime for uniform assertions across providers."""

    ephemeral = bool(getattr(session, "ephemeral", False))
    return {
        "ephemeral": ephemeral,
        "resident": not ephemeral,
        # A bounded session must never carry a resumable coordinate.
        "resumable": bool(getattr(session, "session_id", None)) and not ephemeral,
        "session_ref": str(getattr(session, "session_ref", "UNKNOWN")),
    }


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


def build_platform_approval_evidence(
    request: Mapping[str, Any], option_id: str | None
) -> dict[str, Any]:
    """Create a provider-neutral evidence envelope for one approval event."""

    normalized = normalize_permission_request(request)
    selected = next(
        (
            option
            for option in normalized["options"]
            if option["optionId"] == option_id
        ),
        None,
    )
    decision = "REJECTED" if selected is None else selected["kind"].upper()
    request_id = normalized["request_id"]
    provider = normalized["provider"]
    return {
        "schema": PLATFORM_APPROVAL_EVIDENCE_SCHEMA,
        "evidence_ref": f"platform-approval://{provider}/{request_id}",
        "provider": provider,
        "request_id": request_id,
        "session_id": normalized["session_id"],
        "decision": decision,
        "option_id": option_id or "NONE",
        "observed_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
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
        self._executable = executable.expanduser().resolve()
        self._arguments = tuple(arguments)
        self._process = open_native_cli(
            NativeCliRequest(
                executable=self._executable,
                arguments=self._arguments,
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

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        if self._process.poll() is not None:
            raise AgentSessionError("AGENT_PROCESS_NOT_ALIVE")
        return launched_process_identity(
            self._process,
            executable=self._executable,
            command=[str(self._executable), *self._arguments],
            endpoint=endpoint,
            handshake_token=handshake_token,
        )

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

    def set_permission_requester(self, requester: PermissionRequester) -> None:
        self.session.set_permission_requester(requester)

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        reader = getattr(self.session, "drain_work_statuses", None)
        if not callable(reader):
            return []
        return [dict(item) for item in reader() if isinstance(item, Mapping)]

    def runtime_observation(self) -> dict[str, Any]:
        observer = getattr(self.session, "runtime_observation", None)
        if callable(observer):
            observed = observer()
            if isinstance(observed, Mapping):
                return dict(observed)
        status_reader = getattr(self.session, "session_status", None)
        state = str(status_reader()).strip() if callable(status_reader) else "UNKNOWN"
        return {
            "schema": "universe.provider-runtime-observation.v1",
            "provider": str(getattr(self.session, "provider", "UNKNOWN")),
            "session_ref": self.session_ref,
            "state": state or "UNKNOWN",
            "quota_state": (
                "EXHAUSTED" if state == SESSION_QUOTA_EXHAUSTED else "UNKNOWN"
            ),
            "usage": {},
        }

    def rebind_working_directory(self, cwd: Path) -> str:
        rebind = getattr(self.session, "rebind_working_directory", None)
        if not callable(rebind):
            raise AgentSessionError("AGENT_SESSION_CWD_REBIND_UNAVAILABLE")
        return str(rebind(cwd))

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        resolver = getattr(self.session, "supervisor_process_identity", None)
        if not callable(resolver):
            raise AgentSessionError("AGENT_PROCESS_IDENTITY_UNAVAILABLE")
        return dict(resolver(endpoint, handshake_token))

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
        model: str = "default",
        effort: str = "AUTO",
        permission_requester: PermissionRequester,
        session_observer: Callable[[str], None],
        ephemeral: bool = False,
        response_timeout_seconds: float = 300,
    ) -> None:
        self.cwd = cwd
        self.system_prompt = system_prompt
        # An ephemeral session is bounded: it never resumes a stored session
        # and never reports its id back, so nothing can be resumed later.
        self.ephemeral = bool(ephemeral)
        self.response_timeout_seconds = _positive_timeout_seconds(
            response_timeout_seconds, "GROK_RESPONSE_TIMEOUT_INVALID"
        )
        self.session_id = None if self.ephemeral else session_id
        self.model = _text(model, "model")
        self.effort = _text(effort, "effort").upper()
        if self.effort not in PROVIDER_EFFORTS:
            raise AgentSessionError("GROK_EFFORT_INVALID")
        self.permission_requester = permission_requester
        self.session_observer = session_observer
        self.last_platform_approval_evidence: dict[str, Any] | None = None
        self._active_delta: Callable[[str], None] | None = None
        self._bootstrap_pending = True
        self._git_trace2 = GitTrace2Observer(cwd)
        environment = self._git_trace2.environment(environment)
        transport_arguments = [
            "--no-auto-update",
            "--permission-mode",
            GROK_PERMISSION_MODE,
        ]
        if self.effort != "AUTO":
            transport_arguments.extend(("--reasoning-effort", self.effort.lower()))
        transport_arguments.extend(("agent", "--model", self.model, "stdio"))
        self._transport = JsonRpcStdioProcess(
            executable=executable,
            arguments=tuple(transport_arguments),
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

    def set_permission_requester(self, requester: PermissionRequester) -> None:
        self.permission_requester = requester

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str:
        if not self.session_id:
            raise AgentSessionError("GROK_ACP_SESSION_UNAVAILABLE")
        parts: list[str] = []

        def receive(delta: str) -> None:
            parts.append(delta)
            on_delta(delta)

        self._active_delta = receive
        prompt_text = text
        if self._bootstrap_pending:
            prompt_text = f"{self.system_prompt}\n\n{text}"
            self._bootstrap_pending = False
        try:
            result = self._transport.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": prompt_text,
                        }
                    ],
                },
                timeout_seconds=self.response_timeout_seconds,
            )
        finally:
            self._active_delta = None
        if not isinstance(result, Mapping):
            raise AgentSessionError("GROK_ACP_PROMPT_RESULT_INVALID")
        output = "".join(parts).strip()
        if not output:
            raise AgentSessionError("GROK_ACP_RESPONSE_MISSING")
        return output

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        return self._git_trace2.drain_work_statuses()

    def close(self) -> None:
        self._transport.close()
        self._git_trace2.close()

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        return self._transport.supervisor_process_identity(endpoint, handshake_token)

    def rebind_working_directory(self, cwd: Path) -> str:
        if self.ephemeral or not self.session_id:
            raise AgentSessionError("GROK_ACP_CWD_REBIND_UNAVAILABLE")
        target = cwd.expanduser().resolve(strict=True)
        if not target.is_dir():
            raise AgentSessionError("GROK_ACP_CWD_INVALID")
        result = self._transport.request(
            "session/load",
            {
                "sessionId": self.session_id,
                "cwd": str(target),
                "mcpServers": [],
            },
            timeout_seconds=30,
        )
        if (
            not isinstance(result, Mapping)
            or result.get("sessionId") != self.session_id
        ):
            raise AgentSessionError("GROK_ACP_CWD_REBIND_FAILED")
        self.cwd = target
        return str(target)

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
        if self.session_id and can_load and not self.ephemeral:
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
        if not self.ephemeral:
            # A bounded worker must not persist a resumable coordinate.
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
        self.last_platform_approval_evidence = build_platform_approval_evidence(
            request, option_id
        )
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
        model: str = "default",
        effort: str = "AUTO",
        permission_requester: PermissionRequester,
        session_observer: Callable[[str], None],
        ephemeral: bool = False,
        response_timeout_seconds: float = 300,
    ) -> None:
        self.cwd = cwd
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.model = _text(model, "model")
        self.effort = _text(effort, "effort").upper()
        if self.effort not in PROVIDER_EFFORTS:
            raise AgentSessionError("CODEX_EFFORT_INVALID")
        self.permission_requester = permission_requester
        self.session_observer = session_observer
        self.last_platform_approval_evidence: dict[str, Any] | None = None
        self.ephemeral = bool(ephemeral)
        self.response_timeout_seconds = _positive_timeout_seconds(
            response_timeout_seconds, "CODEX_RESPONSE_TIMEOUT_INVALID"
        )
        self._active_delta: Callable[[str], None] | None = None
        self._active_final_text: str | None = None
        self._turn_events: dict[str, threading.Event] = {}
        self._completed_turns: set[str] = set()
        self._turn_statuses: dict[str, str] = {}
        self._bootstrap_pending = False
        self._git_trace2 = GitTrace2Observer(cwd)
        environment = self._git_trace2.environment(environment)
        transport_arguments = ["app-server"]
        if self.model.casefold() not in {"auto", "default"}:
            transport_arguments.extend(("-c", f"model={json.dumps(self.model)}"))
        if self.effort != "AUTO":
            transport_arguments.extend(
                ("-c", f"model_reasoning_effort={json.dumps(self.effort.lower())}")
            )
        transport_arguments.extend(("--listen", "stdio://"))
        self._transport = JsonRpcStdioProcess(
            executable=executable,
            arguments=tuple(transport_arguments),
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

    def set_permission_requester(self, requester: PermissionRequester) -> None:
        self.permission_requester = requester

    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str:
        if not self.session_id:
            raise AgentSessionError("CODEX_APP_SESSION_UNAVAILABLE")
        parts: list[str] = []

        def receive(delta: str) -> None:
            parts.append(delta)
            on_delta(delta)

        self._active_delta = receive
        self._active_final_text = None
        prompt_text = text
        if self._bootstrap_pending:
            prompt_text = f"{self.system_prompt}\n\n{text}"
            self._bootstrap_pending = False
        try:
            result = self._transport.request(
                "turn/start",
                {
                    "threadId": self.session_id,
                    "input": [
                        {
                            "type": "text",
                            "text": prompt_text,
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
            if not event.wait(self.response_timeout_seconds):
                raise AgentSessionError("CODEX_TURN_TIMED_OUT")
            turn_status = self._turn_statuses.pop(turn_id, "completed")
            if turn_status != "completed":
                raise AgentSessionError("CODEX_TURN_FAILED")
        finally:
            self._active_delta = None
        final_text = self._active_final_text
        self._active_final_text = None
        if final_text is not None:
            output = final_text.strip()
            if output and not parts:
                on_delta(output)
        else:
            output = "".join(parts).strip()
        if not output:
            raise AgentSessionError("CODEX_RESPONSE_MISSING")
        return output

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        return self._git_trace2.drain_work_statuses()

    def close(self) -> None:
        self._transport.close()
        self._git_trace2.close()

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        return self._transport.supervisor_process_identity(endpoint, handshake_token)

    def rebind_working_directory(self, cwd: Path) -> str:
        if self.ephemeral or not self.session_id:
            raise AgentSessionError("CODEX_APP_CWD_REBIND_UNAVAILABLE")
        target = cwd.expanduser().resolve(strict=True)
        if not target.is_dir():
            raise AgentSessionError("CODEX_APP_CWD_INVALID")
        result = self._transport.request(
            "thread/resume",
            {
                "threadId": self.session_id,
                "cwd": str(target),
                "approvalPolicy": CODEX_APPROVAL_POLICY,
                "approvalsReviewer": "user",
                "sandbox": "read-only",
            },
            timeout_seconds=30,
        )
        if not isinstance(result, Mapping) or not isinstance(
            result.get("thread"), Mapping
        ):
            raise AgentSessionError("CODEX_APP_CWD_REBIND_FAILED")
        if _text(result["thread"].get("id"), "thread.id") != self.session_id:
            raise AgentSessionError("CODEX_APP_CWD_REBIND_SESSION_MISMATCH")
        self.cwd = target
        return str(target)

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
                self._bootstrap_pending = True
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
        if method == "item/completed":
            item = params.get("item")
            if (
                isinstance(item, Mapping)
                and item.get("type") == "agentMessage"
                and isinstance(item.get("text"), str)
            ):
                self._active_final_text = item["text"]
            return
        if method != "turn/completed":
            return
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            return
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            return
        status = turn.get("status")
        if isinstance(status, str) and status:
            self._turn_statuses[turn_id] = status
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
        if method == "item/permissions/requestApproval":
            return self._codex_permissions_approval(params)
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
        self.last_platform_approval_evidence = build_platform_approval_evidence(
            request, option_id
        )
        if option_id not in {option["optionId"] for option in request["options"]}:
            raise AgentSessionError("AGENT_PERMISSION_OPTION_UNKNOWN")
        return {"decision": option_id}

    def _codex_permissions_approval(
        self,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        permissions = params.get("permissions")
        if not isinstance(permissions, Mapping):
            raise AgentSessionError("CODEX_PERMISSION_PROFILE_INVALID")
        requested_permissions = _json_object(permissions)
        options = [
            {
                "optionId": "grantForTurn",
                "name": "Allow for this turn",
                "kind": "allow_once",
            },
            {
                "optionId": "grantForSession",
                "name": "Allow for this session",
                "kind": "allow_always",
            },
            {
                "optionId": "decline",
                "name": "Reject",
                "kind": "reject_once",
            },
        ]
        request = normalize_permission_request(
            {
                "request_id": f"permission_{uuid4().hex}",
                "provider": "CODEX",
                "session_id": params.get("threadId"),
                "tool_call": {
                    "toolCallId": params.get("itemId"),
                    "title": "item/permissions/requestApproval",
                    "cwd": params.get("cwd"),
                    "environmentId": params.get("environmentId"),
                    "reason": params.get("reason"),
                    "requestedPermissions": requested_permissions,
                },
                "options": options,
            }
        )
        option_id = self.permission_requester(request) or "decline"
        self.last_platform_approval_evidence = build_platform_approval_evidence(
            request, option_id
        )
        if option_id == "grantForTurn":
            return {"permissions": requested_permissions, "scope": "turn"}
        if option_id == "grantForSession":
            return {"permissions": requested_permissions, "scope": "session"}
        if option_id == "decline":
            return {"permissions": {}, "scope": "turn"}
        raise AgentSessionError("AGENT_PERMISSION_OPTION_UNKNOWN")


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
        model: str = "default",
        permission_requester: PermissionRequester,
        session_observer: Callable[[str], None],
        ephemeral: bool = False,
        max_turns: int = 8,
        response_timeout_seconds: float = 300,
        json_schema: Mapping[str, Any] | None = None,
        native_runner: Callable[[NativeCliRequest], NativeCliResult] = run_native_cli,
    ) -> None:
        self.executable = executable
        self.cwd = cwd
        self._git_trace2 = GitTrace2Observer(cwd)
        self.environment = self._git_trace2.environment(environment)
        self.system_prompt = system_prompt
        self.model = _text(model, "model")
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
        self.response_timeout_seconds = _positive_timeout_seconds(
            response_timeout_seconds, "CLAUDE_RESPONSE_TIMEOUT_INVALID"
        )
        self.json_schema = (
            _json_object(json_schema) if json_schema is not None else None
        )
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
            "--model",
            self.model,
            "--max-turns",
            str(self.max_turns),
            "--strict-mcp-config",
            "--tools",
            "" if self.ephemeral else ",".join(CLAUDE_READ_ONLY_TOOLS),
        ]
        if self.ephemeral:
            arguments.append("--no-session-persistence")
        elif self._resume_session and self.session_id:
            arguments.extend(("--resume", self.session_id))
        elif self.session_id:
            arguments.extend(("--session-id", self.session_id))
        if self.json_schema is not None:
            arguments.extend(
                (
                    "--json-schema",
                    json.dumps(
                        self.json_schema,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
        try:
            self._assert_fail_closed(arguments)
            result = self.native_runner(
                NativeCliRequest(
                    executable=self.executable,
                    arguments=tuple(arguments),
                    cwd=self.cwd,
                    timeout_seconds=self.response_timeout_seconds,
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
        if self.json_schema is not None:
            structured_output = payload.get("structured_output")
            if not isinstance(structured_output, Mapping):
                raise AgentSessionError("CLAUDE_CODE_STRUCTURED_OUTPUT_MISSING")
            output = json.dumps(
                _json_object(structured_output),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        else:
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

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        return self._git_trace2.drain_work_statuses()

    def close(self) -> None:
        self._git_trace2.close()

    @staticmethod
    def _argument_value(arguments: list[str], flag: str, error_code: str) -> str:
        if flag not in arguments:
            raise AgentSessionError(error_code)
        index = arguments.index(flag) + 1
        if index >= len(arguments):
            raise AgentSessionError(error_code)
        return arguments[index]

    @staticmethod
    def _assert_fail_closed(arguments: list[str]) -> None:
        """Refuse to launch unless the argument vector keeps the prompt gate.

        Anything beyond the read-only tool set must reach the Universe
        permission UI through the ``--permission-prompt-tool`` bridge, so the
        adapter must never pre-approve a write, shell, or network capable tool
        and must never bypass the CLI permission check.
        """

        if CLAUDE_FORBIDDEN_ARGUMENTS.intersection(arguments):
            raise AgentSessionError("CLAUDE_PERMISSION_BYPASS_FORBIDDEN")
        mode = ClaudeCodeSession._argument_value(
            arguments, "--permission-mode", "CLAUDE_PERMISSION_MODE_REQUIRED"
        )
        if mode in CLAUDE_FORBIDDEN_PERMISSION_MODES:
            raise AgentSessionError("CLAUDE_PERMISSION_MODE_FORBIDDEN")
        granted = ClaudeCodeSession._argument_value(
            arguments, "--tools", "CLAUDE_TOOL_GRANT_REQUIRED"
        )
        requested = {name.strip() for name in granted.split(",") if name.strip()}
        if not requested.issubset(CLAUDE_READ_ONLY_TOOLS):
            raise AgentSessionError("CLAUDE_WRITE_TOOL_FORBIDDEN")

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


def _positive_timeout_seconds(value: Any, error_code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
    ):
        raise AgentSessionError(error_code)
    return float(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 4096:
        raise AgentSessionError(f"{field.upper()}_REQUIRED")
    return value.strip()
