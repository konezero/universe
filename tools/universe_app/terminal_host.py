"""Process-local tabbed CLI terminals for the Universe browser dock."""

from __future__ import annotations

import os
import queue
import secrets
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_profile import resolve_host_tool
from claude_channel_broker import ClaudeChannelBroker, MCP_SERVER_NAME

TERMINAL_SCHEMA = "universe.cli-terminal.v1"
PROVIDER_TOOLS = {
    "GROK": "grok",
    "CODEX": "codex",
    "CLAUDE": "claude",
}


class TerminalHostError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class TerminalSession:
    terminal_id: str
    project_id: str
    mode: str
    provider: str
    supervisor_session_id: str
    cwd: str
    executable: str
    created_at: str
    model_ref: str = ""
    effort: str = "AUTO"
    state: str = "STARTING"
    cols: int = 120
    rows: int = 32
    backend: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: list = field(default_factory=list)
    replay: deque = field(default_factory=lambda: deque(maxlen=80))
    pump_stop: threading.Event = field(default_factory=threading.Event)
    pump_thread: Any = None
    exit_detail: str | None = None
    channel_broker: ClaudeChannelBroker | None = None

    def public(self) -> dict[str, Any]:
        return {
            "schema": TERMINAL_SCHEMA,
            "terminal_id": self.terminal_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "provider": self.provider,
            "model_ref": self.model_ref,
            "effort": self.effort,
            "supervisor_session_id": self.supervisor_session_id,
            "cwd": self.cwd,
            "executable": Path(self.executable).name,
            "state": self.state,
            "cols": self.cols,
            "rows": self.rows,
            "created_at": self.created_at,
            "pid": self.live_pid(),
            "exit_detail": self.exit_detail,
            "automation_transport": (
                "CLAUDE_CODE_CHANNEL" if self.channel_broker is not None else "PTY"
            ),
            "channel_registered": (
                self.channel_broker.registered if self.channel_broker is not None else False
            ),
        }

    def live_pid(self) -> int | None:
        backend = self.backend
        if backend is None:
            return None
        pid = getattr(backend, "pid", None)
        if not (isinstance(pid, int) and pid > 0):
            pty = getattr(backend, "_pty", None)
            pid = getattr(pty, "pid", None)
        return int(pid) if isinstance(pid, int) and pid > 0 else None


