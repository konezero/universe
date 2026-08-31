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
from functools import wraps
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from host_profile import resolve_host_tool
from universe_app.managed_shell import (
    CLI_START_FAILED,
    CLI_STARTING,
    DEFAULT_INTERRUPT_GRACE_SECONDS,
    HOOK_TIMEOUT,
    PROCESS_INSPECTION_UNAVAILABLE,
    PTY_RESPONSIVENESS_UNKNOWN,
    PTY_UNRESPONSIVE,
    SHELL_EXITED,
    SHELL_IDLE,
    AttachEvidence,
    ManagedShell,
    ManagedShellError,
    ProcessIdentity,
    ShellObservation,
    host_process_probes,
    managed_host_provider_command,
    managed_shell_cmdline,
    observe_process_tree,
    plan_hook_timeout_recovery,
)
from universe_app.reconnection_host import (
    ReconnectionHostError,
    ReconnectionHostRegistry,
    ReconnectionPty,
    evaluate_runtime_compatibility,
    runtime_version_snapshot,
)


MANAGED_SAMPLE_INTERVAL_SECONDS = 5.0


def _serialize_reconnection_lifecycle(method):
    @wraps(method)
    def serialized(self, *args, **kwargs):
        with self._reconnection_lifecycle_lock:
            return method(self, *args, **kwargs)

    return serialized


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
    session_lookup_path,
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
SESSION_BOOTSTRAP_PROMPT = (
    "Initialize this Universe session without calling tools. "
    "Reply exactly SESSION_READY and wait for user instructions."
)

