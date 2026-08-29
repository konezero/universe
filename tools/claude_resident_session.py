"""Resident Claude Code session over the stream-json protocol.

Codex runs as an app-server resident and Grok as an ACP resident. This module
gives Claude the same shape: one long-lived ``claude -p`` process per session
whose stdin/stdout stay open for the life of the session, instead of
re-executing the CLI for every message.

Server-owned processes use the PTY Supervisor's single launch path:
a headless managed ``cmd`` owns the long-lived provider CLI and its exact
PID/start-time identity. Tests and explicitly standalone callers retain the
Windows native CLI transport as a compatibility fallback.
"""

from __future__ import annotations

from collections import deque
import json
import queue
import subprocess  # nosec B404
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from agent_session_gateway import (
    GitTrace2Observer,
    _supervised_json_lines,
    _supervised_process_identity,
)
from process_identity import launched_process_identity
from windows_native_cli import NativeCliRequest, open_native_cli

# Environment names that carry the permission capability token. They belong to
# the MCP child process only: Claude's own process must never see them, or a
# Bash call inside Claude could read the token that gates its own approvals.
CLAUDE_FORBIDDEN_ENVIRONMENT = frozenset(
    {
        "UNIVERSE_CLAUDE_PERMISSION_TOKEN",
        "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT",
    }
)


CLAUDE_STREAM_ARGUMENTS = (
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
)

# Claude routes a permission prompt to the MCP tool named here. plan mode is
# what keeps file edits and file-modifying shell commands off the auto-approve
# path so they reach the prompt tool.
CLAUDE_PERMISSION_MODE = "plan"
CLAUDE_PERMISSION_TOOL_SERVER = "universe_permission"
CLAUDE_PERMISSION_TOOL_NAME = "approve"
CLAUDE_PERMISSION_PROMPT_TOOL = (
    f"mcp__{CLAUDE_PERMISSION_TOOL_SERVER}__{CLAUDE_PERMISSION_TOOL_NAME}"
)
# Never bypass or pre-approve the permission check.
CLAUDE_FORBIDDEN_ARGUMENTS = frozenset(
    {
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--yolo",
    }
)

# Lifecycle states are owned by the shared gateway contract, not redefined
# here, so Codex / Grok / Claude report one vocabulary upward.
from agent_session_gateway import (  # noqa: E402
    PROVIDER_EFFORTS,
    SESSION_BUSY,
    SESSION_CONNECTING,
    SESSION_FAILED,
    SESSION_QUOTA_EXHAUSTED,
    SESSION_READY,
    SESSION_STATES,
    SESSION_STOPPED,
)

# Result subtypes and retry categories that mean the account limit is spent.
# These must never trigger an automatic retry loop.
QUOTA_ERROR_CATEGORIES = frozenset({"rate_limit", "billing_error"})
QUOTA_RESULT_SUBTYPES = frozenset({"error_quota_exhausted", "error_rate_limit"})
# ``rate_limit_event`` is routine telemetry: an ordinary successful turn emits
# it. Both sets below are explicit -- a prefix match would silently admit any
# future status. Observed on claude-code 2.1.212: ``allowed`` and
# ``allowed_warning`` (the latter carries ``utilization`` /
# ``surpassedThreshold`` and still completes the turn).
RATE_LIMIT_ALLOWED_STATUSES = frozenset({"allowed", "allowed_warning"})
# Only statuses that explicitly block. Keep this set to values that have been
# observed or documented as blocking; do not guess.
RATE_LIMIT_EXHAUSTED_STATUSES = frozenset({"rejected", "blocked", "exhausted"})
# Anything else is neither allowed nor spent: it must not auto-allow and must
# not be mistaken for QUOTA_EXHAUSTED. The turn continues and the ``result``
# event decides the outcome.
RATE_LIMIT_STATUS_UNKNOWN = "RATE_LIMIT_STATUS_UNKNOWN"
STARTUP_PROBE_SECONDS = 2.0


class ClaudeResidentError(RuntimeError):
    pass


