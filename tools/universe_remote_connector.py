from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlsplit


CONNECTOR_SCHEMA = "universe.remote-connector.v1"
CONFIG_SCHEMA = "universe.remote-connector-config.v1"
TRANSPORT_KIND = "SSH_REVERSE_TUNNEL"
HOST_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|"
    r"\[[0-9A-Fa-f:]+\])$"
)
USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


class RemoteConnectorError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def default_data_dir() -> Path:
    override = os.environ.get("UNIVERSE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def default_connector_state_path() -> Path:
    override = os.environ.get("UNIVERSE_REMOTE_CONNECTOR_STATE")
    return (
        Path(override).expanduser()
        if override
        else default_data_dir() / "remote-connector.json"
    )


def default_connector_config_path() -> Path:
    override = os.environ.get("UNIVERSE_REMOTE_CONNECTOR_CONFIG")
    return (
        Path(override).expanduser()
        if override
        else default_data_dir() / "remote-connector-config.json"
    )


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _required_text(value: Any, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID", f"{field} is required"
        )
    normalized = value.strip()
    if len(normalized) > limit:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID", f"{field} exceeds {limit} characters"
        )
    return normalized


def _port(value: Any, field: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID", f"{field} must be an integer"
        )
    if value < minimum or value > 65535:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID",
            f"{field} must be {minimum}..65535",
        )
    return value


def _https_origin(value: Any) -> str:
    normalized = _required_text(value, "public_base_url", limit=2048).rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID",
            "public_base_url must be one HTTPS origin without credentials, query, or path",
        )
    return normalized


def _loopback_gateway_endpoint(value: Any) -> str:
    normalized = _required_text(value, "gateway_endpoint", limit=2048).rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_GATEWAY_INVALID",
            "gateway endpoint must be one loopback HTTP origin",
        )
    return normalized


def normalize_connector_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID", "connector configuration is required"
        )
    kind = str(value.get("transport_kind") or TRANSPORT_KIND).strip().upper()
    if kind != TRANSPORT_KIND:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID",
            f"unsupported transport_kind: {kind or 'EMPTY'}",
        )
    ssh_host = _required_text(value.get("ssh_host"), "ssh_host", limit=253)
    if not HOST_PATTERN.fullmatch(ssh_host) or ".." in ssh_host:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID", "ssh_host is invalid"
        )
    ssh_user = _required_text(value.get("ssh_user"), "ssh_user", limit=64)
    if not USER_PATTERN.fullmatch(ssh_user):
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_INVALID", "ssh_user is invalid"
        )
    identity_file = (
        Path(_required_text(value.get("identity_file"), "identity_file", limit=2048))
        .expanduser()
        .resolve()
    )
    if not identity_file.is_file():
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_IDENTITY_UNAVAILABLE",
            "identity_file must name an existing private key file",
        )
    known_hosts_raw = str(value.get("known_hosts_file") or "").strip()
    known_hosts_file = (
        Path(known_hosts_raw).expanduser().resolve()
        if known_hosts_raw
        else (Path.home() / ".ssh" / "known_hosts").resolve()
    )
    if not known_hosts_file.is_file():
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_KNOWN_HOSTS_UNAVAILABLE",
            "known_hosts_file must exist before non-interactive connection",
        )
    return {
        "schema": CONFIG_SCHEMA,
        "transport_kind": TRANSPORT_KIND,
        "public_base_url": _https_origin(value.get("public_base_url")),
        "ssh_host": ssh_host,
        "ssh_port": _port(value.get("ssh_port", 22), "ssh_port"),
        "ssh_user": ssh_user,
        "remote_port": _port(
            value.get("remote_port", 18443), "remote_port", minimum=1024
        ),
        "identity_file": str(identity_file),
        "known_hosts_file": str(known_hosts_file),
        "remote_bind_host": "127.0.0.1",
    }


def save_connector_config(
    value: Any, path: Path = default_connector_config_path()
) -> dict[str, Any]:
    config = normalize_connector_config(value)
    _write_json_atomic(path, config)
    return config


def load_connector_config(
    path: Path = default_connector_config_path(),
) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_CONFIG_UNAVAILABLE", str(error), HTTPStatus.NOT_FOUND
        ) from error
    return normalize_connector_config(value)


def build_ssh_command(
    *, ssh_executable: Path, config: Mapping[str, Any], gateway_endpoint: str
) -> list[str]:
    normalized = normalize_connector_config(config)
    endpoint = _loopback_gateway_endpoint(gateway_endpoint)
    local_port = urlsplit(endpoint).port
    if local_port is None:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_GATEWAY_INVALID", "gateway port is required"
        )
    executable = ssh_executable.expanduser().resolve()
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_SSH_UNAVAILABLE", "verified native ssh.exe is required"
        )
    remote = f"127.0.0.1:{normalized['remote_port']}:127.0.0.1:{local_port}"
    return [
        str(executable),
        "-NT",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={normalized['known_hosts_file']}",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(normalized["ssh_port"]),
        "-i",
        normalized["identity_file"],
        "-R",
        remote,
        f"{normalized['ssh_user']}@{normalized['ssh_host']}",
    ]