# Matches ANSI CSI (`ESC[...letter`) and OSC (`ESC]...BEL`/`ESC]...ESC\`)
# sequences so terminal output can be checked as plain text.
_ANSI_ESCAPE_RE = re.compile(rb"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
# Claude has emitted this phrase both as ordinary text (with a space) and with
# a cursor-column jump between the words.  Stripping ANSI escapes collapses the
# latter to ``localdevelopment``, so accept either representation.
_DEV_CHANNEL_PROMPT_RE = re.compile(rb"local\s*development", re.IGNORECASE)


def provider_cli_ready_for_bootstrap(provider: str, output: bytes) -> bool:
    plain = _ANSI_ESCAPE_RE.sub(b"", bytes(output or b""))
    name = str(provider or "").strip().upper()
    if name == "GROK":
        return b"always-approve" in plain
    if name == "CODEX":
        return b"OpenAI Codex" in plain
    return False


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


MANAGED_SHELL_IDENTITY_SCHEMA = "universe.managed-shell-identity.v1"
MANAGED_SHELL_IDENTITY_MISSING = "MANAGED_SHELL_IDENTITY_MISSING"
MANAGED_SHELL_IDENTITY_INVALID = "MANAGED_SHELL_IDENTITY_INVALID"
MANAGED_SHELL_IDENTITY_MISMATCH = "MANAGED_SHELL_IDENTITY_MISMATCH"
MANAGED_SHELL_RECLAIM_STATES = frozenset(
    {
        MANAGED_SHELL_IDENTITY_MISSING,
        MANAGED_SHELL_IDENTITY_INVALID,
        MANAGED_SHELL_IDENTITY_MISMATCH,
        CLI_START_FAILED,
        SHELL_IDLE,
        PTY_UNRESPONSIVE,
        SHELL_EXITED,
    }
)


def managed_shell_identity_path(root: Path, terminal_id: str) -> Path:
    return (
        root.resolve()
        / ".ai"
        / "runtime"
        / "tmp"
        / "managed-shells"
        / f"{terminal_id}.json"
    )


def write_managed_shell_identity(path: Path, session: Any) -> None:
    managed = getattr(session, "managed_shell", None)
    shell = getattr(managed, "shell", None)
    if shell is None:
        return
    payload = {
        "schema": MANAGED_SHELL_IDENTITY_SCHEMA,
        "terminal_id": session.terminal_id,
        "session_anchor_ref": session.session_anchor_ref,
        "shell_pid": shell.pid,
        "shell_started_at": shell.started_at,
        "supervisor_session_id": session.supervisor_session_id,
        "project_id": session.project_id,
        "mode": session.mode,
        "provider": session.provider,
        "cwd": session.cwd,
        "cli_launch_requested_at": getattr(managed, "cli_launch_requested_at", None),
        "cli_ever_attached": bool(getattr(managed, "cli_ever_attached", False)),
        "attach_evidence": (
            managed.attach_evidence.as_dict()
            if getattr(managed, "attach_evidence", None) is not None
            else None
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        staged.write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(staged, path)
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass


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
    launch_profile: str = "INTERACTIVE"
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
    channel_enabled: bool = False
    session_anchor_ref: str = ""
    managed_shell: Any = None
    managed_shell_identity_file: str = ""
    backend_owner: str = "PYTHON_CONPTY"
    reconnection_host_id: str = ""
    host_runtime_versions: dict[str, str] = field(default_factory=dict)
    host_compatibility: str = "NOT_APPLICABLE"
    protocol_state: str = "UNKNOWN"
    bootstrap_input: bytes = b""
    bootstrap_delivered: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "schema": TERMINAL_SCHEMA,
            "terminal_id": self.terminal_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "provider": self.provider,
            "model_ref": self.model_ref,
            "effort": self.effort,
            "launch_profile": self.launch_profile,
            "supervisor_session_id": self.supervisor_session_id,
            "session_anchor_ref": self.session_anchor_ref,
            "managed_shell_identity_file": self.managed_shell_identity_file,
            "backend_owner": self.backend_owner,
            "reconnection_host_id": self.reconnection_host_id,
            "host_session_ref": self.reconnection_host_id,
            "host_runtime_versions": dict(self.host_runtime_versions),
            "host_compatibility": self.host_compatibility,
            "host_protocol_state": self.protocol_state,
            "host_reconnect_eligible": bool(
                self.reconnection_host_id
                and self.state == "LIVE"
                and self.host_compatibility in {"CURRENT", "COMPATIBLE_OLD"}
            ),
            "shell_process": (
                self.managed_shell.shell.as_dict()
                if getattr(self.managed_shell, "shell", None) is not None
                else None
            ),
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
                "CLAUDE_CODE_CHANNEL" if self.channel_enabled else "PTY"
            ),
            "channel_owner": (
                "RUST_SESSION_HOST"
                if self.channel_enabled and isinstance(self.backend, ReconnectionPty)
                else "SUPERVISOR_LEGACY" if self.channel_enabled
                else "NOT_APPLICABLE"
            ),
            "channel_registered": self.channel_is_registered(),
        }

    def channel_is_registered(self) -> bool:
        if self.channel_broker is not None:
            return self.channel_broker.registered
        if self.channel_enabled and isinstance(self.backend, ReconnectionPty):
            try:
                return self.backend.channel_state() == "READY"
            except Exception:  # noqa: BLE001 - public projection stays available
                return False
        return False

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

    def list_created_events(self) -> list[dict[str, Any]]:
        """Return complete creation metadata; liveness is verified by the Host registry."""

        if self._path is None:
            return []
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM terminal_audit_event
                WHERE event_type = 'TERMINAL_CREATED'
                ORDER BY event_id DESC
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
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
        reconnection_registry: ReconnectionHostRegistry | None = None,
    ) -> None:
        from universe_app.session_bus import SessionBus

        # How often the per-terminal pump samples its owned process tree.
        self._managed_sample_interval = MANAGED_SAMPLE_INTERVAL_SECONDS
        self._spawn = spawn or spawn_conpty
        self._lock = threading.Lock()
        self._reconnection_lifecycle_lock = threading.RLock()
        self._sessions: dict[str, TerminalSession] = {}
        self._reconnection_registry = reconnection_registry
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

    def list_hosts(self) -> list[dict[str, Any]]:
        registry = self._reconnection_registry
        if registry is None:
            return []
        return registry.list_observed_hosts()

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

    def reclaim_orphaned_managed_shells(
        self,
        *,
        start_time_of: Callable[[int], float | None] | None = None,
        terminate_instance: Callable[[int, float], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Reclaim exact cmd instances left by an earlier Supervisor process."""

        if start_time_of is None or terminate_instance is None:
            from universe_app.windows_process import (
                process_start_time,
                terminate_process_instance,
            )

            start_time_of = start_time_of or process_start_time
            terminate_instance = terminate_instance or terminate_process_instance
        with self._lock:
            active_terminal_ids = set(self._sessions)
        events = self.audit.list(limit=1000)
        latest: dict[str, str] = {}
        created: dict[str, Mapping[str, Any]] = {}
        for event in events:
            terminal_id = str(event.get("terminal_id") or "").strip()
            if not terminal_id:
                continue
            latest.setdefault(terminal_id, str(event.get("event_type") or ""))
            if (
                terminal_id not in created
                and str(event.get("event_type") or "") == "TERMINAL_CREATED"
            ):
                created[terminal_id] = event
        results: list[dict[str, Any]] = []
        terminal_states = {
            "TERMINAL_CLOSED",
            "TERMINAL_TERMINATED",
            "TERMINAL_ORPHAN_RECLAIMED",
        }
        for terminal_id, event in created.items():
            if terminal_id in active_terminal_ids or latest.get(terminal_id) in terminal_states:
                continue
            details = event.get("details")
            details = details if isinstance(details, Mapping) else {}
            if details.get("backend_owner") == "RUST_RECONNECTION_HOST":
                # A Rust Host owns the ConPTY handles and its cmd child. This
                # legacy identity-file reclaimer must never terminate that cmd,
                # even when Host discovery is disabled or temporarily fails.
                # Reconciliation and registry cleanup own the Host lifecycle.
                host: Mapping[str, Any] = {}
                anchor_ref = str(details.get("session_anchor_ref") or "").strip()
                if self._reconnection_registry is not None and anchor_ref:
                    try:
                        client = self._reconnection_registry.discover(anchor_ref)
                        host = client.status()
                    except ReconnectionHostError:
                        host = {}
                results.append(
                    {
                        "terminal_id": terminal_id,
                        "status": (
                            "HOST_OWNED_ORPHAN_PRESERVED"
                            if host
                            else "HOST_OWNED_ORPHAN_DEFERRED"
                        ),
                        "pid": host.get("child_pid") or event.get("pid"),
                        "host_id": host.get("host_id")
                        or details.get("reconnection_host_id"),
                    }
                )
                continue
            path_text = str(
                details.get("managed_shell_identity_file")
            ).strip()
            if not path_text:
                continue
            path = Path(path_text)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                results.append(
                    {"terminal_id": terminal_id, "status": "INVALID_IDENTITY_REMOVED"}
                )
                continue
            try:
                pid = int(payload.get("shell_pid") or 0)
                started_at = float(payload.get("shell_started_at") or 0.0)
            except (AttributeError, TypeError, ValueError):
                pid, started_at = 0, 0.0
            identity_matches = (
                isinstance(payload, Mapping)
                and payload.get("schema") == MANAGED_SHELL_IDENTITY_SCHEMA
                and str(payload.get("terminal_id") or "") == terminal_id
                and pid > 0
                and started_at > 0
            )
            observed = start_time_of(pid) if identity_matches else None
            exact_live_instance = (
                observed is not None and abs(float(observed) - started_at) <= 0.5
            )
            if exact_live_instance:
                reclaimed = bool(terminate_instance(pid, started_at))
                status = (
                    "TERMINAL_ORPHAN_RECLAIMED"
                    if reclaimed
                    else "TERMINAL_ORPHAN_RECLAIM_FAILED"
                )
                if reclaimed:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                status = "STALE_IDENTITY_REMOVED"
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            result = {
                "terminal_id": terminal_id,
                "status": status,
                "pid": pid or None,
            }
            results.append(result)
            self.record_audit_event(
                status,
                terminal={
                    "terminal_id": terminal_id,
                    "pid": pid or None,
                    "project_id": event.get("project_id"),
                    "mode": event.get("mode"),
                    "provider": event.get("provider"),
                    "supervisor_session_id": event.get("supervisor_session_id"),
                },
                context={"source": "PTY_SUPERVISOR", "access_surface": "SUPERVISOR"},
                details={"identity_file": path_text},
            )
        return results

    @_serialize_reconnection_lifecycle
    def reconcile_reconnection_hosts(self) -> list[dict[str, Any]]:
        """Rebuild process-local TerminalSession rows from live Rust Hosts."""

        registry = self._reconnection_registry
        if registry is None:
            return []
        with self._lock:
            active_terminal_ids = set(self._sessions)
            active_anchor_refs = {
                session.session_anchor_ref
                for session in self._sessions.values()
                if session.state == "LIVE" and session.session_anchor_ref
            }
        latest: dict[str, str] = {}
        for event in self.audit.list(limit=1000):
            terminal_id = str(event.get("terminal_id") or "").strip()
            if terminal_id:
                latest.setdefault(terminal_id, str(event.get("event_type") or ""))
        created = {
            str(event.get("terminal_id") or "").strip(): event
            for event in self.audit.list_created_events()
            if str(event.get("terminal_id") or "").strip()
        }
        list_live_clients = getattr(registry, "list_live_clients", None)
        live_clients = (
            {client.state.anchor_ref: client for client in list_live_clients()}
            if callable(list_live_clients)
            else None
        )
        unmatched_live_anchors = set(live_clients or {})
        terminal_states = {
            "TERMINAL_CLOSED",
            "TERMINAL_TERMINATED",
            "TERMINAL_ORPHAN_RECLAIMED",
        }
        results: list[dict[str, Any]] = []
        for terminal_id, event in created.items():
            if terminal_id in active_terminal_ids:
                continue
            if live_clients is None and latest.get(terminal_id) in terminal_states:
                continue
            details = event.get("details")
            details = details if isinstance(details, Mapping) else {}
            if details.get("backend_owner") != "RUST_RECONNECTION_HOST":
                continue
            anchor_ref = str(details.get("session_anchor_ref") or "").strip()
            if anchor_ref in active_anchor_refs:
                unmatched_live_anchors.discard(anchor_ref)
                continue
            cwd = str(details.get("cwd") or "").strip()
            executable = str(details.get("executable") or "").strip()
            if not anchor_ref or not cwd or not executable:
                results.append(
                    {"terminal_id": terminal_id, "status": "HOST_METADATA_INCOMPLETE"}
                )
                continue
            try:
                if live_clients is None:
                    client = registry.discover(anchor_ref)
                else:
                    client = live_clients.get(anchor_ref)
                    if client is None:
                        continue
                    unmatched_live_anchors.discard(anchor_ref)
                status = client.status()
                if status.get("runtime_state") != "LIVE":
                    raise ReconnectionHostError("Host terminal is not live")
                backend = ReconnectionPty(
                    client,
                    f"terminal-host-{os.getpid()}-{terminal_id}",
                )
            except ReconnectionHostError as error:
                results.append(
                    {
                        "terminal_id": terminal_id,
                        "status": "HOST_REATTACH_FAILED",
                        "detail": str(error),
                    }
                )
                continue
            provider = str(event.get("provider") or "").strip().upper()
            mode = str(event.get("mode") or "").strip().upper()
            project_id = str(event.get("project_id") or "").strip()
            supervisor_session_id = str(
                event.get("supervisor_session_id") or ""
            ).strip()
            launch_profile = str(
                details.get("launch_profile") or "INTERACTIVE"
            ).strip().upper()
            channel_broker: ClaudeChannelBroker | None = None
            channel_enabled = bool(status.get("channel_enabled"))
            channel_state = (
                "READY" if bool(status.get("channel_registered"))
                else "PENDING" if channel_enabled
                else "NOT_APPLICABLE"
            )
            session = TerminalSession(
                terminal_id=terminal_id,
                project_id=project_id,
                mode=mode,
                provider=provider,
                supervisor_session_id=supervisor_session_id,
                cwd=cwd,
                executable=executable,
                created_at=str(details.get("created_at") or event.get("occurred_at") or _now()),
                model_ref=str(details.get("model_ref") or ""),
                effort=str(details.get("effort") or "AUTO"),
                launch_profile=launch_profile,
                state="LIVE",
                cols=max(80, int(details.get("cols") or 120)),
                rows=max(24, int(details.get("rows") or 32)),
                backend=backend,
                channel_broker=channel_broker,
                channel_enabled=channel_enabled,
                session_anchor_ref=anchor_ref,
                managed_shell_identity_file=str(
                    details.get("managed_shell_identity_file") or ""
                ),
                backend_owner="RUST_RECONNECTION_HOST",
                reconnection_host_id=str(status.get("host_id") or backend.host_id),
                host_runtime_versions=runtime_version_snapshot(status),
                host_compatibility=evaluate_runtime_compatibility(status),
                protocol_state=str(status.get("protocol_state") or "UNKNOWN"),
            )
            managed = ManagedShell(
                terminal_id=terminal_id,
                session_anchor_ref=anchor_ref,
                provider=provider,
            )
            managed.bind_shell_identity(resolve_shell_identity(session.live_pid()))
            identity_path = Path(session.managed_shell_identity_file)
            try:
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                identity = {}
            if (
                isinstance(identity, Mapping)
                and identity.get("schema") == MANAGED_SHELL_IDENTITY_SCHEMA
                and str(identity.get("terminal_id") or "") == terminal_id
                and str(identity.get("session_anchor_ref") or "") == anchor_ref
            ):
                launched_at = identity.get("cli_launch_requested_at")
                if isinstance(launched_at, (int, float)):
                    managed.record_cli_launch(at=float(launched_at))
                attach = identity.get("attach_evidence")
                if isinstance(attach, Mapping) and managed.shell is not None:
                    try:
                        cli_pid = int(attach.get("cli_pid") or 0)
                        cli_started_at = float(attach.get("cli_started_at") or 0.0)
                        restored = AttachEvidence(
                            terminal_id=str(attach.get("terminal_id") or ""),
                            shell=ProcessIdentity(
                                pid=int(attach.get("shell_pid") or 0),
                                started_at=float(attach.get("shell_started_at") or 0.0),
                            ),
                            cli=(
                                ProcessIdentity(pid=cli_pid, started_at=cli_started_at)
                                if cli_pid > 0 and cli_started_at > 0
                                else None
                            ),
                            provider=str(attach.get("provider") or provider),
                            provider_session_ref=str(
                                attach.get("provider_session_ref") or ""
                            ),
                            session_anchor_ref=str(
                                attach.get("session_anchor_ref") or ""
                            ),
                            observed_at=str(attach.get("observed_at") or ""),
                        )
                        managed.record_attach_evidence(restored)
                    except (ManagedShellError, TypeError, ValueError):
                        pass
            session.managed_shell = managed
            with self._lock:
                if terminal_id in self._sessions:
                    backend.close()
                    if channel_broker is not None:
                        channel_broker.close()
                    continue
                self._sessions[terminal_id] = session
            self.record_audit_event(
                "TERMINAL_REATTACHED",
                terminal=session.public(),
                context={"source": "PTY_SUPERVISOR", "access_surface": "SUPERVISOR"},
                details={
                    "host_id": session.reconnection_host_id,
                    "session_anchor_ref": anchor_ref,
                    "channel_state": channel_state,
                },
            )
            self._ensure_pump(session)
            active_terminal_ids.add(terminal_id)
            active_anchor_refs.add(anchor_ref)
            results.append(
                {
                    "terminal_id": terminal_id,
                    "status": "TERMINAL_REATTACHED",
                    "host_id": session.reconnection_host_id,
                    "pid": session.live_pid(),
                }
            )
        for anchor_ref in sorted(unmatched_live_anchors):
            client = live_clients[anchor_ref] if live_clients is not None else None
            results.append(
                {
                    "terminal_id": None,
                    "status": "HOST_METADATA_INCOMPLETE",
                    "host_id": client.state.host_id if client is not None else None,
                    "session_anchor_ref": anchor_ref,
                }
            )
        return results

    def cleanup_reconnection_host_registry(self) -> list[dict[str, Any]]:
        """Apply the registry's validated dead-record retention policy."""

        registry = self._reconnection_registry
        if registry is None:
            return []
        return registry.cleanup_stale_records()

    def get(self, terminal_id: str) -> TerminalSession:
        with self._lock:
            session = self._sessions.get(str(terminal_id or "").strip())
        if session is None:
            raise TerminalHostError("TERMINAL_NOT_FOUND", "terminal session does not exist")
        self._refresh_session_state(session)
        return session

    @_serialize_reconnection_lifecycle
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
        replace_terminal_id: str = "",
        replace_host_session_ref: str = "",
        resume_session_ref: str = "",
        launch_profile: str = "INTERACTIVE",
        provider_arguments: Sequence[str] = (),
        provider_environment: Mapping[str, str] | None = None,
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
        replacement_id = str(replace_terminal_id or "").strip()
        replacement_host_ref = str(replace_host_session_ref or "").strip()
        if replacement_id and replacement_host_ref:
            raise TerminalHostError(
                "HOST_REPLACEMENT_COORDINATE_CONFLICT",
                "replace exactly one Host or legacy terminal coordinate",
            )
        if replacement_host_ref:
            with self._lock:
                host_matches = [
                    item
                    for item in self._sessions.values()
                    if item.reconnection_host_id == replacement_host_ref
                ]
            if (
                len(host_matches) != 1
                or host_matches[0].session_anchor_ref != anchor_ref
            ):
                raise TerminalHostError(
                    "HOST_REPLACEMENT_IDENTITY_MISMATCH",
                    "replacement must name the Host owned by this Session Anchor",
                )
            replacement_id = host_matches[0].terminal_id
        if replacement_id:
            with self._lock:
                replacement = self._sessions.get(replacement_id)
            if (
                replacement is None
                or replacement.state != "LIVE"
                or replacement.session_anchor_ref != anchor_ref
            ):
                raise TerminalHostError(
                    "TERMINAL_REPLACEMENT_IDENTITY_MISMATCH",
                    "replacement must name the live terminal on this Session Anchor",
                )
            # create/reconcile share the lifecycle RLock, so the exact old Host
            # cannot be rediscovered between this termination and the spawn.
            self.terminate(
                replacement_id,
                audit_context={"reason": "ATOMIC_TERMINAL_REPLACEMENT"},
            )
        with self._lock:
            anchored_hosts = [
                item
                for item in self._sessions.values()
                if item.state == "LIVE" and item.session_anchor_ref == anchor_ref
            ]
        for anchored in anchored_hosts:
            self._refresh_host_projection(anchored)
            if (
                not anchored.reconnection_host_id
                or anchored.host_compatibility in {"CURRENT", "COMPATIBLE_OLD"}
            ):
                raise TerminalHostError(
                    "TERMINAL_ANCHOR_ALREADY_HOSTED",
                    "this Session Anchor already has a compatible live Host",
                )
            self.terminate(
                anchored.terminal_id,
                audit_context={
                    "reason": "INCOMPATIBLE_HOST_REPLACEMENT",
                    "host_session_ref": anchored.reconnection_host_id,
                    "host_compatibility": anchored.host_compatibility,
                },
            )
        selected = str(provider or "AUTO").strip().upper()
        executable = resolve_cli_executable(selected)
        resolved_provider = selected if selected != "AUTO" else infer_provider(executable)
        selected_model = str(model_ref or "").strip()
        selected_effort = str(effort or "AUTO").strip().upper() or "AUTO"
        supervisor = str(supervisor_session_id or "").strip()
        terminal_id = "term_" + secrets.token_hex(8)
        profile = str(launch_profile or "INTERACTIVE").strip().upper()
        if profile not in {"INTERACTIVE", "SUPERVISED_STDIO"}:
            raise TerminalHostError(
                "TERMINAL_LAUNCH_PROFILE_INVALID",
                "launch_profile must be INTERACTIVE or SUPERVISED_STDIO",
            )
        channel_broker: ClaudeChannelBroker | None = None
        channel_enabled = resolved_provider == "CLAUDE" and profile == "INTERACTIVE"
        channel_bootstrap_token = ""
        channel_session_token = ""
        if channel_enabled:
            # The provider adapter is static, but the authenticated queue lives
            # with the Rust Host so Supervisor restart cannot sever delivery.
            ensure_local_channel_server_registered(str(root.resolve()))
            if self._reconnection_registry is None:
                channel_broker = ClaudeChannelBroker(
                    terminal_id=terminal_id,
                    project_id=project,
                    mode=requested_mode,
                    provider=resolved_provider,
                    supervisor_session_id=supervisor,
                ).start()
            else:
                channel_bootstrap_token = secrets.token_urlsafe(32)
                channel_session_token = secrets.token_urlsafe(32)
        fresh_claude_session_id = (
            str(uuid.uuid4())
            if (
                resolved_provider == "CLAUDE"
                and profile == "INTERACTIVE"
                and not str(resume_session_ref or "").strip()
            )
            else ""
        )
        fresh_grok_session_id = (
            str(uuid.uuid4())
            if (
                resolved_provider == "GROK"
                and profile == "INTERACTIVE"
                and not str(resume_session_ref or "").strip()
            )
            else ""
        )
        if profile == "SUPERVISED_STDIO":
            argv = [str(argument) for argument in provider_arguments]
            if not argv or any(not argument for argument in argv):
                raise TerminalHostError(
                    "TERMINAL_PROVIDER_ARGUMENTS_REQUIRED",
                    "SUPERVISED_STDIO requires a non-empty provider argument list",
                )
        else:
            argv = startup_argv(
                resolved_provider,
                resume_session_ref,
                model_ref=selected_model,
                effort=selected_effort,
                claude_session_id=fresh_claude_session_id,
                grok_session_id=fresh_grok_session_id,
                claude_channel_enabled=channel_enabled,
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
            launch_profile=profile,
            cols=max(80, int(cols or 120)),
            rows=max(24, int(rows or 32)),
            channel_broker=channel_broker,
            channel_enabled=channel_enabled,
            session_anchor_ref=anchor_ref,
        )
        session.managed_shell = ManagedShell(
            terminal_id=terminal_id,
            session_anchor_ref=anchor_ref,
            provider=resolved_provider,
        )
        shell_identity_path = managed_shell_identity_path(root, terminal_id)
        session.managed_shell_identity_file = str(shell_identity_path)
        child_environment = {
            "UNIVERSE_PROJECT_ID": project,
            "UNIVERSE_MODE": requested_mode,
            "UNIVERSE_PROVIDER": resolved_provider,
            "UNIVERSE_MODEL_REF": selected_model,
            "UNIVERSE_EFFORT": selected_effort,
            "UNIVERSE_SUPERVISOR_SESSION_ID": supervisor,
            "UNIVERSE_TERMINAL_ID": terminal_id,
            "UNIVERSE_MANAGED_SHELL_IDENTITY_FILE": str(shell_identity_path),
            "UNIVERSE_SESSION_INBOX_CLI": str(SESSION_INBOX_CLI),
        }
        if resolved_provider == "GROK":
            # Grok scans Claude-compatible hooks by default. Universe already
            # installs one provider-native Grok SessionStart hook, so importing
            # the Claude copies would dispatch the same session injection three
            # times and contend on the same runtime state. Isolate this Host to
            # its native hook path; other Claude compatibility surfaces remain.
            child_environment["GROK_CLAUDE_HOOKS_ENABLED"] = "0"
        reserved_environment = frozenset(child_environment)
        for key, value in dict(provider_environment or {}).items():
            normalized_key = str(key)
            if normalized_key in reserved_environment:
                continue
            child_environment[normalized_key] = str(value)
        # Provider-specific environment is an overlay; Supervisor coordinates win.
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
        pipe_console_input = (
            profile == "SUPERVISED_STDIO" and resolved_provider == "CLAUDE"
        )
        provider_command = ""
        provider_command_environment: dict[str, str] = {}
        if self._reconnection_registry is not None:
            provider_command, provider_command_environment = (
                managed_host_provider_command(
                    [executable, *argv],
                    pipe_console_input=pipe_console_input,
                )
            )
        provider_bootstrap_input = (
            startup_input(resolved_provider, resume_session_ref)
            if profile == "INTERACTIVE"
            else b""
        )
        session.bootstrap_input = provider_bootstrap_input
        shell_argv = (
            managed_shell_cmdline(
                [executable, *argv],
                pipe_console_input=pipe_console_input,
            )
            if self._reconnection_registry is None
            else ""
        )
        child_environment["UNIVERSE_MANAGED_SHELL"] = "1"
        child_environment["UNIVERSE_SESSION_ANCHOR_REF"] = anchor_ref
        try:
            if self._reconnection_registry is not None:
                host_environment = dict(child_environment)
                host_environment.update(provider_command_environment)
                host_environment.setdefault("TERM", "xterm-256color")
                host_environment.setdefault("COLORTERM", "truecolor")
                shell_args = ["/d", "/q"]
                if provider_command_environment:
                    shell_args.append("/v:on")
                shell_args.append("/k")
                client = self._reconnection_registry.launch(
                    anchor_ref,
                    shell=shell_executable,
                    cwd=Path(session.cwd),
                    shell_args=tuple(shell_args),
                    host_kind="SESSION",
                    owner_ref=anchor_ref,
                    channel_lookup_file=(
                        session_lookup_path(terminal_id) if channel_enabled else None
                    ),
                    channel_bootstrap_token=channel_bootstrap_token,
                    channel_session_token=channel_session_token,
                    environment=host_environment,
                    cols=session.cols,
                    rows=session.rows,
                )
                session.backend = ReconnectionPty(
                    client,
                    f"terminal-host-{os.getpid()}-{terminal_id}",
                    after_cursor=(
                        (2**64 - 1)
                    if profile == "SUPERVISED_STDIO"
                    and bool(getattr(client, "reused_existing", False))
                        else 0
                    ),
                )
                session.backend_owner = "RUST_RECONNECTION_HOST"
                session.reconnection_host_id = session.backend.host_id
                session.host_runtime_versions = session.backend.runtime_versions
                session.host_compatibility = session.backend.compatibility
                session.protocol_state = session.backend.protocol_state
            else:
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
        write_managed_shell_identity(shell_identity_path, session)
        session.managed_shell.record_cli_launch(at=time.time())
        if session.managed_shell.shell is not None:
            session.managed_shell.last_state = CLI_STARTING
        session.state = "LIVE"
        with self._lock:
            self._sessions[terminal_id] = session
        if isinstance(session.backend, ReconnectionPty):
            if (
                not session.backend.reused_existing
                or session.protocol_state in {"NEW", "FAILED"}
            ):
                try:
                    session.backend.execute(
                        b"\x1b[1;1R"
                        + provider_command.encode("utf-8")
                        + b"\r\n"
                    )
                except Exception as error:  # noqa: BLE001 - surface Host dispatch failure
                    session.managed_shell.record_failure_evidence(
                        "CLI_START_FAILED", {"detail": str(error)}
                    )
                    try:
                        self.terminate(terminal_id, audit_context=audit_context)
                    except Exception:
                        pass
                    raise TerminalHostError(
                        "TERMINAL_SPAWN_FAILED",
                        str(error) or "failed to dispatch CLI through Session Host",
                    ) from error
        created = session.public()
        self.record_audit_event(
            "TERMINAL_CREATED",
            terminal=created,
            context=audit_context,
            details={
                "managed_shell_identity_file": str(shell_identity_path),
                "session_anchor_ref": anchor_ref,
                "backend_owner": session.backend_owner,
                "reconnection_host_id": session.reconnection_host_id,
                "cwd": session.cwd,
                "executable": session.executable,
                "model_ref": session.model_ref,
                "effort": session.effort,
                "launch_profile": session.launch_profile,
                "cols": session.cols,
                "rows": session.rows,
                "created_at": session.created_at,
            },
        )
        # The Supervisor must drain and monitor every managed shell even
        # when no UI client subscribes.  Lifecycle polling therefore
        # starts with ownership, not with presentation attachment.
        self._ensure_pump(session)
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
        if not (isinstance(cli_pid, int) and cli_pid > 0):
            probes = host_process_probes()
            if probes is not None:
                observed = observe_process_tree(
                    shell.shell,
                    is_alive=probes["is_alive"],
                    children_of=probes["children_of"],
                    start_time_of=probes["start_time_of"],
                    pty_responsive=self._pty_responsive(session),
                )
                if len(observed.cli_children) == 1:
                    cli = observed.cli_children[0]
                    cli_pid = cli.pid
                    cli_started_at = cli.started_at
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
            provider_session_ref=str(evidence.get("provider_session_ref") or ""),
            session_anchor_ref=str(evidence.get("session_anchor_ref") or ""),
            observed_at=str(evidence.get("observed_at") or ""),
        )
        shell.record_attach_evidence(attach)
        identity_path = str(
            getattr(session, "managed_shell_identity_file", "") or ""
        ).strip()
        if identity_path:
            write_managed_shell_identity(Path(identity_path), session)
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
        identity_state = self._managed_shell_identity_state(session)
        if identity_state not in {"VERIFIED", "NOT_CONFIGURED"}:
            previous_state = shell.last_state
            shell.last_state = identity_state
            shell.record_failure_evidence(
                identity_state,
                {
                    "identity_file": getattr(
                        session, "managed_shell_identity_file", ""
                    )
                },
            )
            if identity_state != previous_state:
                self.record_audit_event(
                    "TERMINAL_MANAGED_STATE",
                    terminal=session.public(),
                    context={
                        "lifecycle_state": identity_state,
                        "previous_state": previous_state,
                    },
                )
            return {
                "terminal_id": terminal_id,
                "state": identity_state,
                "recovery": [],
                "identity_file": getattr(
                    session, "managed_shell_identity_file", ""
                ),
            }
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
    def _managed_shell_identity_state(session: TerminalSession) -> str:
        path_text = str(
            getattr(session, "managed_shell_identity_file", "") or ""
        ).strip()
        if not path_text:
            # Test doubles and legacy imported rows can omit the file;
            # every newly created managed terminal sets it before spawn.
            return "NOT_CONFIGURED"
        try:
            payload = json.loads(Path(path_text).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return MANAGED_SHELL_IDENTITY_MISSING
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return MANAGED_SHELL_IDENTITY_INVALID
        shell = getattr(session.managed_shell, "shell", None)
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema") != MANAGED_SHELL_IDENTITY_SCHEMA
            or str(payload.get("terminal_id") or "") != session.terminal_id
            or str(payload.get("session_anchor_ref") or "")
            != session.session_anchor_ref
            or str(payload.get("supervisor_session_id") or "")
            != session.supervisor_session_id
            or shell is None
        ):
            return MANAGED_SHELL_IDENTITY_MISMATCH
        try:
            recorded = ProcessIdentity(
                pid=int(payload.get("shell_pid") or 0),
                started_at=float(payload.get("shell_started_at") or 0.0),
            )
        except (TypeError, ValueError):
            return MANAGED_SHELL_IDENTITY_INVALID
        return "VERIFIED" if recorded.matches(shell) else MANAGED_SHELL_IDENTITY_MISMATCH

    def poll_managed_shell(
        self,
        terminal_id: str,
        *,
        probes: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        result = self.sample_managed_shell(
            terminal_id, probes=probes, now=now
        )
        state = str(result.get("state") or "")
        if state not in MANAGED_SHELL_RECLAIM_STATES:
            result["reclaimed"] = False
            return result
        try:
            closed = self.close(
                terminal_id,
                audit_context={"reason": f"MANAGED_SHELL_RECLAIM:{state}"},
            )
        except TerminalHostError:
            closed = {"status": "TERMINAL_ALREADY_RECLAIMED"}
        result["reclaimed"] = True
        result["reclaim_result"] = closed
        return result

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
        self,
        terminal_id: str,
        *,
        audit_context: Mapping[str, Any] | None = None,
        terminate_host: bool = False,
    ) -> dict[str, Any]:
        session = self.get(terminal_id)
        terminal = session.public()
        request_event = "TERMINATE_REQUESTED" if terminate_host else "DETACH_REQUESTED"
        self.record_audit_event(
            request_event, terminal=terminal, context=audit_context
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
        if terminate_host and isinstance(backend, ReconnectionPty):
            backend.client.shutdown()
        else:
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()
        if session.channel_broker is not None:
            session.channel_broker.close()
        try:
            identity_path = str(
                getattr(session, "managed_shell_identity_file", "") or ""
            ).strip()
            if identity_path:
                Path(identity_path).unlink(missing_ok=True)
            elif getattr(session, "cwd", ""):
                managed_shell_identity_path(
                    Path(session.cwd), session.terminal_id
                ).unlink(missing_ok=True)
        except OSError:
            pass
        event_type = "TERMINAL_TERMINATED" if terminate_host else "TERMINAL_DETACHED"
        self.record_audit_event(event_type, terminal=terminal, context=audit_context)
        return {"status": event_type, "terminal_id": session.terminal_id}

    def terminate(
        self, terminal_id: str, *, audit_context: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.close(
            terminal_id, audit_context=audit_context, terminate_host=True
        )

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
        try:
            if broker is not None:
                return broker.push(payload, on_result=on_result)
            if session.channel_enabled and isinstance(session.backend, ReconnectionPty):
                return session.backend.channel_push(payload)
        except Exception as error:  # noqa: BLE001 - preserve terminal error contract
            raise TerminalHostError("TERMINAL_CHANNEL_UNAVAILABLE", str(error)) from error
        raise TerminalHostError(
            "TERMINAL_CHANNEL_UNAVAILABLE",
            "terminal has no Host-owned message channel",
        )

    def channel_state(self, terminal_id: str) -> str:
        session = self.get(terminal_id)
        if session.channel_broker is not None:
            return "READY" if session.channel_broker.registered else "PENDING"
        if session.channel_enabled and isinstance(session.backend, ReconnectionPty):
            try:
                return session.backend.channel_state()
            except Exception as error:  # noqa: BLE001 - preserve terminal error contract
                raise TerminalHostError("TERMINAL_CHANNEL_UNAVAILABLE", str(error)) from error
        return "UNAVAILABLE"

    def channel_result(self, terminal_id: str, message_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        if session.channel_enabled and isinstance(session.backend, ReconnectionPty):
            try:
                return session.backend.channel_result(message_id)
            except Exception as error:  # noqa: BLE001 - preserve terminal error contract
                raise TerminalHostError("TERMINAL_CHANNEL_UNAVAILABLE", str(error)) from error
        return {"status": "EMPTY", "message_id": str(message_id)}

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
        session_anchor_ref: str = "",
    ) -> dict[str, Any] | None:
        wanted_project = str(project_id or "").strip()
        wanted_mode = str(mode or "").strip().upper()
        wanted_provider = str(provider or "").strip().upper()
        wanted_supervisor = str(supervisor_session_id or "").strip()
        wanted_anchor = str(session_anchor_ref or "").strip()
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._refresh_session_state(session)
        rows = [
            item
            for item in sessions
            if item.state == "LIVE"
            and (
                item.backend_owner != "RUST_RECONNECTION_HOST"
                or (
                    bool(item.reconnection_host_id)
                    and item.host_compatibility in {"CURRENT", "COMPATIBLE_OLD"}
                )
            )
            and item.project_id == wanted_project
            and item.mode == wanted_mode
            and (
                not wanted_provider
                or wanted_provider == "AUTO"
                or item.provider == wanted_provider
            )
            and (
                wanted_anchor
                or not wanted_supervisor
                or item.supervisor_session_id == wanted_supervisor
            )
            and (
                not wanted_anchor
                or item.session_anchor_ref == wanted_anchor
            )
        ]
        if not rows:
            return None
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows[0].public()

    def get_host(self, host_session_ref: str) -> TerminalSession:
        wanted = str(host_session_ref or "").strip()
        if not wanted:
            raise TerminalHostError(
                "HOST_SESSION_REF_REQUIRED", "host_session_ref is required"
            )
        with self._lock:
            matches = [
                session
                for session in self._sessions.values()
                if session.reconnection_host_id == wanted
            ]
        if len(matches) != 1:
            raise TerminalHostError(
                "HOST_SESSION_NOT_FOUND", "host_session_ref is not uniquely managed"
            )
        session = matches[0]
        self._refresh_session_state(session)
        self._refresh_host_projection(session)
        if session.state != "LIVE":
            raise TerminalHostError("HOST_SESSION_NOT_LIVE", "Host session is not live")
        if session.host_compatibility not in {"CURRENT", "COMPATIBLE_OLD"}:
            raise TerminalHostError(
                "HOST_SESSION_INCOMPATIBLE", "Host session runtime tuple is incompatible"
            )
        return session

    def begin_provider_initialization(self, host_session_ref: str) -> str:
        session = self.get_host(host_session_ref)
        transition = getattr(session.backend, "protocol_initialize_begin", None)
        if not callable(transition):
            raise TerminalHostError(
                "HOST_PROTOCOL_CONTROL_UNAVAILABLE",
                "Host does not own provider protocol state",
            )
        state = str(transition() or "UNKNOWN")
        session.protocol_state = state
        return state

    def complete_provider_initialization(self, host_session_ref: str) -> str:
        session = self.get_host(host_session_ref)
        transition = getattr(session.backend, "protocol_initialize_complete", None)
        if not callable(transition):
            raise TerminalHostError(
                "HOST_PROTOCOL_CONTROL_UNAVAILABLE",
                "Host does not own provider protocol state",
            )
        state = str(transition() or "UNKNOWN")
        session.protocol_state = state
        return state

    def fail_provider_initialization(self, host_session_ref: str) -> str:
        session = self.get_host(host_session_ref)
        transition = getattr(session.backend, "protocol_initialize_failed", None)
        if not callable(transition):
            raise TerminalHostError(
                "HOST_PROTOCOL_CONTROL_UNAVAILABLE",
                "Host does not own provider protocol state",
            )
        state = str(transition() or "UNKNOWN")
        session.protocol_state = state
        return state

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

    @staticmethod
    def _refresh_host_projection(session: TerminalSession) -> None:
        if session.backend_owner != "RUST_RECONNECTION_HOST":
            return
        backend = session.backend
        if backend is None:
            session.host_compatibility = "INCOMPATIBLE"
            session.protocol_state = "UNKNOWN"
            return
        try:
            session.host_runtime_versions = backend.runtime_versions
            session.host_compatibility = backend.compatibility
            session.protocol_state = backend.protocol_state
        except ReconnectionHostError:
            session.host_compatibility = "INCOMPATIBLE"
            session.protocol_state = "UNKNOWN"

    def _refresh_session_state(self, session: TerminalSession) -> None:
        if session.state != "LIVE":
            return
        self._refresh_host_projection(session)
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
        supervised_stdio = session.launch_profile == "SUPERVISED_STDIO"
        waiter: queue.Queue = queue.Queue(maxsize=0 if supervised_stdio else 256)
        with session.lock:
            if session.state != "LIVE":
                waiter.put_nowait(None)
                return waiter
            if supervised_stdio:
                # A machine JSONL consumer needs every immutable byte emitted
                # before it attached.  A rendered screen snapshot can begin in
                # the middle of an event and cannot preserve protocol framing.
                for _cursor, chunk in session.output_chunks:
                    waiter.put_nowait(bytes(chunk))
            elif session.screen_snapshot:
                # Interactive attachments receive the current screen projection.
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
        awaiting_bootstrap = bool(
            session.bootstrap_input and not session.bootstrap_delivered
        )
        bootstrap_tail = b""
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
                    # poll_managed_shell wraps sample_managed_shell with
                    # terminal-scoped reclaim for terminal states.
                    sampled = self.poll_managed_shell(
                        session.terminal_id, now=now
                    )
                    if sampled.get("reclaimed"):
                        break
                except Exception:  # noqa: BLE001 - sampling never kills a PTY
                    pass
            try:
                chunk = backend.read(0.2)
            except Exception as error:
                # A reconnectable Host may briefly reject or drop one IPC read
                # while its listener is accepting concurrent UI and monitor
                # requests. Treat the read as terminal only when the Host also
                # proves that its owned process is no longer live.
                if self._backend_is_alive(backend) is not False:
                    time.sleep(0.05)
                    continue
                self._mark_backend_exit(session, str(error))
                break
            if not chunk:
                if self._backend_is_alive(backend) is False:
                    self._mark_backend_exit(session)
                    break
                continue
            if awaiting_bootstrap:
                bootstrap_tail += chunk
                if provider_cli_ready_for_bootstrap(
                    session.provider, bootstrap_tail
                ):
                    try:
                        backend.write(session.bootstrap_input)
                    except Exception:  # noqa: BLE001 - lifecycle retry remains active
                        pass
                    else:
                        session.bootstrap_delivered = True
                        awaiting_bootstrap = False
                        self.record_audit_event(
                            "SESSION_BOOTSTRAP_INPUT_WRITTEN",
                            terminal=session.public(),
                            context={
                                "source": "SUPERVISOR_SESSION_BOOTSTRAP",
                                "access_surface": "SUPERVISOR",
                            },
                            details={
                                "byte_count": len(session.bootstrap_input),
                                "content_persisted": False,
                            },
                        )
                elif len(bootstrap_tail) > 16384:
                    bootstrap_tail = bootstrap_tail[-8192:]
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
    grok_session_id: str = "",
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
    resume_arguments = resume_argv(name, resume_session_ref)
    argv.extend(resume_arguments)
    if name == "GROK" and not resume_arguments:
        session_id = str(grok_session_id or "").strip()
        if session_id:
            argv.extend(("--session-id", session_id))
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


def startup_input(provider: str, resume_session_ref: str) -> bytes:
    """Return the first provider input required to materialize a new session."""

    name = str(provider or "").strip().upper()
    if resume_argv(name, resume_session_ref) or name not in {"GROK", "CODEX"}:
        return b""
    return SESSION_BOOTSTRAP_PROMPT.encode("utf-8") + b"\r"


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
