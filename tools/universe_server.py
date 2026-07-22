from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


API_SCHEMA = "universe.local-service.v1"
PROJECT_SCHEMA = "universe.project-connection.v1"
EVENT_SCHEMA = "universe.project-event.v1"
CONNECTION_PROFILE_SCHEMA = "universe.connection-profile.v1"
AUTH_PROFILE_SCHEMA = "universe.auth-profile.v1"
INTERFACE_PROFILE_SCHEMA = "universe.interface-profile.v1"
CAPABILITY_PROFILE_SCHEMA = "universe.connection-capabilities.v1"
MAX_BODY_BYTES = 1024 * 1024
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CONNECTION_KINDS = frozenset({"LOCAL", "REMOTE", "PEER"})
TRANSPORT_KINDS = frozenset({"HTTP", "GIT", "P2P"})
INTERFACE_KINDS = frozenset({"HTTP_API", "MCP", "CLI"})
ADAPTER_DIRECTIONS = frozenset({"INBOUND", "OUTBOUND"})
AUTH_TYPES = frozenset({"LOCAL_TOKEN", "OAUTH2", "PEER_KEY"})
ALLOWED_REF_KEYS = frozenset(
    {"manifest", "mode_registry", "runtime_status", "anchor_store"}
)
DEFAULT_REFS = {
    "manifest": "REPOSITORY_MANIFEST.md",
    "mode_registry": ".ai/runtime/project_instance/mode_registry.json",
    "runtime_status": ".ai/runtime/project_instance/status.md",
    "anchor_store": ".ai/runtime/anchor_store",
}


class UniverseError(ValueError):
    def __init__(self, code: str, detail: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = int(status)


@dataclass(frozen=True)
class AuthProfile:
    auth_type: str
    credential_ref: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schema": AUTH_PROFILE_SCHEMA,
            "type": self.auth_type,
            "credential_ref": self.credential_ref,
        }


@dataclass(frozen=True)
class ConnectionCapabilities:
    read: bool
    append: bool
    realtime: bool
    bidirectional: bool
    durable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": CAPABILITY_PROFILE_SCHEMA,
            "read": self.read,
            "append": self.append,
            "realtime": self.realtime,
            "bidirectional": self.bidirectional,
            "durable": self.durable,
        }


@dataclass(frozen=True)
class ConnectionProfile:
    connection_id: str
    kind: str
    transport_kind: str
    endpoint: str
    auth: AuthProfile
    capabilities: ConnectionCapabilities

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": CONNECTION_PROFILE_SCHEMA,
            "connection_id": self.connection_id,
            "kind": self.kind,
            "transport_kind": self.transport_kind,
            "endpoint": self.endpoint,
            "auth": self.auth.as_dict(),
            "capabilities": self.capabilities.as_dict(),
        }


@dataclass(frozen=True)
class InterfaceProfile:
    interface_id: str
    kind: str
    direction: str
    active: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": INTERFACE_PROFILE_SCHEMA,
            "interface_id": self.interface_id,
            "kind": self.kind,
            "direction": self.direction,
            "active": self.active,
        }


class AuthProvider(Protocol):
    auth_type: str

    def headers(self) -> dict[str, str]: ...


class UniverseTransport(Protocol):
    def request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


class LocalTokenAuthProvider:
    auth_type = "LOCAL_TOKEN"

    def __init__(self, token: str):
        self._token = _required_text(token, "token")

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def default_database_path() -> Path:
    return default_data_dir() / "universe.sqlite3"


def default_state_path() -> Path:
    return default_data_dir() / "server.json"


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UniverseError("REQUEST_INVALID", f"{field} must be non-empty text")
    return value.strip()