class TerminalHost:
    """In-process registry of ConPTY-backed CLI tabs."""

    def __init__(
        self,
        *,
        spawn: Callable[..., Any] | None = None,
    ) -> None:
        from universe_app.session_bus import SessionBus

        self._spawn = spawn or spawn_conpty
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}
        self.bus = SessionBus()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._refresh_session_state(session)
        rows = [item.public() for item in sessions]
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    def get(self, terminal_id: str) -> TerminalSession:
        with self._lock:
            session = self._sessions.get(str(terminal_id or "").strip())
        if session is None:
            raise TerminalHostError("TERMINAL_NOT_FOUND", "terminal session does not exist")
        self._refresh_session_state(session)
        return session

    def create(
        self,
        *,
        project_id: str,
        mode: str,
        cwd: str,
        provider: str = "AUTO",
        model_ref: str = "",
        effort: str = "AUTO",
        supervisor_session_id: str = "",
        resume_session_ref: str = "",
        cols: int = 120,
        rows: int = 32,
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        requested_mode = str(mode or "").strip().upper()
        workdir = str(cwd or "").strip()
        if not project or not requested_mode or not workdir:
            raise TerminalHostError(
                "TERMINAL_COORDINATE_REQUIRED",
                "project_id, mode, and cwd are required",
            )
        root = Path(workdir).expanduser()
        if not root.is_dir():
            raise TerminalHostError("TERMINAL_CWD_INVALID", "cwd must be a directory")
        selected = str(provider or "AUTO").strip().upper()
        executable = resolve_cli_executable(selected)
        resolved_provider = selected if selected != "AUTO" else infer_provider(executable)
        selected_model = str(model_ref or "").strip()
        selected_effort = str(effort or "AUTO").strip().upper() or "AUTO"
        supervisor = str(supervisor_session_id or "").strip()
        terminal_id = "term_" + secrets.token_hex(8)
        channel_broker: ClaudeChannelBroker | None = None
        channel_config: Path | None = None
        if resolved_provider == "CLAUDE":
            channel_broker = ClaudeChannelBroker(
                terminal_id=terminal_id,
                project_id=project,
                mode=requested_mode,
                provider=resolved_provider,
                supervisor_session_id=supervisor,
            ).start()
            config_root = Path(tempfile.mkdtemp(prefix="universe-claude-channel-"))
            channel_config = channel_broker.write_mcp_config(config_root / "mcp.json")
        argv = startup_argv(
            resolved_provider,
            resume_session_ref,
            model_ref=selected_model,
            effort=selected_effort,
            claude_channel_mcp_config=str(channel_config) if channel_config else "",
        )
        session = TerminalSession(
            terminal_id=terminal_id,
            project_id=project,
            mode=requested_mode,
            provider=resolved_provider,
            supervisor_session_id=supervisor,
            cwd=str(root.resolve()),
            executable=executable,
            created_at=_now(),
            model_ref=selected_model,
            effort=selected_effort,
            cols=max(80, int(cols or 120)),
            rows=max(24, int(rows or 32)),
            channel_broker=channel_broker,
        )
        child_environment = {
            "UNIVERSE_PROJECT_ID": project,
            "UNIVERSE_MODE": requested_mode,
            "UNIVERSE_PROVIDER": resolved_provider,
            "UNIVERSE_MODEL_REF": selected_model,
            "UNIVERSE_EFFORT": selected_effort,
            "UNIVERSE_SUPERVISOR_SESSION_ID": supervisor,
            "UNIVERSE_TERMINAL_ID": terminal_id,
        }
        try:
            session.backend = self._spawn(
                executable,
                session.cwd,
                session.cols,
                session.rows,
                argv,
                child_environment,
            )
        except Exception as error:  # noqa: BLE001 - surface spawn failure
            if channel_broker is not None:
                channel_broker.close()
            raise TerminalHostError(
                "TERMINAL_SPAWN_FAILED",
                str(error) or "failed to start CLI",
            ) from error
        session.state = "LIVE"
        with self._lock:
            self._sessions[terminal_id] = session
        return session.public()

    def close(self, terminal_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        session.pump_stop.set()
        backend = session.backend
        session.state = "CLOSED"
        session.backend = None
        with session.lock:
            waiters = list(session.subscribers)
            session.subscribers.clear()
        for waiter in waiters:
            try:
                waiter.put_nowait(None)
            except Exception:
                pass
        if session.pump_thread is not None:
            session.pump_thread.join(timeout=1)
            session.pump_thread = None
        with self._lock:
            self._sessions.pop(session.terminal_id, None)
        self.bus.drop_terminal(session.terminal_id)
        closer = getattr(backend, "close", None)
        if callable(closer):
            closer()
        if session.channel_broker is not None:
            session.channel_broker.close()
        return {"status": "TERMINAL_CLOSED", "terminal_id": session.terminal_id}

    def write(self, terminal_id: str, data: bytes) -> None:
        session = self.get(terminal_id)
        backend = session.backend
        if backend is None or session.state != "LIVE":
            raise TerminalHostError("TERMINAL_NOT_LIVE", "terminal is not live")
        backend.write(data)

    def emit_output(self, terminal_id: str, data: bytes) -> None:
        """Fan out display-only bytes without sending them to the CLI stdin."""

        session = self.get(terminal_id)
        if session.state != "LIVE":
            raise TerminalHostError("TERMINAL_NOT_LIVE", "terminal is not live")
        with session.lock:
            session.replay.append(bytes(data))
            waiters = list(session.subscribers)
        for waiter in waiters:
            try:
                waiter.put_nowait(bytes(data))
            except queue.Full:
                try:
                    waiter.get_nowait()
                except queue.Empty:
                    pass
                try:
                    waiter.put_nowait(bytes(data))
                except queue.Full:
                    pass

    def push_channel(self, terminal_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        session = self.get(terminal_id)
        broker = session.channel_broker
        if broker is None:
            raise TerminalHostError(
                "TERMINAL_CHANNEL_UNAVAILABLE",
                "terminal has no Claude Code channel bridge",
            )
        try:
            return broker.push(payload)
        except Exception as error:  # noqa: BLE001 - preserve terminal error contract
            raise TerminalHostError("TERMINAL_CHANNEL_UNAVAILABLE", str(error)) from error

    def channel_state(self, terminal_id: str) -> str:
        session = self.get(terminal_id)
        broker = session.channel_broker
        if broker is None:
            return "UNAVAILABLE"
        return "READY" if broker.registered else "PENDING"

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        session = self.get(terminal_id)
        session.cols = max(80, int(cols or session.cols))
        session.rows = max(24, int(rows or session.rows))
        backend = session.backend
        resizer = getattr(backend, "resize", None)
        if callable(resizer):
            resizer(session.cols, session.rows)

    def read(self, terminal_id: str, timeout: float = 0.2) -> bytes:
        session = self.get(terminal_id)
        backend = session.backend
        if backend is None:
            return b""
        return backend.read(timeout)

    def find_live(
        self,
        *,
        project_id: str,
        mode: str,
        provider: str = "",
        supervisor_session_id: str = "",
    ) -> dict[str, Any] | None:
        wanted_project = str(project_id or "").strip()
        wanted_mode = str(mode or "").strip().upper()
        wanted_provider = str(provider or "").strip().upper()
        wanted_supervisor = str(supervisor_session_id or "").strip()
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._refresh_session_state(session)
        rows = [
            item
            for item in sessions
            if item.state == "LIVE"
            and item.project_id == wanted_project
            and item.mode == wanted_mode
            and (
                not wanted_provider
                or wanted_provider == "AUTO"
                or item.provider == wanted_provider
            )
            and (
                not wanted_supervisor
                or item.supervisor_session_id == wanted_supervisor
            )
        ]
        if not rows:
            return None
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows[0].public()

    @staticmethod
    def _backend_is_alive(backend: Any) -> bool | None:
        checker = getattr(backend, "is_alive", None)
        if not callable(checker):
            checker = getattr(backend, "isalive", None)
        if not callable(checker):
            return None
        try:
            return bool(checker())
        except Exception:
            return None

    def _refresh_session_state(self, session: TerminalSession) -> None:
        if session.state != "LIVE":
            return
        backend = session.backend
        if backend is None or self._backend_is_alive(backend) is not False:
            return
        try:
            tail = backend.read(0)
        except Exception:
            tail = b""
        if tail:
            with session.lock:
                session.replay.append(tail)
        self._mark_backend_exit(session)

    def _mark_backend_exit(
        self,
        session: TerminalSession,
        detail: str = "",
    ) -> None:
        with session.lock:
            if session.state != "LIVE":
                return
            chunks = b"".join(session.replay)
            diagnostic = chunks[-4096:].decode("utf-8", errors="replace").strip()
            session.state = "FAILED"
            session.exit_detail = diagnostic or detail or "CLI process exited"
            waiters = list(session.subscribers)
        for waiter in waiters:
            try:
                waiter.put_nowait(None)
            except queue.Full:
                pass

    def bus_directory(self) -> dict[str, Any]:
        return self.bus.directory(self)

    def bus_post(self, value: dict[str, Any] | None) -> dict[str, Any]:
        return self.bus.post(self, value)

    def bus_inbox(self, **kwargs: Any) -> dict[str, Any]:
        return self.bus.inbox(self, **kwargs)

    def bus_ack(self, message_id: str, terminal_id: str) -> dict[str, Any]:
        return self.bus.ack(message_id, terminal_id)

    def bus_unread(self) -> dict[str, Any]:
        return {
            "schema": "universe.session-bus.v1",
            "status": "OK",
            "counts": self.bus.unread_map(),
        }

    def subscribe(self, terminal_id: str) -> queue.Queue:
        session = self.get(terminal_id)
        waiter: queue.Queue = queue.Queue(maxsize=256)
        with session.lock:
            if session.state != "LIVE":
                waiter.put_nowait(None)
                return waiter
            # A browser can attach after the provider has already rendered a
            # prompt or response (for example after a refresh or service
            # restart).  Replay the bounded PTY tail before joining the live
            # fan-out so xterm paints the existing screen without requiring a
            # new keypress.  Clear the new emulator first; the PTY tail may
            # contain cursor/color escapes but must not inherit stale cells.
            replay = b"".join(session.replay)
            if replay:
                waiter.put_nowait(b"\x1b[2J\x1b[H" + replay)
            session.subscribers.append(waiter)
        self._ensure_pump(session)
        return waiter

    def unsubscribe(self, terminal_id: str, waiter: queue.Queue) -> None:
        try:
            session = self.get(terminal_id)
        except TerminalHostError:
            return
        with session.lock:
            session.subscribers = [
                item for item in session.subscribers if item is not waiter
            ]

    def _ensure_pump(self, session: TerminalSession) -> None:
        with session.lock:
            if session.pump_thread is not None and session.pump_thread.is_alive():
                return
            session.pump_stop.clear()
            thread = threading.Thread(
                target=self._pump_session,
                args=(session,),
                name=f"term-fanout-{session.terminal_id}",
                daemon=True,
            )
            session.pump_thread = thread
        thread.start()

    def _pump_session(self, session: TerminalSession) -> None:
        while not session.pump_stop.is_set() and session.state == "LIVE":
            backend = session.backend
            if backend is None:
                break
            try:
                chunk = backend.read(0.2)
            except Exception as error:
                self._mark_backend_exit(session, str(error))
                break
            if not chunk:
                if self._backend_is_alive(backend) is False:
                    self._mark_backend_exit(session)
                    break
                continue
            with session.lock:
                session.replay.append(chunk)
                waiters = list(session.subscribers)
            for waiter in waiters:
                try:
                    waiter.put_nowait(chunk)
                except queue.Full:
                    try:
                        waiter.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        waiter.put_nowait(chunk)
                    except queue.Full:
                        pass


def resume_argv(provider: str, resume_session_ref: str) -> list[str]:
    name = str(provider or "").strip().upper()
    ref = str(resume_session_ref or "").strip()
    if not ref or ref.upper() == "UNKNOWN":
        return []
    prefixes = {
        "CODEX": ("codex-app-server:", "codex-app:", "codex:"),
        "CLAUDE": ("claude-code:",),
        "GROK": ("grok-acp:", "grok-cli:"),
    }
    lowered = ref.lower()
    for owner, known_prefixes in prefixes.items():
        for prefix in known_prefixes:
            if lowered.startswith(prefix):
                if owner != name:
                    raise TerminalHostError(
                        "TERMINAL_RESUME_PROVIDER_MISMATCH",
                        "resume session provider does not match terminal provider",
                    )
                ref = ref[len(prefix) :].strip()
                lowered = ref.lower()
                break
    upper = ref.upper()
    if (
        not ref
        or lowered.startswith(("session_", "session-anchor_", "session_anchor_", "term_", "pty:", "cli-terminal:"))
        or upper.startswith(("UNIVERSE-", "MASTER-CURRENT-", "CONDUCTOR-CURRENT-"))
    ):
        raise TerminalHostError(
            "TERMINAL_RESUME_REF_INVALID",
            "resume_session_ref must be a provider-owned session id",
        )
    if name == "GROK":
        return ["--resume", ref]
    if name == "CLAUDE":
        return ["--resume", ref]
    if name == "CODEX":
        return ["resume", ref]
    return []


def startup_argv(
    provider: str,
    resume_session_ref: str,
    *,
    model_ref: str = "",
    effort: str = "AUTO",
    claude_channel_mcp_config: str = "",
) -> list[str]:
    """Build one interactive CLI command without changing its supervisor anchor.

    Model and effort are per-terminal launch preferences.  They are not written
    back into the project default, and a later provider session id only enriches
    the supervisor session created for this PTY.
    """
    name = str(provider or "").strip().upper()
    model = str(model_ref or "").strip()
    selected_effort = str(effort or "AUTO").strip().upper() or "AUTO"
    argv: list[str] = []
    if name in {"GROK", "CLAUDE", "CODEX"} and model:
        argv.extend(("--model", model))
    if selected_effort != "AUTO":
        if name == "GROK":
            argv.extend(("--reasoning-effort", selected_effort.lower()))
        elif name == "CLAUDE":
            argv.extend(("--effort", selected_effort.lower()))
        elif name == "CODEX":
            argv.extend(("--config", f"model_reasoning_effort={selected_effort.lower()}"))
    argv.extend(resume_argv(name, resume_session_ref))
    if name == "CLAUDE":
        argv.append("--dangerously-skip-permissions")
        channel_config = str(claude_channel_mcp_config or "").strip()
        if channel_config:
            argv.extend(("--mcp-config", channel_config))
    return argv


def infer_provider(executable: str) -> str:
    name = Path(executable).name.lower()
    if name.startswith("codex"):
        return "CODEX"
    if name.startswith("claude"):
        return "CLAUDE"
    if name.startswith("grok"):
        return "GROK"
    return "AUTO"


def resolve_cli_executable(provider: str) -> str:
    selected = str(provider or "").strip().upper()
    mapped = PROVIDER_TOOLS.get(selected)
    if not mapped:
        raise TerminalHostError(
            "TERMINAL_PROVIDER_REQUIRED",
            "choose CODEX, CLAUDE, or GROK for this terminal",
        )
    resolved = resolve_host_tool(mapped)
    executable = getattr(resolved, "executable", None) if resolved is not None else None
    if executable:
        return str(executable)
    raise TerminalHostError(
        "CLI_EXECUTABLE_UNAVAILABLE",
        f"{selected} CLI is not available on this host",
    )


def spawn_conpty(
    executable: str,
    cwd: str,
    cols: int,
    rows: int,
    argv: list[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> Any:
    if os.name == "nt":
        from universe_app.windows_conpty import WindowsConPTY

        return WindowsConPTY(
            executable,
            cwd,
            cols,
            rows,
            argv=argv or [],
            environment=environment,
        )
    raise TerminalHostError("PTY_UNSUPPORTED", "this Host does not expose a PTY")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
