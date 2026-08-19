"""Process-local tabbed CLI terminals for the Universe browser dock."""

from __future__ import annotations

import os
import queue
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_profile import resolve_host_tool

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
    cwd: str
    executable: str
    created_at: str
    state: str = "STARTING"
    cols: int = 120
    rows: int = 32
    backend: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: list = field(default_factory=list)
    replay: deque = field(default_factory=lambda: deque(maxlen=80))
    pump_stop: threading.Event = field(default_factory=threading.Event)
    pump_thread: Any = None

    def public(self) -> dict[str, Any]:
        return {
            "schema": TERMINAL_SCHEMA,
            "terminal_id": self.terminal_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "provider": self.provider,
            "cwd": self.cwd,
            "executable": Path(self.executable).name,
            "state": self.state,
            "cols": self.cols,
            "rows": self.rows,
            "created_at": self.created_at,
            "pid": self.live_pid(),
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
        spawn: Callable[[str, str, int, int], Any] | None = None,
    ) -> None:
        from universe_app.session_bus import SessionBus

        self._spawn = spawn or spawn_conpty
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}
        self.bus = SessionBus()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item.public() for item in self._sessions.values()]
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    def get(self, terminal_id: str) -> TerminalSession:
        with self._lock:
            session = self._sessions.get(str(terminal_id or "").strip())
        if session is None:
            raise TerminalHostError("TERMINAL_NOT_FOUND", "terminal session does not exist")
        return session

    def create(
        self,
        *,
        project_id: str,
        mode: str,
        cwd: str,
        provider: str = "AUTO",
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
        argv = resume_argv(resolved_provider, resume_session_ref)
        terminal_id = "term_" + secrets.token_hex(8)
        session = TerminalSession(
            terminal_id=terminal_id,
            project_id=project,
            mode=requested_mode,
            provider=resolved_provider,
            cwd=str(root.resolve()),
            executable=executable,
            created_at=_now(),
            cols=max(80, int(cols or 120)),
            rows=max(24, int(rows or 32)),
        )
        try:
            session.backend = self._spawn(
                executable, session.cwd, session.cols, session.rows, argv
            )
        except Exception as error:  # noqa: BLE001 - surface spawn failure
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
        return {"status": "TERMINAL_CLOSED", "terminal_id": session.terminal_id}

    def write(self, terminal_id: str, data: bytes) -> None:
        session = self.get(terminal_id)
        backend = session.backend
        if backend is None or session.state != "LIVE":
            raise TerminalHostError("TERMINAL_NOT_LIVE", "terminal is not live")
        backend.write(data)

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
    ) -> dict[str, Any] | None:
        wanted_project = str(project_id or "").strip()
        wanted_mode = str(mode or "").strip().upper()
        wanted_provider = str(provider or "").strip().upper()
        with self._lock:
            rows = [
                item
                for item in self._sessions.values()
                if item.state == "LIVE"
                and item.project_id == wanted_project
                and item.mode == wanted_mode
                and (
                    not wanted_provider
                    or wanted_provider == "AUTO"
                    or item.provider == wanted_provider
                )
            ]
        if not rows:
            return None
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows[0].public()

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
            session.subscribers.append(waiter)
        # Do not dump recent chunks into a new client. CLI TUIs address the
        # cursor; a partial replay plus a live redraw garbles the screen.
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
            except Exception:
                break
            if not chunk:
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
    ref = str(resume_session_ref or "").strip()
    if not ref or ref.upper() == "UNKNOWN":
        return []
    name = str(provider or "").strip().upper()
    if name == "GROK":
        return ["--resume", ref]
    if name == "CLAUDE":
        return ["--resume", ref]
    if name == "CODEX":
        return ["resume", ref]
    return []


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
    order = []
    mapped = PROVIDER_TOOLS.get(str(provider or "").upper())
    if mapped:
        order.append(mapped)
    order.extend(["grok", "codex", "claude"])
    seen: set[str] = set()
    for tool in order:
        if tool in seen:
            continue
        seen.add(tool)
        resolved = resolve_host_tool(tool)
        executable = getattr(resolved, "executable", None) if resolved is not None else None
        if executable:
            return str(executable)
    comspec = str(os.environ.get("COMSPEC") or "").strip()
    if comspec and Path(comspec).is_file():
        return comspec
    raise TerminalHostError("CLI_EXECUTABLE_UNAVAILABLE", "no host CLI is available")


def spawn_conpty(
    executable: str,
    cwd: str,
    cols: int,
    rows: int,
    argv: list[str] | None = None,
) -> Any:
    if os.name == "nt":
        from universe_app.windows_conpty import WindowsConPTY

        return WindowsConPTY(executable, cwd, cols, rows, argv=argv or [])
    raise TerminalHostError("PTY_UNSUPPORTED", "this Host does not expose a PTY")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
