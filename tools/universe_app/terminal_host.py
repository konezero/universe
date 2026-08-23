"""Process-local tabbed CLI terminals for the Universe browser dock."""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import re
import secrets
import uuid
import threading
import time
from collections import deque
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_profile import resolve_host_tool
from claude_channel_broker import (
    ClaudeChannelBroker,
    MCP_SERVER_NAME,
    ensure_local_channel_server_registered,
)

TERMINAL_SCHEMA = "universe.cli-terminal.v1"
TERMINAL_AUDIT_SCHEMA = "universe.terminal-audit-event.v1"
PROVIDER_TOOLS = {
    "GROK": "grok",
    "CODEX": "codex",
    "CLAUDE": "claude",
}

# Matches ANSI CSI (`ESC[...letter`) and OSC (`ESC]...BEL`/`ESC]...ESC\`)
# sequences so terminal output can be checked as plain text.
_ANSI_ESCAPE_RE = re.compile(rb"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
# The CLI renders each word of its --dangerously-load-development-channels
# confirmation prompt with its own cursor-column jump instead of a literal
# space, so after stripping escapes the words land back to back with no
# separator at all - hence no `\s*` between "local" and "development" below.
_DEV_CHANNEL_PROMPT_RE = re.compile(rb"localdevelopment", re.IGNORECASE)


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


class TerminalAuditStore:
    """Append-only, content-free lifecycle evidence for PTY attribution."""

    def __init__(self, database_path: Path | str | None) -> None:
        self._path = Path(database_path).resolve() if database_path else None
        self._lock = threading.Lock()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS terminal_audit_event (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        terminal_id TEXT NOT NULL DEFAULT '',
                        pid INTEGER,
                        project_id TEXT NOT NULL DEFAULT '',
                        mode TEXT NOT NULL DEFAULT '',
                        provider TEXT NOT NULL DEFAULT '',
                        supervisor_session_id TEXT NOT NULL DEFAULT '',
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'UNKNOWN',
                        access_surface TEXT NOT NULL DEFAULT 'UNKNOWN',
                        request_id TEXT NOT NULL DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        occurred_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS terminal_audit_event_terminal_time
                    ON terminal_audit_event(terminal_id, event_id DESC);
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def append(
        self,
        *,
        event_type: str,
        terminal: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if self._path is None:
            return
        row = terminal if isinstance(terminal, Mapping) else {}
        audit = context if isinstance(context, Mapping) else {}
        pid = row.get("pid")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO terminal_audit_event(
                    terminal_id, pid, project_id, mode, provider,
                    supervisor_session_id, event_type, source, access_surface,
                    request_id, details_json, occurred_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(row.get("terminal_id") or ""),
                    int(pid) if isinstance(pid, int) and pid > 0 else None,
                    str(row.get("project_id") or ""),
                    str(row.get("mode") or ""),
                    str(row.get("provider") or ""),
                    str(row.get("supervisor_session_id") or ""),
                    str(event_type or "UNKNOWN").strip().upper(),
                    str(audit.get("source") or "UNKNOWN")[:80],
                    str(audit.get("access_surface") or "UNKNOWN")[:80],
                    str(audit.get("request_id") or "")[:120],
                    json.dumps(dict(details or {}), sort_keys=True, separators=(",", ":")),
                    _now(),
                ),
            )

    def list(self, *, terminal_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if self._path is None:
            return []
        bounded = max(1, min(int(limit or 200), 1000))
        with self._lock, self._connection() as connection:
            if terminal_id:
                rows = connection.execute(
                    """
                    SELECT * FROM terminal_audit_event
                    WHERE terminal_id = ? ORDER BY event_id DESC LIMIT ?
                    """,
                    (terminal_id, bounded),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM terminal_audit_event ORDER BY event_id DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["schema"] = TERMINAL_AUDIT_SCHEMA
            item["details"] = json.loads(item.pop("details_json") or "{}")
            result.append(item)
        return result


class TerminalHost:
    """In-process registry of ConPTY-backed CLI tabs."""

    def __init__(
        self,
        *,
        spawn: Callable[..., Any] | None = None,
        database_path: Path | str | None = None,
        audit_database_path: Path | str | None = None,
    ) -> None:
        from universe_app.session_bus import SessionBus

        self._spawn = spawn or spawn_conpty
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}
        self.bus = SessionBus(database_path=database_path)
        self.audit = TerminalAuditStore(audit_database_path or database_path)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._refresh_session_state(session)
        rows = [item.public() for item in sessions]
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    def audit_events(self, *, terminal_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        return self.audit.list(terminal_id=terminal_id, limit=limit)

    def record_audit_event(
        self,
        event_type: str,
        *,
        terminal: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.audit.append(
            event_type=event_type, terminal=terminal, context=context, details=details
        )

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
        audit_context: Mapping[str, Any] | None = None,
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
        if resolved_provider == "CLAUDE":
            # The --channels allowlist Claude Code checks at startup only
            # recognizes MCP servers that are persistently registered
            # (enterprise/user/project/local scope) - a one-off --mcp-config
            # file is invisible to it. Register the (stable, secret-free)
            # server entry once per project instead; each session's actual
            # broker connection is discovered by the spawned MCP child via
            # UNIVERSE_TERMINAL_ID, not via this registration.
            ensure_local_channel_server_registered(str(root.resolve()))
            channel_broker = ClaudeChannelBroker(
                terminal_id=terminal_id,
                project_id=project,
                mode=requested_mode,
                provider=resolved_provider,
                supervisor_session_id=supervisor,
            ).start()
        fresh_claude_session_id = (
            str(uuid.uuid4())
            if resolved_provider == "CLAUDE" and not str(resume_session_ref or "").strip()
            else ""
        )
        argv = startup_argv(
            resolved_provider,
            resume_session_ref,
            model_ref=selected_model,
            effort=selected_effort,
            claude_session_id=fresh_claude_session_id,
            claude_channel_enabled=channel_broker is not None,
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
        created = session.public()
        self.record_audit_event(
            "TERMINAL_CREATED", terminal=created, context=audit_context
        )
        return created

    def close(
        self, terminal_id: str, *, audit_context: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        session = self.get(terminal_id)
        terminal = session.public()
        self.record_audit_event(
            "CLOSE_REQUESTED", terminal=terminal, context=audit_context
        )
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
        self.record_audit_event(
            "TERMINAL_CLOSED", terminal=terminal, context=audit_context
        )
        return {"status": "TERMINAL_CLOSED", "terminal_id": session.terminal_id}

    def write(
        self,
        terminal_id: str,
        data: bytes,
        *,
        audit_context: Mapping[str, Any] | None = None,
    ) -> None:
        session = self.get(terminal_id)
        backend = session.backend
        if backend is None or session.state != "LIVE":
            raise TerminalHostError("TERMINAL_NOT_LIVE", "terminal is not live")
        backend.write(data)
        controls = _input_control_metadata(data)
        if controls:
            self.record_audit_event(
                "INPUT_CONTROL_WRITTEN",
                terminal=session.public(),
                context=audit_context,
                details=controls,
            )

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
        self.record_audit_event(
            "BACKEND_EXITED",
            terminal=session.public(),
            context={"source": "PTY_MONITOR", "access_surface": "SUPERVISOR"},
            details={
                "diagnostic_present": bool(diagnostic),
                "diagnostic_chars": len(diagnostic),
            },
        )
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
        # --dangerously-load-development-channels gates the MCP channel server
        # behind an interactive "Yes, I am using this for local development"
        # confirmation menu (already-highlighted default, Enter to confirm).
        # No human is at the keyboard for a PTY spawned by the UI, so without
        # this the channel never loads and the session eventually fails on an
        # unrelated-looking error. Watch the CLI's own output for that prompt
        # (rather than guessing a delay) and confirm it the moment it renders.
        awaiting_channel_confirm = session.channel_broker is not None
        channel_confirm_tail = b""
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
            if awaiting_channel_confirm:
                # Search the full accumulated buffer *before* capping it - the
                # confirmation screen is one large write (box borders, ANSI
                # color codes, the warning paragraph, ...) and the phrase can
                # sit well before the end of it, so trimming first would cut
                # the match off.
                channel_confirm_tail += chunk
                if _DEV_CHANNEL_PROMPT_RE.search(_ANSI_ESCAPE_RE.sub(b"", channel_confirm_tail)):
                    awaiting_channel_confirm = False
                    try:
                        backend.write(b"\r")
                        self.record_audit_event(
                            "INPUT_CONTROL_WRITTEN",
                            terminal=session.public(),
                            context={
                                "source": "SUPERVISOR_AUTO_CONFIRM",
                                "access_surface": "SUPERVISOR",
                            },
                            details=_input_control_metadata(b"\r"),
                        )
                    except Exception:  # noqa: BLE001 - best effort
                        pass
                elif len(channel_confirm_tail) > 4096:
                    channel_confirm_tail = channel_confirm_tail[-64:]
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


def _input_control_metadata(data: bytes) -> dict[str, Any]:
    raw = bytes(data or b"")
    names = []
    for value, name in (
        (3, "CTRL_C"),
        (4, "CTRL_D"),
        (26, "CTRL_Z"),
        (27, "ESCAPE"),
        (13, "CARRIAGE_RETURN"),
        (10, "LINE_FEED"),
    ):
        if value in raw:
            names.append(name)
    if not names:
        return {}
    return {
        "byte_count": len(raw),
        "control_classes": names,
        "content_persisted": False,
    }


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
    claude_session_id: str = "",
    claude_channel_enabled: bool = False,
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
        session_id = str(claude_session_id or "").strip()
        if session_id:
            argv.extend(("--session-id", session_id))
        argv.append("--dangerously-skip-permissions")
        if claude_channel_enabled:
            # Claude Code channels are a preview feature restricted to MCP
            # servers already registered in persistent (not --mcp-config)
            # scope - see ensure_local_channel_server_registered. This flag
            # opts that registered server into channel/push privileges for
            # this one launch and triggers its confirmation menu (auto
            # confirmed in _pump_session once it renders).
            argv.extend(("--dangerously-load-development-channels", f"server:{MCP_SERVER_NAME}"))
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
