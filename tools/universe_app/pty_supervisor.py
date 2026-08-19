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
    if state_path is not None:
        env["UNIVERSE_PTY_SUPERVISOR_STATE"] = str(state_path)
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
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
    ) -> dict[str, Any]:
        state = self._refresh()
        body = None
        headers = {
            "Authorization": f"Bearer {state['token']}",
            "Accept": "application/json",
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
                detail = str(parsed.get("error_code") or parsed.get("detail") or detail)
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

    def get(self, terminal_id: str) -> SupervisedSession:
        payload = self._request("GET", f"/v1/terminals/{quote(terminal_id, safe='')}")
        terminal = payload.get("terminal")
        if not isinstance(terminal, dict):
            raise TerminalHostError("TERMINAL_NOT_FOUND", "terminal session does not exist")
        return SupervisedSession(terminal)

    def create(self, **kwargs: Any) -> dict[str, Any]:
        payload = self._request("POST", "/v1/terminals", payload=kwargs)
        terminal = payload.get("terminal")
        if not isinstance(terminal, dict):
            raise TerminalHostError("TERMINAL_SPAWN_FAILED", "supervisor create returned no terminal")
        return terminal

    def close(self, terminal_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/terminals/{quote(terminal_id, safe='')}")

    def write(self, terminal_id: str, data: bytes) -> None:
        self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/write",
            payload={"data_b64": base64.b64encode(data).decode("ascii")},
        )

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        self._request(
            "POST",
            f"/v1/terminals/{quote(terminal_id, safe='')}/resize",
            payload={"cols": cols, "rows": rows},
        )

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
    ) -> dict[str, Any] | None:
        wanted_provider = str(provider or "").strip().upper()
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
        waiter: queue.Queue = queue.Queue(maxsize=256)
        stop = threading.Event()

        def pump() -> None:
            path = (
                f"/v1/terminals/{quote(terminal_id, safe='')}"
                f"/attach/{quote(attach_id, safe='')}/read"
            )
            while not stop.is_set():
                try:
                    payload = self._request("GET", path + "?timeout=0.2", timeout=3.0)
                except TerminalHostError:
                    break
                chunk_b64 = str(payload.get("data_b64") or "")
                if chunk_b64:
                    try:
                        waiter.put_nowait(base64.b64decode(chunk_b64))
                    except queue.Full:
                        try:
                            waiter.get_nowait()
                        except queue.Empty:
                            pass
                if payload.get("closed"):
                    break
            try:
                waiter.put_nowait(None)
            except queue.Full:
                pass

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
