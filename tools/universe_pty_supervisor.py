#!/usr/bin/env python3
"""Standalone PTY supervisor. Outlives the Universe app and owns CLI ConPTYs."""

from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import socket
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# Strip AI-session markers immediately so PTY children never inherit them,
# regardless of how this supervisor process was spawned.
for _k in (
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CONVERSATION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "GROK_SESSION_ID",
    "XAI_SESSION_ID",
    "GROK_CONVERSATION_ID",
):
    os.environ.pop(_k, None)
del _k

from universe_app.pty_supervisor import (  # noqa: E402
    SCHEMA,
    default_state_path,
)
from universe_app.reconnection_host import ReconnectionHostRegistry  # noqa: E402
from universe_app.session_bus import SessionBusError  # noqa: E402
from universe_app.terminal_host import (  # noqa: E402
    TerminalHost,
    TerminalHostError,
)

API_SCHEMA = "universe.pty-supervisor-api.v1"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def reconnection_registry_from_environment(
    state_path: Path,
) -> ReconnectionHostRegistry | None:
    enabled = str(os.environ.get("UNIVERSE_RECONNECTION_HOST_ENABLED") or "")
    if enabled.strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    configured = str(os.environ.get("UNIVERSE_RECONNECTION_HOST_BINARY") or "").strip()
    if configured:
        binary = Path(configured).expanduser()
    else:
        host_root = TOOLS / "session_host" / "target"
        candidates = (
            host_root / "release" / "universe-session-host.exe",
            host_root / "debug" / "universe-session-host.exe",
        )
        binary = next((path for path in candidates if path.is_file()), candidates[0])
    if not binary.is_file():
        raise RuntimeError(f"Rust Reconnection Host binary is absent: {binary}")
    registry_text = str(
        os.environ.get("UNIVERSE_RECONNECTION_HOST_REGISTRY") or ""
    ).strip()
    registry_root = (
        Path(registry_text).expanduser()
        if registry_text
        else state_path.parent / "reconnection-hosts"
    )
    return ReconnectionHostRegistry(registry_root, binary)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PtySupervisor:
    def __init__(
        self,
        host: TerminalHost | None = None,
        *,
        reclaim_poll_seconds: float = 5.0,
    ) -> None:
        self.host = host or TerminalHost()
        self._lock = threading.Lock()
        self._attaches: dict[str, tuple[str, queue.Queue]] = {}
        self._reclaim_poll_seconds = max(0.1, float(reclaim_poll_seconds))
        self._monitor_stop = threading.Event()
        self._reconcile_reconnection_hosts()
        # Legacy Python-owned ConPTY handles cannot be handed to this process.
        # Reclaim only exact PID/start-time instances after the Rust Host path
        # had a chance to reattach its durable sessions.
        self.host.reclaim_orphaned_managed_shells()
        self._monitor = threading.Thread(
            target=self._monitor_orphaned_shells,
            name="universe-pty-supervisor-reclaimer",
            daemon=True,
        )
        self._monitor.start()

    def _reconcile_reconnection_hosts(self) -> None:
        reconcile = getattr(self.host, "reconcile_reconnection_hosts", None)
        if callable(reconcile):
            reconcile()

    def _monitor_orphaned_shells(self) -> None:
        while not self._monitor_stop.wait(self._reclaim_poll_seconds):
            try:
                self._reconcile_reconnection_hosts()
                self.host.reclaim_orphaned_managed_shells()
            except Exception:  # noqa: BLE001 - monitor must remain resident
                continue

    def close(self) -> None:
        self._monitor_stop.set()
        if self._monitor is not threading.current_thread():
            self._monitor.join(timeout=max(1.0, self._reclaim_poll_seconds * 2))

    def public_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        pid = row.get("pid")
        if not (isinstance(pid, int) and pid > 0):
            terminal_id = str(row.get("terminal_id") or "")
            try:
                session = self.host.get(terminal_id)
                live_pid = getattr(session, "live_pid", None)
                pid = live_pid() if callable(live_pid) else None
            except TerminalHostError:
                pid = None
        row["pid"] = int(pid) if isinstance(pid, int) and pid > 0 else None
        return row

    def attach(self, terminal_id: str) -> str:
        waiter = self.host.subscribe(terminal_id)
        attach_id = "att_" + secrets.token_hex(8)
        with self._lock:
            self._attaches[attach_id] = (terminal_id, waiter)
        return attach_id

    def detach(self, attach_id: str) -> None:
        with self._lock:
            item = self._attaches.pop(attach_id, None)
        if item is None:
            return
        terminal_id, waiter = item
        self.host.unsubscribe(terminal_id, waiter)

    def read_attach(self, attach_id: str, timeout: float) -> tuple[bytes, bool]:
        with self._lock:
            item = self._attaches.get(attach_id)
        if item is None:
            return b"", True
        _terminal_id, waiter = item
        try:
            chunk = waiter.get(timeout=max(0.05, timeout))
        except queue.Empty:
            return b"", False
        if chunk is None:
            return b"", True
        return bytes(chunk), False


