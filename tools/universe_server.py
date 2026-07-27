from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

from core_release import CoreReleaseError, verify_release
from release_runtime import ReleaseRuntime, ReleaseRuntimeError
from universe_dispatch import (
    DispatchError,
    HttpProjectWakeAdapter,
    LocalInboxConnector,
    NoWakeAdapter,
    ProjectWakeAdapter,
    normalize_dispatch_request,
    normalize_result_packet,
    transition_event,
)
from project_seed_assets import (
    ProjectSeedAssetError,
    load_project_seed_assets,
    project_seed_template,
)

API_SCHEMA = "universe.local-service.v1"
UNIVERSE_IDENTITY_SCHEMA = "universe.identity.v1"
PROJECT_SCHEMA = "universe.project-connection.v1"
EVENT_SCHEMA = "universe.project-event.v1"
PROJECT_SEED_SCHEMA = "universe.project-seed.v1"
PROJECT_PROJECTION_SCHEMA = "universe.project-projection.v1"
PROJECT_DISCOVERY_DISPATCH_SCHEMA = "universe.project-discovery-dispatch.v1"
DOCUMENT_PROPOSAL_SCHEMA = "universe.document-incorporation-proposal.v1"
RELEASE_ARTIFACT_SCHEMA = "universe.release-artifact.v1"
RELEASE_PROPOSAL_SCHEMA = "universe.project-release-proposal.v1"
CONNECTION_PROFILE_SCHEMA = "universe.connection-profile.v1"
AUTH_PROFILE_SCHEMA = "universe.auth-profile.v1"
INTERFACE_PROFILE_SCHEMA = "universe.interface-profile.v1"
CAPABILITY_PROFILE_SCHEMA = "universe.connection-capabilities.v1"
MAX_BODY_BYTES = 1024 * 1024
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
CONNECTION_KINDS = frozenset({"LOCAL", "REMOTE", "PEER"})
TRANSPORT_KINDS = frozenset({"HTTP", "GIT", "P2P"})
INTERFACE_KINDS = frozenset({"HTTP_API", "MCP", "CLI"})
ADAPTER_DIRECTIONS = frozenset({"INBOUND", "OUTBOUND"})
AUTH_TYPES = frozenset({"LOCAL_TOKEN", "OAUTH2", "PEER_KEY"})
ALLOWED_REF_KEYS = frozenset(
    {
        "manifest",
        "mode_registry",
        "runtime_status",
        "anchor_store",
        "master_inbox",
    }
)
DEFAULT_REFS = {
    "manifest": "REPOSITORY_MANIFEST.md",
    "mode_registry": ".ai/runtime/project_instance/mode_registry.json",
    "runtime_status": ".ai/runtime/project_instance/status.md",
    "anchor_store": ".ai/runtime/anchor_store",
    "master_inbox": ".ai/inbox/MASTER",
}
DOCUMENT_ROLES = frozenset(
    {
        "ARCHITECTURE",
        "CHANGELOG",
        "CONTRACT",
        "DECISION",
        "DESIGN",
        "EVIDENCE",
        "POLICY",
        "REFERENCE",
        "SPECIFICATION",
    }
)
IMPLEMENTATION_NODE_KINDS = frozenset(
    {"PACKAGE", "MODULE", "CLASS", "SERVICE", "ADAPTER", "ENDPOINT"}
)
IMPLEMENTATION_BINDING_RELATIONS = frozenset(
    {"IMPLEMENTS", "SUPPORTS", "ADAPTS", "EXPOSES"}
)
MASTER_MODE = "MASTER"
UNIVERSE_MODE = "UNIVERSE"
UNIVERSE_ROLE = "CONDUCTOR"
UNIVERSE_SCOPE = "project-network/navigation/distribution"
UNIVERSE_MODE_PROFILE = "GOVERNANCE_ONLY"
UNIVERSE_MODE_INTENTS = {
    "UNIVERSE": UNIVERSE_MODE,
    "UNIVERSE MODE": UNIVERSE_MODE,
    "CONDUCTOR": UNIVERSE_MODE,
    "CONDUCTOR MODE": UNIVERSE_MODE,
    "\uc720\ub2c8\ubc84\uc2a4": UNIVERSE_MODE,
    "\uc720\ub2c8\ubc84\uc2a4\ubaa8\ub4dc": UNIVERSE_MODE,
    "\ucee8\ub355\ud130": UNIVERSE_MODE,
    "\ucee8\ub355\ud130\ubaa8\ub4dc": UNIVERSE_MODE,
}
DEFAULT_MODE_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / ".ai"
    / "runtime"
    / "project_instance"
    / "mode_registry.json"
)
UI_ROOT = Path(__file__).resolve().with_name("universe_ui")


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


def resolve_universe_mode_intent(value: Any) -> str:
    intent = _required_text(value, "mode_intent").upper()
    try:
        return UNIVERSE_MODE_INTENTS[intent]
    except KeyError as error:
        raise UniverseError(
            "MODE_INTENT_UNSUPPORTED",
            "Universe accepts only Universe or Conductor Mode intent",
        ) from error


def load_universe_mode_registry(path: Path) -> dict[str, Any]:
    try:
        registry = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UniverseError(
            "UNIVERSE_MODE_REGISTRY_UNAVAILABLE",
            str(error),
        ) from error
    if not isinstance(registry, dict):
        raise UniverseError(
            "UNIVERSE_MODE_REGISTRY_INVALID",
            "Universe Mode Registry must be an object",
        )
    if (
        registry.get("owner") != "universe"
        or registry.get("policy") != "MASTER_MANAGED"
        or registry.get("root_mode") != MASTER_MODE
    ):
        raise UniverseError(
            "UNIVERSE_MODE_CONTRACT_MISMATCH",
            "Universe Mode Registry owner, policy, and root Mode must remain canonical",
        )
    modes = registry.get("modes")
    if not isinstance(modes, dict):
        raise UniverseError(
            "UNIVERSE_MODE_REGISTRY_INVALID",
            "Universe Mode Registry modes must be an object",
        )
    expected = {
        MASTER_MODE: {
            "role": MASTER_MODE,
            "scope": "architecture/governance",
            "mode_profile": "GOVERNANCE_ONLY",
        },
        UNIVERSE_MODE: {
            "role": UNIVERSE_ROLE,
            "scope": UNIVERSE_SCOPE,
            "mode_profile": UNIVERSE_MODE_PROFILE,
        },
    }
    for mode, definition in expected.items():
        if modes.get(mode) != definition:
            raise UniverseError(
                "UNIVERSE_MODE_CONTRACT_MISMATCH",
                f"Universe Mode Registry entry does not match the required contract: {mode}",
            )
    return registry


def require_release_lifecycle_mode(mode: Any) -> None:
    if _required_text(mode, "mode").upper() != MASTER_MODE:
        raise UniverseError(
            "MASTER_MODE_REQUIRED",
            "Release database installation and update require MASTER Mode",
            HTTPStatus.CONFLICT,
        )


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


def _validated_http_request_url(profile: ConnectionProfile, path: str) -> str:
    if profile.transport_kind != "HTTP":
        raise UniverseError(
            "CONNECTION_TRANSPORT_INVALID",
            "HTTP request transport requires an HTTP connection profile",
        )
    endpoint = urlsplit(profile.endpoint)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
        raise UniverseError(
            "CONNECTION_ENDPOINT_INVALID",
            "HTTP transport endpoint must be an absolute HTTP or HTTPS URL",
        )
    normalized_path = _required_text(path, "path")
    path_parts = urlsplit(normalized_path)
    if (
        not normalized_path.startswith("/")
        or path_parts.scheme
        or path_parts.netloc
        or path_parts.fragment
    ):
        raise UniverseError(
            "CONNECTION_PATH_INVALID",
            "HTTP request path must be an absolute path without an origin or fragment",
        )
    request_url = urlsplit(profile.endpoint + normalized_path)
    if (
        request_url.scheme != endpoint.scheme
        or request_url.netloc != endpoint.netloc
        or request_url.scheme not in {"http", "https"}
    ):
        raise UniverseError(
            "CONNECTION_ENDPOINT_INVALID",
            "HTTP request URL must remain within the configured connection origin",
        )
    return request_url.geturl()


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
            _validated_http_request_url(self.profile, path),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:  # nosec B310
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


def _identifier(value: Any, field: str) -> str:
    identifier = _required_text(value, field)
    if not PROJECT_ID_PATTERN.fullmatch(identifier):
        raise UniverseError(
            "IDENTIFIER_INVALID",
            f"{field} must use letters, digits, dot, underscore, or hyphen",
        )
    return identifier