class ClaudeStreamProcess:
    """One resident ``claude -p`` process speaking stream-json on stdio."""

    def __init__(
        self,
        *,
        executable: Path,
        arguments: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        event_handler: Callable[[Mapping[str, Any]], None],
        opener: Callable[..., subprocess.Popen[str]] | None = None,
        terminal_host: Any | None = None,
        project_id: str = "",
        mode: str = "",
        supervisor_session_id: str = "",
        session_anchor_ref: str = "",
    ) -> None:
        self._executable = executable.expanduser().resolve()
        self._arguments = tuple(arguments)
        self._terminal_host = terminal_host
        self._terminal_id = ""
        self._terminal_waiter: queue.Queue | None = None
        self._process = None
        if terminal_host is None:
            request = NativeCliRequest(
                executable=self._executable,
                arguments=self._arguments,
                cwd=cwd,
                timeout_seconds=300,
                output_encoding="utf-8",
                environment={str(k): str(v) for k, v in environment.items()},
            )
            self._process = (
                open_native_cli(request)
                if opener is None
                else open_native_cli(request, opener=opener)
            )
        else:
            terminal = terminal_host.create(
                project_id=project_id,
                mode=mode,
                cwd=str(cwd),
                provider="CLAUDE",
                supervisor_session_id=supervisor_session_id,
                session_anchor_ref=session_anchor_ref,
                launch_profile="SUPERVISED_STDIO",
                provider_arguments=list(self._arguments),
                provider_environment={str(k): str(v) for k, v in environment.items()},
                cols=240,
                rows=40,
            )
            self._terminal_id = str(terminal.get("terminal_id") or "")
            if not self._terminal_id:
                raise ClaudeResidentError("CLAUDE_SUPERVISED_TERMINAL_ID_MISSING")
            self._terminal_waiter = terminal_host.subscribe(self._terminal_id)
        self._event_handler = event_handler
        self._write_lock = threading.Lock()
        self._sent_lines: deque[str] = deque(maxlen=256)
        self._stderr: queue.Queue[str] = queue.Queue(maxsize=100)
        self._closed = threading.Event()
        self._reader = threading.Thread(
            target=self._read_stdout,
            name="universe-claude-stream-reader",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="universe-claude-stream-stderr",
            daemon=True,
        )
        self._reader.start()
        if self._process is not None:
            self._stderr_reader.start()

    @property
    def alive(self) -> bool:
        if self._closed.is_set():
            return False
        if self._terminal_host is not None:
            try:
                terminal = self._terminal_host.get(self._terminal_id)
            except Exception:
                return False
            return str(getattr(terminal, "state", "")) == "LIVE"
        return self._process is not None and self._process.poll() is None

    def send_user_message(self, text: str) -> None:
        """Write one JSONL user message to the resident process stdin."""

        self._send(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": str(text)}],
                },
            }
        )

    def stderr_detail(self) -> str:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._stderr.get_nowait())
            except queue.Empty:
                break
        joined = " ".join(line for line in lines if line).strip()
        return f":{joined[:500]}" if joined else ""

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._terminal_host is not None and self._terminal_id:
            waiter = self._terminal_waiter
            if waiter is not None:
                self._terminal_host.unsubscribe(self._terminal_id, waiter)
            try:
                self._terminal_host.close(self._terminal_id)
            except Exception:
                pass
        elif self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        self._reader.join(timeout=1)
        if self._process is not None:
            self._stderr_reader.join(timeout=1)

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        if self._terminal_host is not None:
            return _supervised_process_identity(
                self._terminal_host,
                self._terminal_id,
                endpoint=endpoint,
                handshake_token=handshake_token,
            )
        if self._process is None or self._process.poll() is not None:
            raise ClaudeResidentError("CLAUDE_PROCESS_NOT_ALIVE")
        return launched_process_identity(
            self._process,
            executable=self._executable,
            command=[str(self._executable), *self._arguments],
            endpoint=endpoint,
            handshake_token=handshake_token,
        )

    def _send(self, message: Mapping[str, Any]) -> None:
        if self._closed.is_set():
            raise ClaudeResidentError(
                "CLAUDE_PROCESS_UNAVAILABLE" + self.stderr_detail()
            )
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            if self._terminal_host is not None:
                self._sent_lines.append(encoded)
                # ConPTY input is terminal input, not a pipe: carriage return
                # is the Enter key that submits one JSON line to the hosted CLI.
                self._terminal_host.write(
                    self._terminal_id, (encoded + "\r").encode("utf-8")
                )
                return
            if self._process is None or self._process.poll() is not None:
                raise ClaudeResidentError(
                    "CLAUDE_PROCESS_UNAVAILABLE" + self.stderr_detail()
                )
            stdin = self._process.stdin
            if stdin is None:
                raise ClaudeResidentError("CLAUDE_PROCESS_STDIN_UNAVAILABLE")
            stdin.write(encoded + "\n")
            stdin.flush()

    def _read_stdout(self) -> None:
        if self._terminal_host is not None:
            waiter = self._terminal_waiter
            if waiter is None:
                self._closed.set()
                self._event_handler({"type": "__stream_closed__"})
                return
            stdout = _supervised_json_lines(waiter)
        else:
            if self._process is None or self._process.stdout is None:
                self._closed.set()
                self._event_handler({"type": "__stream_closed__"})
                return
            stdout = self._process.stdout
        try:
            for line in stdout:
                line = line.strip()
                if not line:
                    continue
                if self._terminal_host is not None:
                    try:
                        self._sent_lines.remove(line)
                    except ValueError:
                        pass
                    else:
                        continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, Mapping):
                    self._event_handler(dict(event))
        finally:
            self._closed.set()
            # Wake any turn still waiting on a result event.
            self._event_handler({"type": "__stream_closed__"})

    def _read_stderr(self) -> None:
        if self._process is None:
            return
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


