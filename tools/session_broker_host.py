"""Independent process host for durable provider session handles.

Universe owns goals, rooms, and policy.  This broker owns the live provider
connection so restarting the Universe HTTP server does not destroy a resumed
Claude, Codex, or Grok session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from project_master_host import ResidentModeSessionHost


SESSION_BROKER_SCHEMA = "universe.session-broker.v1"
SESSION_BROKER_STATE_SCHEMA = "universe.session-broker-state.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionBrokerError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SessionBrokerError(
            "SESSION_BROKER_REQUEST_INVALID", f"{field} is required", 400
        )
    return text


class SessionBrokerService:
    """Own provider hosts and their resume coordinates in one process."""

    def __init__(
        self,
        database_path: Path,
        *,
        host_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.host_factory = host_factory or self._default_host
        self._lock = threading.RLock()
        self._hosts: dict[str, tuple[tuple[str, ...], Any]] = {}

    def _default_host(self, descriptor: Mapping[str, Any]) -> ResidentModeSessionHost:
        chat_key = _required(descriptor.get("chat_key"), "descriptor.chat_key")
        store_key = hashlib.sha256(chat_key.encode("utf-8")).hexdigest()[:24]
        session_database = self.database_path.with_name(
            f"{self.database_path.stem}-{store_key}{self.database_path.suffix}"
        )
        return ResidentModeSessionHost(
            Path(_required(descriptor.get("repository_root"), "descriptor.repository_root")),
            f"session-broker-{chat_key[-12:]}",
            _required(descriptor.get("mode"), "descriptor.mode").upper(),
            session_database,
            actor_label=str(
                descriptor.get("alias")
                or f"{descriptor.get('project_id')} {descriptor.get('mode')}"
            ),
            session_node=str(descriptor.get("node") or descriptor.get("project_id")),
            target_kind="PROVIDER_SESSION",
        )

    @staticmethod
    def _fingerprint(descriptor: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(
            str(descriptor.get(field) or "")
            for field in (
                "provider",
                "provider_session_ref",
                "project_id",
                "node",
                "mode",
                "repository_root",
                "model_ref",
            )
        )

    def _host(self, descriptor: Mapping[str, Any]) -> Any:
        chat_key = _required(descriptor.get("chat_key"), "descriptor.chat_key")
        provider = _required(descriptor.get("provider"), "descriptor.provider").upper()
        provider_ref = _required(
            descriptor.get("provider_session_ref"), "descriptor.provider_session_ref"
        )
        fingerprint = self._fingerprint(descriptor)
        with self._lock:
            existing = self._hosts.get(chat_key)
            if existing is not None and existing[0] == fingerprint:
                return existing[1]
            if existing is not None:
                existing[1].close()
                self._hosts.pop(chat_key, None)
            host = self.host_factory(descriptor)
            observe = getattr(getattr(host, "store", None), "observe_provider_session", None)
            if not callable(observe):
                host.close()
                raise SessionBrokerError(
                    "SESSION_BROKER_HOST_INVALID",
                    "broker host cannot bind an exact provider session",
                    500,
                )
            observe(provider, provider_ref)
            self._hosts[chat_key] = (fingerprint, host)
            return host

    def turn(self, value: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = value.get("descriptor")
        if not isinstance(descriptor, Mapping):
            raise SessionBrokerError(
                "SESSION_BROKER_REQUEST_INVALID", "descriptor is required", 400
            )
        body = _required(value.get("body"), "body")
        if len(body) > 24000:
            raise SessionBrokerError(
                "SESSION_BROKER_MESSAGE_TOO_LARGE", "body exceeds 24000 characters", 413
            )
        provider = _required(descriptor.get("provider"), "descriptor.provider").upper()
        host = self._host(descriptor)
        try:
            connection = host.prepare(provider, session_action="RESUME")
            message_id = _required(value.get("message_id"), "message_id")
            result = host.reply(
                provider,
                {
                    "kind": "QUESTION",
                    "sender": "USER",
                    "body": body,
                    "message_id": message_id,
                    "runtime_context": {
                        "requested_mode": descriptor.get("mode"),
                        "node": descriptor.get("node"),
                        "current_anchor_ref": descriptor.get("current_anchor_ref")
                        or "UNKNOWN",
                        "commander_surface": "UNIVERSE_SESSION_BROKER",
                        "conversation_transport": "BROKER_IPC",
                    },
                },
            )
        except Exception as error:  # provider boundary
            code = str(
                getattr(error, "code", None) or str(error) or type(error).__name__
            ).upper()
            raise SessionBrokerError(code, str(error), 409) from error
        return {
            "schema": SESSION_BROKER_SCHEMA,
            "status": "SESSION_BROKER_TURN_COMPLETED",
            "chat_key": descriptor.get("chat_key"),
            "provider": provider,
            "body": str(result.get("text") or ""),
            "connection": connection,
            "runtime_state": "LIVE",
            "persistence_state": "SAVED",
        }

    def create_session(self, value: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = dict(value.get("descriptor") or value)
        chat_key = _required(descriptor.get("chat_key"), "descriptor.chat_key")
        provider = _required(descriptor.get("provider"), "descriptor.provider").upper()
        if descriptor.get("provider_session_ref"):
            raise SessionBrokerError(
                "SESSION_BROKER_NEW_SESSION_REF_FORBIDDEN",
                "a fresh broker session cannot supply a resume reference",
                409,
            )
        with self._lock:
            if chat_key in self._hosts:
                raise SessionBrokerError(
                    "SESSION_BROKER_SESSION_EXISTS",
                    "the broker chat key already owns a live session",
                    409,
                )
            host = self.host_factory(descriptor)
            try:
                connection = host.prepare(provider, session_action="NEW")
                active_ref = getattr(host, "active_provider_session_ref", lambda: None)()
                provider_ref = _required(active_ref, "provider_session_ref")
            except Exception as error:
                host.close()
                code = str(
                    getattr(error, "code", None) or str(error) or type(error).__name__
                ).upper()
                raise SessionBrokerError(code, str(error), 409) from error
            descriptor["provider"] = provider
            descriptor["provider_session_ref"] = provider_ref
            fingerprint = self._fingerprint(descriptor)
            self._hosts[chat_key] = (fingerprint, host)
        return {
            "schema": SESSION_BROKER_SCHEMA,
            "status": "SESSION_BROKER_SESSION_CREATED",
            "chat_key": chat_key,
            "provider": provider,
            "provider_session_ref": provider_ref,
            "connection": connection,
            "runtime_state": "LIVE",
            "persistence_state": "SAVED",
            "lifecycle_owner": "MEETING",
        }

    def archive_session(self, chat_key: str) -> dict[str, Any]:
        key = _required(chat_key, "chat_key")
        with self._lock:
            resident = self._hosts.pop(key, None)
        if resident is None:
            return {
                "schema": SESSION_BROKER_SCHEMA,
                "status": "SESSION_BROKER_SESSION_ALREADY_ARCHIVED",
                "chat_key": key,
                "runtime_state": "ARCHIVED",
                "persistence_state": "SAVED",
            }
        resident[1].close()
        return {
            "schema": SESSION_BROKER_SCHEMA,
            "status": "SESSION_BROKER_SESSION_ARCHIVED",
            "chat_key": key,
            "runtime_state": "ARCHIVED",
            "persistence_state": "SAVED",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            sessions = []
            for chat_key, (_fingerprint, host) in self._hosts.items():
                try:
                    connection = host.status()
                    runtime_state = "LIVE"
                except Exception:
                    connection = None
                    runtime_state = "UNKNOWN"
                sessions.append(
                    {
                        "chat_key": chat_key,
                        "runtime_state": runtime_state,
                        "persistence_state": "SAVED",
                        "connection": connection,
                    }
                )
        return {
            "schema": SESSION_BROKER_SCHEMA,
            "status": "SESSION_BROKER_READY",
            "pid": os.getpid(),
            "sessions": sessions,
        }

    def close(self) -> None:
        with self._lock:
            hosts = [item[1] for item in self._hosts.values()]
            self._hosts.clear()
        for host in hosts:
            host.close()


class _BrokerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: SessionBrokerService, token: str):
        super().__init__(address, _BrokerHandler)
        self.service = service
        self.token = token


class _BrokerHandler(BaseHTTPRequestHandler):
    server: _BrokerHTTPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.server.token}"

    def _write(self, status: int, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write(401, {"error_code": "SESSION_BROKER_UNAUTHORIZED"})
            return
        if self.path != "/v1/status":
            self._write(404, {"error_code": "SESSION_BROKER_ROUTE_NOT_FOUND"})
            return
        self._write(200, self.server.service.snapshot())

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write(401, {"error_code": "SESSION_BROKER_UNAUTHORIZED"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, Mapping):
                raise SessionBrokerError(
                    "SESSION_BROKER_REQUEST_INVALID", "request must be an object", 400
                )
            if self.path == "/v1/turn":
                result = self.server.service.turn(value)
            elif self.path == "/v1/sessions":
                result = self.server.service.create_session(value)
            else:
                archive_match = re.fullmatch(r"/v1/sessions/([^/]+)/archive", self.path)
                if archive_match is None:
                    self._write(404, {"error_code": "SESSION_BROKER_ROUTE_NOT_FOUND"})
                    return
                result = self.server.service.archive_session(archive_match.group(1))
            self._write(200, result)
        except SessionBrokerError as error:
            self._write(
                error.status,
                {"error_code": error.code, "detail": error.detail},
            )
        except (ValueError, json.JSONDecodeError):
            self._write(400, {"error_code": "SESSION_BROKER_REQUEST_INVALID"})


class SessionBrokerClient:
    """Reconnect to or launch the independent broker process."""

    def __init__(self, state_path: Path, database_path: Path, *, timeout: float = 620.0):
        self.state_path = state_path.expanduser().resolve()
        self.database_path = database_path.expanduser().resolve()
        self.timeout = timeout
        self._lock = threading.RLock()

    def _state(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _call(self, method: str, path: str, value: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = self._state()
        if state is None:
            raise SessionBrokerError(
                "SESSION_BROKER_OFFLINE", "Session Broker state is unavailable", 503
            )
        data = None if value is None else json.dumps(value).encode("utf-8")
        request = Request(
            f"{state['endpoint']}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {state['token']}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                failure = json.loads(error.read().decode("utf-8"))
            except (ValueError, OSError):
                failure = {}
            raise SessionBrokerError(
                str(failure.get("error_code") or "SESSION_BROKER_REQUEST_FAILED"),
                str(failure.get("detail") or error.reason),
                error.code,
            ) from error
        except (URLError, OSError, ValueError) as error:
            raise SessionBrokerError(
                "SESSION_BROKER_OFFLINE", "Session Broker is not reachable", 503
            ) from error
        if not isinstance(result, dict):
            raise SessionBrokerError(
                "SESSION_BROKER_RESPONSE_INVALID", "broker returned a non-object", 502
            )
        return result

    def ensure(self) -> dict[str, Any]:
        with self._lock:
            try:
                return self._call("GET", "/v1/status")
            except SessionBrokerError:
                pass
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "serve",
                "--state",
                str(self.state_path),
                "--database",
                str(self.database_path),
            ]
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                time.sleep(0.05)
                try:
                    return self._call("GET", "/v1/status")
                except SessionBrokerError:
                    continue
        raise SessionBrokerError(
            "SESSION_BROKER_START_FAILED", "Session Broker did not become ready", 503
        )

    def turn(self, descriptor: Mapping[str, Any], body: str, message_id: str) -> dict[str, Any]:
        self.ensure()
        return self._call(
            "POST",
            "/v1/turn",
            {"descriptor": dict(descriptor), "body": body, "message_id": message_id},
        )

    def create_session(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        self.ensure()
        return self._call("POST", "/v1/sessions", {"descriptor": dict(descriptor)})

    def archive_session(self, chat_key: str) -> dict[str, Any]:
        self.ensure()
        return self._call("POST", f"/v1/sessions/{chat_key}/archive", {})


def serve(state_path: Path, database_path: Path) -> int:
    token = secrets.token_urlsafe(32)
    service = SessionBrokerService(database_path)
    server = _BrokerHTTPServer(("127.0.0.1", 0), service, token)
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema": SESSION_BROKER_STATE_SCHEMA,
                "endpoint": endpoint,
                "token": token,
                "pid": os.getpid(),
                "started_at": _utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        service.close()
        server.server_close()
        try:
            current = json.loads(state_path.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                state_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--state", required=True, type=Path)
    serve_parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args.state, args.database)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