def _exact_object_fields(
    value: Any,
    *,
    field: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UniverseError("REQUEST_INVALID", f"{field} must be an object")
    missing = required - set(value)
    if missing:
        raise UniverseError(
            "REQUEST_INVALID",
            f"{field} is missing: {', '.join(sorted(missing))}",
        )
    unknown = set(value) - required - optional
    if unknown:
        raise UniverseError(
            "REQUEST_INVALID",
            f"{field} contains unsupported fields: {', '.join(sorted(unknown))}",
        )
    return dict(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o444)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(value: Any, field: str) -> str:
    digest = _required_text(value, field).lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise UniverseError("SHA256_INVALID", f"{field} must be lowercase SHA-256")
    return digest


def _source_commit(value: Any) -> str:
    commit = _required_text(value, "source.commit").lower()
    if not SOURCE_COMMIT_PATTERN.fullmatch(commit):
        raise UniverseError(
            "SOURCE_COMMIT_INVALID",
            "source.commit must be a lowercase immutable commit digest",
        )
    return commit


def _canonical_project_root(value: Any) -> Path:
    raw = Path(_required_text(value, "project_root")).expanduser()
    try:
        root = raw.resolve(strict=True)
    except OSError as error:
        raise UniverseError("PROJECT_ROOT_UNAVAILABLE", str(error)) from error
    if not root.is_dir():
        raise UniverseError("PROJECT_ROOT_INVALID", "project_root must be a directory")
    return root


def _relative_project_path(project_root: Path, value: Any, field: str) -> str:
    text = _required_text(value, field).replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise UniverseError(
            "PROJECT_REF_INVALID",
            f"{field} must remain relative to project_root",
        )
    resolved = (project_root / path).resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as error:
        raise UniverseError(
            "PROJECT_REF_OUTSIDE_ROOT",
            f"{field} escapes project_root",
        ) from error
    return path.as_posix()


def _relative_ref(project_root: Path, value: Any, field: str) -> str:
    return _relative_project_path(project_root, value, f"refs.{field}")


def _project_file_ref(project_root: Path, value: Any, field: str) -> dict[str, str]:
    ref = _exact_object_fields(
        value,
        field=field,
        required=frozenset({"path", "sha256"}),
        optional=frozenset({"symbol", "kind"}),
    )
    relative_path = _relative_project_path(project_root, ref["path"], f"{field}.path")
    target = project_root / relative_path
    if not target.is_file() or target.is_symlink():
        raise UniverseError(
            "PROJECT_FILE_REF_UNAVAILABLE",
            f"{field}.path is not a regular project file: {relative_path}",
        )
    expected = _sha256(ref["sha256"], f"{field}.sha256")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise UniverseError(
            "PROJECT_FILE_REF_DIGEST_MISMATCH",
            f"{field}.sha256 does not match {relative_path}",
            HTTPStatus.CONFLICT,
        )
    normalized = {"path": relative_path, "sha256": expected}
    if "kind" in ref:
        normalized["kind"] = _identifier(ref["kind"], f"{field}.kind").upper()
    if "symbol" in ref:
        normalized["symbol"] = _required_text(ref["symbol"], f"{field}.symbol")
    return normalized


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


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise UniverseError("REQUEST_INVALID", f"{field} must be an array")
    return list(value)


def _string_array(value: Any, field: str) -> list[str]:
    values = _array(value, field)
    normalized = [_required_text(item, f"{field}[]") for item in values]
    if len(set(normalized)) != len(normalized):
        raise UniverseError("REQUEST_INVALID", f"{field} must not contain duplicates")
    return normalized


def normalize_project_seed(
    project: dict[str, Any], value: Any
) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="project_seed",
        required=frozenset(
            {"seed_id", "source", "project", "nodes", "edges", "documents"}
        ),
        optional=frozenset({"implementation_nodes", "implementation_bindings"}),
    )
    project_root = Path(project["project_root"]).resolve(strict=True)
    source = _exact_object_fields(
        request["source"],
        field="source",
        required=frozenset({"ref", "commit"}),
    )
    project_input = _exact_object_fields(
        request["project"],
        field="project",
        required=frozenset({"kind", "technologies", "goal"}),
        optional=frozenset({"summary", "working_rules"}),
    )
    technologies = sorted(
        set(
            item.casefold()
            for item in _string_array(
                project_input["technologies"], "project.technologies"
            )
        )
    )
    if not technologies:
        raise UniverseError(
            "PROJECT_SEED_TECHNOLOGIES_REQUIRED",
            "project.technologies must not be empty",
        )
    working_rules = [
        _required_text(rule, f"project.working_rules[{index}]")
        for index, rule in enumerate(
            _array(project_input.get("working_rules", []), "project.working_rules")
        )
    ]

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw_node in enumerate(_array(request["nodes"], "nodes")):
        field = f"nodes[{index}]"
        node = _exact_object_fields(
            raw_node,
            field=field,
            required=frozenset({"node_id", "kind", "title", "refs"}),
            optional=frozenset({"summary"}),
        )
        node_id = _identifier(node["node_id"], f"{field}.node_id")
        if node_id in node_ids:
            raise UniverseError("PROJECT_SEED_NODE_DUPLICATE", f"duplicate node: {node_id}")
        node_ids.add(node_id)
        normalized_node: dict[str, Any] = {
            "node_id": node_id,
            "kind": _identifier(node["kind"], f"{field}.kind").upper(),
            "title": _required_text(node["title"], f"{field}.title"),
            "refs": [
                _project_file_ref(project_root, ref, f"{field}.refs[{ref_index}]")
                for ref_index, ref in enumerate(_array(node["refs"], f"{field}.refs"))
            ],
        }
        if "summary" in node:
            normalized_node["summary"] = _required_text(
                node["summary"], f"{field}.summary"
            )
        nodes.append(normalized_node)
    if not nodes:
        raise UniverseError("PROJECT_SEED_NODES_REQUIRED", "nodes must not be empty")

    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    for index, raw_edge in enumerate(_array(request["edges"], "edges")):
        field = f"edges[{index}]"
        edge = _exact_object_fields(
            raw_edge,
            field=field,
            required=frozenset({"edge_id", "from_node", "to_node", "kind"}),
            optional=frozenset({"summary", "contract_ref"}),
        )
        edge_id = _identifier(edge["edge_id"], f"{field}.edge_id")
        if edge_id in edge_ids:
            raise UniverseError("PROJECT_SEED_EDGE_DUPLICATE", f"duplicate edge: {edge_id}")
        edge_ids.add(edge_id)
        from_node = _identifier(edge["from_node"], f"{field}.from_node")
        to_node = _identifier(edge["to_node"], f"{field}.to_node")
        if from_node not in node_ids or to_node not in node_ids:
            raise UniverseError(
                "PROJECT_SEED_EDGE_NODE_UNKNOWN",
                f"{field} references an unknown node",
            )
        normalized_edge: dict[str, Any] = {
            "edge_id": edge_id,
            "from_node": from_node,
            "to_node": to_node,
            "kind": _identifier(edge["kind"], f"{field}.kind").upper(),
        }
        if "summary" in edge:
            normalized_edge["summary"] = _required_text(
                edge["summary"], f"{field}.summary"
            )
        if "contract_ref" in edge:
            normalized_edge["contract_ref"] = _project_file_ref(
                project_root, edge["contract_ref"], f"{field}.contract_ref"
            )
        edges.append(normalized_edge)

    implementation_nodes: list[dict[str, Any]] = []
    implementation_ids: set[str] = set()
    for index, raw_node in enumerate(
        _array(request.get("implementation_nodes", []), "implementation_nodes")
    ):
        field = f"implementation_nodes[{index}]"
        implementation = _exact_object_fields(
            raw_node,
            field=field,
            required=frozenset({"implementation_id", "kind", "title", "refs"}),
            optional=frozenset({"summary"}),
        )
        implementation_id = _identifier(
            implementation["implementation_id"], f"{field}.implementation_id"
        )
        if implementation_id in implementation_ids:
            raise UniverseError(
                "PROJECT_SEED_IMPLEMENTATION_DUPLICATE",
                f"duplicate implementation node: {implementation_id}",
            )
        implementation_ids.add(implementation_id)
        kind = _identifier(implementation["kind"], f"{field}.kind").upper()
        if kind not in IMPLEMENTATION_NODE_KINDS:
            raise UniverseError(
                "PROJECT_SEED_IMPLEMENTATION_KIND_INVALID",
                f"unsupported implementation node kind: {kind}",
            )
        normalized_implementation: dict[str, Any] = {
            "implementation_id": implementation_id,
            "kind": kind,
            "title": _required_text(implementation["title"], f"{field}.title"),
            "refs": [
                _project_file_ref(project_root, ref, f"{field}.refs[{ref_index}]")
                for ref_index, ref in enumerate(
                    _array(implementation["refs"], f"{field}.refs")
                )
            ],
        }
        if "summary" in implementation:
            normalized_implementation["summary"] = _required_text(
                implementation["summary"], f"{field}.summary"
            )
        implementation_nodes.append(normalized_implementation)

    implementation_bindings: list[dict[str, Any]] = []
    binding_ids: set[str] = set()
    for index, raw_binding in enumerate(
        _array(request.get("implementation_bindings", []), "implementation_bindings")
    ):
        field = f"implementation_bindings[{index}]"
        binding = _exact_object_fields(
            raw_binding,
            field=field,
            required=frozenset(
                {
                    "binding_id",
                    "functional_node_id",
                    "implementation_node_id",
                    "relation",
                }
            ),
            optional=frozenset({"summary"}),
        )
        binding_id = _identifier(binding["binding_id"], f"{field}.binding_id")
        if binding_id in binding_ids:
            raise UniverseError(
                "PROJECT_SEED_IMPLEMENTATION_BINDING_DUPLICATE",
                f"duplicate implementation binding: {binding_id}",
            )
        binding_ids.add(binding_id)
        functional_node_id = _identifier(
            binding["functional_node_id"], f"{field}.functional_node_id"
        )
        implementation_node_id = _identifier(
            binding["implementation_node_id"], f"{field}.implementation_node_id"
        )
        if functional_node_id not in node_ids:
            raise UniverseError(
                "PROJECT_SEED_FUNCTIONAL_NODE_UNKNOWN",
                f"{field} references an unknown functional node",
            )
        if implementation_node_id not in implementation_ids:
            raise UniverseError(
                "PROJECT_SEED_IMPLEMENTATION_NODE_UNKNOWN",
                f"{field} references an unknown implementation node",
            )
        relation = _identifier(binding["relation"], f"{field}.relation").upper()
        if relation not in IMPLEMENTATION_BINDING_RELATIONS:
            raise UniverseError(
                "PROJECT_SEED_IMPLEMENTATION_BINDING_RELATION_INVALID",
                f"unsupported implementation binding relation: {relation}",
            )
        normalized_binding: dict[str, Any] = {
            "binding_id": binding_id,
            "functional_node_id": functional_node_id,
            "implementation_node_id": implementation_node_id,
            "relation": relation,
        }
        if "summary" in binding:
            normalized_binding["summary"] = _required_text(
                binding["summary"], f"{field}.summary"
            )
        implementation_bindings.append(normalized_binding)

    documents: list[dict[str, Any]] = []
    document_ids: set[str] = set()
    for index, raw_document in enumerate(_array(request["documents"], "documents")):
        field = f"documents[{index}]"
        document = _exact_object_fields(
            raw_document,
            field=field,
            required=frozenset({"document_id", "path", "sha256", "role"}),
            optional=frozenset({"node_ids", "project_wide", "title"}),
        )
        document_id = _identifier(
            document["document_id"], f"{field}.document_id"
        )
        if document_id in document_ids:
            raise UniverseError(
                "PROJECT_SEED_DOCUMENT_DUPLICATE",
                f"duplicate document: {document_id}",
            )
        document_ids.add(document_id)
        role = _identifier(document["role"], f"{field}.role").upper()
        if role not in DOCUMENT_ROLES:
            raise UniverseError(
                "PROJECT_DOCUMENT_ROLE_INVALID",
                f"unsupported document role: {role}",
            )
        ref = _project_file_ref(
            project_root,
            {"path": document["path"], "sha256": document["sha256"]},
            field,
        )
        linked_nodes = _string_array(document.get("node_ids", []), f"{field}.node_ids")
        if any(node_id not in node_ids for node_id in linked_nodes):
            raise UniverseError(
                "PROJECT_DOCUMENT_NODE_UNKNOWN",
                f"{field}.node_ids contains an unknown node",
            )
        project_wide = document.get("project_wide", False)
        if not isinstance(project_wide, bool):
            raise UniverseError(
                "PROJECT_DOCUMENT_PROJECT_WIDE_INVALID",
                f"{field}.project_wide must be a boolean",
            )
        if project_wide and linked_nodes:
            raise UniverseError(
                "PROJECT_DOCUMENT_ATTACHMENT_INVALID",
                f"{field}.project_wide cannot be combined with node_ids",
            )
        normalized_document: dict[str, Any] = {
            "document_id": document_id,
            "path": ref["path"],
            "sha256": ref["sha256"],
            "role": role,
            "node_ids": linked_nodes,
        }
        if "title" in document:
            normalized_document["title"] = _required_text(
                document["title"], f"{field}.title"
            )
        if project_wide:
            normalized_document["project_wide"] = True
        documents.append(normalized_document)

    normalized = {
        "schema": PROJECT_SEED_SCHEMA,
        "seed_id": _identifier(request["seed_id"], "seed_id"),
        "project_id": project["project_id"],
        "source": {
            "ref": _required_text(source["ref"], "source.ref"),
            "commit": _source_commit(source["commit"]),
        },
        "verification": {
            "source_commit": "PROJECT_SUBMITTED",
            "file_digests": "VERIFIED_LOCAL",
            "raw_file_content_stored": False,
        },
        "project": {
            "kind": _identifier(project_input["kind"], "project.kind"),
            "technologies": technologies,
            "goal": _required_text(project_input["goal"], "project.goal"),
        },
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "edges": sorted(edges, key=lambda item: item["edge_id"]),
        "implementation": {
            "nodes": sorted(
                implementation_nodes, key=lambda item: item["implementation_id"]
            )
        },
        "implementation_bindings": sorted(
            implementation_bindings, key=lambda item: item["binding_id"]
        ),
        "documents": sorted(documents, key=lambda item: item["document_id"]),
    }
    if "summary" in project_input:
        normalized["project"]["summary"] = _required_text(
            project_input["summary"], "project.summary"
        )
    if working_rules:
        normalized["project"]["working_rules"] = working_rules
    normalized["seed_digest"] = _json_sha256(normalized)
    return normalized


