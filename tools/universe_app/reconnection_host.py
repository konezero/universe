"""Anchor-first discovery and reattachment for the Rust Reconnection Host.

The registry is deliberately narrower than ``TerminalHost``.  It does not own
Universe lifecycle meaning; it validates one Host discovery record and exposes
the Host-owned PTY through the backend shape used by the Python Supervisor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .windows_process import process_is_alive, process_start_time


STATE_SCHEMA = "universe.reconnection-host-state.v1"
RESPONSE_SCHEMA = "universe.reconnection-host-response.v1"
MAX_STATE_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
# The Rust Host accepts a 16 KiB JSON request. Base64 plus envelope metadata
# leaves less than 12 KiB for raw PTY input, so stream larger writes safely.
HOST_WRITE_CHUNK_BYTES = 8 * 1024
DEFAULT_STALE_AFTER_SECONDS = 24 * 60 * 60
SESSION_MARKER_ENVIRONMENT = (
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CONVERSATION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "GROK_SESSION_ID",
    "XAI_SESSION_ID",
    "GROK_CONVERSATION_ID",
)


class ReconnectionHostError(RuntimeError):
    """Raised when discovery, validation, or authenticated IPC fails."""


def provision_private_registry_directory(
    root: Path,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Create the registry and restrict it to the current user plus SYSTEM."""

    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target, 0o700)
    except OSError:
        pass
    if (platform_name or os.name) != "nt":
        return
    values = environment if environment is not None else os.environ
    username = str(values.get("USERNAME") or "").strip()
    domain = str(values.get("USERDOMAIN") or "").strip()
    if not username:
        raise ReconnectionHostError("Windows user identity is unavailable for Host ACL")
    principal = f"{domain}\\{username}" if domain else username
    system_root = Path(str(values.get("SystemRoot") or r"C:\Windows"))
    executable = system_root / "System32" / "icacls.exe"
    completed = subprocess.run(
        [
            str(executable),
            str(target),
            "/inheritance:r",
            "/grant:r",
            f"{principal}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ReconnectionHostError(f"Host registry ACL provisioning failed: {detail}")


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconnectionHostError(f"Host state {name} must be a non-empty string")
    return value


def _require_positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ReconnectionHostError(f"Host state {name} must be a positive integer")
    return value


@dataclass(frozen=True)
class ReconnectionHostState:
    anchor_ref: str
    host_id: str
    endpoint: str
    pid: int
    started_at_unix_ms: int
    auth_token: str
    child_pid: int | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReconnectionHostState":
        if value.get("schema") != STATE_SCHEMA:
            raise ReconnectionHostError("Host state schema is unsupported")
        child_pid = value.get("child_pid")
        if child_pid is not None:
            child_pid = _require_positive_int(child_pid, "child_pid")
        return cls(
            anchor_ref=_require_string(value.get("anchor_ref"), "anchor_ref"),
            host_id=_require_string(value.get("host_id"), "host_id"),
            endpoint=_require_string(value.get("endpoint"), "endpoint"),
            pid=_require_positive_int(value.get("pid"), "pid"),
            started_at_unix_ms=_require_positive_int(
                value.get("started_at_unix_ms"), "started_at_unix_ms"
            ),
            auth_token=_require_string(value.get("auth_token"), "auth_token"),
            child_pid=child_pid,
        )


class ReconnectionHostClient:
    def __init__(self, state: ReconnectionHostState, *, timeout: float = 5.0) -> None:
        self.state = state
        self.timeout = timeout

    def request(self, action: str, **fields: Any) -> dict[str, Any]:
        endpoint = self.state.endpoint
        if not endpoint.startswith("tcp://"):
            raise ReconnectionHostError("Host endpoint transport is unsupported")
        address = endpoint.removeprefix("tcp://")
        try:
            host, port_text = address.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, TypeError) as error:
            raise ReconnectionHostError("Host endpoint is invalid") from error
        body = {"token": self.state.auth_token, "action": action, **fields}
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            with socket.create_connection((host, port), timeout=self.timeout) as client:
                client.sendall(encoded)
                response = bytearray()
                while b"\n" not in response:
                    chunk = client.recv(8192)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > MAX_RESPONSE_BYTES:
                        raise ReconnectionHostError("Host response exceeds size limit")
        except OSError as error:
            raise ReconnectionHostError(f"Host IPC failed: {error}") from error
        try:
            payload = json.loads(response.split(b"\n", 1)[0])
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ReconnectionHostError("Host response is invalid JSON") from error
        if payload.get("schema") != RESPONSE_SCHEMA:
            raise ReconnectionHostError("Host response schema is unsupported")
        if payload.get("status") != "OK":
            code = payload.get("error_code", "HOST_REQUEST_FAILED")
            detail = payload.get("detail", "Host request failed")
            raise ReconnectionHostError(f"{code}: {detail}")
        return payload

    def status(self) -> dict[str, Any]:
        last_error: ReconnectionHostError | None = None
        for attempt in range(3):
            try:
                return self.request("status")["host"]
            except ReconnectionHostError as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.025)
        raise ReconnectionHostError(f"Host status failed: {last_error}") from last_error

    def shutdown(self) -> None:
        self.request("shutdown")