def _loopback_request(
    endpoint: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 2,
) -> tuple[int, bytes]:
    parsed = urlsplit(_loopback_gateway_endpoint(endpoint))
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=dict(headers or {}))
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def load_connector_state(path: Path = default_connector_state_path()) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_STATE_UNAVAILABLE", str(error), HTTPStatus.NOT_FOUND
        ) from error
    if not isinstance(value, dict) or value.get("schema") != CONNECTOR_SCHEMA:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_STATE_INVALID", "connector state schema is invalid"
        )
    return value


def _public_state(
    state: Mapping[str, Any], *, include_config: bool = False
) -> dict[str, Any]:
    public_fields = (
        "schema",
        "status",
        "transport_kind",
        "public_base_url",
        "started_at",
        "ssh_process_alive",
        "ssh_return_code",
    )
    result = {key: state[key] for key in public_fields if key in state}
    config_path = state.get("config_path")
    if include_config and isinstance(config_path, str):
        try:
            result["configuration"] = load_connector_config(Path(config_path))
        except RemoteConnectorError:
            result["configuration"] = None
    return result


def connector_status(
    state_path: Path = default_connector_state_path(),
    *,
    include_config: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    try:
        state = load_connector_state(state_path)
        status, payload = _loopback_request(str(state["control_endpoint"]), "/health")
        if status != HTTPStatus.OK:
            raise RemoteConnectorError(
                "REMOTE_CONNECTOR_UNHEALTHY", f"health status {status}"
            )
        health = json.loads(payload.decode("utf-8"))
        return _public_state({**state, **health}, include_config=include_config)
    except (RemoteConnectorError, OSError, UnicodeError, json.JSONDecodeError):
        saved_path = (config_path or default_connector_config_path()).expanduser()
        result = {
            "schema": CONNECTOR_SCHEMA,
            "status": "OFFLINE",
            "state_file": str(state_path.expanduser().resolve()),
            "configuration_available": saved_path.is_file(),
        }
        if include_config and saved_path.is_file():
            try:
                result["configuration"] = load_connector_config(saved_path)
            except RemoteConnectorError:
                result["configuration"] = None
        return result


class ConnectorControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        process: subprocess.Popen[bytes],
        control_token: str,
        public_base_url: str,
    ) -> None:
        super().__init__(address, ConnectorControlHandler)
        self.process = process
        self.control_token = control_token
        self.public_base_url = public_base_url


class ConnectorControlHandler(BaseHTTPRequestHandler):
    server: ConnectorControlServer

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        return_code = self.server.process.poll()
        body = json.dumps(
            {
                "status": "READY" if return_code is None else "TUNNEL_FAILED",
                "transport_kind": TRANSPORT_KIND,
                "public_base_url": self.server.public_base_url,
                "ssh_process_alive": return_code is None,
                "ssh_return_code": return_code,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/stop":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Authorization") != f"Bearer {self.server.control_token}":
            self.send_error(HTTPStatus.UNAUTHORIZED)
            return
        self.send_response(HTTPStatus.ACCEPTED)
        self.send_header("Content-Length", "0")
        self.end_headers()
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:
        return


def start_connector(
    *,
    config: Any,
    gateway_endpoint: str,
    ssh_executable: Path,
    python_executable: Path,
    state_path: Path = default_connector_state_path(),
    config_path: Path = default_connector_config_path(),
) -> dict[str, Any]:
    current = connector_status(state_path, include_config=True)
    if current.get("status") == "READY":
        normalized = normalize_connector_config(config)
        if current.get("configuration") == normalized:
            return current
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_ALREADY_RUNNING",
            "stop the current connector before changing its configuration",
            HTTPStatus.CONFLICT,
        )
    normalized = save_connector_config(config, config_path)
    gateway = _loopback_gateway_endpoint(gateway_endpoint)
    python = python_executable.expanduser().resolve()
    ssh = ssh_executable.expanduser().resolve()
    if not python.is_file() or python.suffix.lower() != ".exe":
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_PYTHON_UNAVAILABLE",
            "verified native python.exe is required",
        )
    build_ssh_command(ssh_executable=ssh, config=normalized, gateway_endpoint=gateway)
    command = [
        str(python),
        str(Path(__file__).resolve()),
        "serve",
        "--config",
        str(config_path.expanduser().resolve()),
        "--gateway-endpoint",
        gateway,
        "--ssh-executable",
        str(ssh),
        "--state-file",
        str(state_path.expanduser().resolve()),
    ]
    log_path = default_data_dir() / "remote-connector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open("ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    process = subprocess.Popen(  # nosec B603
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=output,
        close_fds=True,
        creationflags=creationflags,
    )
    output.close()
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        status = connector_status(state_path, include_config=True)
        if status.get("status") in {"READY", "TUNNEL_FAILED"}:
            return status
        if process.poll() is not None:
            break
        time.sleep(0.15)
    raise RemoteConnectorError(
        "REMOTE_CONNECTOR_START_FAILED",
        f"connector did not become ready; inspect {log_path}",
        HTTPStatus.SERVICE_UNAVAILABLE,
    )