def connection_profile(
    *,
    connection_id: str,
    kind: str,
    transport_kind: str,
    endpoint: str,
    auth_type: str,
    credential_ref: str,
    capabilities: ConnectionCapabilities,
) -> ConnectionProfile:
    normalized_id = _required_text(connection_id, "connection_id")
    normalized_kind = _required_text(kind, "connection_kind").upper()
    normalized_transport = _required_text(
        transport_kind, "transport_kind"
    ).upper()
    normalized_auth = _required_text(auth_type, "auth_type").upper()
    normalized_endpoint = _required_text(endpoint, "endpoint").rstrip("/")
    if normalized_kind not in CONNECTION_KINDS:
        raise UniverseError(
            "CONNECTION_KIND_INVALID",
            f"unsupported connection kind: {normalized_kind}",
        )
    if normalized_auth not in AUTH_TYPES:
        raise UniverseError(
            "AUTH_TYPE_INVALID",
            f"unsupported authentication type: {normalized_auth}",
        )
    if normalized_transport not in TRANSPORT_KINDS:
        raise UniverseError(
            "TRANSPORT_KIND_INVALID",
            f"unsupported transport kind: {normalized_transport}",
        )
    if not isinstance(capabilities, ConnectionCapabilities):
        raise UniverseError(
            "CONNECTION_CAPABILITIES_INVALID",
            "capabilities must be a ConnectionCapabilities value",
        )
    if normalized_transport == "HTTP":
        parsed = urlsplit(normalized_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UniverseError(
                "CONNECTION_ENDPOINT_INVALID",
                "HTTP transport endpoint must be an absolute HTTP or HTTPS URL",
            )
        if normalized_kind == "LOCAL":
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError as error:
                raise UniverseError(
                    "LOCAL_CONNECTION_ENDPOINT_INVALID",
                    "local HTTP endpoint must use a literal loopback address",
                ) from error
            if not address.is_loopback:
                raise UniverseError(
                    "LOCAL_CONNECTION_ENDPOINT_INVALID",
                    "local HTTP endpoint must use a loopback address",
                )
    return ConnectionProfile(
        connection_id=normalized_id,
        kind=normalized_kind,
        transport_kind=normalized_transport,
        endpoint=normalized_endpoint,
        auth=AuthProfile(
            auth_type=normalized_auth,
            credential_ref=_required_text(credential_ref, "credential_ref"),
        ),
        capabilities=capabilities,
    )


def local_connection_profile(endpoint: str) -> ConnectionProfile:
    return connection_profile(
        connection_id="local",
        kind="LOCAL",
        transport_kind="HTTP",
        endpoint=endpoint,
        auth_type="LOCAL_TOKEN",
        credential_ref="server-state://token",
        capabilities=ConnectionCapabilities(
            read=True,
            append=True,
            realtime=True,
            bidirectional=True,
            durable=True,
        ),
    )


def interface_profile(
    *, interface_id: str, kind: str, direction: str, active: bool
) -> InterfaceProfile:
    normalized_kind = _required_text(kind, "interface_kind").upper()
    normalized_direction = _required_text(direction, "interface_direction").upper()
    if normalized_kind not in INTERFACE_KINDS:
        raise UniverseError(
            "INTERFACE_KIND_INVALID",
            f"unsupported interface kind: {normalized_kind}",
        )
    if normalized_direction not in ADAPTER_DIRECTIONS:
        raise UniverseError(
            "INTERFACE_DIRECTION_INVALID",
            f"unsupported interface direction: {normalized_direction}",
        )
    if not isinstance(active, bool):
        raise UniverseError("INTERFACE_STATE_INVALID", "active must be boolean")
    return InterfaceProfile(
        interface_id=_required_text(interface_id, "interface_id"),
        kind=normalized_kind,
        direction=normalized_direction,
        active=active,
    )


def local_http_interface_profile() -> InterfaceProfile:
    return interface_profile(
        interface_id="local-http-api",
        kind="HTTP_API",
        direction="INBOUND",
        active=True,
    )


def auth_provider_for(profile: ConnectionProfile, credential: str) -> AuthProvider:
    if profile.auth.auth_type == "LOCAL_TOKEN":
        return LocalTokenAuthProvider(credential)
    raise UniverseError(
        "AUTH_PROVIDER_NOT_IMPLEMENTED",
        f"authentication provider is reserved but not implemented: {profile.auth.auth_type}",
        HTTPStatus.NOT_IMPLEMENTED,
    )


class HttpUniverseTransport:
    def __init__(self, profile: ConnectionProfile, auth_provider: AuthProvider):
        self.profile = profile
        self.auth_provider = auth_provider

    def request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = None
        headers = self.auth_provider.headers()
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.profile.endpoint + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise UniverseError("SERVICE_UNAVAILABLE", str(error)) from error


def _project_id(value: Any) -> str:
    project_id = _required_text(value, "project_id")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise UniverseError(
            "PROJECT_ID_INVALID",
            "project_id must use letters, digits, dot, underscore, or hyphen",
        )
    return project_id


def _event_type(value: Any) -> str:
    event_type = _required_text(value, "event_type").upper()
    if not EVENT_TYPE_PATTERN.fullmatch(event_type):
        raise UniverseError(
            "EVENT_TYPE_INVALID",
            "event_type must be an uppercase identifier",
        )
    return event_type


def _canonical_project_root(value: Any) -> Path:
    raw = Path(_required_text(value, "project_root")).expanduser()
    try:
        root = raw.resolve(strict=True)
    except OSError as error:
        raise UniverseError("PROJECT_ROOT_UNAVAILABLE", str(error)) from error
    if not root.is_dir():
        raise UniverseError("PROJECT_ROOT_INVALID", "project_root must be a directory")
    return root


def _relative_ref(project_root: Path, value: Any, field: str) -> str:
    text = _required_text(value, f"refs.{field}").replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise UniverseError(
            "PROJECT_REF_INVALID",
            f"refs.{field} must remain relative to project_root",
        )
    resolved = (project_root / path).resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as error:
        raise UniverseError(
            "PROJECT_REF_OUTSIDE_ROOT",
            f"refs.{field} escapes project_root",
        ) from error
    return path.as_posix()


def normalize_registration(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UniverseError("REQUEST_INVALID", "registration body must be an object")
    project_id = _project_id(value.get("project_id"))
    project_root = _canonical_project_root(value.get("project_root"))

    refs_value = value.get("refs", DEFAULT_REFS)
    if not isinstance(refs_value, dict):
        raise UniverseError("PROJECT_REFS_INVALID", "refs must be an object")
    unknown_refs = set(refs_value) - ALLOWED_REF_KEYS
    if unknown_refs:
        raise UniverseError(
            "PROJECT_REFS_INVALID",
            f"unsupported refs: {', '.join(sorted(unknown_refs))}",
        )
    refs = {
        key: _relative_ref(project_root, refs_value.get(key, default), key)
        for key, default in DEFAULT_REFS.items()
    }
    manifest_path = project_root / refs["manifest"]
    if not manifest_path.is_file():
        raise UniverseError(
            "PROJECT_MANIFEST_UNAVAILABLE",
            f"manifest does not exist: {refs['manifest']}",
        )

    mode_registry_path = project_root / refs["mode_registry"]
    if mode_registry_path.is_file():
        try:
            registry = json.loads(mode_registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise UniverseError("MODE_REGISTRY_INVALID", str(error)) from error
        owner = registry.get("owner") if isinstance(registry, dict) else None
        if isinstance(owner, str) and owner.casefold() != project_id.casefold():
            raise UniverseError(
                "PROJECT_IDENTITY_MISMATCH",
                "project_id does not match Mode Registry owner",
                HTTPStatus.CONFLICT,
            )

    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise UniverseError("PROJECT_METADATA_INVALID", "metadata must be an object")
    return {
        "schema": PROJECT_SCHEMA,
        "project_id": project_id,
        "project_root": str(project_root),
        "refs": refs,
        "metadata": metadata,
    }


def normalize_event(project_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UniverseError("REQUEST_INVALID", "event body must be an object")
    payload = value.get("payload", {})
    if not isinstance(payload, dict):
        raise UniverseError("EVENT_PAYLOAD_INVALID", "payload must be an object")
    event_id = value.get("event_id") or "event_" + uuid.uuid4().hex
    event_id = _required_text(event_id, "event_id")
    if len(event_id) > 160:
        raise UniverseError("EVENT_ID_INVALID", "event_id is too long")
    return {
        "schema": EVENT_SCHEMA,
        "event_id": event_id,
        "project_id": _project_id(project_id),
        "event_type": _event_type(value.get("event_type")),
        "payload": payload,
        "created_at": utc_now(),
    }


class UniverseStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_connection (
                    project_id TEXT PRIMARY KEY,
                    project_root TEXT NOT NULL UNIQUE,
                    refs_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_event (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS project_event_project_time
                ON project_event(project_id, created_at, event_id);
                """
            )

    def register_project(self, value: Any) -> tuple[dict[str, Any], bool]:
        project = normalize_registration(value)
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT project_root FROM project_connection WHERE project_id = ?",
                (project["project_id"],),
            ).fetchone()
            if existing is not None and existing["project_root"] != project["project_root"]:
                raise UniverseError(
                    "PROJECT_ID_ALREADY_BOUND",
                    "project_id is already attached to another root",
                    HTTPStatus.CONFLICT,
                )
            root_owner = connection.execute(
                "SELECT project_id FROM project_connection WHERE project_root = ?",
                (project["project_root"],),
            ).fetchone()
            if root_owner is not None and root_owner["project_id"] != project["project_id"]:
                raise UniverseError(
                    "PROJECT_ROOT_ALREADY_BOUND",
                    "project_root is already attached to another project",
                    HTTPStatus.CONFLICT,
                )
            created = existing is None
            connection.execute(
                """
                INSERT INTO project_connection(
                    project_id, project_root, refs_json, metadata_json,
                    registered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    project_root = excluded.project_root,
                    refs_json = excluded.refs_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    project["project_id"],
                    project["project_root"],
                    json.dumps(project["refs"], sort_keys=True, separators=(",", ":")),
                    json.dumps(project["metadata"], sort_keys=True, separators=(",", ":")),
                    now,
                    now,
                ),
            )
        return self.get_project(project["project_id"]), created

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT project_id, project_root, refs_json, metadata_json,
                       registered_at, updated_at
                FROM project_connection
                ORDER BY project_id
                """
            ).fetchall()
        return [self._project_row(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        normalized = _project_id(project_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT project_id, project_root, refs_json, metadata_json,
                       registered_at, updated_at
                FROM project_connection
                WHERE project_id = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "PROJECT_NOT_FOUND",
                f"project is not attached: {normalized}",
                HTTPStatus.NOT_FOUND,
            )
        return self._project_row(row)

    def delete_project(self, project_id: str) -> dict[str, Any]:
        normalized = _project_id(project_id)
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM project_connection WHERE project_id = ?", (normalized,)
            )
        if cursor.rowcount != 1:
            raise UniverseError(
                "PROJECT_NOT_FOUND",
                f"project is not attached: {normalized}",
                HTTPStatus.NOT_FOUND,
            )
        return {"project_id": normalized, "detached": True}

    def append_event(self, project_id: str, value: Any) -> tuple[dict[str, Any], bool]:
        event = normalize_event(project_id, value)
        self.get_project(event["project_id"])
        payload_json = json.dumps(event["payload"], sort_keys=True, separators=(",", ":"))
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT project_id, event_type, payload_json, created_at
                FROM project_event WHERE event_id = ?
                """,
                (event["event_id"],),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["project_id"] == event["project_id"]
                    and existing["event_type"] == event["event_type"]
                    and existing["payload_json"] == payload_json
                )
                if not same:
                    raise UniverseError(
                        "EVENT_ID_CONFLICT",
                        "event_id already refers to different content",
                        HTTPStatus.CONFLICT,
                    )
                event["created_at"] = existing["created_at"]
                return event, False
            connection.execute(
                """
                INSERT INTO project_event(
                    event_id, project_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"], event["project_id"], event["event_type"],
                    payload_json, event["created_at"],
                ),
            )
        return event, True

    def list_events(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        normalized = _project_id(project_id)
        self.get_project(normalized)
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT event_id, project_id, event_type, payload_json, created_at
                FROM project_event
                WHERE project_id = ?
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
                """,
                (normalized, limit),
            ).fetchall()
        return [
            {
                "schema": EVENT_SCHEMA,
                "event_id": row["event_id"],
                "project_id": row["project_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": PROJECT_SCHEMA,
            "project_id": row["project_id"],
            "project_root": row["project_root"],
            "refs": json.loads(row["refs_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "registered_at": row["registered_at"],
            "updated_at": row["updated_at"],
        }


class UniverseHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: UniverseStore, token: str):
        self.store = store
        self.token = token
        super().__init__(address, UniverseRequestHandler)
        host, port = self.server_address[:2]
        self.connection_profile = local_connection_profile(f"http://{host}:{port}")
        self.interface_profiles = (local_http_interface_profile(),)


class UniverseRequestHandler(BaseHTTPRequestHandler):
    server: UniverseHTTPServer
    server_version = "UniverseLocal/1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "READY",
                    "connection": self.server.connection_profile.as_dict(),
                    "interfaces": [
                        profile.as_dict() for profile in self.server.interface_profiles
                    ],
                },
            )
            return
        if not self._authorize():
            return
        if path == "/v1/projects":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "PROJECTS_COLLECTED",
                    "projects": self.server.store.list_projects(),
                },
            )
            return
        parts = self._project_path(path)
        if parts is None:
            self._not_found()
            return
        project_id, suffix = parts
        try:
            if suffix == "":
                self._send(HTTPStatus.OK, self.server.store.get_project(project_id))
                return
            if suffix == "/events":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_EVENTS_COLLECTED",
                        "project_id": project_id,
                        "events": self.server.store.list_events(project_id),
                    },
                )
                return
        except UniverseError as error:
            self._send_error(error)
            return
        self._not_found()

    def do_POST(self) -> None:
        if not self._authorize():
            return
        path = urlsplit(self.path).path
        try:
            body = self._read_json()
            if path == "/v1/projects/register":
                project, created = self.server.store.register_project(body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_REGISTERED" if created else "PROJECT_REFRESHED",
                        "project": project,
                    },
                )
                return
            parts = self._project_path(path)
            if parts is not None and parts[1] == "/events":
                event, created = self.server.store.append_event(parts[0], body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_EVENT_APPENDED" if created else "PROJECT_EVENT_ALREADY_RECORDED",
                        "event": event,
                    },
                )
                return
            self._not_found()
        except UniverseError as error:
            self._send_error(error)
        except (OSError, sqlite3.Error) as error:
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "schema": API_SCHEMA,
                    "status": "ERROR",
                    "error_code": "SERVICE_FAILURE",
                    "detail": str(error),
                },
            )

    def do_DELETE(self) -> None:
        if not self._authorize():
            return
        parts = self._project_path(urlsplit(self.path).path)
        if parts is None or parts[1] != "":
            self._not_found()
            return
        try:
            result = self.server.store.delete_project(parts[0])
            self._send(
                HTTPStatus.OK,
                {"schema": API_SCHEMA, "status": "PROJECT_DETACHED", **result},
            )
        except UniverseError as error:
            self._send_error(error)

    def _authorize(self) -> bool:
        expected = f"Bearer {self.server.token}"
        provided = self.headers.get("Authorization", "")
        if secrets.compare_digest(provided, expected):
            return True
        self._send(
            HTTPStatus.UNAUTHORIZED,
            {
                "schema": API_SCHEMA,
                "status": "UNAUTHORIZED",
                "error_code": "LOCAL_TOKEN_REQUIRED",
            },
        )
        return False

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as error:
            raise UniverseError("REQUEST_INVALID", "invalid Content-Length") from error
        if length <= 0 or length > MAX_BODY_BYTES:
            raise UniverseError(
                "REQUEST_SIZE_INVALID",
                f"request body must be 1..{MAX_BODY_BYTES} bytes",
            )
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise UniverseError("REQUEST_INVALID", "body must be UTF-8 JSON") from error

    @staticmethod
    def _project_path(path: str) -> tuple[str, str] | None:
        prefix = "/v1/projects/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix):]
        if remainder.endswith("/events"):
            return unquote(remainder[:-len("/events")]), "/events"
        return unquote(remainder), ""

    def _not_found(self) -> None:
        self._send(
            HTTPStatus.NOT_FOUND,
            {"schema": API_SCHEMA, "status": "NOT_FOUND", "error_code": "ROUTE_NOT_FOUND"},
        )

    def _send_error(self, error: UniverseError) -> None:
        self._send(
            error.status,
            {
                "schema": API_SCHEMA,
                "status": "ERROR",
                "error_code": error.code,
                "detail": error.detail,
            },
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    *, database_path: Path, token: str, host: str = "127.0.0.1", port: int = 0
) -> UniverseHTTPServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise UniverseError(
            "SERVER_HOST_INVALID", "host must be a literal loopback IP address"
        ) from error
    if not address.is_loopback:
        raise UniverseError(
            "SERVER_HOST_FORBIDDEN", "Universe local service may listen only on loopback"
        )
    return UniverseHTTPServer(
        (host, port), UniverseStore(database_path), _required_text(token, "token")
    )