class ClaudeResidentSession:
    """Resident Claude session with the shared provider session contract.

    ``start_or_resume`` / ``send_message`` / ``stream_events`` /
    ``cancel_turn`` / ``session_status`` / ``close`` mirror the Codex and Grok
    residents. The process starts once and is reused for every turn; it is only
    re-launched after a failure, on the next user request.
    """

    provider = "CLAUDE"

    def __init__(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        system_prompt: str,
        session_id: str | None,
        session_observer: Callable[[str], None],
        model: str = "default",
        effort: str = "AUTO",
        extra_arguments: tuple[str, ...] = (),
        turn_timeout_seconds: float = 600.0,
        permission_mcp_config: Path | None = None,
        permission_bridge: Any | None = None,
        permission_ready: Callable[[], bool] | None = None,
        permission_failure: Callable[[], None] | None = None,
        process_factory: Callable[..., ClaudeStreamProcess] | None = None,
        terminal_host: Any | None = None,
        project_id: str = "",
        mode: str = "",
        supervisor_session_id: str = "",
        session_anchor_ref: str = "",
    ) -> None:
        self.permission_mcp_config = permission_mcp_config
        self._supervisor_transport = {
            "terminal_host": terminal_host,
            "project_id": project_id,
            "mode": mode,
            "supervisor_session_id": supervisor_session_id,
            "session_anchor_ref": session_anchor_ref,
        }
        self.permission_bridge = permission_bridge
        # Blocks until the MCP permission server has registered. A turn must
        # not start before the approval path exists.
        self.permission_ready = permission_ready
        self.permission_failure = permission_failure
        self.executable = executable
        self.cwd = cwd
        self._git_trace2 = GitTrace2Observer(cwd)
        self.environment = self._git_trace2.environment(environment)
        leaked = CLAUDE_FORBIDDEN_ENVIRONMENT.intersection(self.environment)
        if leaked:
            # Claude must not be able to read its own approval capability.
            raise ClaudeResidentError("CLAUDE_PERMISSION_TOKEN_LEAKED_TO_PROVIDER")
        self.system_prompt = str(system_prompt)
        self.model = str(model or "default")
        self.effort = str(effort or "AUTO").strip().upper()
        if self.effort not in PROVIDER_EFFORTS:
            raise ClaudeResidentError("CLAUDE_EFFORT_INVALID")
        # Always give a fresh resident an explicit provider session id.  Claude
        # can terminate an input-stream process that has no initial
        # ``--session-id`` before the first user turn, which would publish a
        # dead resident as READY.  A stored id remains a resume coordinate;
        # a generated id starts a new provider conversation.
        self.session_id = session_id or str(uuid4())
        self._resume = bool(session_id)
        self.session_observer = session_observer
        self.extra_arguments = tuple(extra_arguments)
        self.turn_timeout_seconds = float(turn_timeout_seconds)
        self._process_factory = process_factory or ClaudeStreamProcess

        self._process: ClaudeStreamProcess | None = None
        self._state = SESSION_STOPPED
        self._state_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._turn_done = threading.Event()
        self._closed = threading.Event()
        self._turn_deltas: list[str] = []
        self._turn_text: list[str] = []
        self._turn_error: str | None = None
        self._on_delta: Callable[[str], None] | None = None
        self._events: list[dict[str, Any]] = []
        self._greeted = False
        self._launch_count = 0
        self._turn_id: str | None = None
        # Observability for rate-limit telemetry; never used to auto-allow.
        self.last_rate_limit_status: str | None = None
        self.last_rate_limit_info: dict[str, Any] = {}
        self.unknown_rate_limit_statuses: list[str] = []

    # -- shared contract ------------------------------------------------

    @property
    def session_ref(self) -> str:
        return f"claude-code:{self.session_id}" if self.session_id else "claude-code:pending"

    @property
    def launch_count(self) -> int:
        """How many OS processes this session has started (tests assert 1)."""

        return self._launch_count

    def set_permission_requester(
        self,
        requester: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        if self.permission_bridge is None:
            raise ClaudeResidentError("CLAUDE_PERMISSION_BRIDGE_UNAVAILABLE")
        setter = getattr(self.permission_bridge, "set_permission_requester", None)
        if not callable(setter):
            raise ClaudeResidentError("CLAUDE_PERMISSION_REBIND_UNAVAILABLE")
        setter(requester)

    def session_status(self) -> str:
        with self._state_lock:
            state = self._state
        process = self._process
        if (
            state in {SESSION_READY, SESSION_BUSY}
            and process is not None
            and not process.alive
        ):
            return SESSION_FAILED
        return state

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def runtime_observation(self) -> dict[str, Any]:
        quota_state = "UNKNOWN"
        if self.session_status() == SESSION_QUOTA_EXHAUSTED:
            quota_state = "EXHAUSTED"
        elif self.last_rate_limit_status == "allowed_warning":
            quota_state = "WARNING"
        elif self.last_rate_limit_status == "allowed":
            quota_state = "AVAILABLE"
        result = next(
            (event for event in reversed(self._events) if event.get("type") == "result"),
            {},
        )
        usage: dict[str, int | float] = {}
        raw_usage = result.get("usage") if isinstance(result, Mapping) else None
        if isinstance(raw_usage, Mapping):
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ):
                value = raw_usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = value
        if isinstance(result, Mapping):
            for source_key, target_key in (
                ("duration_ms", "duration_ms"),
                ("duration_api_ms", "duration_api_ms"),
                ("total_cost_usd", "cost_usd"),
                ("num_turns", "turn_count"),
            ):
                value = result.get(source_key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[target_key] = value
        windows: list[dict[str, Any]] = []
        utilization = self.last_rate_limit_info.get("utilization")
        if isinstance(utilization, (int, float)) and not isinstance(utilization, bool):
            used_percent = float(utilization)
            if 0 <= used_percent <= 1:
                used_percent *= 100
            window: dict[str, Any] = {
                "name": str(
                    self.last_rate_limit_info.get("rateLimitType") or "RATE_LIMIT"
                ).upper(),
                "used_percent": used_percent,
            }
            reset = self.last_rate_limit_info.get("resetsAt")
            if reset is None:
                reset = self.last_rate_limit_info.get("resetAt")
            if isinstance(reset, (str, int, float)) and not isinstance(reset, bool):
                window["resets_at"] = reset
            windows.append(window)
        quota = {
            "schema": "universe.provider-quota-snapshot.v1",
            "provider": "CLAUDE",
            "source": "rate_limit_event",
            "state": quota_state,
            "windows": windows,
        }
        return {
            "schema": "universe.provider-runtime-observation.v1",
            "provider": self.provider,
            "session_ref": self.session_ref,
            "state": self.session_status(),
            "quota_state": quota_state,
            "rate_limit_status": self.last_rate_limit_status or "UNKNOWN",
            "usage": usage,
            "quota": quota,
        }

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        process = self._process
        if process is None or not process.alive:
            raise ClaudeResidentError("CLAUDE_PROCESS_NOT_ALIVE")
        return process.supervisor_process_identity(endpoint, handshake_token)

    def start_or_resume(self) -> str:
        """Start the resident process, or reuse the live one."""

        if self._process is not None and self._process.alive:
            return self.session_status()
        if self.session_status() == SESSION_QUOTA_EXHAUSTED:
            # Never spin a retry loop against a spent quota.
            raise ClaudeResidentError("CLAUDE_QUOTA_EXHAUSTED")
        self._turn_error = None
        self._set_state(SESSION_CONNECTING)
        try:
            process_arguments: dict[str, Any] = {
                "executable": self.executable,
                "arguments": self._arguments(),
                "cwd": self.cwd,
                "environment": self.environment,
                "event_handler": self._handle_event,
            }
            if self._supervisor_transport["terminal_host"] is not None:
                process_arguments.update(self._supervisor_transport)
            self._process = self._process_factory(**process_arguments)
        except Exception:
            self._abort_permission_path()
            self._set_state(SESSION_FAILED)
            raise
        self._launch_count += 1
        self._set_state(SESSION_READY)
        # ``claude --resume`` can reject a deleted provider-side session after
        # emitting its result event. Wait long enough to observe that event
        # before publishing a resident as READY; a valid stream stays open
        # without requiring a first user turn.
        deadline = time.monotonic() + STARTUP_PROBE_SECONDS
        while self._process is not None and self._process.alive:
            if self._turn_error == "CLAUDE_SESSION_RESUME_NOT_FOUND":
                self._set_state(SESSION_FAILED)
                process, self._process = self._process, None
                if process is not None:
                    process.close()
                raise ClaudeResidentError("CLAUDE_SESSION_RESUME_NOT_FOUND")
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if self._process is not None and not self._process.alive:
            if self._turn_error == "CLAUDE_SESSION_RESUME_NOT_FOUND":
                self._set_state(SESSION_FAILED)
                raise ClaudeResidentError("CLAUDE_SESSION_RESUME_NOT_FOUND")
            self._set_state(SESSION_FAILED)
            raise ClaudeResidentError(
                "CLAUDE_PROCESS_NOT_ALIVE" + self._process.stderr_detail()
            )
        return self.session_status()

    def send_message(self, text: str, on_delta: Callable[[str], None]) -> str:
        if self.session_status() == SESSION_QUOTA_EXHAUSTED:
            # Refuse at the request entry point: a live process must not be
            # used to retry against a spent quota either.
            raise ClaudeResidentError("CLAUDE_QUOTA_EXHAUSTED")
        if not self._turn_lock.acquire(blocking=False):
            # One turn at a time per session.
            raise ClaudeResidentError("CLAUDE_TURN_ALREADY_ACTIVE")
        try:
            if self._closed.is_set():
                raise ClaudeResidentError("CLAUDE_TURN_CANCELLED")
            self.start_or_resume()
            process = self._process
            if self._closed.is_set():
                raise ClaudeResidentError("CLAUDE_TURN_CANCELLED")
            if process is None or not process.alive:
                self._set_state(SESSION_FAILED)
                raise ClaudeResidentError("CLAUDE_PROCESS_UNAVAILABLE")
            self._turn_done.clear()
            self._turn_deltas = []
            self._turn_text = []
            self._turn_error = None
            self._on_delta = on_delta
            self._turn_id = uuid4().hex
            if self.permission_bridge is not None:
                # Bind approvals to this exact turn.
                self.permission_bridge.bind_turn(self._turn_id)
            self._set_state(SESSION_BUSY)
            # Claude's streaming-input SDK starts MCP initialization only after
            # the first user message is written.  Strict MCP config keeps the
            # provider turn fail-closed while the permission server registers;
            # never accept a result until that registration is observed.
            process.send_user_message(self._compose(text))
            if self.permission_ready is not None and not self.permission_ready():
                self._abort_permission_path()
                self._set_state(SESSION_FAILED)
                raise ClaudeResidentError("CLAUDE_PERMISSION_MCP_NOT_REGISTERED")
            if not self._turn_done.wait(self.turn_timeout_seconds):
                self._set_state(SESSION_FAILED)
                raise ClaudeResidentError("CLAUDE_TURN_TIMEOUT")
            if self._turn_error is not None:
                if self.session_status() != SESSION_QUOTA_EXHAUSTED:
                    self._set_state(SESSION_FAILED)
                raise ClaudeResidentError(self._turn_error)
            if self.session_status() == SESSION_BUSY:
                self._set_state(SESSION_READY)
            return "".join(self._turn_text).strip()
        finally:
            self._on_delta = None
            self._turn_lock.release()

    # ``prompt`` keeps the existing AgentSession protocol working.
    def prompt(self, text: str, on_delta: Callable[[str], None]) -> str:
        return self.send_message(text, on_delta)

    def cancel_turn(self) -> None:
        """Release a waiting turn without pretending the work completed."""

        if self._turn_done.is_set():
            return
        self._turn_error = "CLAUDE_TURN_CANCELLED"
        self._turn_done.set()

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        return self._git_trace2.drain_work_statuses()

    def close(self) -> None:
        # Publish cancellation before process teardown can surface as an
        # unavailable/closed-stream error in the active turn.
        self._closed.set()
        self.cancel_turn()
        process, self._process = self._process, None
        if process is not None:
            process.close()
        self._set_state(SESSION_STOPPED)
        self._git_trace2.close()

    def rebind_working_directory(self, cwd: Path) -> str:
        target = cwd.expanduser().resolve(strict=True)
        if not target.is_dir():
            raise ClaudeResidentError("CLAUDE_CWD_INVALID")
        if not self.session_id:
            raise ClaudeResidentError("CLAUDE_CWD_REBIND_SESSION_UNAVAILABLE")
        if not self._turn_lock.acquire(blocking=False):
            raise ClaudeResidentError("CLAUDE_TURN_ALREADY_ACTIVE")
        previous = self.cwd
        try:
            process, self._process = self._process, None
            if process is not None:
                process.close()
            self.cwd = target
            self._resume = True
            self._set_state(SESSION_STOPPED)
            try:
                self.start_or_resume()
            except Exception as error:
                self.cwd = previous
                self._process = None
                self._set_state(SESSION_STOPPED)
                try:
                    self.start_or_resume()
                except Exception as rollback_error:
                    raise ClaudeResidentError(
                        "CLAUDE_CWD_REBIND_ROLLBACK_FAILED"
                    ) from rollback_error
                raise ClaudeResidentError("CLAUDE_CWD_REBIND_FAILED") from error
            return str(target)
        finally:
            self._turn_lock.release()

    # -- internals ------------------------------------------------------

    def _arguments(self) -> tuple[str, ...]:
        arguments = [*CLAUDE_STREAM_ARGUMENTS, "--model", self.model]
        if self.effort != "AUTO":
            arguments.extend(("--effort", self.effort.lower()))
        if self._resume and self.session_id:
            arguments.extend(("--resume", self.session_id))
        elif self.session_id:
            arguments.extend(("--session-id", self.session_id))
        if self.permission_mcp_config is not None:
            # Route every prompt that is not pre-approved to the Universe
            # permission UI. plan mode keeps file edits and file-modifying
            # shell commands off the auto-approve path.
            arguments.extend(
                (
                    "--permission-mode",
                    CLAUDE_PERMISSION_MODE,
                    "--mcp-config",
                    str(self.permission_mcp_config),
                    "--strict-mcp-config",
                    "--permission-prompt-tool",
                    CLAUDE_PERMISSION_PROMPT_TOOL,
                )
            )
        arguments.extend(self.extra_arguments)
        for forbidden in CLAUDE_FORBIDDEN_ARGUMENTS:
            if forbidden in arguments:
                raise ClaudeResidentError("CLAUDE_PERMISSION_BYPASS_FORBIDDEN")
        return tuple(arguments)

    def _compose(self, text: str) -> str:
        """Send the Mode greeting only on a new session or provider swap."""

        if self._greeted or self._resume:
            self._greeted = True
            return str(text)
        self._greeted = True
        return f"{self.system_prompt}\n\n{text}"

    def _set_state(self, state: str) -> None:
        if state not in SESSION_STATES:
            raise ClaudeResidentError(f"CLAUDE_SESSION_STATE_INVALID:{state}")
        with self._state_lock:
            self._state = state

    def _abort_permission_path(self) -> None:
        if self.permission_failure is not None:
            try:
                self.permission_failure()
            except Exception:
                pass
        process, self._process = self._process, None
        if process is not None:
            process.close()

    def _observe_session(self, value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        observed = value.strip()
        if observed == self.session_id:
            return
        self.session_id = observed
        self._resume = True
        self.session_observer(observed)

    def _emit(self, text: str) -> None:
        if not text:
            return
        self._turn_text.append(text)
        self._turn_deltas.append(text)
        if self._on_delta is not None:
            self._on_delta(text)

    def _handle_event(self, event: Mapping[str, Any]) -> None:
        self._events.append(dict(event))
        kind = event.get("type")
        if kind == "__stream_closed__":
            if not self._turn_done.is_set() and self.session_status() == SESSION_BUSY:
                self._turn_error = (
                    "CLAUDE_TURN_CANCELLED"
                    if self._closed.is_set()
                    else "CLAUDE_STREAM_CLOSED"
                )
                self._turn_done.set()
            return
        if kind == "system":
            self._handle_system(event)
            return
        if kind == "rate_limit_event":
            self._handle_rate_limit_telemetry(event)
            return
        if kind == "stream_event":
            self._handle_partial(event)
            return
        if kind == "assistant":
            self._handle_assistant(event)
            return
        if kind == "result":
            self._handle_result(event)

    def _handle_system(self, event: Mapping[str, Any]) -> None:
        subtype = event.get("subtype")
        if subtype == "init":
            self._observe_session(event.get("session_id"))
            return
        if subtype == "api_retry":
            if str(event.get("error") or "") in QUOTA_ERROR_CATEGORIES:
                self._handle_rate_limit(event)

    def _handle_rate_limit_telemetry(self, event: Mapping[str, Any]) -> None:
        """Classify one ``rate_limit_event`` into allowed / spent / unknown."""

        info = event.get("rate_limit_info")
        self.last_rate_limit_info = {
            key: value
            for key in (
                "status",
                "rateLimitType",
                "utilization",
                "surpassedThreshold",
                "resetsAt",
                "resetAt",
            )
            if isinstance(info, Mapping)
            and (value := info.get(key)) is not None
            and isinstance(value, (str, int, float, bool))
        }
        status = str(info.get("status") or "").strip() if isinstance(info, Mapping) else ""
        if status in RATE_LIMIT_ALLOWED_STATUSES:
            self.last_rate_limit_status = status
            return
        if status in RATE_LIMIT_EXHAUSTED_STATUSES:
            self.last_rate_limit_status = status
            self._handle_rate_limit(event)
            return
        # Unknown or missing: record it, do not allow and do not claim the
        # quota is spent. The result event decides this turn.
        self.last_rate_limit_status = RATE_LIMIT_STATUS_UNKNOWN
        self.unknown_rate_limit_statuses.append(status)

    def _handle_rate_limit(self, event: Mapping[str, Any]) -> None:
        self._set_state(SESSION_QUOTA_EXHAUSTED)
        if not self._turn_done.is_set():
            self._turn_error = "CLAUDE_QUOTA_EXHAUSTED"
            self._turn_done.set()

    def _handle_partial(self, event: Mapping[str, Any]) -> None:
        inner = event.get("event")
        if not isinstance(inner, Mapping):
            return
        delta = inner.get("delta")
        if isinstance(delta, Mapping) and delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str):
                self._emit(text)

    def _handle_assistant(self, event: Mapping[str, Any]) -> None:
        # Without --include-partial-messages the assistant text arrives as one
        # complete block; forward it on the same delta path.
        if any(item.get("type") == "stream_event" for item in self._events):
            return
        message = event.get("message")
        if not isinstance(message, Mapping):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    self._emit(text)

    def _handle_result(self, event: Mapping[str, Any]) -> None:
        self._observe_session(event.get("session_id"))
        subtype = str(event.get("subtype") or "")
        if subtype in QUOTA_RESULT_SUBTYPES:
            self._handle_rate_limit(event)
            return
        errors = event.get("errors")
        if (
            subtype == "error_during_execution"
            and isinstance(errors, list)
            and any(
                "No conversation found with session ID:" in str(error)
                for error in errors
            )
        ):
            self._turn_error = "CLAUDE_SESSION_RESUME_NOT_FOUND"
            self._turn_done.set()
            return
        if event.get("is_error") is True or subtype != "success":
            self._turn_error = f"CLAUDE_RESULT_FAILED:{subtype or 'unknown'}"
            self._turn_done.set()
            return
        if not self._turn_text:
            result = event.get("result")
            if isinstance(result, str) and result.strip():
                self._emit(result)
        self._turn_done.set()