def stop_connector(
    state_path: Path = default_connector_state_path(),
) -> dict[str, Any]:
    try:
        state = load_connector_state(state_path)
    except RemoteConnectorError:
        return {"schema": CONNECTOR_SCHEMA, "status": "OFFLINE"}
    try:
        _loopback_request(
            str(state["control_endpoint"]),
            "/stop",
            method="POST",
            body=b"{}",
            headers={"Authorization": f"Bearer {state['control_token']}"},
            timeout=3,
        )
    except (RemoteConnectorError, OSError):
        pass
    deadline = time.monotonic() + 7
    while time.monotonic() < deadline:
        if connector_status(state_path).get("status") == "OFFLINE":
            state_path.expanduser().unlink(missing_ok=True)
            return {"schema": CONNECTOR_SCHEMA, "status": "OFFLINE"}
        time.sleep(0.1)
    return {"schema": CONNECTOR_SCHEMA, "status": "STOP_TIMEOUT"}


def serve_connector(args: argparse.Namespace) -> int:
    config = load_connector_config(args.config)
    command = build_ssh_command(
        ssh_executable=args.ssh_executable,
        config=config,
        gateway_endpoint=args.gateway_endpoint,
    )
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ssh_log_path = default_data_dir() / "remote-ssh.log"
    ssh_log_path.parent.mkdir(parents=True, exist_ok=True)
    ssh_output = ssh_log_path.open("ab")
    process = subprocess.Popen(  # nosec B603
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=ssh_output,
        stderr=subprocess.STDOUT,
        close_fds=True,
        creationflags=creationflags,
    )
    ssh_output.close()
    time.sleep(0.75)
    if process.poll() is not None:
        raise RemoteConnectorError(
            "REMOTE_CONNECTOR_SSH_EXITED",
            "ssh exited before tunnel readiness with code "
            f"{process.returncode}; inspect {ssh_log_path}",
        )
    control_token = secrets.token_urlsafe(32)
    server = ConnectorControlServer(
        ("127.0.0.1", 0),
        process=process,
        control_token=control_token,
        public_base_url=config["public_base_url"],
    )
    _, control_port = server.server_address[:2]
    state = {
        "schema": CONNECTOR_SCHEMA,
        "status": "READY",
        "transport_kind": TRANSPORT_KIND,
        "pid": os.getpid(),
        "ssh_pid": process.pid,
        "started_at": utc_now(),
        "public_base_url": config["public_base_url"],
        "ssh_host": config["ssh_host"],
        "ssh_port": config["ssh_port"],
        "ssh_user": config["ssh_user"],
        "remote_port": config["remote_port"],
        "identity_file_name": Path(config["identity_file"]).name,
        "gateway_endpoint": args.gateway_endpoint,
        "control_endpoint": f"http://127.0.0.1:{control_port}",
        "control_token": control_token,
        "config_path": str(args.config.expanduser().resolve()),
    }
    _write_json_atomic(args.state_file, state)
    print(json.dumps(_public_state(state), ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            current = load_connector_state(args.state_file)
        except RemoteConnectorError:
            current = {}
        if current.get("pid") == os.getpid():
            args.state_file.expanduser().unlink(missing_ok=True)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Universe outbound remote connector")
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument(
        "--state-file", type=Path, default=default_connector_state_path()
    )
    stop = commands.add_parser("stop")
    stop.add_argument("--state-file", type=Path, default=default_connector_state_path())
    serve = commands.add_parser("serve")
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument("--gateway-endpoint", required=True)
    serve.add_argument("--ssh-executable", type=Path, required=True)
    serve.add_argument(
        "--state-file", type=Path, default=default_connector_state_path()
    )
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "status":
        print(
            json.dumps(connector_status(args.state_file), ensure_ascii=False, indent=2)
        )
        return 0
    if args.command == "stop":
        result = stop_connector(args.state_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "OFFLINE" else 1
    try:
        return serve_connector(args)
    except RemoteConnectorError as error:
        print(
            json.dumps(
                {"status": "FAILED", "error_code": error.code, "detail": error.detail},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