def build_projection(seed: dict[str, Any]) -> dict[str, Any]:
    degree = {node["node_id"]: 0 for node in seed["nodes"]}
    missing_connections: list[dict[str, Any]] = []
    predicted_paths: list[dict[str, Any]] = []
    for edge in seed["edges"]:
        degree[edge["from_node"]] += 1
        degree[edge["to_node"]] += 1
        if "contract_ref" not in edge:
            missing_connections.append(
                {
                    "kind": "CONTRACT_REFERENCE_MISSING",
                    "subject_ref": f"edge:{edge['edge_id']}",
                }
            )
            predicted_paths.append(
                {
                    "candidate_id": f"document-contract-{edge['edge_id']}",
                    "action": "DOCUMENT_CONNECTION_CONTRACT",
                    "subject_ref": f"edge:{edge['edge_id']}",
                    "selection_state": "USER_SELECTION_REQUIRED",
                }
            )
    for node_id, count in sorted(degree.items()):
        if count == 0:
            missing_connections.append(
                {"kind": "NODE_DISCONNECTED", "subject_ref": f"node:{node_id}"}
            )
            predicted_paths.append(
                {
                    "candidate_id": f"connect-{node_id}",
                    "action": "CONNECT_NODE",
                    "subject_ref": f"node:{node_id}",
                    "selection_state": "USER_SELECTION_REQUIRED",
                }
            )
    for document in seed["documents"]:
        if not document["node_ids"] and not document.get("project_wide", False):
            missing_connections.append(
                {
                    "kind": "DOCUMENT_UNMAPPED",
                    "subject_ref": f"document:{document['document_id']}",
                }
            )
            predicted_paths.append(
                {
                    "candidate_id": f"map-{document['document_id']}",
                    "action": "MAP_DOCUMENT_TO_NODE",
                    "subject_ref": f"document:{document['document_id']}",
                    "selection_state": "USER_SELECTION_REQUIRED",
                }
            )
    material = {
        "schema": PROJECT_PROJECTION_SCHEMA,
        "project_id": seed["project_id"],
        "seed_id": seed["seed_id"],
        "seed_digest": seed["seed_digest"],
        "source": seed["source"],
        "project": seed["project"],
        "nodes": seed["nodes"],
        "edges": seed["edges"],
        "implementation": seed.get("implementation", {"nodes": []}),
        "implementation_bindings": seed.get("implementation_bindings", []),
        "documents": seed["documents"],
        "missing_connections": missing_connections,
        "predicted_paths": predicted_paths,
        "effects": {
            "project_source_write": "NONE",
            "project_document_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
        },
    }
    material["projection_digest"] = _json_sha256(material)
    material["projection_id"] = "projection_" + material["projection_digest"][:20]
    material["status"] = "CURRENT"
    return material


def _document_target(document: dict[str, Any]) -> str:
    filename = Path(document["path"]).name
    stable_name = f"{document['document_id']}-{filename}"
    role = document["role"].casefold()
    if document["role"] == "DECISION":
        return f".ai/universe/documents/decisions/{stable_name}"
    if document["role"] == "CONTRACT":
        return f".ai/universe/documents/connections/{stable_name}"
    if document["role"] == "EVIDENCE":
        return f".ai/universe/documents/evidence/{stable_name}"
    if document["node_ids"]:
        return (
            f".ai/universe/documents/nodes/{document['node_ids'][0]}/"
            f"{role}/{stable_name}"
        )
    return f".ai/universe/documents/reference/{stable_name}"


def build_document_incorporation_proposal(
    projection: dict[str, Any]
) -> dict[str, Any]:
    operations = []
    for document in projection["documents"]:
        already_incorporated = document["path"].startswith(".ai/universe/")
        target_path = (
            document["path"] if already_incorporated else _document_target(document)
        )
        operations.append(
            {
                "document_id": document["document_id"],
                "operation": "RETAIN" if already_incorporated else "DERIVE",
                "source_path": document["path"],
                "source_sha256": document["sha256"],
                "target_path": target_path,
                "node_ids": document["node_ids"],
                "role": document["role"],
            }
        )
    material = {
        "schema": DOCUMENT_PROPOSAL_SCHEMA,
        "project_id": projection["project_id"],
        "projection_id": projection["projection_id"],
        "projection_digest": projection["projection_digest"],
        "operations": operations,
        "approval": "REQUIRED",
        "execution_owner": "PROJECT",
        "effects": {
            "project_write": "NONE",
            "documents_moved": 0,
            "directories_created": 0,
        },
        "next_operation": "USER_APPROVAL_AND_PROJECT_MUTATION",
    }
    material["proposal_digest"] = _json_sha256(material)
    material["proposal_id"] = "incorporation_" + material["proposal_digest"][:20]
    material["status"] = "INCORPORATION_PROPOSAL_READY"
    return material


class UniverseStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.release_artifact_root = self.database_path.parent / "release-artifacts"
        self.release_artifact_root.mkdir(parents=True, exist_ok=True)
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

                CREATE TABLE IF NOT EXISTS project_seed (
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    seed_id TEXT NOT NULL,
                    seed_digest TEXT NOT NULL,
                    seed_json TEXT NOT NULL,
                    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, seed_id),
                    UNIQUE(project_id, seed_digest)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS project_seed_current
                ON project_seed(project_id)
                WHERE is_current = 1;

                CREATE TABLE IF NOT EXISTS project_projection (
                    projection_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    seed_id TEXT NOT NULL,
                    seed_digest TEXT NOT NULL,
                    projection_digest TEXT NOT NULL UNIQUE,
                    projection_json TEXT NOT NULL,
                    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
                    built_at TEXT NOT NULL,
                    FOREIGN KEY(project_id, seed_id)
                        REFERENCES project_seed(project_id, seed_id)
                        ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS project_projection_current
                ON project_projection(project_id)
                WHERE is_current = 1;

                CREATE TABLE IF NOT EXISTS document_incorporation_proposal (
                    proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    projection_id TEXT NOT NULL
                        REFERENCES project_projection(projection_id)
                        ON DELETE CASCADE,
                    proposal_digest TEXT NOT NULL UNIQUE,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_dispatch (
                    dispatch_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, idempotency_key)
                );

                CREATE INDEX IF NOT EXISTS project_dispatch_project_time
                ON project_dispatch(project_id, created_at, dispatch_id);

                CREATE TABLE IF NOT EXISTS project_dispatch_event (
                    event_id TEXT PRIMARY KEY,
                    dispatch_id TEXT NOT NULL
                        REFERENCES project_dispatch(dispatch_id)
                        ON DELETE CASCADE,
                    project_id TEXT NOT NULL,
                    previous_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS project_dispatch_event_order
                ON project_dispatch_event(dispatch_id, observed_at, event_id);

                CREATE TABLE IF NOT EXISTS project_result_packet (
                    dispatch_id TEXT PRIMARY KEY
                        REFERENCES project_dispatch(dispatch_id)
                        ON DELETE CASCADE,
                    project_id TEXT NOT NULL,
                    result_digest TEXT NOT NULL UNIQUE,
                    packet_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS release_artifact (
                    release_id TEXT PRIMARY KEY,
                    source_repository TEXT NOT NULL,
                    source_commit TEXT NOT NULL,
                    package_name TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    database_sha256 TEXT NOT NULL UNIQUE,
                    manifest_sha256 TEXT NOT NULL,
                    database_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_release_proposal (
                    proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    release_id TEXT NOT NULL
                        REFERENCES release_artifact(release_id),
                    plan_digest TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, release_id, plan_digest)
                );

                CREATE INDEX IF NOT EXISTS project_release_proposal_project_time
                ON project_release_proposal(project_id, created_at, proposal_id);

                CREATE TABLE IF NOT EXISTS universe_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    universe_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO universe_identity(
                    singleton, universe_id, created_at
                )
                VALUES (1, ?, ?)
                """,
                (str(uuid.uuid4()), utc_now()),
            )

    def identity(self) -> dict[str, str]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT universe_id, created_at
                FROM universe_identity
                WHERE singleton = 1
                """
            ).fetchone()
        if row is None:
            raise UniverseError(
                "UNIVERSE_IDENTITY_UNAVAILABLE",
                "Universe identity is missing from the local database",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        universe_id = str(row["universe_id"])
        try:
            uuid.UUID(universe_id)
        except ValueError as error:
            raise UniverseError(
                "UNIVERSE_IDENTITY_INVALID",
                "Universe identity is not a valid UUID",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        return {
            "schema": UNIVERSE_IDENTITY_SCHEMA,
            "universe_id": universe_id,
            "created_at": str(row["created_at"]),
        }

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

    def record_project_seed(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        seed = normalize_project_seed(project, value)
        now = utc_now()
        seed_json = _canonical_json(seed)
        with self._connection() as connection:
            by_id = connection.execute(
                """
                SELECT seed_digest, seed_json, recorded_at, is_current
                FROM project_seed
                WHERE project_id = ? AND seed_id = ?
                """,
                (project["project_id"], seed["seed_id"]),
            ).fetchone()
            if by_id is not None:
                if by_id["seed_digest"] != seed["seed_digest"]:
                    raise UniverseError(
                        "PROJECT_SEED_ID_CONFLICT",
                        "seed_id already refers to different content",
                        HTTPStatus.CONFLICT,
                    )
                if not by_id["is_current"]:
                    connection.execute(
                        "UPDATE project_seed SET is_current = 0 WHERE project_id = ?",
                        (project["project_id"],),
                    )
                    connection.execute(
                        """
                        UPDATE project_seed SET is_current = 1
                        WHERE project_id = ? AND seed_id = ?
                        """,
                        (project["project_id"], seed["seed_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE project_projection SET is_current = 0
                        WHERE project_id = ?
                        """,
                        (project["project_id"],),
                    )
                stored = json.loads(by_id["seed_json"])
                stored["recorded_at"] = by_id["recorded_at"]
                stored["is_current"] = True
                return stored, False
            by_digest = connection.execute(
                """
                SELECT seed_id, seed_json, recorded_at, is_current
                FROM project_seed
                WHERE project_id = ? AND seed_digest = ?
                """,
                (project["project_id"], seed["seed_digest"]),
            ).fetchone()
            if by_digest is not None:
                if not by_digest["is_current"]:
                    connection.execute(
                        "UPDATE project_seed SET is_current = 0 WHERE project_id = ?",
                        (project["project_id"],),
                    )
                    connection.execute(
                        """
                        UPDATE project_seed SET is_current = 1
                        WHERE project_id = ? AND seed_id = ?
                        """,
                        (project["project_id"], by_digest["seed_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE project_projection SET is_current = 0
                        WHERE project_id = ?
                        """,
                        (project["project_id"],),
                    )
                stored = json.loads(by_digest["seed_json"])
                stored["recorded_at"] = by_digest["recorded_at"]
                stored["is_current"] = True
                return stored, False
            connection.execute(
                "UPDATE project_seed SET is_current = 0 WHERE project_id = ?",
                (project["project_id"],),
            )
            connection.execute(
                "UPDATE project_projection SET is_current = 0 WHERE project_id = ?",
                (project["project_id"],),
            )
            connection.execute(
                """
                INSERT INTO project_seed(
                    project_id, seed_id, seed_digest, seed_json,
                    is_current, recorded_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    project["project_id"],
                    seed["seed_id"],
                    seed["seed_digest"],
                    seed_json,
                    now,
                ),
            )
        seed["recorded_at"] = now
        seed["is_current"] = True
        return seed, True

    def sync_project_seed_assets(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        try:
            seed_input = load_project_seed_assets(Path(project["project_root"]))
        except ProjectSeedAssetError as error:
            raise UniverseError(error.args[0], error.args[0], HTTPStatus.CONFLICT) from error
        seed, seed_created = self.record_project_seed(project["project_id"], seed_input)
        projection, projection_created = self.build_project_projection(
            project["project_id"],
            {
                "seed_id": seed["seed_id"],
                "expected_seed_digest": seed["seed_digest"],
            },
        )
        return {
            "schema": PROJECT_SEED_SCHEMA,
            "status": "PROJECT_SEED_ASSETS_SYNCED",
            "project_id": project["project_id"],
            "seed": seed,
            "projection": projection,
            "seed_recorded": seed_created,
            "projection_built": projection_created,
            "asset_root": ".ai/universe",
        }

    def get_project_seed(
        self, project_id: str, seed_id: str = ""
    ) -> dict[str, Any]:
        normalized_project = _project_id(project_id)
        self.get_project(normalized_project)
        with self._connection() as connection:
            if seed_id:
                normalized_seed = _identifier(seed_id, "seed_id")
                row = connection.execute(
                    """
                    SELECT seed_json, recorded_at, is_current
                    FROM project_seed
                    WHERE project_id = ? AND seed_id = ?
                    """,
                    (normalized_project, normalized_seed),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT seed_json, recorded_at, is_current
                    FROM project_seed
                    WHERE project_id = ? AND is_current = 1
                    """,
                    (normalized_project,),
                ).fetchone()
        if row is None:
            raise UniverseError(
                "PROJECT_SEED_NOT_FOUND",
                "project has no matching Project Seed",
                HTTPStatus.NOT_FOUND,
            )
        seed = json.loads(row["seed_json"])
        seed["recorded_at"] = row["recorded_at"]
        seed["is_current"] = bool(row["is_current"])
        return seed

    def build_project_projection(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        request = _exact_object_fields(
            value,
            field="projection_request",
            required=frozenset(),
            optional=frozenset({"seed_id", "expected_seed_digest"}),
        )
        seed = self.get_project_seed(project_id, str(request.get("seed_id", "")))
        if not seed["is_current"]:
            raise UniverseError(
                "PROJECT_SEED_NOT_CURRENT",
                "projection may only be built from the current Project Seed",
                HTTPStatus.CONFLICT,
            )
        expected_digest = request.get("expected_seed_digest")
        if expected_digest is not None and _sha256(
            expected_digest, "expected_seed_digest"
        ) != seed["seed_digest"]:
            raise UniverseError(
                "PROJECT_SEED_DIGEST_MISMATCH",
                "expected_seed_digest does not match the current Project Seed",
                HTTPStatus.CONFLICT,
            )
        projection = build_projection(seed)
        now = utc_now()
        projection_json = _canonical_json(projection)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT projection_json, built_at
                FROM project_projection WHERE projection_digest = ?
                """,
                (projection["projection_digest"],),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE project_projection
                    SET is_current = CASE WHEN projection_digest = ? THEN 1 ELSE 0 END
                    WHERE project_id = ?
                    """,
                    (projection["projection_digest"], projection["project_id"]),
                )
                stored = json.loads(existing["projection_json"])
                stored["built_at"] = existing["built_at"]
                return stored, False
            connection.execute(
                "UPDATE project_projection SET is_current = 0 WHERE project_id = ?",
                (projection["project_id"],),
            )
            connection.execute(
                """
                INSERT INTO project_projection(
                    projection_id, project_id, seed_id, seed_digest,
                    projection_digest, projection_json, is_current, built_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    projection["projection_id"],
                    projection["project_id"],
                    projection["seed_id"],
                    projection["seed_digest"],
                    projection["projection_digest"],
                    projection_json,
                    now,
                ),
            )
        projection["built_at"] = now
        return projection, True

    def get_project_projection(self, project_id: str) -> dict[str, Any]:
        normalized = _project_id(project_id)
        self.get_project(normalized)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT projection_json, built_at
                FROM project_projection
                WHERE project_id = ? AND is_current = 1
                """,
                (normalized,),
            ).fetchone()
            any_projection = connection.execute(
                "SELECT 1 FROM project_projection WHERE project_id = ? LIMIT 1",
                (normalized,),
            ).fetchone()
        if row is None and any_projection is not None:
            raise UniverseError(
                "PROJECT_PROJECTION_REBUILD_REQUIRED",
                "the current Project Seed has no current projection",
                HTTPStatus.CONFLICT,
            )
        if row is None:
            raise UniverseError(
                "PROJECT_PROJECTION_NOT_FOUND",
                "project has no Project Projection",
                HTTPStatus.NOT_FOUND,
            )
        projection = json.loads(row["projection_json"])
        projection["built_at"] = row["built_at"]
        return projection

    def create_document_incorporation_proposal(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        request = _exact_object_fields(
            value,
            field="incorporation_request",
            required=frozenset(),
            optional=frozenset({"projection_id", "expected_projection_digest"}),
        )
        projection = self.get_project_projection(project_id)
        if request.get("projection_id") not in {None, projection["projection_id"]}:
            raise UniverseError(
                "PROJECT_PROJECTION_ID_MISMATCH",
                "projection_id does not match the current projection",
                HTTPStatus.CONFLICT,
            )
        expected = request.get("expected_projection_digest")
        if expected is not None and _sha256(
            expected, "expected_projection_digest"
        ) != projection["projection_digest"]:
            raise UniverseError(
                "PROJECT_PROJECTION_DIGEST_MISMATCH",
                "expected_projection_digest does not match the current projection",
                HTTPStatus.CONFLICT,
            )
        proposal = build_document_incorporation_proposal(projection)
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM document_incorporation_proposal
                WHERE proposal_digest = ?
                """,
                (proposal["proposal_digest"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["proposal_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO document_incorporation_proposal(
                    proposal_id, project_id, projection_id,
                    proposal_digest, proposal_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal["proposal_id"],
                    proposal["project_id"],
                    proposal["projection_id"],
                    proposal["proposal_digest"],
                    _canonical_json(proposal),
                    now,
                ),
            )
        proposal["created_at"] = now
        return proposal, True

    def import_release(self, value: Any) -> tuple[dict[str, Any], bool]:
        request = _exact_object_fields(
            value,
            field="release_import",
            required=frozenset({"database_path", "manifest_path", "mode"}),
        )
        require_release_lifecycle_mode(request["mode"])
        try:
            source_database = Path(
                _required_text(request["database_path"], "database_path")
            ).expanduser().resolve(strict=True)
            source_manifest = Path(
                _required_text(request["manifest_path"], "manifest_path")
            ).expanduser().resolve(strict=True)
        except OSError as error:
            raise UniverseError("RELEASE_ARTIFACT_UNAVAILABLE", str(error)) from error
        if not source_database.is_file() or not source_manifest.is_file():
            raise UniverseError(
                "RELEASE_ARTIFACT_INVALID",
                "release database and manifest must be files",
            )

        database_content = source_database.read_bytes()
        manifest_content = source_manifest.read_bytes()
        database_sha256 = hashlib.sha256(database_content).hexdigest()
        manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()
        try:
            manifest = json.loads(manifest_content.decode("utf-8"))
            if not isinstance(manifest, dict):
                raise UniverseError(
                    "RELEASE_VERIFICATION_FAILED",
                    "release manifest must be an object",
                    HTTPStatus.CONFLICT,
                )
            database_name = _required_text(
                manifest.get("database"),
                "manifest.database",
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise UniverseError(
                "RELEASE_VERIFICATION_FAILED",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        if Path(database_name).name != database_name or database_name in {".", ".."}:
            raise UniverseError(
                "RELEASE_VERIFICATION_FAILED",
                "manifest database name must be a plain file name",
                HTTPStatus.CONFLICT,
            )
        artifact_directory = self.release_artifact_root / database_sha256
        stored_database = artifact_directory / database_name
        stored_manifest = artifact_directory / (
            manifest_sha256 + ".manifest.json"
        )
        for stored_path, expected_sha256, content in (
            (stored_database, database_sha256, database_content),
            (stored_manifest, manifest_sha256, manifest_content),
        ):
            if stored_path.exists():
                if not stored_path.is_file() or stored_path.is_symlink():
                    raise UniverseError(
                        "RELEASE_CATALOG_CORRUPT",
                        f"stored release artifact is not a regular file: {stored_path}",
                        HTTPStatus.CONFLICT,
                    )
                if hashlib.sha256(stored_path.read_bytes()).hexdigest() != expected_sha256:
                    raise UniverseError(
                        "RELEASE_CATALOG_CORRUPT",
                        f"stored release artifact digest mismatch: {stored_path}",
                        HTTPStatus.CONFLICT,
                    )
                continue
            _write_bytes_atomic(stored_path, content)
        try:
            verification = verify_release(
                database_path=stored_database,
                manifest_path=stored_manifest,
            )
        except (
            CoreReleaseError,
            OSError,
            sqlite3.Error,
        ) as error:
            raise UniverseError(
                "RELEASE_VERIFICATION_FAILED",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        release_id = _identifier(verification["release_id"], "release_id")
        source_repository = _required_text(
            manifest.get("source_repository"),
            "manifest.source_repository",
        )
        source_commit = _required_text(
            verification["source_commit"],
            "verification.source_commit",
        )
        package_name = _required_text(
            manifest.get("package_name"),
            "manifest.package_name",
        )
        imported_at = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT database_sha256, manifest_sha256
                FROM release_artifact
                WHERE release_id = ?
                """,
                (release_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["database_sha256"] != database_sha256
                    or existing["manifest_sha256"] != manifest_sha256
                ):
                    raise UniverseError(
                        "RELEASE_ID_CONFLICT",
                        "release_id already refers to another immutable artifact",
                        HTTPStatus.CONFLICT,
                    )
                return self.get_release(release_id), False
            connection.execute(
                """
                INSERT INTO release_artifact(
                    release_id, source_repository, source_commit, package_name,
                    payload_sha256, database_sha256, manifest_sha256,
                    database_path, manifest_path, manifest_json,
                    verification_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    release_id,
                    source_repository,
                    source_commit,
                    package_name,
                    verification["payload_sha256"],
                    database_sha256,
                    manifest_sha256,
                    str(stored_database),
                    str(stored_manifest),
                    _canonical_json(manifest),
                    _canonical_json(verification),
                    imported_at,
                ),
            )
        return self.get_release(release_id), True

    def list_releases(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT release_id, source_repository, source_commit, package_name,
                       payload_sha256, database_sha256, manifest_sha256,
                       verification_json, imported_at
                FROM release_artifact
                ORDER BY imported_at DESC, release_id DESC
                """
            ).fetchall()
        return [self._release_row(row) for row in rows]

    def get_release(self, release_id: str) -> dict[str, Any]:
        normalized = _identifier(release_id, "release_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT release_id, source_repository, source_commit, package_name,
                       payload_sha256, database_sha256, manifest_sha256,
                       verification_json, imported_at
                FROM release_artifact
                WHERE release_id = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "RELEASE_NOT_FOUND",
                f"release is not imported: {normalized}",
                HTTPStatus.NOT_FOUND,
            )
        return self._release_row(row)

    def create_project_release_proposal(
        self,
        project_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], bool]:
        request = _exact_object_fields(
            value,
            field="project_release_proposal",
            required=frozenset({"release_id", "mode"}),
        )
        require_release_lifecycle_mode(request["mode"])
        project = self.get_project(project_id)
        release_id = _identifier(request["release_id"], "release_id")
        with self._connection() as connection:
            artifact = connection.execute(
                """
                SELECT database_path, manifest_path, database_sha256
                FROM release_artifact
                WHERE release_id = ?
                """,
                (release_id,),
            ).fetchone()
        if artifact is None:
            raise UniverseError(
                "RELEASE_NOT_FOUND",
                f"release is not imported: {release_id}",
                HTTPStatus.NOT_FOUND,
            )
        try:
            with ReleaseRuntime(
                database_path=Path(artifact["database_path"]),
                manifest_path=Path(artifact["manifest_path"]),
            ) as runtime:
                plan = runtime.plan_project_install(
                    Path(project["project_root"])
                )
        except (
            CoreReleaseError,
            ReleaseRuntimeError,
            OSError,
            sqlite3.Error,
        ) as error:
            raise UniverseError(
                "PROJECT_RELEASE_PLAN_FAILED",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        material = {
            "schema": RELEASE_PROPOSAL_SCHEMA,
            "project_id": project["project_id"],
            "release_id": release_id,
            "mode": MASTER_MODE,
            "release_database_sha256": artifact["database_sha256"],
            "plan": plan,
            "approval": "REQUIRED",
            "execution_owner": "PROJECT_HOST",
            "effects": {
                "project_write": "NONE",
                "files_changed": 0,
            },
            "next_operation": (
                "RESOLVE_COLLISIONS"
                if plan["collisions"]
                else "USER_APPROVAL_AND_PROJECT_HOST_APPLY"
            ),
        }
        material["proposal_digest"] = _json_sha256(material)
        material["proposal_id"] = "release_proposal_" + material[
            "proposal_digest"
        ][:20]
        material["status"] = (
            "PROJECT_RELEASE_PROPOSAL_BLOCKED"
            if plan["collisions"]
            else "PROJECT_RELEASE_PROPOSAL_READY"
        )
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM project_release_proposal
                WHERE project_id = ? AND release_id = ? AND plan_digest = ?
                """,
                (
                    project["project_id"],
                    release_id,
                    plan["plan_digest"],
                ),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["proposal_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO project_release_proposal(
                    proposal_id, project_id, release_id, plan_digest,
                    proposal_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    material["proposal_id"],
                    project["project_id"],
                    release_id,
                    plan["plan_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def list_project_release_proposals(
        self,
        project_id: str,
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM project_release_proposal
                WHERE project_id = ?
                ORDER BY created_at DESC, proposal_id DESC
                """,
                (project["project_id"],),
            ).fetchall()
        proposals = []
        for row in rows:
            proposal = json.loads(row["proposal_json"])
            proposal["created_at"] = row["created_at"]
            proposals.append(proposal)
        return proposals

    @staticmethod
    def _release_row(row: sqlite3.Row) -> dict[str, Any]:
        verification = json.loads(row["verification_json"])
        return {
            "schema": RELEASE_ARTIFACT_SCHEMA,
            "release_id": row["release_id"],
            "source_repository": row["source_repository"],
            "source_commit": row["source_commit"],
            "package_name": row["package_name"],
            "payload_sha256": row["payload_sha256"],
            "database_sha256": row["database_sha256"],
            "manifest_sha256": row["manifest_sha256"],
            "profile_catalog": verification["profile_catalog"],
            "candidate_execution": verification["candidate_execution"],
            "imported_at": row["imported_at"],
        }

    def create_dispatch(
        self,
        project_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        try:
            envelope = normalize_dispatch_request(project["project_id"], value)
        except DispatchError as error:
            raise UniverseError("DISPATCH_INVALID", str(error)) from error
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT dispatch_id, content_digest
                FROM project_dispatch
                WHERE project_id = ? AND idempotency_key = ?
                """,
                (project["project_id"], envelope["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                if existing["content_digest"] != envelope["content_digest"]:
                    raise UniverseError(
                        "DISPATCH_IDEMPOTENCY_CONFLICT",
                        "idempotency_key already refers to another dispatch",
                        HTTPStatus.CONFLICT,
                    )
                return self.get_dispatch(existing["dispatch_id"]), False
            connection.execute(
                """
                INSERT INTO project_dispatch(
                    dispatch_id, project_id, idempotency_key, content_digest,
                    envelope_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?)
                """,
                (
                    envelope["dispatch_id"],
                    envelope["project_id"],
                    envelope["idempotency_key"],
                    envelope["content_digest"],
                    _canonical_json(envelope),
                    envelope["created_at"],
                    now,
                ),
            )
            queued = {
                "schema": "universe.dispatch-event.v1",
                "event_id": "dispatch_event_" + envelope["content_digest"][:24],
                "dispatch_id": envelope["dispatch_id"],
                "project_id": envelope["project_id"],
                "previous_status": "NONE",
                "status": "QUEUED",
                "evidence_ref": "universe-store:" + envelope["dispatch_id"],
                "details": {},
                "observed_at": envelope["created_at"],
            }
            self._insert_dispatch_event(connection, queued)
        return self.get_dispatch(envelope["dispatch_id"]), True

    def create_project_seed_discovery_dispatch(
        self, project_id: str
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        template = project_seed_template()
        return self.create_dispatch(
            project["project_id"],
            {
                "idempotency_key": (
                    f"project-seed-discovery-{project['project_id']}-{template['template_id']}"
                ),
                "title": "Prepare Universe project seed",
                "instruction": (
                    "Read this Project's policy, source, and existing documents without "
                    "executing Project code. Create or update the canonical .ai/universe "
                    "Seed assets using the supplied template. Keep functional nodes, "
                    "implementation nodes, and their bindings separate. Return the published "
                    "Seed revision; do not mutate application source as part of this request."
                ),
                "constraints": [
                    "STATIC_DISCOVERY_ONLY",
                    "PROJECT_MASTER_OWNS_PROJECT_WRITES",
                    "FUNCTIONAL_AND_IMPLEMENTATION_GRAPHS_MUST_REMAIN_SEPARATE",
                    "DO_NOT_EXECUTE_PROJECT_CODE",
                ],
                "expected_output": {
                    "schema": PROJECT_DISCOVERY_DISPATCH_SCHEMA,
                    "template": template,
                    "result": "PUBLISHED_PROJECT_SEED_ASSETS_OR_BLOCKED_RESULT_PACKET",
                },
                "requested_mode": MASTER_MODE,
            },
        )

    def list_dispatches(
        self,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        bounded_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT dispatch_id, envelope_json, status, updated_at
                FROM project_dispatch
                WHERE project_id = ?
                ORDER BY created_at DESC, dispatch_id DESC
                LIMIT ?
                """,
                (project["project_id"], bounded_limit),
            ).fetchall()
        return [
            self._dispatch_summary(row)
            for row in rows
        ]

    def get_dispatch(self, dispatch_id: str) -> dict[str, Any]:
        normalized = _required_text(dispatch_id, "dispatch_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT dispatch_id, project_id, envelope_json, status,
                       created_at, updated_at
                FROM project_dispatch
                WHERE dispatch_id = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                raise UniverseError(
                    "DISPATCH_NOT_FOUND",
                    f"dispatch is not recorded: {normalized}",
                    HTTPStatus.NOT_FOUND,
                )
            event_rows = connection.execute(
                """
                SELECT event_json
                FROM project_dispatch_event
                WHERE dispatch_id = ?
                ORDER BY rowid
                """,
                (normalized,),
            ).fetchall()
            result_row = connection.execute(
                """
                SELECT packet_json
                FROM project_result_packet
                WHERE dispatch_id = ?
                """,
                (normalized,),
            ).fetchone()
        envelope = json.loads(row["envelope_json"])
        envelope["status"] = row["status"]
        return {
            "dispatch": envelope,
            "updated_at": row["updated_at"],
            "events": [json.loads(item["event_json"]) for item in event_rows],
            "result_packet": (
                json.loads(result_row["packet_json"])
                if result_row is not None
                else None
            ),
        }

    def deliver_dispatch(
        self,
        dispatch_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], bool]:
        request = _exact_object_fields(
            value,
            field="dispatch_delivery",
            required=frozenset(),
            optional=frozenset({"approval"}),
        )
        approval = request.get("approval")
        if not isinstance(approval, str) or approval.strip().upper() != "APPROVED":
            raise UniverseError(
                "DISPATCH_DELIVERY_APPROVAL_REQUIRED",
                "project inbox delivery requires explicit approval",
                HTTPStatus.CONFLICT,
            )
        current = self.get_dispatch(dispatch_id)
        envelope = current["dispatch"]
        if envelope["status"] != "QUEUED":
            delivered = next(
                (
                    event
                    for event in current["events"]
                    if event["status"] == "DELIVERED"
                ),
                None,
            )
            if delivered is not None:
                return self.get_dispatch(dispatch_id), False
            raise UniverseError(
                "DISPATCH_DELIVERY_STATE_INVALID",
                "only a QUEUED dispatch can be delivered",
                HTTPStatus.CONFLICT,
            )
        project = self.get_project(envelope["project_id"])
        try:
            receipt = LocalInboxConnector(
                Path(project["project_root"]),
                project["refs"]["master_inbox"],
            ).deliver(envelope)
            event = transition_event(
                dispatch_id=envelope["dispatch_id"],
                project_id=envelope["project_id"],
                current_status="QUEUED",
                next_status="DELIVERED",
                evidence_ref=(
                    "local-inbox:"
                    + receipt["target_ref"]
                    + "#"
                    + receipt["content_sha256"]
                ),
                details={"delivery_receipt": receipt},
            )
        except DispatchError as error:
            raise UniverseError(
                "DISPATCH_DELIVERY_BLOCKED",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        self._apply_dispatch_transition(event)
        return self.get_dispatch(dispatch_id), True

    def wake_dispatch(self, dispatch_id: str, value: Any) -> dict[str, Any]:
        request = _exact_object_fields(
            value,
            field="wake_request",
            required=frozenset({"kind"}),
            optional=frozenset({"endpoint", "token"}),
        )
        current = self.get_dispatch(dispatch_id)
        envelope = current["dispatch"]
        if envelope["status"] not in {
            "DELIVERED",
            "ACKNOWLEDGED",
            "STARTED",
        }:
            raise UniverseError(
                "DISPATCH_WAKE_STATE_INVALID",
                "dispatch must be delivered before wake",
                HTTPStatus.CONFLICT,
            )
        kind = _required_text(request["kind"], "wake_request.kind").upper()
        try:
            adapter: ProjectWakeAdapter
            if kind == "NONE":
                adapter = NoWakeAdapter()
            elif kind == "HTTP":
                adapter = HttpProjectWakeAdapter(
                    endpoint=_required_text(
                        request.get("endpoint"),
                        "wake_request.endpoint",
                    ),
                    token=_required_text(
                        request.get("token"),
                        "wake_request.token",
                    ),
                )
            else:
                raise UniverseError(
                    "WAKE_ADAPTER_UNSUPPORTED",
                    "wake kind must be NONE or HTTP",
                )
            receipt = adapter.wake(envelope)
        except DispatchError as error:
            raise UniverseError(
                "PROJECT_WAKE_BLOCKED",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        event = {
            "schema": "universe.dispatch-event.v1",
            "event_id": "dispatch_event_" + _json_sha256(receipt)[:24],
            "dispatch_id": envelope["dispatch_id"],
            "project_id": envelope["project_id"],
            "previous_status": envelope["status"],
            "status": envelope["status"],
            "evidence_ref": "wake:" + receipt["status"],
            "details": {"wake_receipt": receipt},
            "observed_at": utc_now(),
        }
        with self._connection() as connection:
            self._insert_dispatch_event(connection, event)
        return self.get_dispatch(dispatch_id)

    def acknowledge_dispatch(
        self,
        dispatch_id: str,
        value: Any,
    ) -> dict[str, Any]:
        return self._transition_from_request(
            dispatch_id,
            value,
            expected_current="DELIVERED",
            target="ACKNOWLEDGED",
        )

    def start_dispatch(self, dispatch_id: str, value: Any) -> dict[str, Any]:
        return self._transition_from_request(
            dispatch_id,
            value,
            expected_current="ACKNOWLEDGED",
            target="STARTED",
        )

    def record_result_packet(
        self,
        dispatch_id: str,
        value: Any,
    ) -> dict[str, Any]:
        current = self.get_dispatch(dispatch_id)
        try:
            packet = normalize_result_packet(
                dispatch=current["dispatch"],
                value=value,
            )
            event = transition_event(
                dispatch_id=packet["dispatch_id"],
                project_id=packet["project_id"],
                current_status="STARTED",
                next_status=packet["status"],
                evidence_ref="result-packet:" + packet["result_digest"],
                details={"result_digest": packet["result_digest"]},
            )
        except DispatchError as error:
            raise UniverseError(
                "RESULT_PACKET_INVALID",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT result_digest
                FROM project_result_packet
                WHERE dispatch_id = ?
                """,
                (packet["dispatch_id"],),
            ).fetchone()
            if existing is not None:
                if existing["result_digest"] != packet["result_digest"]:
                    raise UniverseError(
                        "RESULT_PACKET_CONFLICT",
                        "dispatch already has another Result Packet",
                        HTTPStatus.CONFLICT,
                    )
                return self.get_dispatch(dispatch_id)
            cursor = connection.execute(
                """
                UPDATE project_dispatch
                SET status = ?, envelope_json = ?, updated_at = ?
                WHERE dispatch_id = ? AND status = 'STARTED'
                """,
                (
                    packet["status"],
                    _canonical_json(
                        {**current["dispatch"], "status": packet["status"]}
                    ),
                    event["observed_at"],
                    packet["dispatch_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise UniverseError(
                    "DISPATCH_STATE_CHANGED",
                    "dispatch state changed before Result Packet commit",
                    HTTPStatus.CONFLICT,
                )
            connection.execute(
                """
                INSERT INTO project_result_packet(
                    dispatch_id, project_id, result_digest,
                    packet_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    packet["dispatch_id"],
                    packet["project_id"],
                    packet["result_digest"],
                    _canonical_json(packet),
                    packet["completed_at"],
                ),
            )
            self._insert_dispatch_event(connection, event)
        return self.get_dispatch(dispatch_id)

    def _transition_from_request(
        self,
        dispatch_id: str,
        value: Any,
        *,
        expected_current: str,
        target: str,
    ) -> dict[str, Any]:
        request = _exact_object_fields(
            value,
            field="dispatch_transition",
            required=frozenset({"evidence_ref"}),
            optional=frozenset({"details"}),
        )
        current = self.get_dispatch(dispatch_id)
        envelope = current["dispatch"]
        if envelope["status"] != expected_current:
            raise UniverseError(
                "DISPATCH_TRANSITION_STATE_INVALID",
                f"dispatch must be {expected_current} before {target}",
                HTTPStatus.CONFLICT,
            )
        details = request.get("details", {})
        if not isinstance(details, dict):
            raise UniverseError(
                "DISPATCH_TRANSITION_INVALID",
                "details must be an object",
            )
        try:
            event = transition_event(
                dispatch_id=envelope["dispatch_id"],
                project_id=envelope["project_id"],
                current_status=expected_current,
                next_status=target,
                evidence_ref=_required_text(
                    request["evidence_ref"],
                    "dispatch_transition.evidence_ref",
                ),
                details=details,
            )
        except DispatchError as error:
            raise UniverseError(
                "DISPATCH_TRANSITION_INVALID",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        self._apply_dispatch_transition(event)
        return self.get_dispatch(dispatch_id)

    def _apply_dispatch_transition(self, event: dict[str, Any]) -> None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT envelope_json
                FROM project_dispatch
                WHERE dispatch_id = ? AND status = ?
                """,
                (event["dispatch_id"], event["previous_status"]),
            ).fetchone()
            if row is None:
                raise UniverseError(
                    "DISPATCH_STATE_CHANGED",
                    "dispatch state changed before transition commit",
                    HTTPStatus.CONFLICT,
                )
            envelope = json.loads(row["envelope_json"])
            envelope["status"] = event["status"]
            cursor = connection.execute(
                """
                UPDATE project_dispatch
                SET status = ?, envelope_json = ?, updated_at = ?
                WHERE dispatch_id = ? AND status = ?
                """,
                (
                    event["status"],
                    _canonical_json(envelope),
                    event["observed_at"],
                    event["dispatch_id"],
                    event["previous_status"],
                ),
            )
            if cursor.rowcount != 1:
                raise UniverseError(
                    "DISPATCH_STATE_CHANGED",
                    "dispatch state changed before transition commit",
                    HTTPStatus.CONFLICT,
                )
            self._insert_dispatch_event(connection, event)

    @staticmethod
    def _insert_dispatch_event(
        connection: sqlite3.Connection,
        event: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_dispatch_event(
                event_id, dispatch_id, project_id, previous_status,
                status, event_json, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["dispatch_id"],
                event["project_id"],
                event["previous_status"],
                event["status"],
                _canonical_json(event),
                event["observed_at"],
            ),
        )

    @staticmethod
    def _dispatch_summary(row: sqlite3.Row) -> dict[str, Any]:
        envelope = json.loads(row["envelope_json"])
        envelope["status"] = row["status"]
        return {
            "dispatch": envelope,
            "updated_at": row["updated_at"],
        }

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
        host_text = host.decode("ascii") if isinstance(host, bytes) else host
        self.connection_profile = local_connection_profile(
            f"http://{host_text}:{port}"
        )
        self.interface_profiles = (local_http_interface_profile(),)


class UniverseRequestHandler(BaseHTTPRequestHandler):
    server: UniverseHTTPServer
    server_version = "UniverseLocal/1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/", "/app.js", "/styles.css"}:
            self._send_static(path)
            return
        if path == "/health":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "READY",
                    "universe": self.server.store.identity(),
                    "connection": self.server.connection_profile.as_dict(),
                    "interfaces": [
                        profile.as_dict() for profile in self.server.interface_profiles
                    ],
                },
            )
            return
        if not self._authorize():
            return
        if path == "/v1/releases":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "RELEASES_COLLECTED",
                    "releases": self.server.store.list_releases(),
                },
            )
            return
        release_id = self._release_path(path)
        if release_id is not None:
            try:
                self._send(
                    HTTPStatus.OK,
                    self.server.store.get_release(release_id),
                )
            except UniverseError as error:
                self._send_error(error)
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
        if path == "/v1/templates/project-seed":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "PROJECT_SEED_TEMPLATE_COLLECTED",
                    "template": project_seed_template(),
                },
            )
            return
        dispatch_parts = self._dispatch_path(path)
        if dispatch_parts is not None and dispatch_parts[1] == "":
            try:
                self._send(
                    HTTPStatus.OK,
                    self.server.store.get_dispatch(dispatch_parts[0]),
                )
            except UniverseError as error:
                self._send_error(error)
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
            if suffix == "/dispatches":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_DISPATCHES_COLLECTED",
                        "project_id": project_id,
                        "dispatches": self.server.store.list_dispatches(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/release-proposals":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_RELEASE_PROPOSALS_COLLECTED",
                        "project_id": project_id,
                        "proposals": (
                            self.server.store.list_project_release_proposals(
                                project_id
                            )
                        ),
                    },
                )
                return
            if suffix == "/seed":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_SEED_COLLECTED",
                        "seed": self.server.store.get_project_seed(project_id),
                    },
                )
                return
            if suffix == "/projection":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_PROJECTION_COLLECTED",
                        "projection": self.server.store.get_project_projection(
                            project_id
                        ),
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
            if path == "/v1/releases/import":
                release, created = self.server.store.import_release(body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "RELEASE_IMPORTED"
                            if created
                            else "RELEASE_ALREADY_IMPORTED"
                        ),
                        "release": release,
                    },
                )
                return
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
            if parts is not None and parts[1] == "/release-proposals":
                proposal, created = (
                    self.server.store.create_project_release_proposal(
                        parts[0],
                        body,
                    )
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_RELEASE_PROPOSAL_RECORDED"
                            if created
                            else "PROJECT_RELEASE_PROPOSAL_ALREADY_RECORDED"
                        ),
                        "proposal": proposal,
                    },
                )
                return
            if parts is not None and parts[1] == "/discovery-dispatch":
                dispatch, created = self.server.store.create_project_seed_discovery_dispatch(
                    parts[0]
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_DISCOVERY_DISPATCH_QUEUED"
                            if created
                            else "PROJECT_DISCOVERY_DISPATCH_ALREADY_QUEUED"
                        ),
                        **dispatch,
                    },
                )
                return
            if parts is not None and parts[1] == "/sync":
                result = self.server.store.sync_project_seed_assets(parts[0])
                self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
                return
            if parts is not None and parts[1] == "/dispatches":
                dispatch, created = self.server.store.create_dispatch(
                    parts[0],
                    body,
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "DISPATCH_QUEUED"
                            if created
                            else "DISPATCH_ALREADY_QUEUED"
                        ),
                        **dispatch,
                    },
                )
                return
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
            if parts is not None and parts[1] == "/seed":
                seed, created = self.server.store.record_project_seed(parts[0], body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_SEED_RECORDED"
                            if created
                            else "PROJECT_SEED_ALREADY_RECORDED"
                        ),
                        "seed": seed,
                        "next_operation": "BUILD_PROJECT_PROJECTION",
                    },
                )
                return
            if parts is not None and parts[1] == "/projection":
                projection, created = self.server.store.build_project_projection(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_PROJECTION_BUILT"
                            if created
                            else "PROJECT_PROJECTION_ALREADY_CURRENT"
                        ),
                        "projection": projection,
                        "next_operation": "REVIEW_PREDICTED_PATHS",
                    },
                )
                return
            if (
                parts is not None
                and parts[1] == "/document-incorporation-proposals"
            ):
                proposal, created = (
                    self.server.store.create_document_incorporation_proposal(
                        parts[0], body
                    )
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "INCORPORATION_PROPOSAL_READY"
                            if created
                            else "INCORPORATION_PROPOSAL_ALREADY_RECORDED"
                        ),
                        "proposal": proposal,
                    },
                )
                return
            dispatch_parts = self._dispatch_path(path)
            if dispatch_parts is not None:
                dispatch_id, operation = dispatch_parts
                if operation == "/deliver":
                    dispatch, changed = self.server.store.deliver_dispatch(
                        dispatch_id,
                        body,
                    )
                    self._send(
                        HTTPStatus.OK,
                        {
                            "schema": API_SCHEMA,
                            "status": (
                                "DISPATCH_DELIVERED"
                                if changed
                                else "DISPATCH_ALREADY_DELIVERED"
                            ),
                            **dispatch,
                        },
                    )
                    return
                if operation == "/wake":
                    dispatch = self.server.store.wake_dispatch(
                        dispatch_id,
                        body,
                    )
                    self._send(
                        HTTPStatus.OK,
                        {
                            "schema": API_SCHEMA,
                            "status": "PROJECT_WAKE_RECORDED",
                            **dispatch,
                        },
                    )
                    return
                if operation == "/acknowledge":
                    dispatch = self.server.store.acknowledge_dispatch(
                        dispatch_id,
                        body,
                    )
                    self._send(
                        HTTPStatus.OK,
                        {
                            "schema": API_SCHEMA,
                            "status": "DISPATCH_ACKNOWLEDGED",
                            **dispatch,
                        },
                    )
                    return
                if operation == "/start":
                    dispatch = self.server.store.start_dispatch(
                        dispatch_id,
                        body,
                    )
                    self._send(
                        HTTPStatus.OK,
                        {
                            "schema": API_SCHEMA,
                            "status": "DISPATCH_STARTED",
                            **dispatch,
                        },
                    )
                    return
                if operation == "/result":
                    dispatch = self.server.store.record_result_packet(
                        dispatch_id,
                        body,
                    )
                    self._send(
                        HTTPStatus.OK,
                        {
                            "schema": API_SCHEMA,
                            "status": "RESULT_PACKET_RECORDED",
                            **dispatch,
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
        for suffix in (
            "/document-incorporation-proposals",
            "/release-proposals",
            "/dispatches",
            "/discovery-dispatch",
            "/projection",
            "/events",
            "/seed",
            "/sync",
        ):
            if remainder.endswith(suffix):
                return unquote(remainder[:-len(suffix)]), suffix
        return unquote(remainder), ""

    @staticmethod
    def _release_path(path: str) -> str | None:
        prefix = "/v1/releases/"
        if not path.startswith(prefix):
            return None
        release_id = unquote(path[len(prefix):])
        if not release_id or "/" in release_id:
            return None
        return release_id

    @staticmethod
    def _dispatch_path(path: str) -> tuple[str, str] | None:
        prefix = "/v1/dispatches/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix):]
        for suffix in (
            "/acknowledge",
            "/deliver",
            "/result",
            "/start",
            "/wake",
        ):
            if remainder.endswith(suffix):
                return unquote(remainder[:-len(suffix)]), suffix
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

    def _send_static(self, path: str) -> None:
        filename = {
            "/": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
        }[path]
        target = UI_ROOT / filename
        if not target.is_file() or target.is_symlink():
            self._not_found()
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
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
    path: Path,
    *,
    endpoint: str,
    token: str,
    database_path: Path,
    universe_identity: dict[str, str],
) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "universe.local-service-state.v1",
        "universe": universe_identity,
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
    serve.add_argument("--open-ui", action="store_true")
    serve.add_argument("--mode-registry", type=Path, default=DEFAULT_MODE_REGISTRY_PATH)

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
            mode_registry = load_universe_mode_registry(args.mode_registry)
            token = args.token or os.environ.get("UNIVERSE_TOKEN") or secrets.token_urlsafe(32)
            server = create_server(
                database_path=args.database, token=token, host=args.host, port=args.port
            )
            host, port = server.server_address[:2]
            host_text = host.decode("ascii") if isinstance(host, bytes) else host
            endpoint = f"http://{host_text}:{port}"
            write_server_state(
                args.state_file,
                endpoint=endpoint,
                token=token,
                database_path=args.database,
                universe_identity=server.store.identity(),
            )
            print(
                json.dumps(
                    {
                        "schema": API_SCHEMA,
                        "status": "UNIVERSE_SERVICE_READY",
                        "universe": server.store.identity(),
                        "endpoint": endpoint,
                        "database": str(args.database.expanduser().resolve()),
                        "state_file": str(args.state_file.expanduser().resolve()),
                        "mode_contract": {
                            "mode": UNIVERSE_MODE,
                            "role": UNIVERSE_ROLE,
                            "registry_revision": mode_registry.get("revision", "UNKNOWN"),
                        },
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if args.open_ui:
                webbrowser.open(
                    endpoint + "/#token=" + quote(token, safe=""),
                    new=1,
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
