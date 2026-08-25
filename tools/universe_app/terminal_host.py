"""Process-local tabbed CLI terminals for the Universe browser dock."""

from __future__ import annotations

import base64
import codecs
import json
import os
import queue
import sqlite3
import re
import secrets
import uuid
import threading
import time
import unicodedata
from collections import deque
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_profile import resolve_host_tool
from universe_app.managed_shell import (
    CLI_STARTING,
    DEFAULT_INTERRUPT_GRACE_SECONDS,
    HOOK_TIMEOUT,
    PROCESS_INSPECTION_UNAVAILABLE,
    PTY_RESPONSIVENESS_UNKNOWN,
    AttachEvidence,
    ManagedShell,
    ProcessIdentity,
    ShellObservation,
    host_process_probes,
    managed_shell_cmdline,
    observe_process_tree,
    plan_hook_timeout_recovery,
)


MANAGED_SAMPLE_INTERVAL_SECONDS = 5.0


def resolve_shell_identity(pid: int | None) -> ProcessIdentity | None:
    """Pair a spawned shell PID with its OS start time.

    Returns None when the Host cannot inspect processes, so the caller reports
    PROCESS_INSPECTION_UNAVAILABLE instead of trusting a bare PID.
    """

    if not (isinstance(pid, int) and pid > 0):
        return None
    probes = host_process_probes()
    if probes is None:
        return None
    started = probes["start_time_of"](pid)
    if started is None:
        return None
    return ProcessIdentity(pid=pid, started_at=float(started))
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
SESSION_INBOX_CLI = Path(__file__).resolve().parents[1] / "universe_session_inbox.py"
TERMINAL_HISTORY_MAX_BYTES = 4 * 1024 * 1024
TERMINAL_HISTORY_CHUNK_BYTES = 32 * 1024
TERMINAL_HISTORY_PAGE_LIMIT = 100
TERMINAL_HISTORY_PAGE_MAX_BYTES = 256 * 1024

