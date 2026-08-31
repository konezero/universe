"""Client and lifecycle helper for the standalone PTY supervisor."""

from __future__ import annotations

import base64
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from universe_app.terminal_host import TerminalHostError

SCHEMA = "universe.pty-supervisor.v1"
STATE_NAME = "pty-supervisor.json"


def default_data_dir() -> Path:
    override = os.environ.get("UNIVERSE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def default_state_path() -> Path:
    override = os.environ.get("UNIVERSE_PTY_SUPERVISOR_STATE")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / STATE_NAME


def pid_is_running(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = (
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        )
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(
                ctypes.c_void_p(handle), ctypes.byref(exit_code)
            ):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def load_state(path: Path | None = None) -> dict[str, Any] | None:
    state_path = path or default_state_path()
    if not state_path.is_file():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return None
    return payload


def probe_health(endpoint: str, token: str, *, timeout: float = 1.5) -> dict[str, Any] | None:
    if not endpoint or not token:
        return None
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/health",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def supervisor_script() -> Path:
    return Path(__file__).resolve().parents[1] / "universe_pty_supervisor.py"


def _reconnection_host_binary_available() -> bool:
    host_root = supervisor_script().parent / "session_host" / "target"
    return any(
        path.is_file()
        for path in (
            host_root / "release" / "universe-session-host.exe",
            host_root / "debug" / "universe-session-host.exe",
        )
    )


def spawn_supervisor(*, state_path: Path | None = None) -> None:
    env = os.environ.copy()
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
        env.pop(_k, None)
    if (
        "UNIVERSE_RECONNECTION_HOST_ENABLED" not in env
        and _reconnection_host_binary_available()
    ):
        env["UNIVERSE_RECONNECTION_HOST_ENABLED"] = "1"
    if state_path is not None:
        env["UNIVERSE_PTY_SUPERVISOR_STATE"] = str(state_path)
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    subprocess.Popen(  # noqa: S603
        [sys.executable, str(supervisor_script())],
        env=env,
        cwd=str(supervisor_script().parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


def ensure_supervisor(
    *,
    state_path: Path | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    path = state_path or default_state_path()
    existing = load_state(path)
    if existing is not None:
        pid = int(existing.get("pid") or 0)
        endpoint = str(existing.get("endpoint") or "")
        token = str(existing.get("token") or "")
        if pid_is_running(pid) and probe_health(endpoint, token) is not None:
            return existing
    spawn_supervisor(state_path=path)
    deadline = time.time() + max(1.0, timeout)
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = load_state(path)
        if last is not None:
            token = str(last.get("token") or "")
            endpoint = str(last.get("endpoint") or "")
            if probe_health(endpoint, token) is not None:
                return last
        time.sleep(0.15)
    raise TerminalHostError(
        "PTY_SUPERVISOR_UNAVAILABLE",
        "standalone PTY supervisor did not become ready",
    )


def _terminate_supervisor_process(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_terminate = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
        if not handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.TerminateProcess(handle, 0))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 15)
    except OSError:
        return False
    return True


def restart_supervisor(*, state_path: Path | None = None, timeout: float = 8.0) -> dict[str, Any]:
    """Restart the Supervisor while Host-owned sessions remain reconnectable."""
    path = state_path or default_state_path()
    previous = load_state(path) or {}
    pid = int(previous.get("pid") or 0)
    if pid > 0 and pid_is_running(pid):
        if not _terminate_supervisor_process(pid):
            raise TerminalHostError(
                "PTY_SUPERVISOR_STOP_FAILED",
                "could not stop the recorded PTY supervisor process",
            )
        deadline = time.time() + max(1.0, timeout)
        while time.time() < deadline:
            if not pid_is_running(pid):
                break
            time.sleep(0.1)
        if pid_is_running(pid):
            raise TerminalHostError(
                "PTY_SUPERVISOR_STOP_TIMEOUT",
                "PTY supervisor did not exit before restart timeout",
            )
    current = ensure_supervisor(state_path=path, timeout=timeout)
    return {
        "schema": SCHEMA,
        "status": "RESTARTED",
        "previous_pid": pid or None,
        "pid": int(current.get("pid") or 0) or None,
        "endpoint": str(current.get("endpoint") or ""),
        "previous_supervisor_ended": bool(pid),
        "terminal_continuity": "HOST_OWNED_RECONCILED_LEGACY_ENDED",
    }


@dataclass
class SupervisedSession:
    payload: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return dict(self.payload)

    @property
    def terminal_id(self) -> str:
        return str(self.payload.get("terminal_id") or "")

    @property
    def project_id(self) -> str:
        return str(self.payload.get("project_id") or "")

    @property
    def mode(self) -> str:
        return str(self.payload.get("mode") or "")

    @property
    def executable(self) -> str:
        return str(self.payload.get("executable") or "")

    @property
    def cwd(self) -> str:
        return str(self.payload.get("cwd") or "")

    @property
    def state(self) -> str:
        return str(self.payload.get("state") or "")


class SupervisedTerminalHost:
    """Universe-side handle to the standalone PTY supervisor."""

    def __init__(self, *, state_path: Path | None = None) -> None:
        self._state_path = state_path or default_state_path()
        self._state = ensure_supervisor(state_path=self._state_path)
        self._lock = threading.Lock()

    def _refresh(self) -> dict[str, Any]:
        self._state = ensure_supervisor(state_path=self._state_path)
        return self._state

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 8.0,
        audit_source: str = "UNIVERSE_SERVER",
    ) -> dict[str, Any]:
        state = self._refresh()
        body = None
        headers = {
            "Authorization": f"Bearer {state['token']}",
            "Accept": "application/json",
            "X-Universe-Audit-Source": audit_source,
            "X-Universe-Request-Id": "termreq_" + os.urandom(8).hex(),
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            str(state["endpoint"]).rstrip("/") + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.reason
            try:
                parsed = json.loads(error.read().decode("utf-8"))
                error_code = str(parsed.get("error_code") or "").strip()
                error_detail = str(parsed.get("detail") or "").strip()
                detail = (
                    f"{error_code}: {error_detail}"
                    if error_code and error_detail
                    else error_code or error_detail or str(detail)
                )
            except Exception:
                pass
            raise TerminalHostError(str(detail), str(detail)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TerminalHostError(
                "PTY_SUPERVISOR_UNAVAILABLE", str(error)
            ) from error
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TerminalHostError("PTY_SUPERVISOR_RESULT_INVALID", str(error)) from error
        return parsed if isinstance(parsed, dict) else {}

    def list_sessions(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/terminals").get("terminals") or [])

    def list_hosts(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/terminals").get("hosts") or [])

    def get(self, terminal_id: str) -> SupervisedSession:
        payload = self._request("GET", f"/v1/terminals/{quote(terminal_id, safe='')}")
        terminal = payload.get("terminal")
        if not isinstance(terminal, dict):
            raise TerminalHostError("TERMINAL_NOT_FOUND", "terminal session does not exist")
        return SupervisedSession(terminal)

    def get_host(self, host_session_ref: str) -> SupervisedSession:
        wanted = str(host_session_ref or "").strip()
        if not wanted:
            raise TerminalHostError(
                "HOST_SESSION_REF_REQUIRED", "host_session_ref is required"
            )
        matches = [
            item
            for item in self.list_sessions()
            if str(item.get("host_session_ref") or item.get("reconnection_host_id") or "")
            == wanted
        ]
        if len(matches) != 1:
            raise TerminalHostError(
                "HOST_SESSION_NOT_FOUND", "Host session does not exist"
            )
        return SupervisedSession(matches[0])

    def history(
        self, terminal_id: str, *, before_cursor: int | None = None, limit: int = 100
    ) -> dict[str, Any]:
        query = {"limit": str(limit)}
        if before_cursor is not None:
            query["before_cursor"] = str(before_cursor)
        return self._request(
            "GET",
            f"/v1/terminals/{quote(terminal_id, safe='')}/history?{urlencode(query)}",
        )

    def create(self, **kwargs: Any) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/v1/terminals",
            payload=kwargs,
            timeout=90.0,
            audit_source="UNIVERSE_TERMINAL_CREATE",
        )
        terminal = payload.get("terminal")
        if not isinstance(terminal, dict):
            raise TerminalHostError("TERMINAL_SPAWN_FAILED", "supervisor create returned no terminal")
        return terminal

    def close(self, terminal_id: str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v1/terminals/{quote(terminal_id, safe='')}",
            audit_source="UNIVERSE_TERMINAL_DELETE",
        )

    def terminate(self, terminal_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/terminate",
            audit_source="UNIVERSE_TERMINAL_TERMINATE",
        )

    def channel_state(self, terminal_id: str) -> str:
        payload = self._request(
            "GET", f"/v1/terminals/{quote(terminal_id, safe='')}/channel"
        )
        return str(payload.get("channel_state") or "UNAVAILABLE")

    def push_channel(
        self,
        terminal_id: str,
        payload: Mapping[str, Any],
        *,
        on_result: Any = None,
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/channel",
            payload=dict(payload),
            audit_source="UNIVERSE_TERMINAL_CHANNEL",
        ).get("channel_result")
        result = dict(result) if isinstance(result, Mapping) else {}
        message_id = str(result.get("message_id") or payload.get("message_id") or "")
        if on_result is not None and message_id:
            def poll_result() -> None:
                deadline = time.monotonic() + 24 * 60 * 60
                while time.monotonic() < deadline:
                    try:
                        observed = self._request(
                            "GET",
                            f"/v1/terminals/{quote(terminal_id, safe='')}/channel/results/{quote(message_id, safe='')}",
                        ).get("channel_result")
                    except TerminalHostError:
                        time.sleep(0.25)
                        continue
                    if isinstance(observed, Mapping) and observed.get("status") in {"ACCEPTED", "DUPLICATE"}:
                        on_result(dict(observed))
                        return
                    time.sleep(0.2)
            threading.Thread(
                target=poll_result,
                name=f"universe-channel-result-{terminal_id}",
                daemon=True,
            ).start()
        return result

    def record_managed_attach(
        self, terminal_id: str, evidence: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/managed-attach",
            payload=dict(evidence),
            audit_source="UNIVERSE_SESSION_START",
        )

    def write(self, terminal_id: str, data: bytes) -> None:
        self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/write",
            payload={"data_b64": base64.b64encode(data).decode("ascii")},
            audit_source="UNIVERSE_TERMINAL_STREAM",
        )

    def emit_output(self, terminal_id: str, data: bytes) -> None:
        """Fan out display-only bytes without sending them to CLI stdin."""
        self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/emit",
            payload={"data_b64": base64.b64encode(data).decode("ascii")},
        )

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/resize",
            payload={"cols": cols, "rows": rows},
        )

    def audit_events(
        self, *, terminal_id: str = "", limit: int = 200
    ) -> list[dict[str, Any]]:
        query = {"limit": str(max(1, min(int(limit or 200), 1000)))}
        if terminal_id:
            query["terminal_id"] = terminal_id
        payload = self._request("GET", "/v1/audit-events?" + urlencode(query))
        return list(payload.get("events") or [])

    def bus_directory(self) -> dict[str, Any]:
        return self._request("GET", "/v1/bus/directory")

    def bus_unread(self) -> dict[str, Any]:
        return self._request("GET", "/v1/bus/unread")

    def bus_post(self, value: dict[str, Any] | None) -> dict[str, Any]:
        return self._request("POST", "/v1/bus/messages", payload=dict(value or {}))

    def bus_inbox(self, **kwargs: Any) -> dict[str, Any]:
        query = {
            key: ("1" if key == "headers_only" else str(value))
            for key, value in kwargs.items()
            if value not in (None, "", False)
        }
        if "headers_only" in query:
            query["headers"] = query.pop("headers_only")
        suffix = ("?" + urlencode(query)) if query else ""
        return self._request("GET", "/v1/bus/inbox" + suffix)

    def bus_ack(self, message_id: str, terminal_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/bus/messages/{quote(message_id, safe='')}/ack",
            payload={"terminal_id": terminal_id},
        )

    def find_live(
        self,
        *,
        project_id: str,
        mode: str,
        provider: str = "",
        supervisor_session_id: str = "",
        session_anchor_ref: str = "",
    ) -> dict[str, Any] | None:
        wanted_provider = str(provider or "").strip().upper()
        wanted_supervisor = str(supervisor_session_id or "").strip()
        wanted_anchor = str(session_anchor_ref or "").strip()
        rows = [
            item
            for item in self.list_sessions()
            if item.get("state") == "LIVE"
            and item.get("project_id") == project_id
            and str(item.get("mode") or "").upper() == str(mode or "").upper()
            and (
                not wanted_provider
                or wanted_provider == "AUTO"
                or str(item.get("provider") or "").upper() == wanted_provider
            )
            and (
                wanted_anchor
                or not wanted_supervisor
                or str(item.get("supervisor_session_id") or "")
                == wanted_supervisor
            )
            and (
                not wanted_anchor
                or str(item.get("session_anchor_ref") or "") == wanted_anchor
            )
        ]
        if not rows:
            return None
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[0]

    def subscribe(self, terminal_id: str) -> queue.Queue:
        attach = self._request(
            "POST", f"/v1/terminals/{quote(terminal_id, safe='')}/attach"
        )
        attach_id = str(attach.get("attach_id") or "")
        if not attach_id:
            raise TerminalHostError("TERMINAL_ATTACH_FAILED", "supervisor attach failed")
        # Machine JSONL is lossless. A bounded client queue made a slow
        # parser silently discard arbitrary protocol bytes and even the close
        # sentinel, so attachment backlogs must remain intact until consumed.
        waiter: queue.Queue = queue.Queue()
        stop = threading.Event()

        def pump() -> None:
            path = (
                f"/v1/terminals/{quote(terminal_id, safe='')}"
                f"/attach/{quote(attach_id, safe='')}/read"
            )
            ack_sequence: int | None = None
            while not stop.is_set():
                query = "?timeout=0.2"
                if ack_sequence is not None:
                    query += f"&ack_sequence={ack_sequence}"
                try:
                    payload = self._request("GET", path + query, timeout=3.0)
                except TerminalHostError:
                    try:
                        terminal = self.get(terminal_id).payload
                    except TerminalHostError:
                        break
                    if str(terminal.get("state") or "").upper() != "LIVE":
                        break
                    if stop.wait(0.5):
                        break
                    continue
                chunk_b64 = str(payload.get("data_b64") or "")
                if chunk_b64:
                    waiter.put_nowait(base64.b64decode(chunk_b64))
                sequence = payload.get("sequence")
                if isinstance(sequence, int) and sequence > 0:
                    ack_sequence = sequence
                if payload.get("closed"):
                    break
            waiter.put_nowait(None)

        thread = threading.Thread(
            target=pump, name=f"pty-sup-{terminal_id}", daemon=True
        )
        waiter.stop = stop  # type: ignore[attr-defined]
        waiter.attach_id = attach_id  # type: ignore[attr-defined]
        waiter.terminal_id = terminal_id  # type: ignore[attr-defined]
        thread.start()
        waiter.thread = thread  # type: ignore[attr-defined]
        return waiter

    def unsubscribe(self, terminal_id: str, waiter: queue.Queue) -> None:
        stop = getattr(waiter, "stop", None)
        if stop is not None:
            stop.set()
        attach_id = str(getattr(waiter, "attach_id", "") or "")
        if attach_id:
            try:
                self._request(
                    "DELETE",
                    f"/v1/terminals/{quote(terminal_id, safe='')}/attach/{quote(attach_id, safe='')}",
                )
            except TerminalHostError:
                pass
