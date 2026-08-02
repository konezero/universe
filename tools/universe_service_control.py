"""Local Universe service lifecycle helpers (status / stop / start / restart).

Packaging entry points use these helpers. They do not create project authority.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import os
# Subprocess starts only the fixed Universe server entry with sys.executable.
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = Path(__file__).resolve().with_name("universe_server.py")


def default_data_dir() -> Path:
    portable = os.environ.get("UNIVERSE_DATA_DIR")
    if portable:
        return Path(portable).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def default_state_path() -> Path:
    override = os.environ.get("UNIVERSE_STATE_FILE")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "server.json"


def _is_loopback_http_origin(endpoint: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "http"
        or host is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def default_database_path() -> Path:
    override = os.environ.get("UNIVERSE_DATABASE")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "universe.sqlite3"


def default_log_path() -> Path:
    override = os.environ.get("UNIVERSE_LOG_FILE")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "service.log"


def default_mode_registry_path() -> Path:
    override = os.environ.get("UNIVERSE_MODE_REGISTRY")
    if override:
        return Path(override).expanduser()
    return (
        Path(__file__).resolve().parents[1]
        / ".ai"
        / "runtime"
        / "project_instance"
        / "mode_registry.json"
    )


def load_state(path: Path | None = None) -> dict[str, Any] | None:
    state_path = (path or default_state_path()).expanduser()
    if not state_path.is_file():
        return None
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
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
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(
                ctypes.c_void_p(handle), ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def probe_health(endpoint: str, *, timeout: float = 2.0) -> dict[str, Any] | None:
    if not endpoint or not _is_loopback_http_origin(endpoint):
        return None
    try:
        # The endpoint is a validated loopback HTTP origin.
        with urlopen(  # nosec B310
            endpoint.rstrip("/") + "/health", timeout=timeout
        ) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return payload if isinstance(payload, dict) else None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None


def request_graceful_shutdown(
    endpoint: str,
    token: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    if not endpoint or not token or not _is_loopback_http_origin(endpoint):
        return None
    request = Request(
        endpoint.rstrip("/") + "/v1/service/shutdown",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        # The request URL uses the validated loopback origin.
        with urlopen(  # nosec B310
            request, timeout=timeout
        ) as response:
            raw = response.read().decode("utf-8")
            value = json.loads(raw) if raw else {}
            return value if isinstance(value, dict) else None
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def service_status(state_path: Path | None = None) -> dict[str, Any]:
    path = (state_path or default_state_path()).expanduser()
    state = load_state(path)
    if state is None:
        return {
            "schema": "universe.local-service-control.v1",
            "status": "STOPPED",
            "state_file": str(path),
            "pid": None,
            "pid_running": False,
            "endpoint": None,
            "health": None,
        }
    pid = state.get("pid")
    try:
        pid_int = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        pid_int = 0
    running = pid_is_running(pid_int)
    endpoint = str(state.get("endpoint") or "") or None
    health = probe_health(endpoint) if endpoint and running else None
    if health and health.get("status") == "READY":
        status = "READY"
    elif running:
        status = "STARTING_OR_UNHEALTHY"
    else:
        status = "STOPPED"
    return {
        "schema": "universe.local-service-control.v1",
        "status": status,
        "state_file": str(path),
        "pid": pid_int or None,
        "pid_running": running,
        "endpoint": endpoint,
        "database": state.get("database"),
        "universe": state.get("universe"),
        "started_at": state.get("started_at"),
        "health": health,
    }


def stop_service(
    state_path: Path | None = None,
    *,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    path = (state_path or default_state_path()).expanduser()
    before = service_status(path)
    state = load_state(path)
    pid = before.get("pid")
    if not before.get("pid_running") or not pid:
        return {
            "schema": "universe.local-service-control.v1",
            "status": "ALREADY_STOPPED",
            "state_file": str(path),
            "previous": before,
        }
    pid_int = int(pid)
    endpoint = str((state or {}).get("endpoint") or "")
    token = str((state or {}).get("token") or "")
    receipt = request_graceful_shutdown(endpoint, token)
    if receipt is None or receipt.get("status") != "SERVICE_SHUTDOWN_ACCEPTED":
        return {
            "schema": "universe.local-service-control.v1",
            "status": "STOP_FAILED",
            "state_file": str(path),
            "error": "AUTHENTICATED_GRACEFUL_SHUTDOWN_UNAVAILABLE",
            "destructive_fallback_performed": False,
            "previous": before,
        }
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_is_running(pid_int):
            break
        time.sleep(0.15)
    after = service_status(path)
    return {
        "schema": "universe.local-service-control.v1",
        "status": "STOPPED" if not after.get("pid_running") else "STOP_TIMEOUT",
        "state_file": str(path),
        "previous": before,
        "current": after,
        "shutdown_receipt": receipt,
        "destructive_fallback_performed": False,
    }


def start_service(
    *,
    state_path: Path | None = None,
    database_path: Path | None = None,
    mode_registry: Path | None = None,
    open_ui: bool = True,
    python_executable: str | None = None,
    log_path: Path | None = None,
    working_directory: Path | None = None,
    wait_seconds: float = 12.0,
) -> dict[str, Any]:
    path = (state_path or default_state_path()).expanduser()
    database = (database_path or default_database_path()).expanduser()
    registry = (mode_registry or default_mode_registry_path()).expanduser()
    workdir = (working_directory or ROOT).expanduser()
    current = service_status(path)
    if current.get("status") == "READY" and current.get("pid_running"):
        return {
            "schema": "universe.local-service-control.v1",
            "status": "ALREADY_RUNNING",
            "current": current,
        }
    python = python_executable or sys.executable
    log_file = (log_path or default_log_path()).expanduser()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        python,
        str(SERVER_SCRIPT),
        "serve",
        "--state-file",
        str(path),
        "--database",
        str(database),
        "--mode-registry",
        str(registry),
    ]
    if open_ui:
        args.append("--open-ui")
    else:
        args.append("--no-open-ui")
    stdout = open(log_file, "a", encoding="utf-8")  # noqa: SIM115 - detached child keeps handle
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    # sys.executable and SERVER_SCRIPT are fixed local executable paths.
    process = subprocess.Popen(  # nosec B603
        args,
        cwd=str(workdir),
        stdout=stdout,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=os.name != "nt",
    )
    stdout.close()
    deadline = time.time() + wait_seconds
    latest = service_status(path)
    while time.time() < deadline:
        latest = service_status(path)
        if latest.get("status") == "READY":
            break
        if process.poll() is not None and latest.get("status") != "READY":
            break
        time.sleep(0.25)
    return {
        "schema": "universe.local-service-control.v1",
        "status": "READY" if latest.get("status") == "READY" else "START_ISSUED",
        "launcher_pid": process.pid,
        "log_file": str(log_file),
        "current": latest,
        "command": args,
    }


def restart_service(
    *,
    state_path: Path | None = None,
    database_path: Path | None = None,
    mode_registry: Path | None = None,
    open_ui: bool = False,
    working_directory: Path | None = None,
) -> dict[str, Any]:
    path = state_path or default_state_path()
    stop_result = stop_service(path)
    start_result = start_service(
        state_path=path,
        database_path=database_path,
        mode_registry=mode_registry,
        open_ui=open_ui,
        working_directory=working_directory,
    )
    return {
        "schema": "universe.local-service-control.v1",
        "status": start_result.get("status"),
        "stop": stop_result,
        "start": start_result,
    }