# Matches ANSI CSI (`ESC[...letter`) and OSC (`ESC]...BEL`/`ESC]...ESC\`)
# sequences so terminal output can be checked as plain text.
_ANSI_ESCAPE_RE = re.compile(rb"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
# The CLI renders each word of its --dangerously-load-development-channels
# confirmation prompt with its own cursor-column jump instead of a literal
# space, so after stripping escapes the words land back to back with no
# separator at all - hence no `\s*` between "local" and "development" below.
_DEV_CHANNEL_PROMPT_RE = re.compile(rb"localdevelopment", re.IGNORECASE)


class TerminalScreenProjection:
    """Small VT screen model used only to create bounded reconnect snapshots."""

    def __init__(self, cols: int, rows: int) -> None:
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.grid = self._blank_grid()
        self.cursor_x = 0
        self.cursor_y = 0
        self.saved_cursor = (0, 0)
        self._main_state: tuple[list[list[str]], int, int] | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._escape_mode = ""
        self._escape_buffer = ""

    def _blank_grid(self) -> list[list[str]]:
        return [[" "] * self.cols for _ in range(self.rows)]

    def _scroll_up(self, count: int = 1) -> None:
        for _index in range(max(1, count)):
            self.grid.pop(0)
            self.grid.append([" "] * self.cols)

    def _scroll_down(self, count: int = 1) -> None:
        for _index in range(max(1, count)):
            self.grid.pop()
            self.grid.insert(0, [" "] * self.cols)

    def _linefeed(self) -> None:
        if self.cursor_y >= self.rows - 1:
            self._scroll_up()
        else:
            self.cursor_y += 1

    def _put(self, char: str) -> None:
        if not char or ord(char) < 0x20:
            return
        if unicodedata.combining(char):
            if self.cursor_x > 0:
                self.grid[self.cursor_y][self.cursor_x - 1] += char
            return
        width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if self.cursor_x >= self.cols or (width == 2 and self.cursor_x == self.cols - 1):
            self.cursor_x = 0
            self._linefeed()
        row = self.grid[self.cursor_y]
        if row[self.cursor_x] == "" and self.cursor_x > 0:
            row[self.cursor_x - 1] = " "
        if self.cursor_x + 1 < self.cols and row[self.cursor_x + 1] == "":
            row[self.cursor_x + 1] = " "
        row[self.cursor_x] = char
        if width == 2 and self.cursor_x + 1 < self.cols:
            self.grid[self.cursor_y][self.cursor_x + 1] = ""
        self.cursor_x += width

    @staticmethod
    def _params(raw: str) -> tuple[bool, list[int]]:
        private = raw.startswith("?")
        body = raw[1:] if private else raw
        values = []
        for token in body.split(";") if body else []:
            try:
                values.append(int(token or "0"))
            except ValueError:
                values.append(0)
        return private, values

    @staticmethod
    def _value(values: list[int], index: int, default: int = 1) -> int:
        if index >= len(values) or values[index] == 0:
            return default
        return values[index]

    def _clear_display(self, mode: int) -> None:
        if mode in {2, 3}:
            self.grid = self._blank_grid()
            return
        if mode == 1:
            for row in range(self.cursor_y):
                self.grid[row] = [" "] * self.cols
            self.grid[self.cursor_y][: self.cursor_x + 1] = [" "] * (self.cursor_x + 1)
            return
        self.grid[self.cursor_y][self.cursor_x :] = [" "] * (self.cols - self.cursor_x)
        for row in range(self.cursor_y + 1, self.rows):
            self.grid[row] = [" "] * self.cols

    def _clear_line(self, mode: int) -> None:
        if mode == 2:
            self.grid[self.cursor_y] = [" "] * self.cols
        elif mode == 1:
            self.grid[self.cursor_y][: self.cursor_x + 1] = [" "] * (self.cursor_x + 1)
        else:
            self.grid[self.cursor_y][self.cursor_x :] = [" "] * (self.cols - self.cursor_x)

    def _alternate_screen(self, enabled: bool) -> None:
        if enabled:
            if self._main_state is None:
                self._main_state = ([row[:] for row in self.grid], self.cursor_x, self.cursor_y)
            self.grid = self._blank_grid()
            self.cursor_x = 0
            self.cursor_y = 0
        elif self._main_state is not None:
            self.grid, self.cursor_x, self.cursor_y = self._main_state
            self._main_state = None

    def _csi(self, raw: str, final: str) -> None:
        private, values = self._params(raw)
        amount = self._value(values, 0)
        if final in {"H", "f"}:
            self.cursor_y = min(self.rows - 1, self._value(values, 0) - 1)
            self.cursor_x = min(self.cols - 1, self._value(values, 1) - 1)
        elif final == "A":
            self.cursor_y = max(0, self.cursor_y - amount)
        elif final == "B":
            self.cursor_y = min(self.rows - 1, self.cursor_y + amount)
        elif final == "C":
            self.cursor_x = min(self.cols - 1, self.cursor_x + amount)
        elif final == "D":
            self.cursor_x = max(0, self.cursor_x - amount)
        elif final == "E":
            self.cursor_y = min(self.rows - 1, self.cursor_y + amount)
            self.cursor_x = 0
        elif final == "F":
            self.cursor_y = max(0, self.cursor_y - amount)
            self.cursor_x = 0
        elif final == "G":
            self.cursor_x = min(self.cols - 1, amount - 1)
        elif final == "d":
            self.cursor_y = min(self.rows - 1, amount - 1)
        elif final == "J":
            self._clear_display(values[0] if values else 0)
        elif final == "K":
            self._clear_line(values[0] if values else 0)
        elif final == "s":
            self.saved_cursor = (self.cursor_x, self.cursor_y)
        elif final == "u":
            self.cursor_x, self.cursor_y = self.saved_cursor
        elif final == "@":
            row = self.grid[self.cursor_y]
            row[self.cursor_x : self.cursor_x] = [" "] * amount
            del row[self.cols :]
        elif final == "P":
            row = self.grid[self.cursor_y]
            del row[self.cursor_x : self.cursor_x + amount]
            row.extend([" "] * (self.cols - len(row)))
        elif final == "X":
            end = min(self.cols, self.cursor_x + amount)
            self.grid[self.cursor_y][self.cursor_x : end] = [" "] * (end - self.cursor_x)
        elif final == "L":
            for _index in range(amount):
                self.grid.insert(self.cursor_y, [" "] * self.cols)
                self.grid.pop()
        elif final == "M":
            for _index in range(amount):
                self.grid.pop(self.cursor_y)
                self.grid.append([" "] * self.cols)
        elif final == "S":
            self._scroll_up(amount)
        elif final == "T":
            self._scroll_down(amount)
        elif private and 1049 in values and final in {"h", "l"}:
            self._alternate_screen(final == "h")

    def _plain(self, char: str) -> None:
        if char == "\r":
            self.cursor_x = 0
        elif char == "\n":
            self._linefeed()
        elif char == "\b":
            self.cursor_x = max(0, self.cursor_x - 1)
        elif char == "\t":
            self.cursor_x = min(self.cols - 1, ((self.cursor_x // 8) + 1) * 8)
        elif ord(char) >= 0x20:
            self._put(char)

    def feed(self, data: bytes) -> None:
        for char in self._decoder.decode(bytes(data or b""), final=False):
            if self._escape_mode == "STRING":
                if char == "\x07":
                    self._escape_mode = ""
                    self._escape_buffer = ""
                elif self._escape_buffer.endswith("\x1b") and char == "\\":
                    self._escape_mode = ""
                    self._escape_buffer = ""
                else:
                    self._escape_buffer = (self._escape_buffer + char)[-2:]
                continue
            if self._escape_mode == "ESC_INTERMEDIATE":
                self._escape_mode = ""
                self._escape_buffer = ""
                continue
            if self._escape_mode == "CSI":
                if "@" <= char <= "~":
                    self._csi(self._escape_buffer, char)
                    self._escape_mode = ""
                    self._escape_buffer = ""
                else:
                    self._escape_buffer += char
                continue
            if self._escape_mode == "ESC":
                self._escape_mode = ""
                if char == "[":
                    self._escape_mode = "CSI"
                    self._escape_buffer = ""
                elif char in {"]", "P", "X", "^", "_"}:
                    self._escape_mode = "STRING"
                    self._escape_buffer = ""
                elif char in {"(", ")", "*", "+", "-", ".", "/", "#", "%"}:
                    self._escape_mode = "ESC_INTERMEDIATE"
                    self._escape_buffer = char
                elif char == "7":
                    self.saved_cursor = (self.cursor_x, self.cursor_y)
                elif char == "8":
                    self.cursor_x, self.cursor_y = self.saved_cursor
                elif char == "D":
                    self._linefeed()
                elif char == "M":
                    if self.cursor_y == 0:
                        self._scroll_down()
                    else:
                        self.cursor_y -= 1
                elif char == "c":
                    self.grid = self._blank_grid()
                    self.cursor_x = 0
                    self.cursor_y = 0
                continue
            if char == "\x1b":
                self._escape_mode = "ESC"
            else:
                self._plain(char)

    def resize(self, cols: int, rows: int) -> None:
        new_cols = max(1, int(cols))
        new_rows = max(1, int(rows))
        resized = [row[:new_cols] + [" "] * max(0, new_cols - len(row)) for row in self.grid[:new_rows]]
        resized.extend([[" "] * new_cols for _index in range(new_rows - len(resized))])
        self.cols = new_cols
        self.rows = new_rows
        self.grid = resized
        self.cursor_x = min(self.cursor_x, self.cols - 1)
        self.cursor_y = min(self.cursor_y, self.rows - 1)

    def snapshot(self) -> bytes:
        last_row = max(
            [self.cursor_y] + [index for index, row in enumerate(self.grid) if "".join(row).rstrip()]
        )
        body = "\r\n".join("".join(row).rstrip() for row in self.grid[: last_row + 1])
        cursor = f"\x1b[{self.cursor_y + 1};{self.cursor_x + 1}H"
        return ("\x1b[2J\x1b[H" + body + cursor).encode("utf-8")


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
    output_chunks: deque = field(default_factory=deque)
    output_cursor: int = 0
    output_bytes: int = 0
    screen_snapshot: bytes = b""
    screen_projection: TerminalScreenProjection | None = None
    pump_stop: threading.Event = field(default_factory=threading.Event)
    pump_thread: Any = None
    exit_detail: str | None = None
    channel_broker: ClaudeChannelBroker | None = None
    session_anchor_ref: str = ""
    managed_shell: Any = None

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
            "session_anchor_ref": self.session_anchor_ref,
            "lifecycle_state": (
                self.managed_shell.last_state
                if getattr(self.managed_shell, "last_state", "")
                else ""
            ),
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

        # How often the per-terminal pump samples its owned process tree.
        self._managed_sample_interval = MANAGED_SAMPLE_INTERVAL_SECONDS
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
        session_anchor_ref: str = "",
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
        # Anchor-before-spawn: the Supervisor resolves the Session Anchor first,
        # so the PTY is only ever an attachment to an existing opaque Anchor.
        anchor_ref = str(session_anchor_ref or "").strip()
        if not anchor_ref:
            raise TerminalHostError(
                "TERMINAL_ANCHOR_REQUIRED",
                "a resolved Session Anchor is required before spawning a terminal",
            )
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
            session_anchor_ref=anchor_ref,
        )
        session.managed_shell = ManagedShell(
            terminal_id=terminal_id,
            session_anchor_ref=anchor_ref,
            provider=resolved_provider,
        )
        child_environment = {
            "UNIVERSE_PROJECT_ID": project,
            "UNIVERSE_MODE": requested_mode,
            "UNIVERSE_PROVIDER": resolved_provider,
            "UNIVERSE_MODEL_REF": selected_model,
            "UNIVERSE_EFFORT": selected_effort,
            "UNIVERSE_SUPERVISOR_SESSION_ID": supervisor,
            "UNIVERSE_TERMINAL_ID": terminal_id,
            "UNIVERSE_SESSION_INBOX_CLI": str(SESSION_INBOX_CLI),
        }
        # One managed path per terminal: the Supervisor owns a headless cmd.exe
        # ConPTY and the provider CLI runs inside it.  There is no second
        # launcher and no separate handshake path.
        # ComSpec is the Host's own record of its command processor; the tool
        # registry does not carry cmd.exe.
        shell_executable = os.environ.get("ComSpec") or "cmd.exe"
        # startup_argv returns CLI flags only; the executable is passed
        # separately, so the whole flag list belongs in the hosted command.
        # A raw command line, not an argv list: the /s form cannot survive a
        # second round of quoting (see managed_shell_cmdline).
        shell_argv = managed_shell_cmdline([executable, *argv])
        child_environment["UNIVERSE_MANAGED_SHELL"] = "1"
        child_environment["UNIVERSE_SESSION_ANCHOR_REF"] = anchor_ref
        try:
            session.backend = self._spawn(
                shell_executable,
                session.cwd,
                session.cols,
                session.rows,
                shell_argv,
                child_environment,
            )
        except Exception as error:  # noqa: BLE001 - surface spawn failure
            if channel_broker is not None:
                channel_broker.close()
            session.managed_shell.record_failure_evidence(
                "CLI_START_FAILED", {"detail": str(error)}
            )
            raise TerminalHostError(
                "TERMINAL_SPAWN_FAILED",
                str(error) or "failed to start CLI",
            ) from error
        # The CLI is requested, not yet attached.  Only a SessionStart hook
        # receipt correlated to this shell can advance it past CLI_STARTING.
        # Seal the exact cmd process this terminal owns.  Without a bound shell
        # identity every attach receipt is rejected as a mismatch and the whole
        # lifecycle is inert.
        session.managed_shell.bind_shell_identity(
            resolve_shell_identity(session.live_pid())
        )
        session.managed_shell.record_cli_launch(at=time.time())
        if session.managed_shell.shell is not None:
            session.managed_shell.last_state = CLI_STARTING
        session.state = "LIVE"
        with self._lock:
            self._sessions[terminal_id] = session
        created = session.public()
        self.record_audit_event(
            "TERMINAL_CREATED", terminal=created, context=audit_context
        )
        return created

    def record_managed_attach(
        self, terminal_id: str, evidence: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Correlate one SessionStart attach receipt with its managed shell.

        Correlation is by (pid, start time) identity.  A receipt that names a
        different terminal, or a shell this Supervisor does not own, is
        rejected rather than trusted by recency.
        """

        session = self.get(terminal_id)
        shell = session.managed_shell
        if shell is None:
            raise TerminalHostError(
                "TERMINAL_NOT_MANAGED", "terminal has no managed shell"
            )
        if str(evidence.get("status") or "") != "OBSERVED":
            shell.record_failure_evidence(
                "ATTACH_EVIDENCE_UNUSABLE", dict(evidence)
            )
            return {"status": "ATTACH_EVIDENCE_UNUSABLE", "terminal_id": terminal_id}
        claimed_anchor = str(evidence.get("session_anchor_ref") or "").strip()
        terminal_anchor = str(session.session_anchor_ref or "").strip()
        if not claimed_anchor:
            shell.record_failure_evidence("ATTACH_ANCHOR_REQUIRED", dict(evidence))
            return {"status": "ATTACH_ANCHOR_REQUIRED", "terminal_id": terminal_id}
        if claimed_anchor != terminal_anchor or claimed_anchor != str(
            shell.session_anchor_ref or ""
        ).strip():
            shell.record_failure_evidence("ATTACH_ANCHOR_MISMATCH", dict(evidence))
            return {"status": "ATTACH_ANCHOR_MISMATCH", "terminal_id": terminal_id}
        selected = self._select_managed_shell_candidate(shell, evidence)
        if selected is None:
            # No reported ancestor cmd is the shell this Supervisor owns.  The
            # receipt describes some other process tree, so it is rejected
            # rather than resolved by proximity or recency.
            shell.record_failure_evidence(
                "ATTACH_SHELL_NOT_MATCHED",
                {
                    "candidates": list(evidence.get("shell_candidates") or []),
                    "owned_shell": shell.shell.as_dict() if shell.shell else None,
                },
            )
            return {
                "status": "ATTACH_SHELL_NOT_MATCHED",
                "terminal_id": terminal_id,
            }
        cli_pid = selected.get("cli_pid")
        cli_started_at = selected.get("cli_started_at")
        attach = AttachEvidence(
            terminal_id=str(evidence.get("terminal_id") or ""),
            shell=ProcessIdentity(
                pid=int(selected.get("shell_pid") or 0),
                started_at=float(selected.get("shell_started_at") or 0.0),
            ),
            cli=(
                ProcessIdentity(
                    pid=int(cli_pid), started_at=float(cli_started_at or 0.0)
                )
                if isinstance(cli_pid, int) and cli_pid > 0
                else None
            ),
            provider=session.provider,
            session_anchor_ref=str(evidence.get("session_anchor_ref") or ""),
        )
        shell.record_attach_evidence(attach)
        return {
            "status": "MANAGED_SHELL_ATTACHED",
            "terminal_id": terminal_id,
            "session_anchor_ref": session.session_anchor_ref,
        }

    @staticmethod
    def _select_managed_shell_candidate(
        shell: Any, evidence: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Pick the reported candidate that is this Supervisor's own shell.

        A provider may run SessionStart through an inner ``cmd /c``, so the
        hook can see several ancestor shells.  Selection is by exact
        (pid, start time) identity against the shell this terminal spawned --
        never the nearest ancestor and never the most recent, either of which
        would seal a transient hook shell and its python child instead of the
        managed shell and the provider CLI.
        """

        owned = getattr(shell, "shell", None)
        if owned is None:
            return None
        reported = evidence.get("shell_candidates")
        if not isinstance(reported, (list, tuple)) or not reported:
            # A single-cmd Host (or an older hook) reports one flat pair.
            reported = [
                {
                    "shell_pid": evidence.get("shell_pid"),
                    "shell_started_at": evidence.get("shell_started_at"),
                    "cli_pid": evidence.get("cli_pid"),
                    "cli_started_at": evidence.get("cli_started_at"),
                }
            ]
        for candidate in reported:
            if not isinstance(candidate, Mapping):
                continue
            try:
                identity = ProcessIdentity(
                    pid=int(candidate.get("shell_pid") or 0),
                    started_at=float(candidate.get("shell_started_at") or 0.0),
                )
            except (TypeError, ValueError):
                continue
            if identity.matches(owned):
                return dict(candidate)
        return None

    def observe_managed_state(
        self,
        terminal_id: str,
        observation: Any,
        *,
        now: float | None = None,
    ) -> str:
        """Evaluate one managed shell from an owned process-tree sample."""

        session = self.get(terminal_id)
        shell = session.managed_shell
        if shell is None:
            raise TerminalHostError(
                "TERMINAL_NOT_MANAGED", "terminal has no managed shell"
            )
        return shell.evaluate(observation, now=time.time() if now is None else now)

    def sample_managed_shell(
        self,
        terminal_id: str,
        *,
        probes: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Sample one owned process tree and record the resulting state.

        A Host that cannot inspect processes yields
        PROCESS_INSPECTION_UNAVAILABLE rather than silent non-operation.
        """

        session = self.get(terminal_id)
        shell = session.managed_shell
        if shell is None:
            raise TerminalHostError(
                "TERMINAL_NOT_MANAGED", "terminal has no managed shell"
            )
        resolved = probes if probes is not None else host_process_probes()
        shell_previous_state = shell.last_state
        if resolved is None:
            state = shell.evaluate(
                ShellObservation(shell_alive=False, inspection_available=False),
                now=time.time() if now is None else now,
            )
            if state != shell_previous_state:
                self.record_audit_event(
                    "TERMINAL_MANAGED_STATE",
                    terminal=session.public(),
                    context={
                        "lifecycle_state": state,
                        "previous_state": shell_previous_state,
                    },
                )
            return {"terminal_id": terminal_id, "state": state, "recovery": []}
        observation = observe_process_tree(
            shell.shell,
            is_alive=resolved["is_alive"],
            children_of=resolved["children_of"],
            start_time_of=resolved["start_time_of"],
            pty_responsive=self._pty_responsive(session),
        )
        previous_state = shell.last_state
        state = shell.evaluate(observation, now=time.time() if now is None else now)
        if not observation.cli_children and shell.grace_deadline is not None:
            # The CLI honoured the interrupt.  Close the grace window here as
            # well as in recovery, because once the CLI is gone the state is no
            # longer HOOK_TIMEOUT and recovery does not run again.
            shell.grace_deadline = None
        if state != previous_state:
            # Periodic sampling runs every few seconds per terminal.  Auditing
            # each sample would fill the durable trail with unchanged rows, so
            # only transitions are recorded.
            self.record_audit_event(
                "TERMINAL_MANAGED_STATE",
                terminal=session.public(),
                context={"lifecycle_state": state, "previous_state": previous_state},
            )
        recovery: list[dict[str, Any]] = []
        if state == HOOK_TIMEOUT:
            recovery = self._run_hook_timeout_recovery(
                session, shell, observation, now=time.time() if now is None else now
            )
        return {
            "terminal_id": terminal_id,
            "state": state,
            "recovery": recovery,
            "inspection_source": resolved.get("source", "UNKNOWN"),
        }

    @staticmethod
    def _pty_responsive(session: TerminalSession) -> str:
        """Report PTY responsiveness as UNKNOWN.

        The only I/O signal available here is the pump's own read loop, and it
        cannot support a responsiveness claim: the sample runs before the read,
        so a hung read stops sampling altogether, while an ordinary timed-out
        read returns constantly and would keep any freshness stamp warm.  Both
        directions are unfalsifiable, so this reports UNKNOWN instead of
        inventing an answer.  A defensible out-of-band heartbeat would replace
        this; until then PTY_UNRESPONSIVE is never derived.
        """

        return PTY_RESPONSIVENESS_UNKNOWN

    def _run_hook_timeout_recovery(
        self,
        session: TerminalSession,
        shell: Any,
        observation: Any,
        *,
        now: float,
        grace_seconds: float = DEFAULT_INTERRUPT_GRACE_SECONDS,
    ) -> list[dict[str, Any]]:
        """Execute the bounded recovery for exactly one terminal.

        The grace window is a deadline carried on the shell, not a sleep: this
        returns immediately and the terminal is only closed on a later sample
        once the window has expired with the CLI still running.
        """

        performed: list[dict[str, Any]] = []
        for action in plan_hook_timeout_recovery(
            shell, observation, grace_seconds=grace_seconds, now=now
        ):
            if action.terminal_id != session.terminal_id:
                # Recovery is per-terminal by construction; never widen it.
                continue
            if action.step == "START_GRACE":
                shell.grace_deadline = now + grace_seconds
            elif action.step == "GRACE_SATISFIED":
                shell.grace_deadline = None
            elif action.step == "RECORD_FAILURE_EVIDENCE":
                shell.record_failure_evidence(
                    "HOOK_TIMEOUT",
                    {
                        "shell": shell.shell.as_dict() if shell.shell else None,
                        "cli_children": [
                            child.as_dict() for child in observation.cli_children
                        ],
                    },
                )
            elif action.step == "INTERRUPT_CLI":
                # History is preserved: interrupt the CLI in place rather than
                # discarding the terminal's scrollback.
                try:
                    self.write(session.terminal_id, b"")
                except TerminalHostError:
                    pass
            elif action.step == "CLOSE_SHELL_PTY":
                self.close(
                    session.terminal_id,
                    audit_context={"reason": "MANAGED_HOOK_TIMEOUT_RECOVERY"},
                )
            performed.append({"step": action.step, "target_pid": action.target_pid})
        steps = [item["step"] for item in performed]
        # Sampling revisits an open grace window every few seconds.  Those
        # passes do nothing but wait, so they must not each write a durable
        # recovery row; the opening pass and any terminal action still do.
        if steps and steps != ["GRACE"]:
            self.record_audit_event(
                "TERMINAL_HOOK_TIMEOUT_RECOVERY",
                terminal=session.public(),
                context={"steps": steps},
            )
        return performed

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
            # Grace expiry closes the terminal from inside its own pump thread.
            # A thread cannot join itself, and the resulting RuntimeError would
            # abort cleanup, so skip only the join and still drop the handle.
            # pump_stop is already set and the loop exits on state != LIVE.
            if session.pump_thread is not threading.current_thread():
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


    @classmethod
    def _record_output(cls, session: TerminalSession, data: bytes) -> None:
        raw = bytes(data or b"")
        for offset in range(0, len(raw), TERMINAL_HISTORY_CHUNK_BYTES):
            chunk = raw[offset : offset + TERMINAL_HISTORY_CHUNK_BYTES]
            session.output_cursor += 1
            session.output_chunks.append((session.output_cursor, chunk))
            session.output_bytes += len(chunk)
        while (
            session.output_bytes > TERMINAL_HISTORY_MAX_BYTES
            and len(session.output_chunks) > 1
        ):
            _cursor, removed = session.output_chunks.popleft()
            session.output_bytes -= len(removed)
        if session.screen_projection is None:
            session.screen_projection = TerminalScreenProjection(session.cols, session.rows)
        session.screen_projection.feed(raw)
        session.screen_snapshot = session.screen_projection.snapshot()

    def terminal_snapshot(self, terminal_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        with session.lock:
            snapshot = bytes(session.screen_snapshot)
            cursor = session.output_cursor
            cols = session.cols
            rows = session.rows
        return {
            "schema": "universe.terminal-screen-snapshot.v1",
            "status": "TERMINAL_SCREEN_SNAPSHOT_COLLECTED",
            "terminal_id": session.terminal_id,
            "cursor": cursor,
            "cols": cols,
            "rows": rows,
            "data_base64": base64.b64encode(snapshot).decode("ascii"),
        }

    def history(
        self,
        terminal_id: str,
        *,
        before_cursor: int | None = None,
        limit: int = TERMINAL_HISTORY_PAGE_LIMIT,
    ) -> dict[str, Any]:
        session = self.get(terminal_id)
        normalized_limit = max(1, min(TERMINAL_HISTORY_PAGE_LIMIT, int(limit)))
        with session.lock:
            rows = list(session.output_chunks)
            latest_cursor = session.output_cursor
            screen_snapshot = bytes(session.screen_snapshot)
            upper = (
                latest_cursor + 1
                if before_cursor is None
                else max(1, int(before_cursor))
            )
        eligible = [row for row in rows if row[0] < upper]
        selected_reversed = []
        selected_bytes = 0
        for row in reversed(eligible):
            if selected_reversed and (
                len(selected_reversed) >= normalized_limit
                or selected_bytes + len(row[1]) > TERMINAL_HISTORY_PAGE_MAX_BYTES
            ):
                break
            selected_reversed.append(row)
            selected_bytes += len(row[1])
        selected = list(reversed(selected_reversed))
        next_before = selected[0][0] if selected else upper
        return {
            "schema": "universe.terminal-output-history.v1",
            "status": "TERMINAL_HISTORY_COLLECTED",
            "terminal_id": session.terminal_id,
            "latest_cursor": latest_cursor,
            "screen_snapshot_base64": base64.b64encode(
                screen_snapshot
            ).decode("ascii"),
            "before_cursor": upper,
            "next_before_cursor": next_before,
            "has_more": bool(selected and eligible[0][0] < selected[0][0]),
            "chunks": [
                {
                    "cursor": cursor,
                    "byte_count": len(chunk),
                    "data_base64": base64.b64encode(chunk).decode("ascii"),
                }
                for cursor, chunk in selected
            ],
        }

    def emit_output(self, terminal_id: str, data: bytes) -> None:
        """Fan out display-only bytes without sending them to the CLI stdin."""

        session = self.get(terminal_id)
        if session.state != "LIVE":
            raise TerminalHostError("TERMINAL_NOT_LIVE", "terminal is not live")
        with session.lock:
            self._record_output(session, bytes(data))
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

    def push_channel(
        self,
        terminal_id: str,
        payload: Mapping[str, Any],
        *,
        on_result: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        session = self.get(terminal_id)
        broker = session.channel_broker
        if broker is None:
            raise TerminalHostError(
                "TERMINAL_CHANNEL_UNAVAILABLE",
                "terminal has no Claude Code channel bridge",
            )
        try:
            return broker.push(payload, on_result=on_result)
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
        with session.lock:
            session.cols = max(80, int(cols or session.cols))
            session.rows = max(24, int(rows or session.rows))
            if session.screen_projection is not None:
                session.screen_projection.resize(session.cols, session.rows)
                session.screen_snapshot = session.screen_projection.snapshot()
            backend = session.backend
            current_cols = session.cols
            current_rows = session.rows
        resizer = getattr(backend, "resize", None)
        if callable(resizer):
            resizer(current_cols, current_rows)

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
                self._record_output(session, tail)
        self._mark_backend_exit(session)

    def _mark_backend_exit(
        self,
        session: TerminalSession,
        detail: str = "",
    ) -> None:
        with session.lock:
            if session.state != "LIVE":
                return
            chunks = b"".join(chunk for _cursor, chunk in session.output_chunks)
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
            # Initial attachment receives the current screen projection only.
            # Immutable output chunks remain separately pageable by cursor.
            if session.screen_snapshot:
                waiter.put_nowait(bytes(session.screen_snapshot))
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
        next_managed_sample = time.time() + self._managed_sample_interval
        while not session.pump_stop.is_set() and session.state == "LIVE":
            backend = session.backend
            if backend is None:
                break
            # Periodic managed-shell sampling rides this loop rather than a
            # separate scheduler: it is already the per-terminal lifecycle
            # thread, so state and bounded recovery stay scoped to one PTY.
            now = time.time()
            if session.managed_shell is not None and now >= next_managed_sample:
                next_managed_sample = now + self._managed_sample_interval
                try:
                    self.sample_managed_shell(session.terminal_id, now=now)
                except Exception:  # noqa: BLE001 - sampling never kills a PTY
                    pass
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
                self._record_output(session, chunk)
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
        or lowered.startswith(("session_", "session-", "session-anchor_", "session_anchor_", "term_", "pty:", "cli-terminal:"))
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
    argv: "list[str] | str | None" = None,
    environment: Mapping[str, str] | None = None,
) -> Any:
    if os.name == "nt":
        from universe_app.windows_conpty import WindowsConPTY

        return WindowsConPTY(
            executable,
            cwd,
            cols,
            rows,
            # Preserve a raw command line exactly.  ``argv or []`` would turn
            # an empty string into a list and hide the distinction between the
            # two spawn shapes.
            argv=[] if argv is None else argv,
            environment=environment,
        )
    raise TerminalHostError("PTY_UNSUPPORTED", "this Host does not expose a PTY")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