class ReconnectionHostRegistry:
    """Discover a live Host by exact Anchor, or launch one when absent."""

    def __init__(
        self,
        root: Path,
        binary: Path,
        *,
        start_tolerance: float = 10.0,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self.root = Path(root)
        self.binary = Path(binary)
        self.start_tolerance = start_tolerance
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))
        self._launched_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._prepared = False

    def prepare(self) -> None:
        if self._prepared:
            return
        provision_private_registry_directory(self.root)
        self._prepared = True

    def state_path(self, anchor_ref: str) -> Path:
        if not anchor_ref.strip():
            raise ValueError("anchor_ref must not be empty")
        digest = hashlib.sha256(anchor_ref.encode("utf-8")).hexdigest()
        return self.root / f"anchor-{digest}.json"

    def reap_launched_process(self, anchor_ref: str, *, timeout: float = 5.0) -> int | None:
        """Wait for a Host launched by this registry after an explicit shutdown."""
        process = self._launched_processes.get(anchor_ref)
        if process is None:
            return None
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self._launched_processes.pop(anchor_ref, None)
        return return_code

    def _read_state_path(self, path: Path) -> ReconnectionHostState:
        try:
            if path.stat().st_size > MAX_STATE_BYTES:
                raise ReconnectionHostError("Host state exceeds size limit")
            state = ReconnectionHostState.from_mapping(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except FileNotFoundError as error:
            raise ReconnectionHostError("Host state is absent") from error
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ReconnectionHostError(f"Host state is unreadable: {error}") from error
        return state

    def _read_state(self, anchor_ref: str) -> ReconnectionHostState:
        path = self.state_path(anchor_ref)
        state = self._read_state_path(path)
        if state.anchor_ref != anchor_ref:
            raise ReconnectionHostError("Host state Anchor does not match the requested Anchor")
        return state

    def cleanup_stale_records(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Remove only validated dead discovery records older than retention."""

        self.prepare()
        observed_at = time.time() if now is None else float(now)
        results: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("anchor-*.json")):
            if path.is_symlink() or not path.is_file():
                results.append({"path": str(path), "status": "INVALID_RECORD_PRESERVED"})
                continue
            try:
                state = self._read_state_path(path)
                if self.state_path(state.anchor_ref) != path:
                    raise ReconnectionHostError("Host state filename does not match Anchor")
            except ReconnectionHostError as error:
                results.append(
                    {
                        "path": str(path),
                        "status": "INVALID_RECORD_PRESERVED",
                        "detail": str(error),
                    }
                )
                continue
            observed_start = process_start_time(state.pid)
            expected_start = state.started_at_unix_ms / 1000.0
            exact_process_live = (
                process_is_alive(state.pid)
                and observed_start is not None
                and abs(observed_start - expected_start) <= self.start_tolerance
            )
            if exact_process_live:
                results.append(
                    {
                        "path": str(path),
                        "status": "LIVE_RECORD_PRESERVED",
                        "host_id": state.host_id,
                    }
                )
                continue
            try:
                age_seconds = max(0.0, observed_at - path.stat().st_mtime)
            except OSError as error:
                results.append(
                    {
                        "path": str(path),
                        "status": "INVALID_RECORD_PRESERVED",
                        "detail": str(error),
                    }
                )
                continue
            if age_seconds < self.stale_after_seconds:
                results.append(
                    {
                        "path": str(path),
                        "status": "STALE_RECORD_DEFERRED",
                        "host_id": state.host_id,
                        "age_seconds": age_seconds,
                    }
                )
                continue
            try:
                path.unlink()
            except OSError as error:
                results.append(
                    {
                        "path": str(path),
                        "status": "STALE_RECORD_REMOVE_FAILED",
                        "detail": str(error),
                    }
                )
                continue
            results.append(
                {
                    "path": str(path),
                    "status": "STALE_RECORD_REMOVED",
                    "host_id": state.host_id,
                    "age_seconds": age_seconds,
                }
            )
        return results

    def discover(self, anchor_ref: str) -> ReconnectionHostClient:
        state = self._read_state(anchor_ref)
        if not process_is_alive(state.pid):
            raise ReconnectionHostError("Host PID is not live")
        observed_start = process_start_time(state.pid)
        expected_start = state.started_at_unix_ms / 1000.0
        if observed_start is None or abs(observed_start - expected_start) > self.start_tolerance:
            raise ReconnectionHostError("Host PID start time does not match discovery state")
        client = ReconnectionHostClient(state)
        observed: dict[str, Any] | None = None
        last_handshake_error: ReconnectionHostError | None = None
        for attempt in range(5):
            try:
                observed = client.status()
                break
            except ReconnectionHostError as error:
                last_handshake_error = error
                if attempt < 4 and process_is_alive(state.pid):
                    time.sleep(0.025)
        if observed is None:
            raise ReconnectionHostError(
                f"Authenticated Host handshake failed: {last_handshake_error}"
            ) from last_handshake_error
        comparisons = {
            "anchor_ref": state.anchor_ref,
            "host_id": state.host_id,
            "pid": state.pid,
            "started_at_unix_ms": state.started_at_unix_ms,
        }
        for name, expected in comparisons.items():
            if observed.get(name) != expected:
                raise ReconnectionHostError(
                    f"Authenticated Host handshake returned a different {name}"
                )
        return client

    def list_live_clients(self) -> list[ReconnectionHostClient]:
        """Return every registry Host that passes authenticated LIVE handshake."""

        self.prepare()
        clients: list[ReconnectionHostClient] = []
        for path in sorted(self.root.glob("anchor-*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                state = self._read_state_path(path)
                if self.state_path(state.anchor_ref) != path:
                    continue
                client = self.discover(state.anchor_ref)
                if client.status().get("runtime_state") != "LIVE":
                    continue
            except ReconnectionHostError:
                continue
            clients.append(client)
        return clients

    def launch(
        self,
        anchor_ref: str,
        *,
        shell: str = "cmd.exe",
        cwd: Path | None = None,
        shell_args: Sequence[str] = (),
        environment: Mapping[str, str] | None = None,
        cols: int = 120,
        rows: int = 30,
        timeout: float = 10.0,
    ) -> ReconnectionHostClient:
        if not self.binary.is_file():
            raise ReconnectionHostError(f"Reconnection Host binary is absent: {self.binary}")
        self.prepare()
        state_path = self.state_path(anchor_ref)
        if state_path.exists():
            try:
                return self.discover(anchor_ref)
            except ReconnectionHostError as discovery_error:
                try:
                    stale_state = self._read_state(anchor_ref)
                except ReconnectionHostError as state_error:
                    raise ReconnectionHostError(
                        "Refusing to replace an unvalidated Host discovery record"
                    ) from state_error
                if process_is_alive(stale_state.pid):
                    raise ReconnectionHostError(
                        "Refusing to replace discovery for a live but unreachable Host"
                    ) from discovery_error
                state_path.unlink()
        token = secrets.token_urlsafe(32)
        args = [
            str(self.binary),
            "serve",
            "--state-file",
            str(state_path),
            "--anchor-ref",
            anchor_ref,
            "--token",
            token,
            "--shell",
            shell,
            "--cols",
            str(cols),
            "--rows",
            str(rows),
        ]
        if cwd is not None:
            args.extend(("--cwd", str(cwd)))
        for value in shell_args:
            args.extend(("--shell-arg", value))
        for name, value in (environment or {}).items():
            if not name or "=" in name:
                raise ValueError("environment names must be non-empty and exclude '='")
            args.extend(("--env", f"{name}={value}"))
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            args,
            cwd=self.binary.parent,
            env={
                name: value
                for name, value in os.environ.items()
                if name not in SESSION_MARKER_ENVIRONMENT
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        self._launched_processes[anchor_ref] = process
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                client = self.discover(anchor_ref)
                try:
                    os.chmod(state_path, 0o600)
                except OSError:
                    pass
                return client
            except ReconnectionHostError as error:
                last_error = error
                time.sleep(0.05)
        raise ReconnectionHostError(f"Host did not become discoverable: {last_error}")


class ReconnectionPty:
    """Supervisor PTY backend attached to a Host-owned terminal."""

    def __init__(
        self,
        client: ReconnectionHostClient,
        supervisor_id: str | None = None,
        *,
        after_cursor: int = 0,
    ) -> None:
        self.client = client
        self.supervisor_id = supervisor_id or f"supervisor-{os.getpid()}-{uuid.uuid4().hex}"
        attached = self.client.request("attach", supervisor_id=self.supervisor_id)["host"]
        self._pid = attached.get("child_pid")
        self._cursor = max(0, after_cursor)
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def host_id(self) -> str:
        return self.client.state.host_id

    @property
    def anchor_ref(self) -> str:
        return self.client.state.anchor_ref

    @property
    def output_cursor(self) -> int:
        return self._cursor

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ReconnectionHostError("PTY adapter is closed")
        raw = bytes(data)
        chunks = (
            [raw[offset : offset + HOST_WRITE_CHUNK_BYTES]
             for offset in range(0, len(raw), HOST_WRITE_CHUNK_BYTES)]
            if raw
            else [b""]
        )
        for chunk in chunks:
            self.client.request(
                "write",
                supervisor_id=self.supervisor_id,
                input_base64=base64.b64encode(chunk).decode("ascii"),
            )

    def read(self, timeout: float = 0.0) -> bytes:
        if self._closed:
            return b""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                response = self.client.request(
                    "read",
                    supervisor_id=self.supervisor_id,
                    after_cursor=self._cursor,
                )
            except ReconnectionHostError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
                continue
            output = response["output"]
            self._cursor = int(output["next_cursor"])
            data = base64.b64decode(output["data_base64"])
            if data or time.monotonic() >= deadline:
                return data
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

    def resize(self, cols: int, rows: int) -> None:
        self.client.request(
            "resize",
            supervisor_id=self.supervisor_id,
            cols=cols,
            rows=rows,
        )

    def is_alive(self) -> bool:
        # Preserve the distinction between a proven non-LIVE Host and an IPC
        # observation failure. TerminalHost treats an exception as UNKNOWN and
        # retries; returning False here would incorrectly retire a live session.
        return self.client.status().get("runtime_state") == "LIVE"

    isalive = is_alive

    def close(self) -> None:
        if self._closed:
            return
        self.client.request("detach", supervisor_id=self.supervisor_id)
        self._closed = True