class Handler(BaseHTTPRequestHandler):
    server: "Server"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self) -> None:
        self._send(
            HTTPStatus.UNAUTHORIZED,
            {"schema": API_SCHEMA, "status": "UNAUTHORIZED", "error_code": "TOKEN_REQUIRED"},
        )

    def _authorize(self) -> bool:
        expected = f"Bearer {self.server.token}"
        if (self.headers.get("Authorization") or "") != expected:
            self._unauthorized()
            return False
        return True

    def _audit_context(self, fallback_source: str) -> dict[str, str]:
        return {
            "source": str(
                self.headers.get("X-Universe-Audit-Source") or fallback_source
            ),
            "access_surface": str(
                self.headers.get("X-Universe-Access-Surface") or "UNKNOWN"
            ),
            "request_id": str(self.headers.get("X-Universe-Request-Id") or ""),
        }

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1_000_000))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == "/health":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": SCHEMA,
                    "status": "READY",
                    "pid": os.getpid(),
                    "started_at": self.server.started_at,
                },
            )
            return
        if not self._authorize():
            return
        supervisor = self.server.supervisor
        if path == "/v1/audit-events":
            query = parse_qs(parsed.query)
            try:
                limit = int((query.get("limit") or ["200"])[0])
            except ValueError:
                limit = 200
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "TERMINAL_AUDIT_EVENTS_COLLECTED",
                    "events": supervisor.host.audit_events(
                        terminal_id=(query.get("terminal_id") or [""])[0],
                        limit=limit,
                    ),
                },
            )
            return
        if path == "/v1/bus/directory":
            self._send(HTTPStatus.OK, supervisor.host.bus.directory(supervisor.host))
            return
        if path == "/v1/bus/unread":
            self._send(HTTPStatus.OK, supervisor.host.bus_unread())
            return
        if path == "/v1/bus/inbox":
            query = parse_qs(parsed.query)
            try:
                payload = supervisor.host.bus.inbox(
                    supervisor.host,
                    terminal_id=(query.get("terminal_id") or [""])[0],
                    project_id=(query.get("project_id") or [""])[0],
                    mode=(query.get("mode") or [""])[0],
                    provider=(query.get("provider") or [""])[0],
                    room_id=(query.get("room_id") or [""])[0],
                    headers_only=(query.get("headers") or [""])[0] == "1",
                )
            except SessionBusError as error:
                self._send(
                    error.status,
                    {
                        "schema": API_SCHEMA,
                        "status": "ERROR",
                        "error_code": error.code,
                        "detail": error.detail,
                    },
                )
                return
            self._send(HTTPStatus.OK, payload)
            return
        if path == "/v1/terminals":
            rows = [supervisor.public_session(item) for item in supervisor.host.list_sessions()]
            self._send(
                HTTPStatus.OK,
                {"schema": API_SCHEMA, "status": "OK", "terminals": rows},
            )
            return
        terminal_history = path.split("/")
        if (
            len(terminal_history) == 5
            and terminal_history[1:3] == ["v1", "terminals"]
            and terminal_history[4] == "history"
        ):
            query = parse_qs(parsed.query)
            try:
                before_raw = str((query.get("before_cursor") or [""])[0]).strip()
                before_cursor = int(before_raw) if before_raw else None
                limit = int((query.get("limit") or ["100"])[0])
                payload = supervisor.host.history(
                    terminal_history[3], before_cursor=before_cursor, limit=limit
                )
            except ValueError:
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "schema": API_SCHEMA,
                        "status": "ERROR",
                        "error_code": "TERMINAL_HISTORY_CURSOR_INVALID",
                    },
                )
                return
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(HTTPStatus.OK, payload)
            return
        attach_read = path.split("/")
        if (
            len(attach_read) == 7
            and attach_read[1:3] == ["v1", "terminals"]
            and attach_read[4] == "attach"
            and attach_read[6] == "read"
        ):
            timeout = 0.2
            query = parse_qs(parsed.query)
            if query.get("timeout"):
                try:
                    timeout = float(query["timeout"][0])
                except ValueError:
                    timeout = 0.2
            chunk, closed = supervisor.read_attach(attach_read[5], timeout)
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "OK",
                    "data_b64": base64.b64encode(chunk).decode("ascii") if chunk else "",
                    "closed": closed,
                },
            )
            return
        if path.startswith("/v1/terminals/") and path.count("/") == 3:
            terminal_id = path.rsplit("/", 1)[-1]
            try:
                session = supervisor.host.get(terminal_id)
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "OK",
                    "terminal": supervisor.public_session(session.public()),
                },
            )
            return
        self._send(HTTPStatus.NOT_FOUND, {"schema": API_SCHEMA, "status": "NOT_FOUND"})

    def do_POST(self) -> None:
        if not self._authorize():
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        supervisor = self.server.supervisor
        body = self._read_json()
        if path == "/v1/bus/messages":
            try:
                created = supervisor.host.bus.post(supervisor.host, body)
            except SessionBusError as error:
                self._send(
                    error.status,
                    {
                        "schema": API_SCHEMA,
                        "status": "ERROR",
                        "error_code": error.code,
                        "detail": error.detail,
                    },
                )
                return
            self._send(HTTPStatus.CREATED, created)
            return
        bus_ack = path.split("/")
        if (
            len(bus_ack) == 6
            and bus_ack[1:4] == ["v1", "bus", "messages"]
            and bus_ack[5] == "ack"
        ):
            try:
                result = supervisor.host.bus.ack(
                    bus_ack[4], str(body.get("terminal_id") or "")
                )
            except SessionBusError as error:
                self._send(
                    error.status,
                    {
                        "schema": API_SCHEMA,
                        "status": "ERROR",
                        "error_code": error.code,
                        "detail": error.detail,
                    },
                )
                return
            self._send(HTTPStatus.OK, result)
            return
        if path == "/v1/terminals":
            # Anchor-before-spawn: this Supervisor owns the terminal, so it
            # resolves the Session Anchor itself when the caller did not supply
            # one.  The PTY never invents its own coordinate after starting.
            spawn_anchor_ref = str(body.get("session_anchor_ref") or "").strip()
            if not spawn_anchor_ref:
                spawn_anchor_ref = "session_anchor_" + secrets.token_hex(12)
            try:
                created = supervisor.host.create(
                    project_id=str(body.get("project_id") or ""),
                    mode=str(body.get("mode") or "MASTER"),
                    cwd=str(body.get("cwd") or ""),
                    provider=str(body.get("provider") or "AUTO"),
                    model_ref=str(body.get("model_ref") or ""),
                    effort=str(body.get("effort") or "AUTO"),
                    supervisor_session_id=str(
                        body.get("supervisor_session_id") or ""
                    ),
                    session_anchor_ref=spawn_anchor_ref,
                    resume_session_ref=str(body.get("resume_session_ref") or ""),
                    launch_profile=str(body.get("launch_profile") or "INTERACTIVE"),
                    provider_arguments=tuple(body.get("provider_arguments") or ()),
                    provider_environment=(
                        dict(body.get("provider_environment") or {})
                        if isinstance(body.get("provider_environment"), Mapping)
                        else {}
                    ),
                    cols=int(body.get("cols") or 120),
                    rows=int(body.get("rows") or 32),
                    audit_context=self._audit_context("PTY_SUPERVISOR_CREATE"),
                )
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.CONFLICT,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(
                HTTPStatus.CREATED,
                {
                    "schema": API_SCHEMA,
                    "status": "CREATED",
                    "terminal": supervisor.public_session(created),
                },
            )
            return
        if (
            path.startswith("/v1/terminals/")
            and path.endswith("/managed-attach")
            and path.count("/") == 4
        ):
            terminal_id = path.split("/")[3]
            try:
                result = supervisor.host.record_managed_attach(terminal_id, body)
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.CONFLICT,
                    {
                        "schema": API_SCHEMA,
                        "status": "ERROR",
                        "error_code": error.code,
                    },
                )
                return
            self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
            return
        if path.startswith("/v1/terminals/") and path.endswith("/attach") and path.count("/") == 4:
            terminal_id = path.split("/")[3]
            try:
                attach_id = supervisor.attach(terminal_id)
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(
                HTTPStatus.CREATED,
                {"schema": API_SCHEMA, "status": "ATTACHED", "attach_id": attach_id},
            )
            return
        if path.startswith("/v1/terminals/") and path.endswith("/write"):
            terminal_id = path.split("/")[3]
            raw = str(body.get("data_b64") or "")
            try:
                data = base64.b64decode(raw) if raw else b""
                supervisor.host.write(
                    terminal_id,
                    data,
                    audit_context=self._audit_context("PTY_SUPERVISOR_WRITE"),
                )
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.CONFLICT,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(HTTPStatus.OK, {"schema": API_SCHEMA, "status": "OK"})
            return
        if path.startswith("/v1/terminals/") and path.endswith("/emit"):
            terminal_id = path.split("/")[3]
            raw = str(body.get("data_b64") or "")
            try:
                data = base64.b64decode(raw) if raw else b""
                supervisor.host.emit_output(terminal_id, data)
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.CONFLICT,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(HTTPStatus.OK, {"schema": API_SCHEMA, "status": "OK"})
            return
        if path.startswith("/v1/terminals/") and path.endswith("/resize"):
            terminal_id = path.split("/")[3]
            try:
                supervisor.host.resize(
                    terminal_id,
                    int(body.get("cols") or 120),
                    int(body.get("rows") or 32),
                )
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.CONFLICT,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(HTTPStatus.OK, {"schema": API_SCHEMA, "status": "OK"})
            return
        self._send(HTTPStatus.NOT_FOUND, {"schema": API_SCHEMA, "status": "NOT_FOUND"})

    def do_DELETE(self) -> None:
        if not self._authorize():
            return
        path = unquote(urlsplit(self.path).path)
        supervisor = self.server.supervisor
        parts = path.split("/")
        if len(parts) == 6 and parts[1:3] == ["v1", "terminals"] and parts[4] == "attach":
            supervisor.detach(parts[5])
            self._send(HTTPStatus.OK, {"schema": API_SCHEMA, "status": "DETACHED"})
            return
        if path.startswith("/v1/terminals/") and path.count("/") == 3:
            terminal_id = path.rsplit("/", 1)[-1]
            try:
                result = supervisor.host.close(
                    terminal_id,
                    audit_context=self._audit_context("PTY_SUPERVISOR_DELETE"),
                )
            except TerminalHostError as error:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"schema": API_SCHEMA, "status": "ERROR", "error_code": error.code},
                )
                return
            self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
            return
        self._send(HTTPStatus.NOT_FOUND, {"schema": API_SCHEMA, "status": "NOT_FOUND"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, token: str, host: TerminalHost | None = None) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.token = token
        self.supervisor = PtySupervisor(host)
        self.started_at = utc_now()


def main() -> int:
    state_path = default_state_path()
    token = secrets.token_urlsafe(24)
    audit_database_path = state_path.parent / "universe.sqlite3"
    reconnection_registry = reconnection_registry_from_environment(state_path)
    terminal_host = TerminalHost(
        audit_database_path=audit_database_path,
        reconnection_registry=reconnection_registry,
    )
    server = Server(token, host=terminal_host)
    host, port = server.server_address[:2]
    endpoint = f"http://{host}:{port}"
    _write_json_atomic(
        state_path,
        {
            "schema": SCHEMA,
            "status": "READY",
            "pid": os.getpid(),
            "started_at": server.started_at,
            "endpoint": endpoint,
            "token": token,
        },
    )
    terminal_host.record_audit_event(
        "SUPERVISOR_STARTED",
        context={"source": "PTY_SUPERVISOR", "access_surface": "SUPERVISOR"},
        details={
            "pid": os.getpid(),
            "endpoint": endpoint,
            "reconnection_host_enabled": reconnection_registry is not None,
        },
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        terminal_host.record_audit_event(
            "SUPERVISOR_STOPPING",
            context={"source": "PTY_SUPERVISOR", "access_surface": "SUPERVISOR"},
            details={"pid": os.getpid()},
        )
        server.supervisor.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