def write_server_state(
    path: Path, *, endpoint: str, token: str, database_path: Path
) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "universe.local-service-state.v1",
        "endpoint": endpoint,
        "token": token,
        "connection_profile": local_connection_profile(endpoint).as_dict(),
        "interface_profiles": [local_http_interface_profile().as_dict()],
        "database": str(database_path.expanduser().resolve()),
        "pid": os.getpid(),
        "started_at": utc_now(),
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_server_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UniverseError("SERVER_STATE_UNAVAILABLE", str(error)) from error
    if not isinstance(value, dict):
        raise UniverseError("SERVER_STATE_INVALID", "server state must be an object")
    return value


def request_json(
    *, endpoint: str, token: str, method: str, path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    profile = local_connection_profile(endpoint)
    transport: UniverseTransport = HttpUniverseTransport(
        profile,
        auth_provider_for(profile, token),
    )
    return transport.request_json(method=method, path=path, payload=payload)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Universe local application service")
    commands = root.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--database", type=Path, default=default_database_path())
    serve.add_argument("--state-file", type=Path, default=default_state_path())
    serve.add_argument("--token", default="")

    register = commands.add_parser("register")
    register.add_argument("--project-id", required=True)
    register.add_argument("--project-root", type=Path, required=True)
    register.add_argument("--endpoint", default="")
    register.add_argument("--token", default="")
    register.add_argument("--state-file", type=Path, default=default_state_path())

    list_command = commands.add_parser("list")
    list_command.add_argument("--endpoint", default="")
    list_command.add_argument("--token", default="")
    list_command.add_argument("--state-file", type=Path, default=default_state_path())
    return root


def _connection_options(args: argparse.Namespace) -> tuple[str, str]:
    if args.endpoint and args.token:
        return args.endpoint, args.token
    state = load_server_state(args.state_file)
    return (
        _required_text(state.get("endpoint"), "state.endpoint"),
        _required_text(state.get("token"), "state.token"),
    )


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "serve":
            token = args.token or os.environ.get("UNIVERSE_TOKEN") or secrets.token_urlsafe(32)
            server = create_server(
                database_path=args.database, token=token, host=args.host, port=args.port
            )
            host, port = server.server_address[:2]
            endpoint = f"http://{host}:{port}"
            write_server_state(
                args.state_file,
                endpoint=endpoint,
                token=token,
                database_path=args.database,
            )
            print(
                json.dumps(
                    {
                        "schema": API_SCHEMA,
                        "status": "UNIVERSE_SERVICE_READY",
                        "endpoint": endpoint,
                        "database": str(args.database.expanduser().resolve()),
                        "state_file": str(args.state_file.expanduser().resolve()),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            try:
                server.serve_forever(poll_interval=0.2)
            finally:
                server.server_close()
            return 0

        endpoint, token = _connection_options(args)
        if args.command == "register":
            status, result = request_json(
                endpoint=endpoint,
                token=token,
                method="POST",
                path="/v1/projects/register",
                payload={
                    "project_id": args.project_id,
                    "project_root": str(args.project_root),
                    "refs": DEFAULT_REFS,
                },
            )
        else:
            status, result = request_json(
                endpoint=endpoint, token=token, method="GET", path="/v1/projects"
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if 200 <= status < 300 else 1
    except (UniverseError, OSError, sqlite3.Error) as error:
        code = error.code if isinstance(error, UniverseError) else "SERVICE_FAILURE"
        print(
            json.dumps(
                {
                    "schema": API_SCHEMA,
                    "status": "ERROR",
                    "error_code": code,
                    "detail": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
