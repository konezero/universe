from __future__ import annotations

import argparse
from collections import deque
import hmac
import hashlib
import ipaddress
import json
import math
import mimetypes
import os
import queue
import re
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen

from core_release import CoreReleaseError, verify_release
from agent_session_gateway import (
    PERMISSION_DECISION_SCHEMA,
    PERMISSION_REQUEST_SCHEMA,
    AgentSessionError,
    normalize_permission_request,
)
from host_profile import HostProfileError, HostProfileStore
from release_runtime import ReleaseRuntime, ReleaseRuntimeError
from universe_dispatch import (
    DispatchError,
    HttpProjectMasterBridge,
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
    build_project_seed_asset_proposal,
    load_project_seed_assets,
    project_seed_template,
)
from project_seed_apply import build_project_seed_asset_approval
from project_skill_plan_apply import (
    ProjectSkillPlanApplyError,
    build_project_skill_plan_approval,
)
from project_release_apply import (
    ProjectReleaseApplyError,
    apply_project_release_proposal,
    build_project_release_approval,
    plan_project_release_lifecycle,
)
from project_master_host import (
    ProjectMasterHostError,
    ResidentModeSessionHost,
    ResidentProjectMasterHostManager,
)
from seed import DEFAULT_DATABASE as OFFICIAL_SEED_DATABASE
from seed import SeedError, suggest_paths
from universe_memory import (
    MEMORY_SCHEMA,
    MemoryError,
    filter_llm_proposals,
    merge_proposals,
    normalize_memory_create,
    normalize_memory_link,
    normalize_memory_maintain,
    propose_node_links,
    propose_node_links_heuristic,
    select_best_proposals,
)
from universe_runtime_host import (
    RuntimeHostError,
    UniverseRuntimeHost,
    redacted_invocation_record,
)
from universe_conductor_runtime import (
    UniverseConductorRuntime,
    UniverseConductorRuntimeError,
)

API_SCHEMA = "universe.local-service.v1"
UNIVERSE_IDENTITY_SCHEMA = "universe.identity.v1"
UNIVERSE_MODE_CONTRACT_SCHEMA = "universe.mode-contract.v1"
PROJECT_SCHEMA = "universe.project-connection.v1"
EVENT_SCHEMA = "universe.project-event.v1"
TODO_SCHEMA = "universe.todo.v1"
PROJECT_SEED_SCHEMA = "universe.project-seed.v1"
PROJECT_SEED_ASSET_PROPOSAL_SCHEMA = "universe.project-seed-asset-proposal.v1"
PROJECT_PROJECTION_SCHEMA = "universe.project-projection.v1"
PROJECT_DISCOVERY_DISPATCH_SCHEMA = "universe.project-discovery-dispatch.v1"
DOCUMENT_PROPOSAL_SCHEMA = "universe.document-incorporation-proposal.v1"
RELEASE_ARTIFACT_SCHEMA = "universe.release-artifact.v1"
RELEASE_PROPOSAL_SCHEMA = "universe.project-release-proposal.v1"
CONNECTION_PROFILE_SCHEMA = "universe.connection-profile.v1"
AUTH_PROFILE_SCHEMA = "universe.auth-profile.v1"
INTERFACE_PROFILE_SCHEMA = "universe.interface-profile.v1"
CAPABILITY_PROFILE_SCHEMA = "universe.connection-capabilities.v1"
PROJECT_ROOM_MESSAGE_SCHEMA = "universe.project-room-message.v1"
CONDUCTOR_ROOM_MESSAGE_SCHEMA = "universe.conductor-room-message.v1"
CONDUCTOR_ROOM_UI_ACTION_SCHEMA = "universe.conductor-room-ui-action.v1"
CONDUCTOR_ROOM_DELIVERY_STATES = frozenset(
    {
        "QUEUED",
        "WAITING_FOR_RUNTIME_BINDING",
        "PROCESSING",
        "ANSWERED",
        "FAILED",
    }
)
CONDUCTOR_ROOM_PROVIDERS = frozenset({"AUTO", "GROK", "CODEX"})
PROVIDER_SETTING_SCHEMA = "universe.cli-provider-setting.v1"
PROVIDER_SETTING_CHOICES = frozenset({"AUTO", "GROK", "CODEX"})
PROVIDER_SETTING_SCOPES = frozenset({"UNIVERSE_CONDUCTOR", "PROJECT_MASTER"})
PROJECT_MASTER_BRIDGE_SCHEMA = "universe.project-master-bridge.v1"
PROJECT_MASTER_BRIDGE_REPLY_SCHEMA = "universe.project-master-bridge-reply.v1"
PROJECT_MASTER_STREAM_SCHEMA = "universe.project-master-stream-event.v1"
PROJECT_ROOM_STREAM_SCHEMA = "universe.project-room-stream.v1"
SKILL_OBSERVATION_CANDIDATE_SCHEMA = "ai-career.skill-observation-candidate.v1"
SKILL_OBSERVATION_PUBLICATION_APPROVAL_SCHEMA = (
    "universe.skill-observation-publication-approval.v1"
)
SKILL_RUN_OBSERVATION_SCHEMA = "universe.skill-run-observation.v1"
SKILL_OBSERVATION_QUEUE_SCHEMA = "universe.skill-observation-queue-item.v1"
SKILL_BENCH_SCHEMA = "universe.skill-bench.v1"
PROJECT_CONTEXT_PACK_SCHEMA = "universe.project-context-pack.v1"
PROJECT_SKILL_PLAN_SCHEMA = "universe.project-skill-plan.v1"
PROJECT_SKILL_PLAN_ADOPTION_SCHEMA = "universe.project-skill-plan-adoption.v1"
FRESH_PROJECT_COMPOSITION_SCHEMA = "universe.fresh-project-composition.v1"
FRESH_PROJECT_COMPOSITION_ADOPTION_SCHEMA = (
    "universe.fresh-project-composition-adoption.v1"
)
FRESH_PROJECT_REFINEMENT_REQUEST_SCHEMA = "universe.fresh-project-refinement-request.v1"
FRESH_PROJECT_REFINEMENT_CANDIDATE_SCHEMA = (
    "universe.fresh-project-refinement-candidate.v1"
)
FRESH_PROJECT_REFINEMENT_ADOPTION_SCHEMA = (
    "universe.fresh-project-refinement-adoption.v1"
)
FRESH_PROJECT_REFINEMENT_WORKER_OUTPUT_SCHEMA = (
    "universe.fresh-project-refinement-worker-output.v1"
)
FRESH_PROJECT_REFINEMENT_RUN_SCHEMA = "universe.fresh-project-refinement-run.v1"
PLANNING_RUNTIME_BINDING_SCHEMA = "universe.planning-runtime-binding.v1"
PROJECT_MASTER_HANDOFF_SCHEMA = "universe.project-master-handoff.v1"
PROJECT_SKILL_PLAN_MASTER_APPLICATION_SCHEMA = (
    "universe.project-skill-plan-master-application.v1"
)
PROJECT_ARCHIVE_RECEIPT_CANDIDATE_SCHEMA = (
    "universe.project-archive-receipt-candidate.v1"
)
EXPERIENCE_CASE_SCHEMA = "universe.experience-case.v1"
EXPERIENCE_PATTERN_PROPOSAL_SCHEMA = "universe.experience-pattern-proposal.v1"
CAREER_PROMOTION_CANDIDATE_SCHEMA = "universe.career-promotion-candidate.v1"
CAREER_PROMOTION_QUEUE_SCHEMA = "universe.career-promotion-queue-item.v1"
RUNTIME_WORKER_INVOCATION_SCHEMA = "universe.runtime-worker-invocation.v1"
MAX_BODY_BYTES = 1024 * 1024
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
CONNECTION_KINDS = frozenset({"LOCAL", "REMOTE", "PEER"})
TRANSPORT_KINDS = frozenset({"HTTP", "GIT", "P2P"})
INTERFACE_KINDS = frozenset({"HTTP_API", "MCP", "CLI"})
ADAPTER_DIRECTIONS = frozenset({"INBOUND", "OUTBOUND"})
AUTH_TYPES = frozenset({"NONE", "LOCAL_TOKEN", "OAUTH2", "PEER_KEY"})
SKILL_OPERATION_CLASSES = frozenset({"READ", "PROPOSE", "EXECUTE"})
SKILL_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "UNKNOWN"})
SKILL_VALIDATION_STATES = frozenset({"PASS", "FAIL", "NOT_RUN", "UNKNOWN"})
TODO_SCOPE_KINDS = frozenset({"UNIVERSE", "PROJECT", "NODE"})
TODO_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
TODO_STATES = frozenset(
    {"BACKLOG", "READY", "IN_PROGRESS", "BLOCKED", "DONE"}
)
TODO_SOURCE_KINDS = frozenset({"USER", "CONDUCTOR", "MASTER"})
FRESH_PROJECT_REFINEMENT_PROVIDERS = frozenset({"GROK", "CODEX"})
SKILL_METRIC_KEYS = frozenset(
    {"duration_ms", "input_tokens", "output_tokens", "cost_units"}
)
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
UNIVERSE_MODE = "CONDUCTOR"
UNIVERSE_ALIAS_MODE = "UNIVERSE"
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


DEFAULT_MODE_REGISTRY_PATH = default_mode_registry_path()
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


class LoopbackNoAuthProvider:
    auth_type = "NONE"

    def headers(self) -> dict[str, str]:
        return {}


class LocalTokenAuthProvider:
    auth_type = "LOCAL_TOKEN"

    def __init__(self, token: str):
        self._token = _required_text(token, "token")

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def default_data_dir() -> Path:
    portable = os.environ.get("UNIVERSE_DATA_DIR")
    if portable:
        return Path(portable).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def default_database_path() -> Path:
    override = os.environ.get("UNIVERSE_DATABASE")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "universe.sqlite3"


def default_state_path() -> Path:
    override = os.environ.get("UNIVERSE_STATE_FILE")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "server.json"


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UniverseError("REQUEST_INVALID", f"{field} must be non-empty text")
    return value.strip()


def provider_ref_from_model_ref(model_ref: Any) -> str:
    """Derive an explicit provider dimension from a canonical model reference."""

    normalized = _required_text(model_ref, "model_ref")
    match = re.fullmatch(
        r"provider://([A-Za-z0-9][A-Za-z0-9._-]{0,127})/model/.+",
        normalized,
    )
    return match.group(1).upper() if match else "UNKNOWN"


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
        UNIVERSE_ALIAS_MODE: {
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


def universe_mode_contract(registry: dict[str, Any]) -> dict[str, Any]:
    definition = registry["modes"][UNIVERSE_MODE]
    return {
        "schema": UNIVERSE_MODE_CONTRACT_SCHEMA,
        "status": "ACTIVE",
        "mode": UNIVERSE_MODE,
        "role": definition["role"],
        "scope": definition["scope"],
        "mode_profile": definition["mode_profile"],
        "registry_revision": registry.get("revision", "UNKNOWN"),
    }


def unknown_universe_mode_contract() -> dict[str, Any]:
    return {
        "schema": UNIVERSE_MODE_CONTRACT_SCHEMA,
        "status": "UNKNOWN",
        "mode": UNIVERSE_MODE,
        "role": UNIVERSE_ROLE,
        "scope": UNIVERSE_SCOPE,
        "mode_profile": UNIVERSE_MODE_PROFILE,
        "registry_revision": "UNKNOWN",
    }


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
    normalized_transport = _required_text(transport_kind, "transport_kind").upper()
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
        auth_type="NONE",
        credential_ref="NONE",
        capabilities=ConnectionCapabilities(
            read=True,
            append=True,
            realtime=True,
            bidirectional=True,
            durable=True,
        ),
    )


def _loopback_endpoint_reachable(endpoint: str) -> bool:
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError:
        return False
    if parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or port is None:
        return False
    try:
        with socket.create_connection((parsed.hostname, port), timeout=0.2):
            return True
    except OSError:
        return False


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
    if profile.auth.auth_type == "NONE":
        return LoopbackNoAuthProvider()
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


def normalize_todo(value: Any, *, updating: bool = False) -> dict[str, Any]:
    required = {
        "scope_kind",
        "title",
        "detail",
        "priority",
        "state",
        "source_kind",
        "sort_order",
    }
    if updating:
        required.add("revision")
    request = _exact_object_fields(
        value,
        field="todo",
        required=frozenset(required),
        optional=frozenset({"todo_id", "project_id", "node_ref"}),
    )
    scope_kind = _required_text(request["scope_kind"], "scope_kind").upper()
    if scope_kind not in TODO_SCOPE_KINDS:
        raise UniverseError(
            "TODO_SCOPE_INVALID",
            "scope_kind must be UNIVERSE, PROJECT, or NODE",
        )
    title = _required_text(request["title"], "title")
    if len(title) > 160:
        raise UniverseError("TODO_TITLE_INVALID", "title is too long")
    detail = request["detail"]
    if not isinstance(detail, str) or len(detail) > 4000:
        raise UniverseError(
            "TODO_DETAIL_INVALID",
            "detail must be a string no longer than 4000 characters",
        )
    priority = _required_text(request["priority"], "priority").upper()
    if priority not in TODO_PRIORITIES:
        raise UniverseError(
            "TODO_PRIORITY_INVALID",
            "priority must be P0, P1, P2, or P3",
        )
    state = _required_text(request["state"], "state").upper()
    if state not in TODO_STATES:
        raise UniverseError(
            "TODO_STATE_INVALID",
            "state must be BACKLOG, READY, IN_PROGRESS, BLOCKED, or DONE",
        )
    source_kind = _required_text(request["source_kind"], "source_kind").upper()
    if source_kind not in TODO_SOURCE_KINDS:
        raise UniverseError(
            "TODO_SOURCE_INVALID",
            "source_kind must be USER, CONDUCTOR, or MASTER",
        )
    sort_order = request["sort_order"]
    if isinstance(sort_order, bool) or not isinstance(sort_order, int):
        raise UniverseError("TODO_SORT_ORDER_INVALID", "sort_order must be an integer")

    project_id = request.get("project_id")
    node_ref = request.get("node_ref")
    if scope_kind == "UNIVERSE":
        if project_id is not None or node_ref is not None:
            raise UniverseError(
                "TODO_SCOPE_COORDINATE_INVALID",
                "UNIVERSE Todo cannot bind a project or node",
            )
        normalized_project = None
        normalized_node = None
    elif scope_kind == "PROJECT":
        normalized_project = _project_id(project_id)
        if node_ref is not None:
            raise UniverseError(
                "TODO_SCOPE_COORDINATE_INVALID",
                "PROJECT Todo cannot bind a node",
            )
        normalized_node = None
    else:
        normalized_project = _project_id(project_id)
        normalized_node = _identifier(node_ref, "node_ref")

    normalized = {
        "scope_kind": scope_kind,
        "project_id": normalized_project,
        "node_ref": normalized_node,
        "title": title,
        "detail": detail,
        "priority": priority,
        "state": state,
        "source_kind": source_kind,
        "sort_order": sort_order,
    }
    if "todo_id" in request:
        normalized["todo_id"] = _identifier(request["todo_id"], "todo_id")
    if updating:
        revision = request["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise UniverseError(
                "TODO_REVISION_INVALID",
                "revision must be a positive integer",
            )
        normalized["revision"] = revision
    return normalized


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


def _observed_at(value: Any, field: str) -> str:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise UniverseError(
            "OBSERVATION_TIME_INVALID",
            f"{field} must be an ISO-8601 timestamp",
        ) from error
    if parsed.tzinfo is None:
        raise UniverseError(
            "OBSERVATION_TIME_INVALID",
            f"{field} must include a timezone",
        )
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _skill_metrics(value: Any, field: str) -> dict[str, int | float]:
    metrics = _exact_object_fields(
        value,
        field=field,
        required=frozenset(),
        optional=SKILL_METRIC_KEYS,
    )
    normalized: dict[str, int | float] = {}
    for key, metric in metrics.items():
        if isinstance(metric, bool) or not isinstance(metric, (int, float)):
            raise UniverseError(
                "SKILL_METRICS_INVALID",
                f"{field}.{key} must be a non-negative number",
            )
        if not math.isfinite(metric) or metric < 0:
            raise UniverseError(
                "SKILL_METRICS_INVALID",
                f"{field}.{key} must be a non-negative number",
            )
        normalized[key] = metric
    return normalized


def normalize_skill_observation_candidate(
    project_id: str, value: Any
) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="skill_observation_request",
        required=frozenset({"candidate_id", "candidate"}),
    )
    candidate = _exact_object_fields(
        request["candidate"],
        field="skill_observation_candidate",
        required=frozenset(
            {
                "schema",
                "project_ref",
                "task_frame_ref",
                "source_ref",
                "observations",
                "observed_at",
                "target_ref",
                "redaction_state",
            }
        ),
    )
    normalized_project = _project_id(project_id)
    if candidate["schema"] != SKILL_OBSERVATION_CANDIDATE_SCHEMA:
        raise UniverseError(
            "SKILL_OBSERVATION_SCHEMA_INVALID",
            "candidate schema must be the ai-career redacted Skill observation schema",
        )
    if candidate["redaction_state"] != "REDACTED":
        raise UniverseError(
            "SKILL_OBSERVATION_REDACTION_REQUIRED",
            "Universe accepts only REDACTED Skill observation candidates",
        )
    expected_project_ref = f"project://{normalized_project}"
    if candidate["project_ref"] != expected_project_ref:
        raise UniverseError(
            "SKILL_OBSERVATION_PROJECT_MISMATCH",
            "candidate project_ref must match the attached project",
            HTTPStatus.CONFLICT,
        )
    observations = _array(candidate["observations"], "candidate.observations")
    if not observations or len(observations) > 256:
        raise UniverseError(
            "SKILL_OBSERVATION_COUNT_INVALID",
            "candidate.observations must contain 1..256 observations",
        )
    normalized_observations: list[dict[str, Any]] = []
    observation_digests: set[str] = set()
    for index, observation_value in enumerate(observations):
        field = f"candidate.observations[{index}]"
        observation = _exact_object_fields(
            observation_value,
            field=field,
            required=frozenset(
                {
                    "observation_digest",
                    "skill_binding_digest",
                    "skill",
                    "model_ref",
                    "outcome",
                    "validation_state",
                    "evidence_refs",
                    "metrics",
                }
            ),
        )
        skill = _exact_object_fields(
            observation["skill"],
            field=f"{field}.skill",
            required=frozenset(
                {"skill_id", "skill_version", "operation_class", "context_pack_digest"}
            ),
        )
        operation_class = _required_text(
            skill["operation_class"], f"{field}.skill.operation_class"
        ).upper()
        if operation_class not in SKILL_OPERATION_CLASSES:
            raise UniverseError(
                "SKILL_OPERATION_CLASS_INVALID",
                f"{field}.skill.operation_class is unsupported",
            )
        outcome = _required_text(observation["outcome"], f"{field}.outcome").upper()
        if outcome not in SKILL_OUTCOMES:
            raise UniverseError(
                "SKILL_OUTCOME_INVALID", f"{field}.outcome is unsupported"
            )
        validation_state = _required_text(
            observation["validation_state"], f"{field}.validation_state"
        ).upper()
        if validation_state not in SKILL_VALIDATION_STATES:
            raise UniverseError(
                "SKILL_VALIDATION_STATE_INVALID",
                f"{field}.validation_state is unsupported",
            )
        observation_digest = _sha256(
            observation["observation_digest"], f"{field}.observation_digest"
        )
        if observation_digest in observation_digests:
            raise UniverseError(
                "SKILL_OBSERVATION_DUPLICATE",
                "candidate.observations must not repeat observation_digest",
            )
        observation_digests.add(observation_digest)
        normalized_observations.append(
            {
                "observation_digest": observation_digest,
                "skill_binding_digest": _sha256(
                    observation["skill_binding_digest"], f"{field}.skill_binding_digest"
                ),
                "skill": {
                    "skill_id": _identifier(
                        skill["skill_id"], f"{field}.skill.skill_id"
                    ),
                    "skill_version": _required_text(
                        skill["skill_version"], f"{field}.skill.skill_version"
                    ),
                    "operation_class": operation_class,
                    "context_pack_digest": _sha256(
                        skill["context_pack_digest"],
                        f"{field}.skill.context_pack_digest",
                    ),
                },
                "model_ref": _required_text(
                    observation["model_ref"], f"{field}.model_ref"
                ),
                "outcome": outcome,
                "validation_state": validation_state,
                "evidence_refs": _string_array(
                    observation["evidence_refs"], f"{field}.evidence_refs"
                ),
                "metrics": _skill_metrics(observation["metrics"], f"{field}.metrics"),
            }
        )
    normalized_candidate = {
        "schema": SKILL_OBSERVATION_CANDIDATE_SCHEMA,
        "project_ref": expected_project_ref,
        "task_frame_ref": _required_text(
            candidate["task_frame_ref"], "candidate.task_frame_ref"
        ),
        "source_ref": _required_text(candidate["source_ref"], "candidate.source_ref"),
        "observations": normalized_observations,
        "observed_at": _observed_at(candidate["observed_at"], "candidate.observed_at"),
        "target_ref": _required_text(candidate["target_ref"], "candidate.target_ref"),
        "redaction_state": "REDACTED",
    }
    return {
        "candidate_id": _identifier(request["candidate_id"], "candidate_id"),
        "candidate": normalized_candidate,
        "candidate_digest": _json_sha256(normalized_candidate),
    }


def normalize_skill_observation_publication(
    project_id: str, value: Any
) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="skill_observation_publication",
        required=frozenset({"candidate_id", "candidate", "publication_approval"}),
    )
    normalized = normalize_skill_observation_candidate(
        project_id,
        {
            "candidate_id": request["candidate_id"],
            "candidate": request["candidate"],
        },
    )
    approval = _exact_object_fields(
        request["publication_approval"],
        field="publication_approval",
        required=frozenset(
            {
                "schema",
                "status",
                "operation_class",
                "project_ref",
                "candidate_id",
                "candidate_digest",
                "selection_ref",
                "approver",
                "evidence_ref",
            }
        ),
    )
    expected_project_ref = f"project://{_project_id(project_id)}"
    expected = {
        "schema": SKILL_OBSERVATION_PUBLICATION_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "operation_class": "UNIVERSE_OBSERVATION_QUEUE",
        "project_ref": expected_project_ref,
        "candidate_id": normalized["candidate_id"],
        "candidate_digest": normalized["candidate_digest"],
        "approver": "PROJECT_MASTER",
    }
    mismatch = next(
        (
            field
            for field, expected_value in expected.items()
            if approval.get(field) != expected_value
        ),
        None,
    )
    if mismatch is not None:
        raise UniverseError(
            "SKILL_OBSERVATION_PUBLICATION_APPROVAL_MISMATCH",
            f"publication_approval.{mismatch} does not match the prepared candidate",
            HTTPStatus.CONFLICT,
        )
    normalized_approval = {
        **expected,
        "selection_ref": _required_text(
            approval["selection_ref"], "publication_approval.selection_ref"
        ),
        "evidence_ref": _required_text(
            approval["evidence_ref"], "publication_approval.evidence_ref"
        ),
    }
    return {
        **normalized,
        "publication_approval": normalized_approval,
        "publication_approval_digest": _json_sha256(normalized_approval),
    }


def normalize_fresh_project_intent(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="fresh_project_intent",
        required=frozenset({"project", "kind", "technologies", "goal"}),
        optional=frozenset({"limit"}),
    )
    technologies = _string_array(request["technologies"], "technologies")
    if len(technologies) > 64:
        raise UniverseError(
            "FRESH_PROJECT_TECHNOLOGIES_INVALID",
            "technologies must contain at most 64 entries",
        )
    limit = request.get("limit", 3)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise UniverseError(
            "FRESH_PROJECT_LIMIT_INVALID",
            "limit must be an integer from 1 through 10",
        )
    return {
        "project": _required_text(request["project"], "project"),
        "kind": _required_text(request["kind"], "kind"),
        "technologies": technologies,
        "goal": _required_text(request["goal"], "goal"),
        "limit": limit,
    }


def normalize_fresh_project_composition_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="fresh_project_composition_request",
        required=frozenset({"intent", "route_id"}),
    )
    intent = _exact_object_fields(
        request["intent"],
        field="fresh_project_composition_request.intent",
        required=frozenset({"project", "kind", "technologies", "goal"}),
        optional=frozenset({"constraints", "target_users"}),
    )
    normalized_intent = normalize_fresh_project_intent(
        {
            "project": intent["project"],
            "kind": intent["kind"],
            "technologies": intent["technologies"],
            "goal": intent["goal"],
        }
    )
    constraints = _string_array(intent.get("constraints", []), "intent.constraints")
    if len(constraints) > 32:
        raise UniverseError(
            "FRESH_PROJECT_CONSTRAINTS_INVALID",
            "intent.constraints must contain at most 32 entries",
        )
    normalized_intent["constraints"] = constraints
    if "target_users" in intent:
        normalized_intent["target_users"] = _required_text(
            intent["target_users"], "intent.target_users"
        )
    return {
        "intent": normalized_intent,
        "route_id": _identifier(request["route_id"], "route_id"),
    }


def normalize_fresh_project_composition_adoption_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="fresh_project_composition_adoption_request",
        required=frozenset({"composition_id", "approval"}),
        optional=frozenset({"user_notes"}),
    )
    if request["approval"] != "ADOPTED":
        raise UniverseError(
            "FRESH_PROJECT_COMPOSITION_ADOPTION_REQUIRED",
            "approval must be ADOPTED before recording a Composition selection",
            HTTPStatus.CONFLICT,
        )
    normalized = {
        "composition_id": _identifier(request["composition_id"], "composition_id")
    }
    if "user_notes" in request:
        normalized["user_notes"] = _required_text(request["user_notes"], "user_notes")
    return normalized


def normalize_fresh_project_refinement_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="fresh_project_refinement_request",
        required=frozenset({"composition_id"}),
        optional=frozenset({"purpose"}),
    )
    purpose = _required_text(
        request.get("purpose", "SPECIFICATION_AND_DESIGN"),
        "purpose",
    ).upper()
    if purpose != "SPECIFICATION_AND_DESIGN":
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_PURPOSE_INVALID",
            "purpose must be SPECIFICATION_AND_DESIGN",
        )
    return {
        "composition_id": _identifier(request["composition_id"], "composition_id"),
        "purpose": purpose,
    }


def _normalize_fresh_project_refinement_document(
    value: Any, field: str
) -> dict[str, str]:
    document = _exact_object_fields(
        value,
        field=field,
        required=frozenset({"document_id", "role", "title"}),
    )
    role = _required_text(document["role"], f"{field}.role").upper()
    if role not in DOCUMENT_ROLES:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_DOCUMENT_ROLE_INVALID",
            f"{field}.role must be a supported document role",
        )
    return {
        "document_id": _identifier(document["document_id"], f"{field}.document_id"),
        "role": role,
        "title": _required_text(document["title"], f"{field}.title"),
    }


def _normalize_fresh_project_refinement(value: Any, field: str) -> dict[str, Any]:
    refinement = _exact_object_fields(
        value,
        field=field,
        required=frozenset(
            {
                "problem_statement",
                "target_users",
                "constraints",
                "design_direction",
                "technology_recommendations",
                "document_additions",
                "risk_additions",
            }
        ),
    )
    constraints = _string_array(refinement["constraints"], f"{field}.constraints")
    risk_additions = _string_array(
        refinement["risk_additions"], f"{field}.risk_additions"
    )
    if len(constraints) > 32 or len(risk_additions) > 32:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_LIST_LIMIT_EXCEEDED",
            "constraints and risk_additions must contain at most 32 entries",
        )
    recommendations = []
    for index, item in enumerate(
        _array(
            refinement["technology_recommendations"],
            f"{field}.technology_recommendations",
        )
    ):
        recommendation = _exact_object_fields(
            item,
            field=f"{field}.technology_recommendations[{index}]",
            required=frozenset({"technology", "rationale"}),
        )
        recommendations.append(
            {
                "technology": _required_text(
                    recommendation["technology"],
                    f"{field}.technology_recommendations[].technology",
                ),
                "rationale": _required_text(
                    recommendation["rationale"],
                    f"{field}.technology_recommendations[].rationale",
                ),
            }
        )
    if len(recommendations) > 32:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_LIST_LIMIT_EXCEEDED",
            "technology_recommendations must contain at most 32 entries",
        )
    if len({item["technology"] for item in recommendations}) != len(recommendations):
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_DUPLICATE_TECHNOLOGY",
            "technology_recommendations must not repeat a technology",
        )
    document_additions = [
        _normalize_fresh_project_refinement_document(
            item,
            f"{field}.document_additions[{index}]",
        )
        for index, item in enumerate(
            _array(refinement["document_additions"], f"{field}.document_additions")
        )
    ]
    if len(document_additions) > 32:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_LIST_LIMIT_EXCEEDED",
            "document_additions must contain at most 32 entries",
        )
    if len({item["document_id"] for item in document_additions}) != len(
        document_additions
    ):
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_DUPLICATE_DOCUMENT",
            "document_additions must not repeat a document_id",
        )
    return {
        "problem_statement": _required_text(
            refinement["problem_statement"], f"{field}.problem_statement"
        ),
        "target_users": _required_text(
            refinement["target_users"], f"{field}.target_users"
        ),
        "constraints": constraints,
        "design_direction": _required_text(
            refinement["design_direction"], f"{field}.design_direction"
        ),
        "technology_recommendations": recommendations,
        "document_additions": document_additions,
        "risk_additions": risk_additions,
    }


def normalize_fresh_project_refinement_candidate(value: Any) -> dict[str, Any]:
    candidate = _exact_object_fields(
        value,
        field="fresh_project_refinement_candidate",
        required=frozenset(
            {
                "schema",
                "request_id",
                "request_digest",
                "composition_id",
                "composition_digest",
                "producer",
                "refinement",
            }
        ),
    )
    if candidate["schema"] != FRESH_PROJECT_REFINEMENT_CANDIDATE_SCHEMA:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_SCHEMA_INVALID",
            "candidate schema is unsupported",
        )
    producer = _exact_object_fields(
        candidate["producer"],
        field="fresh_project_refinement_candidate.producer",
        required=frozenset(
            {"provider", "model_ref", "worker_id", "result_receipt_ref"}
        ),
    )
    provider = _required_text(
        producer["provider"], "fresh_project_refinement_candidate.producer.provider"
    ).upper()
    if provider not in FRESH_PROJECT_REFINEMENT_PROVIDERS:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_PROVIDER_INVALID",
            "producer.provider must be a supported Runtime Host provider",
        )
    refinement = _normalize_fresh_project_refinement(
        candidate["refinement"],
        "fresh_project_refinement_candidate.refinement",
    )
    return {
        "schema": FRESH_PROJECT_REFINEMENT_CANDIDATE_SCHEMA,
        "request_id": _identifier(candidate["request_id"], "request_id"),
        "request_digest": _required_text(candidate["request_digest"], "request_digest"),
        "composition_id": _identifier(candidate["composition_id"], "composition_id"),
        "composition_digest": _required_text(
            candidate["composition_digest"], "composition_digest"
        ),
        "producer": {
            "provider": provider,
            "model_ref": _required_text(producer["model_ref"], "producer.model_ref"),
            "worker_id": _required_text(producer["worker_id"], "producer.worker_id"),
            "result_receipt_ref": _required_text(
                producer["result_receipt_ref"], "producer.result_receipt_ref"
            ),
        },
        "refinement": refinement,
    }


def normalize_fresh_project_refinement_worker_output(value: Any) -> dict[str, Any]:
    output = _exact_object_fields(
        value,
        field="fresh_project_refinement_worker_output",
        required=frozenset({"schema", "refinement"}),
    )
    if output["schema"] != FRESH_PROJECT_REFINEMENT_WORKER_OUTPUT_SCHEMA:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_WORKER_SCHEMA_INVALID",
            "Planning Worker output schema is unsupported",
        )
    return {
        "schema": FRESH_PROJECT_REFINEMENT_WORKER_OUTPUT_SCHEMA,
        "refinement": _normalize_fresh_project_refinement(
            output["refinement"],
            "fresh_project_refinement_worker_output.refinement",
        ),
    }


def normalize_planning_runtime_binding(value: Any) -> dict[str, Any]:
    binding = _exact_object_fields(
        value,
        field="planning_runtime_binding",
        required=frozenset(
            {
                "schema",
                "endpoint",
                "token",
                "session_id",
                "origin_anchor_ref",
                "origin_frame_id",
                "parent_actor_ref",
                "parent_evidence_ref",
                "binding_evidence_ref",
            }
        ),
    )
    if binding["schema"] != PLANNING_RUNTIME_BINDING_SCHEMA:
        raise UniverseError(
            "PLANNING_RUNTIME_BINDING_SCHEMA_INVALID",
            "Planning Runtime binding schema is unsupported",
        )
    endpoint = _required_text(binding["endpoint"], "planning_runtime_binding.endpoint")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UniverseError(
            "PLANNING_RUNTIME_ENDPOINT_INVALID",
            "Planning Runtime endpoint must be loopback HTTP",
        )
    return {
        "schema": PLANNING_RUNTIME_BINDING_SCHEMA,
        "endpoint": endpoint.rstrip("/"),
        "token": _required_text(binding["token"], "planning_runtime_binding.token"),
        "session_id": _required_text(
            binding["session_id"], "planning_runtime_binding.session_id"
        ),
        "origin_anchor_ref": _required_text(
            binding["origin_anchor_ref"],
            "planning_runtime_binding.origin_anchor_ref",
        ),
        "origin_frame_id": _required_text(
            binding["origin_frame_id"],
            "planning_runtime_binding.origin_frame_id",
        ),
        "parent_actor_ref": _required_text(
            binding["parent_actor_ref"],
            "planning_runtime_binding.parent_actor_ref",
        ),
        "parent_evidence_ref": _required_text(
            binding["parent_evidence_ref"],
            "planning_runtime_binding.parent_evidence_ref",
        ),
        "binding_evidence_ref": _required_text(
            binding["binding_evidence_ref"],
            "planning_runtime_binding.binding_evidence_ref",
        ),
    }


def normalize_fresh_project_refinement_run_request(value: Any) -> dict[str, str]:
    request = _exact_object_fields(
        value,
        field="fresh_project_refinement_run_request",
        required=frozenset({"request_id", "provider"}),
    )
    provider = _required_text(
        request["provider"], "fresh_project_refinement_run_request.provider"
    ).upper()
    if provider not in FRESH_PROJECT_REFINEMENT_PROVIDERS:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_PROVIDER_INVALID",
            "provider must be GROK or CODEX",
        )
    return {
        "request_id": _identifier(request["request_id"], "request_id"),
        "provider": provider,
    }


def normalize_fresh_project_refinement_run_approval(
    value: Any,
) -> dict[str, str]:
    approval = _exact_object_fields(
        value,
        field="fresh_project_refinement_run_approval",
        required=frozenset({"approval", "proposal_id", "plan_digest"}),
    )
    if approval["approval"] != "APPROVED":
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_RUN_APPROVAL_REQUIRED",
            "approval must be APPROVED before provider execution",
            HTTPStatus.CONFLICT,
        )
    return {
        "approval": "APPROVED",
        "proposal_id": _identifier(approval["proposal_id"], "proposal_id"),
        "plan_digest": _required_text(approval["plan_digest"], "plan_digest"),
    }


def normalize_fresh_project_refinement_adoption_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="fresh_project_refinement_adoption_request",
        required=frozenset({"candidate_id", "approval"}),
        optional=frozenset({"user_notes"}),
    )
    if request["approval"] != "ADOPTED":
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_ADOPTION_REQUIRED",
            "approval must be ADOPTED before recording a refinement selection",
            HTTPStatus.CONFLICT,
        )
    normalized = {"candidate_id": _identifier(request["candidate_id"], "candidate_id")}
    if "user_notes" in request:
        normalized["user_notes"] = _required_text(request["user_notes"], "user_notes")
    return normalized


def normalize_project_seed_asset_apply_request(value: Any) -> dict[str, str]:
    request = _exact_object_fields(
        value,
        field="project_seed_asset_apply_request",
        required=frozenset({"approval", "proposal_id", "proposal_digest"}),
    )
    if request["approval"] != "APPROVED":
        raise UniverseError(
            "PROJECT_SEED_ASSET_APPROVAL_REQUIRED",
            "approval must be APPROVED before Project Host application",
            HTTPStatus.CONFLICT,
        )
    return {
        "approval": "APPROVED",
        "proposal_id": _identifier(request["proposal_id"], "proposal_id"),
        "proposal_digest": _sha256(request["proposal_digest"], "proposal_digest"),
    }


def normalize_project_release_apply_request(value: Any) -> dict[str, str]:
    request = _exact_object_fields(
        value,
        field="project_release_apply_request",
        required=frozenset({"approval", "proposal_id", "proposal_digest"}),
    )
    if request["approval"] != "APPROVED":
        raise UniverseError(
            "PROJECT_RELEASE_APPROVAL_REQUIRED",
            "approval must be APPROVED before Project Runtime lifecycle application",
            HTTPStatus.CONFLICT,
        )
    return {
        "approval": "APPROVED",
        "proposal_id": _identifier(request["proposal_id"], "proposal_id"),
        "proposal_digest": _sha256(request["proposal_digest"], "proposal_digest"),
    }


def normalize_master_handoff_proposal_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="master_handoff_proposal_request",
        required=frozenset({"source"}),
        optional=frozenset({"purpose"}),
    )
    source = _exact_object_fields(
        request["source"],
        field="master_handoff_proposal_request.source",
        required=frozenset({"kind", "adoption_id"}),
    )
    kind = _identifier(source["kind"], "source.kind").upper()
    if kind not in {"FRESH_PROJECT_COMPOSITION", "SKILL_PLAN"}:
        raise UniverseError(
            "MASTER_HANDOFF_SOURCE_INVALID",
            "source.kind must be FRESH_PROJECT_COMPOSITION or SKILL_PLAN",
        )
    normalized: dict[str, Any] = {
        "source": {
            "kind": kind,
            "adoption_id": _identifier(source["adoption_id"], "source.adoption_id"),
        }
    }
    if "purpose" in request:
        normalized["purpose"] = _required_text(request["purpose"], "purpose")
    return normalized


def normalize_master_handoff_delivery_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="master_handoff_delivery_request",
        required=frozenset({"approval"}),
    )
    if request["approval"] != "DELIVER":
        raise UniverseError(
            "MASTER_HANDOFF_DELIVERY_APPROVAL_REQUIRED",
            "approval must be DELIVER before sending a handoff to Project Master",
            HTTPStatus.CONFLICT,
        )
    return {"approval": "DELIVER"}


def normalize_experience_case_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="experience_case_request",
        required=frozenset({"observation_ids"}),
        optional=frozenset({"title"}),
    )
    observation_ids = [
        _identifier(item, "observation_ids[]")
        for item in _array(request["observation_ids"], "observation_ids")
    ]
    if (
        not observation_ids
        or len(observation_ids) > 64
        or len(set(observation_ids)) != len(observation_ids)
    ):
        raise UniverseError(
            "EXPERIENCE_CASE_OBSERVATIONS_INVALID",
            "observation_ids must contain 1..64 unique observed records",
        )
    normalized: dict[str, Any] = {"observation_ids": sorted(observation_ids)}
    if "title" in request:
        normalized["title"] = _required_text(request["title"], "title")
    return normalized


def normalize_experience_match_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="experience_match_request",
        required=frozenset({"case_id"}),
        optional=frozenset({"limit"}),
    )
    limit = request.get("limit", 20)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise UniverseError(
            "EXPERIENCE_MATCH_LIMIT_INVALID",
            "limit must be an integer from 1 through 100",
        )
    return {
        "case_id": _identifier(request["case_id"], "case_id"),
        "limit": limit,
    }


def normalize_experience_pattern_proposal_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="experience_pattern_proposal_request",
        required=frozenset({"case_id"}),
        optional=frozenset({"minimum_support"}),
    )
    minimum_support = request.get("minimum_support", 2)
    if (
        isinstance(minimum_support, bool)
        or not isinstance(minimum_support, int)
        or not 2 <= minimum_support <= 100
    ):
        raise UniverseError(
            "EXPERIENCE_PATTERN_SUPPORT_INVALID",
            "minimum_support must be an integer from 2 through 100",
        )
    return {
        "case_id": _identifier(request["case_id"], "case_id"),
        "minimum_support": minimum_support,
    }


def normalize_context_pack_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="context_pack_request",
        required=frozenset({"purpose", "node_ids"}),
        optional=frozenset({"bench_limit"}),
    )
    node_ids = [
        _identifier(item, "node_ids[]")
        for item in _array(request["node_ids"], "node_ids")
    ]
    if not node_ids or len(node_ids) > 32 or len(set(node_ids)) != len(node_ids):
        raise UniverseError(
            "CONTEXT_PACK_NODES_INVALID",
            "node_ids must contain 1..32 unique node identifiers",
        )
    bench_limit = request.get("bench_limit", 20)
    if (
        isinstance(bench_limit, bool)
        or not isinstance(bench_limit, int)
        or not 0 <= bench_limit <= 100
    ):
        raise UniverseError(
            "CONTEXT_PACK_BENCH_LIMIT_INVALID",
            "bench_limit must be an integer from 0 through 100",
        )
    return {
        "purpose": _required_text(request["purpose"], "purpose"),
        "node_ids": sorted(node_ids),
        "bench_limit": bench_limit,
    }


def normalize_skill_plan_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="skill_plan_request",
        required=frozenset({"context_pack_id", "purpose"}),
        optional=frozenset({"max_candidates"}),
    )
    max_candidates = request.get("max_candidates", 10)
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not 1 <= max_candidates <= 32
    ):
        raise UniverseError(
            "SKILL_PLAN_LIMIT_INVALID",
            "max_candidates must be an integer from 1 through 32",
        )
    return {
        "context_pack_id": _identifier(request["context_pack_id"], "context_pack_id"),
        "purpose": _required_text(request["purpose"], "purpose"),
        "max_candidates": max_candidates,
    }


def normalize_skill_plan_adoption_request(value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="skill_plan_adoption_request",
        required=frozenset({"proposal_id", "candidate_ids", "approval"}),
    )
    if request["approval"] != "ADOPTED":
        raise UniverseError(
            "SKILL_PLAN_ADOPTION_REQUIRED",
            "approval must be ADOPTED before recording a Skill Plan selection",
            HTTPStatus.CONFLICT,
        )
    candidate_ids = [
        _identifier(item, "candidate_ids[]")
        for item in _array(request["candidate_ids"], "candidate_ids")
    ]
    if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
        raise UniverseError(
            "SKILL_PLAN_CANDIDATES_INVALID",
            "candidate_ids must be a non-empty unique array",
        )
    return {
        "proposal_id": _identifier(request["proposal_id"], "proposal_id"),
        "candidate_ids": sorted(candidate_ids),
    }


def normalize_project_seed(project: dict[str, Any], value: Any) -> dict[str, Any]:
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
            raise UniverseError(
                "PROJECT_SEED_NODE_DUPLICATE", f"duplicate node: {node_id}"
            )
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
            raise UniverseError(
                "PROJECT_SEED_EDGE_DUPLICATE", f"duplicate edge: {edge_id}"
            )
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
        document_id = _identifier(document["document_id"], f"{field}.document_id")
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


def normalize_room_message(project_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UniverseError("REQUEST_INVALID", "room message body must be an object")
    kind = _identifier(value.get("kind", "QUESTION"), "kind").upper()
    if kind not in {"QUESTION", "REVIEW", "STATUS", "TASK_DRAFT", "RESULT"}:
        raise UniverseError(
            "ROOM_MESSAGE_KIND_INVALID", "unsupported room message kind"
        )
    body = _required_text(value.get("body"), "body")
    if len(body) > 12000:
        raise UniverseError("ROOM_MESSAGE_BODY_INVALID", "body is too long")
    sender = _identifier(value.get("sender", "UNIVERSE_CONDUCTOR"), "sender").upper()
    idempotency_key = _required_text(value.get("idempotency_key"), "idempotency_key")
    in_reply_to = value.get("in_reply_to")
    if in_reply_to is not None:
        in_reply_to = _required_text(in_reply_to, "in_reply_to")
        if len(in_reply_to) > 160:
            raise UniverseError("ROOM_MESSAGE_REPLY_INVALID", "in_reply_to is too long")
    material = {
        "project_id": _project_id(project_id),
        "kind": kind,
        "sender": sender,
        "body": body,
    }
    if in_reply_to is not None:
        material["in_reply_to"] = in_reply_to
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    message = {
        "schema": PROJECT_ROOM_MESSAGE_SCHEMA,
        "message_id": "room_" + uuid.uuid4().hex,
        "project_id": material["project_id"],
        "idempotency_key": idempotency_key,
        "kind": kind,
        "sender": sender,
        "body": body,
        "content_digest": digest,
        "delivery_state": "RECORDED",
        "created_at": utc_now(),
    }
    if in_reply_to is not None:
        message["in_reply_to"] = in_reply_to
    return message


def normalize_conductor_ui_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    request = _exact_object_fields(
        value,
        field="ui_context",
        required=frozenset(),
        optional=frozenset(
            {"selected_project_id", "selected_node_ref", "selected_node_label"}
        ),
    )
    project_id = request.get("selected_project_id")
    node_ref = request.get("selected_node_ref")
    node_label = request.get("selected_node_label")
    normalized: dict[str, Any] = {}
    if project_id is not None:
        normalized["selected_project_id"] = _project_id(project_id)
    if node_ref is not None:
        if project_id is None:
            raise UniverseError(
                "CONDUCTOR_UI_CONTEXT_INVALID",
                "selected_node_ref requires selected_project_id",
            )
        normalized["selected_node_ref"] = _identifier(node_ref, "selected_node_ref")
    if node_label is not None:
        if not isinstance(node_label, str) or len(node_label) > 160:
            raise UniverseError(
                "CONDUCTOR_UI_CONTEXT_INVALID",
                "selected_node_label must be a string no longer than 160 characters",
            )
        normalized["selected_node_label"] = node_label
    return normalized


def normalize_conductor_ui_action(value: Any) -> dict[str, Any]:
    fresh_project_fields = frozenset(
        {
            "project",
            "project_kind",
            "goal",
            "target_users",
            "technologies",
            "constraints",
        }
    )
    request = _exact_object_fields(
        value,
        field="conductor_ui_action",
        required=frozenset({"kind"}),
        optional=frozenset({"todo", "intent"}) | fresh_project_fields,
    )
    kind = _required_text(request["kind"], "conductor_ui_action.kind").upper()
    if kind == "NONE":
        if (
            request.get("todo") is not None
            or request.get("intent") is not None
            or fresh_project_fields.intersection(request)
        ):
            raise UniverseError(
                "CONDUCTOR_UI_ACTION_INVALID",
                "NONE action cannot include a UI draft",
            )
        return {"schema": CONDUCTOR_ROOM_UI_ACTION_SCHEMA, "kind": "NONE"}

    if kind == "TODO_DRAFT":
        if (
            request.get("intent") is not None
            or fresh_project_fields.intersection(request)
        ):
            raise UniverseError(
                "CONDUCTOR_UI_ACTION_INVALID",
                "TODO_DRAFT cannot include a Fresh Project intent",
            )
        todo_value = request.get("todo")
        if not isinstance(todo_value, dict):
            raise UniverseError(
                "CONDUCTOR_TODO_DRAFT_INVALID",
                "TODO_DRAFT requires a Todo object",
            )
        draft_request = _exact_object_fields(
            todo_value,
            field="conductor_todo_draft",
            required=frozenset(
                {"scope_kind", "title", "detail", "priority", "state"}
            ),
            optional=frozenset({"project_id", "node_ref"}),
        )
        scope_kind = _required_text(
            draft_request["scope_kind"],
            "conductor_todo_draft.scope_kind",
        ).upper()
        canonical_draft = dict(draft_request)
        if scope_kind == "UNIVERSE":
            canonical_draft.pop("project_id", None)
            canonical_draft.pop("node_ref", None)
        elif scope_kind == "PROJECT":
            canonical_draft.pop("node_ref", None)
        todo = normalize_todo(
            {
                **canonical_draft,
                "source_kind": "CONDUCTOR",
                "sort_order": 0,
            }
        )
        return {
            "schema": CONDUCTOR_ROOM_UI_ACTION_SCHEMA,
            "kind": "TODO_DRAFT",
            "todo": todo,
        }

    if kind != "FRESH_PROJECT_DRAFT":
        raise UniverseError(
            "CONDUCTOR_UI_ACTION_INVALID",
            "action kind must be NONE, TODO_DRAFT, or FRESH_PROJECT_DRAFT",
        )
    if request.get("todo") is not None:
        raise UniverseError(
            "CONDUCTOR_UI_ACTION_INVALID",
            "FRESH_PROJECT_DRAFT cannot include a Todo",
        )
    intent_value = request.get("intent")
    direct_fields = fresh_project_fields.intersection(request)
    if intent_value is not None and direct_fields:
        raise UniverseError(
            "CONDUCTOR_FRESH_PROJECT_DRAFT_INVALID",
            "Fresh Project draft cannot mix nested and direct intent fields",
        )
    if intent_value is None:
        intent_value = {
            "project": request.get("project", ""),
            "kind": request.get("project_kind", ""),
            "goal": request.get("goal", ""),
            "target_users": request.get("target_users", ""),
            "technologies": request.get("technologies", []),
            "constraints": request.get("constraints", []),
        }
    if not isinstance(intent_value, dict):
        raise UniverseError(
            "CONDUCTOR_FRESH_PROJECT_DRAFT_INVALID",
            "FRESH_PROJECT_DRAFT requires an intent object",
        )
    intent_request = _exact_object_fields(
        intent_value,
        field="conductor_fresh_project_draft",
        required=frozenset(
            {
                "project",
                "kind",
                "goal",
                "target_users",
                "technologies",
                "constraints",
            }
        ),
    )

    def draft_text(field: str, limit: int) -> str:
        raw = intent_request[field]
        if not isinstance(raw, str):
            raise UniverseError(
                "CONDUCTOR_FRESH_PROJECT_DRAFT_INVALID",
                f"{field} must be a string",
            )
        normalized = raw.strip()
        if len(normalized) > limit:
            raise UniverseError(
                "CONDUCTOR_FRESH_PROJECT_DRAFT_INVALID",
                f"{field} must be no longer than {limit} characters",
            )
        return normalized

    technologies = _string_array(
        intent_request["technologies"],
        "conductor_fresh_project_draft.technologies",
    )
    constraints = _string_array(
        intent_request["constraints"],
        "conductor_fresh_project_draft.constraints",
    )
    if len(technologies) > 64 or len(constraints) > 32:
        raise UniverseError(
            "CONDUCTOR_FRESH_PROJECT_DRAFT_INVALID",
            "Fresh Project draft contains too many technologies or constraints",
        )
    return {
        "schema": CONDUCTOR_ROOM_UI_ACTION_SCHEMA,
        "kind": "FRESH_PROJECT_DRAFT",
        "intent": {
            "project": draft_text("project", 160),
            "kind": draft_text("kind", 160),
            "goal": draft_text("goal", 4000),
            "target_users": draft_text("target_users", 1000),
            "technologies": technologies,
            "constraints": constraints,
        },
    }


def normalize_conductor_room_message(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UniverseError(
            "REQUEST_INVALID", "conductor room message body must be an object"
        )
    kind = _identifier(value.get("kind", "QUESTION"), "kind").upper()
    if kind not in {"QUESTION", "REVIEW", "STATUS", "TASK_DRAFT", "RESULT"}:
        raise UniverseError(
            "CONDUCTOR_ROOM_MESSAGE_KIND_INVALID",
            "unsupported conductor room message kind",
        )
    body = _required_text(value.get("body"), "body")
    if len(body) > 12000:
        raise UniverseError("CONDUCTOR_ROOM_MESSAGE_BODY_INVALID", "body is too long")
    sender = _identifier(value.get("sender", "USER"), "sender").upper()
    requested_provider = _identifier(value.get("provider", "AUTO"), "provider").upper()
    if requested_provider not in CONDUCTOR_ROOM_PROVIDERS:
        raise UniverseError(
            "CONDUCTOR_ROOM_PROVIDER_INVALID",
            "provider must be AUTO, GROK, or CODEX",
        )
    idempotency_key = _required_text(value.get("idempotency_key"), "idempotency_key")
    ui_context = normalize_conductor_ui_context(value.get("ui_context"))
    in_reply_to = value.get("in_reply_to")
    if in_reply_to is not None:
        in_reply_to = _required_text(in_reply_to, "in_reply_to")
        if len(in_reply_to) > 160:
            raise UniverseError(
                "CONDUCTOR_ROOM_MESSAGE_REPLY_INVALID", "in_reply_to is too long"
            )
    material = {
        "room_id": "UNIVERSE_CONDUCTOR",
        "kind": kind,
        "sender": sender,
        "body": body,
        "requested_provider": requested_provider,
        "ui_context": ui_context,
    }
    if in_reply_to is not None:
        material["in_reply_to"] = in_reply_to
    message = {
        "schema": CONDUCTOR_ROOM_MESSAGE_SCHEMA,
        "message_id": "conductor_" + uuid.uuid4().hex,
        "room_id": material["room_id"],
        "idempotency_key": idempotency_key,
        "kind": kind,
        "sender": sender,
        "body": body,
        "content_digest": hashlib.sha256(
            _canonical_json(material).encode("utf-8")
        ).hexdigest(),
        "requested_provider": requested_provider,
        "ui_context": ui_context,
        "delivery_state": "QUEUED",
        "created_at": utc_now(),
    }
    if in_reply_to is not None:
        message["in_reply_to"] = in_reply_to
    return message


def normalize_master_bridge(project_id: str, value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="master_bridge",
        required=frozenset(
            {
                "endpoint",
                "credential_env",
                "master_session_ref",
                "binding_evidence_ref",
            }
        ),
    )
    try:
        endpoint = HttpProjectMasterBridge(
            endpoint=_required_text(request["endpoint"], "master_bridge.endpoint"),
            credential_env=_required_text(
                request["credential_env"], "master_bridge.credential_env"
            ),
        ).validate()
    except DispatchError as error:
        raise UniverseError("MASTER_BRIDGE_INVALID", str(error)) from error
    normalized_project = _project_id(project_id)
    credential_env = _required_text(
        request["credential_env"], "master_bridge.credential_env"
    )
    master_session_ref = _required_text(
        request["master_session_ref"], "master_bridge.master_session_ref"
    )
    binding_evidence_ref = _required_text(
        request["binding_evidence_ref"], "master_bridge.binding_evidence_ref"
    )
    material = {
        "project_id": normalized_project,
        "endpoint": endpoint,
        "credential_env": credential_env,
        "master_session_ref": master_session_ref,
        "binding_evidence_ref": binding_evidence_ref,
    }
    return {
        "schema": PROJECT_MASTER_BRIDGE_SCHEMA,
        "bridge_id": "bridge_" + _json_sha256(material)[:24],
        **material,
        "status": "REGISTERED",
    }


def normalize_master_bridge_reply(project_id: str, value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="master_bridge_reply",
        required=frozenset(
            {"bridge_id", "in_reply_to", "kind", "body", "idempotency_key"}
        ),
    )
    return {
        "schema": PROJECT_MASTER_BRIDGE_REPLY_SCHEMA,
        "bridge_id": _required_text(
            request["bridge_id"], "master_bridge_reply.bridge_id"
        ),
        "message": normalize_room_message(
            project_id,
            {
                "kind": request["kind"],
                "sender": "PROJECT_MASTER",
                "body": request["body"],
                "in_reply_to": request["in_reply_to"],
                "idempotency_key": request["idempotency_key"],
            },
        ),
    }


def normalize_master_bridge_stream(project_id: str, value: Any) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="master_bridge_stream",
        required=frozenset(
            {
                "bridge_id",
                "in_reply_to",
                "event",
                "sequence",
                "delta",
                "detail",
            }
        ),
    )
    event = _required_text(request["event"], "master_bridge_stream.event").upper()
    if event not in {"STARTED", "DELTA", "COMPLETED", "FAILED"}:
        raise UniverseError(
            "MASTER_STREAM_EVENT_INVALID",
            "unsupported Project Master stream event",
        )
    sequence = request["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise UniverseError(
            "MASTER_STREAM_SEQUENCE_INVALID",
            "Project Master stream sequence must be a non-negative integer",
        )
    delta = request["delta"]
    detail = request["detail"]
    if not isinstance(delta, str) or len(delta.encode("utf-8")) > MAX_BODY_BYTES:
        raise UniverseError(
            "MASTER_STREAM_DELTA_INVALID",
            "Project Master stream delta is invalid",
        )
    if not isinstance(detail, str) or len(detail.encode("utf-8")) > 4000:
        raise UniverseError(
            "MASTER_STREAM_DETAIL_INVALID",
            "Project Master stream detail is invalid",
        )
    return {
        "schema": PROJECT_MASTER_STREAM_SCHEMA,
        "project_id": _project_id(project_id),
        "bridge_id": _required_text(
            request["bridge_id"], "master_bridge_stream.bridge_id"
        ),
        "in_reply_to": _required_text(
            request["in_reply_to"], "master_bridge_stream.in_reply_to"
        ),
        "event": event,
        "sequence": sequence,
        "delta": delta,
        "detail": detail,
    }


def normalize_master_bridge_permission(
    project_id: str,
    value: Any,
) -> dict[str, Any]:
    request = _exact_object_fields(
        value,
        field="master_bridge_permission",
        required=frozenset({"bridge_id", "in_reply_to", "permission"}),
    )
    try:
        permission = normalize_permission_request(request["permission"])
    except AgentSessionError as error:
        raise UniverseError(
            "AGENT_PERMISSION_REQUEST_INVALID",
            str(error),
        ) from error
    return {
        "project_id": _project_id(project_id),
        "bridge_id": _required_text(
            request["bridge_id"], "master_bridge_permission.bridge_id"
        ),
        "in_reply_to": _required_text(
            request["in_reply_to"], "master_bridge_permission.in_reply_to"
        ),
        "permission": permission,
    }


def normalize_permission_decision(value: Any) -> dict[str, str]:
    request = _exact_object_fields(
        value,
        field="agent_permission_decision",
        required=frozenset({"option_id"}),
    )
    return {
        "schema": PERMISSION_DECISION_SCHEMA,
        "option_id": _required_text(
            request["option_id"], "agent_permission_decision.option_id"
        ),
    }


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


def build_fresh_project_composition(
    intent: dict[str, Any], route: dict[str, Any], seed_result: dict[str, Any]
) -> dict[str, Any]:
    functional_nodes = [
        {
            "node_id": "capability_" + step["id"],
            "title": step["title"],
            "purpose": step["purpose"],
            "acceptance_condition": step["exit_evidence"],
            "state": "PROPOSED",
        }
        for step in route["steps"]
    ]
    implementation_workstreams = [
        {
            "workstream_id": "implementation_" + step["id"],
            "functional_node_id": "capability_" + step["id"],
            "title": step["title"],
            "planning_state": "TECHNOLOGY_AND_DESIGN_SELECTION_REQUIRED",
        }
        for step in route["steps"]
    ]
    material = {
        "schema": FRESH_PROJECT_COMPOSITION_SCHEMA,
        "intent": {key: value for key, value in intent.items() if key != "limit"},
        "seed": seed_result["seed"],
        "selected_route": {
            "route_id": route["route_id"],
            "title": route["title"],
            "description": route["description"],
            "support_level": route["support_level"],
            "matches": route["matches"],
        },
        "specification": {
            "problem_statement": intent["goal"],
            "target_users": intent.get("target_users", "USER_SELECTION_REQUIRED"),
            "constraints": intent["constraints"],
            "functional_nodes": functional_nodes,
            "completion_conditions": [step["exit_evidence"] for step in route["steps"]],
        },
        "design": {
            "direction": route["description"],
            "state": "USER_AND_LLM_DESIGN_REFINEMENT_REQUIRED",
        },
        "technology": {
            "selected_signals": intent["technologies"],
            "unresolved_signals": seed_result["input"]["unresolved_technologies"],
            "selection_state": "USER_SELECTION_REQUIRED",
        },
        "implementation_workstreams": implementation_workstreams,
        "document_plan": [
            {
                "document_id": "project-specification",
                "role": "SPECIFICATION",
                "title": "Project specification",
            },
            {
                "document_id": "project-design",
                "role": "DESIGN",
                "title": "Project design direction",
            },
            {
                "document_id": "project-architecture",
                "role": "ARCHITECTURE",
                "title": "Project architecture and implementation bindings",
            },
            {
                "document_id": "project-decisions",
                "role": "DECISION",
                "title": "Selected technology and route decisions",
            },
            {
                "document_id": "project-acceptance",
                "role": "CONTRACT",
                "title": "Acceptance and completion conditions",
            },
        ],
        "risk_conditions": route["risk_patterns"],
        "selection_state": "USER_SELECTION_REQUIRED",
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "USER_ADOPTION_OR_COMPOSITION_REVISION",
    }
    material["composition_digest"] = _json_sha256(material)
    material["composition_id"] = "composition_" + material["composition_digest"][:24]
    material["status"] = "FRESH_PROJECT_COMPOSITION_PROPOSAL_READY"
    return material


def build_fresh_project_refinement_request(
    composition: dict[str, Any], *, purpose: str
) -> dict[str, Any]:
    context = {
        "intent": composition["intent"],
        "selected_route": composition["selected_route"],
        "specification": composition["specification"],
        "design": composition["design"],
        "technology": composition["technology"],
        "document_plan": composition["document_plan"],
        "risk_conditions": composition["risk_conditions"],
    }
    material = {
        "schema": FRESH_PROJECT_REFINEMENT_REQUEST_SCHEMA,
        "composition_id": composition["composition_id"],
        "composition_digest": composition["composition_digest"],
        "purpose": purpose,
        "context": context,
        "output_contract": {
            "schema": FRESH_PROJECT_REFINEMENT_WORKER_OUTPUT_SCHEMA,
            "required": [
                "schema",
                "refinement",
            ],
            "refinement_fields": [
                "problem_statement",
                "target_users",
                "constraints",
                "design_direction",
                "technology_recommendations",
                "document_additions",
                "risk_additions",
            ],
            "raw_worker_text": "FORBIDDEN",
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema", "refinement"],
                "properties": {
                    "schema": {
                        "type": "string",
                        "enum": [FRESH_PROJECT_REFINEMENT_WORKER_OUTPUT_SCHEMA],
                    },
                    "refinement": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "problem_statement",
                            "target_users",
                            "constraints",
                            "design_direction",
                            "technology_recommendations",
                            "document_additions",
                            "risk_additions",
                        ],
                        "properties": {
                            "problem_statement": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "target_users": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "constraints": {
                                "type": "array",
                                "maxItems": 32,
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "design_direction": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "technology_recommendations": {
                                "type": "array",
                                "maxItems": 32,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["technology", "rationale"],
                                    "properties": {
                                        "technology": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                        "rationale": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                    },
                                },
                            },
                            "document_additions": {
                                "type": "array",
                                "maxItems": 32,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["document_id", "role", "title"],
                                    "properties": {
                                        "document_id": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                        "role": {
                                            "type": "string",
                                            "enum": sorted(DOCUMENT_ROLES),
                                        },
                                        "title": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                    },
                                },
                            },
                            "risk_additions": {
                                "type": "array",
                                "maxItems": 32,
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
        "runtime_boundary": {
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "task_frame": "UNIVERSE_PLANNING_FRAME_REQUIRED",
            "provider_invocation": "NOT_REQUESTED",
        },
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "UNIVERSE_PLANNING_FRAME_BINDING_REQUIRED",
    }
    material["request_digest"] = _json_sha256(material)
    material["request_id"] = "refinementreq_" + material["request_digest"][:24]
    material["status"] = "FRESH_PROJECT_REFINEMENT_REQUEST_READY"
    return material


def build_fresh_project_refinement_run(
    request: dict[str, Any],
    planning: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    proposal = planning["execution_proposal"]
    material = {
        "schema": FRESH_PROJECT_REFINEMENT_RUN_SCHEMA,
        "run_id": run_id,
        "request_id": request["request_id"],
        "request_digest": request["request_digest"],
        "composition_id": request["composition_id"],
        "composition_digest": request["composition_digest"],
        "provider": planning["provider"],
        "model_ref": planning["model_ref"],
        "frame_id": planning["frame_id"],
        "turn_id": planning["turn_id"],
        "task_frame_execution_proposal": proposal,
        "proposal_id": proposal["proposal_id"],
        "plan_digest": proposal["plan_digest"],
        "approval_required": True,
        "state": "PROPOSED",
        "runtime_binding": "PROCESS_LOCAL_REQUIRED",
        "repository_write_scope": "NONE",
        "mutation_scope": {"operations": [], "targets": []},
        "raw_worker_text": "FORBIDDEN",
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
        },
        "next_operation": "USER_PROVIDER_EXECUTION_APPROVAL",
    }
    material["run_digest"] = _json_sha256(material)
    return material


def build_fresh_project_refinement_candidate(
    normalized: dict[str, Any],
) -> dict[str, Any]:
    material = {
        **normalized,
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "USER_REFINEMENT_ADOPTION_OR_REVISION",
    }
    material["candidate_digest"] = _json_sha256(material)
    material["candidate_id"] = "refinement_" + material["candidate_digest"][:24]
    material["status"] = "FRESH_PROJECT_REFINEMENT_CANDIDATE_READY"
    return material


def build_refined_fresh_project_composition(
    composition: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    refinement = candidate["refinement"]
    original_document_ids = {
        document["document_id"] for document in composition["document_plan"]
    }
    conflicting = original_document_ids.intersection(
        document["document_id"] for document in refinement["document_additions"]
    )
    if conflicting:
        raise UniverseError(
            "FRESH_PROJECT_REFINEMENT_DOCUMENT_CONFLICT",
            "document_additions must not replace an existing document_id: "
            + ", ".join(sorted(conflicting)),
            HTTPStatus.CONFLICT,
        )
    material = json.loads(_canonical_json(composition))
    for field in ("composition_id", "composition_digest", "created_at", "status"):
        material.pop(field, None)
    material["specification"] = {
        **material["specification"],
        "problem_statement": refinement["problem_statement"],
        "target_users": refinement["target_users"],
        "constraints": refinement["constraints"],
    }
    material["design"] = {
        **material["design"],
        "direction": refinement["design_direction"],
        "state": "USER_SELECTION_REQUIRED",
    }
    material["technology"] = {
        **material["technology"],
        "recommendations": refinement["technology_recommendations"],
        "selection_state": "USER_SELECTION_REQUIRED",
    }
    material["document_plan"] = [
        *material["document_plan"],
        *refinement["document_additions"],
    ]
    merged_risks = []
    seen_risk_digests = set()
    for risk in [*material["risk_conditions"], *refinement["risk_additions"]]:
        risk_digest = _json_sha256(risk)
        if risk_digest in seen_risk_digests:
            continue
        seen_risk_digests.add(risk_digest)
        merged_risks.append(risk)
    material["risk_conditions"] = merged_risks
    material["refinement"] = {
        "base_composition_id": composition["composition_id"],
        "base_composition_digest": composition["composition_digest"],
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": candidate["candidate_digest"],
        "producer": {
            "provider": candidate["producer"]["provider"],
            "model_ref": candidate["producer"]["model_ref"],
            "result_receipt_ref": candidate["producer"]["result_receipt_ref"],
        },
    }
    material["selection_state"] = "USER_SELECTION_REQUIRED"
    material["next_operation"] = "USER_ADOPTION_OR_COMPOSITION_REVISION"
    material["composition_digest"] = _json_sha256(material)
    material["composition_id"] = "composition_" + material["composition_digest"][:24]
    material["status"] = "FRESH_PROJECT_COMPOSITION_PROPOSAL_READY"
    return material


def build_fresh_project_refinement_adoption(
    candidate: dict[str, Any],
    refined_composition: dict[str, Any],
    request: dict[str, Any],
    *,
    user_notes: str | None = None,
) -> dict[str, Any]:
    material = {
        "schema": FRESH_PROJECT_REFINEMENT_ADOPTION_SCHEMA,
        "request_id": request["request_id"],
        "request_digest": request["request_digest"],
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": candidate["candidate_digest"],
        "source_composition_id": candidate["composition_id"],
        "source_composition_digest": candidate["composition_digest"],
        "refined_composition_id": refined_composition["composition_id"],
        "refined_composition_digest": refined_composition["composition_digest"],
        "next_operation": "USER_ADOPTION_OF_REFINED_COMPOSITION_REQUIRED",
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
    }
    if user_notes is not None:
        material["user_notes"] = user_notes
    material["selection_digest"] = _json_sha256(material)
    material["adoption_id"] = "refinementadopt_" + material["selection_digest"][:24]
    material["status"] = "FRESH_PROJECT_REFINEMENT_ADOPTED"
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


def build_document_incorporation_proposal(projection: dict[str, Any]) -> dict[str, Any]:
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

                CREATE TABLE IF NOT EXISTS project_todo (
                    todo_id TEXT PRIMARY KEY,
                    scope_kind TEXT NOT NULL,
                    project_id TEXT
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    node_ref TEXT,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(scope_kind IN ('UNIVERSE', 'PROJECT', 'NODE')),
                    CHECK(priority IN ('P0', 'P1', 'P2', 'P3')),
                    CHECK(state IN ('BACKLOG', 'READY', 'IN_PROGRESS', 'BLOCKED', 'DONE')),
                    CHECK(source_kind IN ('USER', 'CONDUCTOR', 'MASTER')),
                    CHECK(
                        (scope_kind = 'UNIVERSE' AND project_id IS NULL AND node_ref IS NULL)
                        OR (scope_kind = 'PROJECT' AND project_id IS NOT NULL AND node_ref IS NULL)
                        OR (scope_kind = 'NODE' AND project_id IS NOT NULL AND node_ref IS NOT NULL)
                    )
                );

                CREATE INDEX IF NOT EXISTS project_todo_scope_order
                ON project_todo(
                    scope_kind, project_id, node_ref, state, priority,
                    sort_order, updated_at, todo_id
                );

                CREATE TABLE IF NOT EXISTS skill_catalog (
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    operation_class TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, skill_version, operation_class)
                );

                CREATE TABLE IF NOT EXISTS skill_run_observation (
                    observation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    task_frame_ref TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    observation_digest TEXT NOT NULL,
                    skill_binding_digest TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    operation_class TEXT NOT NULL,
                    context_pack_digest TEXT NOT NULL,
                    model_ref TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    validation_state TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(project_id, candidate_id, observation_digest)
                );

                CREATE INDEX IF NOT EXISTS skill_run_observation_project_time
                ON skill_run_observation(project_id, observed_at, observation_id);

                CREATE INDEX IF NOT EXISTS skill_run_observation_skill_model
                ON skill_run_observation(
                    skill_id, skill_version, operation_class, model_ref
                );

                CREATE TABLE IF NOT EXISTS project_skill_observation_queue (
                    queue_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    publication_approval_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED', 'INGESTED')),
                    queued_at TEXT NOT NULL,
                    ingested_at TEXT,
                    UNIQUE(project_id, candidate_id)
                );

                CREATE INDEX IF NOT EXISTS project_skill_observation_queue_state
                ON project_skill_observation_queue(status, queued_at, queue_id);

                CREATE TABLE IF NOT EXISTS project_context_pack (
                    context_pack_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    seed_id TEXT NOT NULL,
                    seed_digest TEXT NOT NULL,
                    context_pack_digest TEXT NOT NULL UNIQUE,
                    pack_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS project_context_pack_project_time
                ON project_context_pack(project_id, created_at, context_pack_id);

                CREATE TABLE IF NOT EXISTS project_skill_plan_proposal (
                    proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    context_pack_id TEXT NOT NULL
                        REFERENCES project_context_pack(context_pack_id)
                        ON DELETE CASCADE,
                    proposal_digest TEXT NOT NULL UNIQUE,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS project_skill_plan_project_time
                ON project_skill_plan_proposal(project_id, created_at, proposal_id);

                CREATE TABLE IF NOT EXISTS project_skill_plan_adoption (
                    adoption_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    proposal_id TEXT NOT NULL
                        REFERENCES project_skill_plan_proposal(proposal_id)
                        ON DELETE CASCADE,
                    selection_digest TEXT NOT NULL,
                    adoption_json TEXT NOT NULL,
                    adopted_at TEXT NOT NULL,
                    UNIQUE(proposal_id, selection_digest)
                );

                CREATE INDEX IF NOT EXISTS project_skill_plan_adoption_project_time
                ON project_skill_plan_adoption(project_id, adopted_at, adoption_id);

                CREATE TABLE IF NOT EXISTS fresh_project_composition (
                    composition_id TEXT PRIMARY KEY,
                    composition_digest TEXT NOT NULL UNIQUE,
                    intent_json TEXT NOT NULL,
                    composition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS fresh_project_composition_created
                ON fresh_project_composition(created_at, composition_id);

                CREATE TABLE IF NOT EXISTS fresh_project_composition_adoption (
                    adoption_id TEXT PRIMARY KEY,
                    composition_id TEXT NOT NULL
                        REFERENCES fresh_project_composition(composition_id)
                        ON DELETE CASCADE,
                    selection_digest TEXT NOT NULL,
                    adoption_json TEXT NOT NULL,
                    adopted_at TEXT NOT NULL,
                    UNIQUE(composition_id, selection_digest)
                );

                CREATE INDEX IF NOT EXISTS fresh_project_composition_adoption_time
                ON fresh_project_composition_adoption(adopted_at, adoption_id);

                CREATE TABLE IF NOT EXISTS fresh_project_refinement_request (
                    request_id TEXT PRIMARY KEY,
                    composition_id TEXT NOT NULL
                        REFERENCES fresh_project_composition(composition_id)
                        ON DELETE CASCADE,
                    composition_digest TEXT NOT NULL,
                    request_digest TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS fresh_project_refinement_request_time
                ON fresh_project_refinement_request(created_at, request_id);

                CREATE TABLE IF NOT EXISTS fresh_project_refinement_run (
                    run_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL
                        REFERENCES fresh_project_refinement_request(request_id)
                        ON DELETE CASCADE,
                    run_digest TEXT NOT NULL UNIQUE,
                    proposal_id TEXT NOT NULL UNIQUE,
                    plan_digest TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK(state IN ('PROPOSED', 'RUNNING', 'COMPLETED', 'FAILED')),
                    run_json TEXT NOT NULL,
                    candidate_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS fresh_project_refinement_run_time
                ON fresh_project_refinement_run(created_at, run_id);

                CREATE TABLE IF NOT EXISTS fresh_project_refinement_candidate (
                    candidate_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL
                        REFERENCES fresh_project_refinement_request(request_id)
                        ON DELETE CASCADE,
                    candidate_digest TEXT NOT NULL UNIQUE,
                    candidate_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(request_id, candidate_digest)
                );

                CREATE INDEX IF NOT EXISTS fresh_project_refinement_candidate_time
                ON fresh_project_refinement_candidate(created_at, candidate_id);

                CREATE TABLE IF NOT EXISTS fresh_project_refinement_adoption (
                    adoption_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL
                        REFERENCES fresh_project_refinement_candidate(candidate_id)
                        ON DELETE CASCADE,
                    refined_composition_id TEXT NOT NULL
                        REFERENCES fresh_project_composition(composition_id)
                        ON DELETE RESTRICT,
                    selection_digest TEXT NOT NULL,
                    adoption_json TEXT NOT NULL,
                    adopted_at TEXT NOT NULL,
                    UNIQUE(candidate_id, selection_digest)
                );

                CREATE INDEX IF NOT EXISTS fresh_project_refinement_adoption_time
                ON fresh_project_refinement_adoption(adopted_at, adoption_id);

                CREATE TABLE IF NOT EXISTS project_master_handoff (
                    handoff_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    source_kind TEXT NOT NULL,
                    source_adoption_id TEXT NOT NULL,
                    handoff_digest TEXT NOT NULL UNIQUE,
                    handoff_json TEXT NOT NULL,
                    delivery_state TEXT NOT NULL,
                    room_message_id TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    UNIQUE(project_id, source_kind, source_adoption_id)
                );

                CREATE INDEX IF NOT EXISTS project_master_handoff_project_time
                ON project_master_handoff(project_id, created_at, handoff_id);

                CREATE TABLE IF NOT EXISTS project_skill_plan_master_application (
                    handoff_id TEXT PRIMARY KEY
                        REFERENCES project_master_handoff(handoff_id)
                        ON DELETE CASCADE,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    adoption_id TEXT NOT NULL UNIQUE,
                    approval_digest TEXT NOT NULL,
                    application_digest TEXT NOT NULL UNIQUE,
                    application_json TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS project_skill_plan_application_project_time
                ON project_skill_plan_master_application(
                    project_id, applied_at, handoff_id
                );

                CREATE TABLE IF NOT EXISTS experience_case (
                    case_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    case_digest TEXT NOT NULL UNIQUE,
                    case_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, case_digest)
                );

                CREATE INDEX IF NOT EXISTS experience_case_project_time
                ON experience_case(project_id, created_at, case_id);

                CREATE TABLE IF NOT EXISTS experience_case_observation (
                    case_id TEXT NOT NULL
                        REFERENCES experience_case(case_id)
                        ON DELETE CASCADE,
                    observation_id TEXT NOT NULL
                        REFERENCES skill_run_observation(observation_id)
                        ON DELETE RESTRICT,
                    PRIMARY KEY(case_id, observation_id)
                );

                CREATE TABLE IF NOT EXISTS experience_pattern_proposal (
                    proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    proposal_digest TEXT NOT NULL UNIQUE,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, proposal_digest)
                );

                CREATE INDEX IF NOT EXISTS experience_pattern_proposal_project_time
                ON experience_pattern_proposal(project_id, created_at, proposal_id);

                CREATE TABLE IF NOT EXISTS project_memory (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    state TEXT NOT NULL,
                    link_state TEXT NOT NULL,
                    node_ref TEXT,
                    graph TEXT,
                    origin_ref TEXT,
                    memory_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(state IN ('BRAINSTORM', 'OBSERVED', 'QUESTION', 'DECISION_NOTE')),
                    CHECK(link_state IN ('UNLINKED', 'LINKED', 'PROPOSED')),
                    CHECK(
                        (link_state = 'UNLINKED' AND node_ref IS NULL AND graph IS NULL)
                        OR (
                            link_state IN ('LINKED', 'PROPOSED')
                            AND node_ref IS NOT NULL
                            AND graph IN ('functional', 'implementation')
                        )
                    )
                );

                CREATE INDEX IF NOT EXISTS project_memory_project_time
                ON project_memory(project_id, updated_at, memory_id);

                CREATE INDEX IF NOT EXISTS project_memory_project_link
                ON project_memory(project_id, link_state, node_ref, memory_id);

                CREATE TABLE IF NOT EXISTS career_promotion_queue (
                    queue_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    source_proposal_id TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL UNIQUE,
                    candidate_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED')),
                    queued_at TEXT NOT NULL,
                    UNIQUE(project_id, source_proposal_id)
                );

                CREATE INDEX IF NOT EXISTS career_promotion_queue_order
                ON career_promotion_queue(queued_at, queue_id);

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

                CREATE TABLE IF NOT EXISTS project_room_message (
                    message_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    delivery_state TEXT NOT NULL,
                    delivery_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, idempotency_key)
                );

                CREATE INDEX IF NOT EXISTS project_room_message_project_time
                ON project_room_message(project_id, created_at, message_id);

                CREATE TABLE IF NOT EXISTS agent_permission_request (
                    request_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    in_reply_to TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_call_json TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK(state IN ('PENDING', 'RESOLVED', 'CANCELLED')),
                    selected_option_id TEXT,
                    requested_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE INDEX IF NOT EXISTS agent_permission_project_time
                ON agent_permission_request(project_id, requested_at, request_id);

                CREATE TABLE IF NOT EXISTS conductor_room_message (
                    message_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    message_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS conductor_room_message_time
                ON conductor_room_message(created_at, message_id);

                CREATE TABLE IF NOT EXISTS project_master_bridge (
                    project_id TEXT PRIMARY KEY
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    bridge_id TEXT NOT NULL UNIQUE,
                    endpoint TEXT NOT NULL,
                    credential_env TEXT NOT NULL,
                    master_session_ref TEXT NOT NULL,
                    binding_evidence_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_delivery_at TEXT
                );

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

                CREATE TABLE IF NOT EXISTS runtime_worker_invocation (
                    invocation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    request_digest TEXT NOT NULL,
                    invocation_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(project_id, invocation_id)
                );

                CREATE INDEX IF NOT EXISTS runtime_worker_invocation_project_time
                ON runtime_worker_invocation(project_id, created_at, invocation_id);

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

                CREATE TABLE IF NOT EXISTS project_release_application (
                    application_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL UNIQUE
                        REFERENCES project_release_proposal(proposal_id)
                        ON DELETE CASCADE,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    release_id TEXT NOT NULL
                        REFERENCES release_artifact(release_id),
                    approval_digest TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS project_release_application_project_time
                ON project_release_application(project_id, applied_at, application_id);

                CREATE TABLE IF NOT EXISTS universe_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    universe_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cli_provider_setting (
                    scope_kind TEXT NOT NULL
                        CHECK(scope_kind IN ('UNIVERSE_CONDUCTOR', 'PROJECT_MASTER')),
                    scope_id TEXT NOT NULL,
                    provider TEXT NOT NULL
                        CHECK(provider IN ('AUTO', 'GROK', 'CODEX')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope_kind, scope_id)
                );

                CREATE TABLE IF NOT EXISTS service_setting (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO service_setting(setting_key, setting_value, updated_at)
                VALUES ('memory_maintain_interval_hours', '0', ?)
                """,
                (utc_now(),),
            )
            room_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(project_room_message)"
                ).fetchall()
            }
            if "delivery_json" not in room_columns:
                connection.execute(
                    "ALTER TABLE project_room_message "
                    "ADD COLUMN delivery_json TEXT NOT NULL DEFAULT '{}'"
                )
            observation_queue_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(project_skill_observation_queue)"
                ).fetchall()
            }
            if "publication_approval_json" not in observation_queue_columns:
                connection.execute(
                    "ALTER TABLE project_skill_observation_queue "
                    "ADD COLUMN publication_approval_json "
                    "TEXT NOT NULL DEFAULT '{}'"
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

    def provider_setting(self, scope_kind: str, scope_id: str) -> dict[str, Any]:
        normalized_scope = str(scope_kind).strip().upper()
        if normalized_scope not in PROVIDER_SETTING_SCOPES:
            raise UniverseError(
                "PROVIDER_SETTING_SCOPE_INVALID",
                f"unsupported provider setting scope: {normalized_scope}",
            )
        normalized_id = _required_text(scope_id, "scope_id")
        if normalized_scope == "PROJECT_MASTER":
            normalized_id = _project_id(normalized_id)
            self.get_project(normalized_id)
        elif normalized_id != "CONDUCTOR":
            raise UniverseError(
                "PROVIDER_SETTING_SCOPE_INVALID",
                "Universe Conductor setting scope_id must be CONDUCTOR",
            )
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT provider, updated_at
                FROM cli_provider_setting
                WHERE scope_kind = ? AND scope_id = ?
                """,
                (normalized_scope, normalized_id),
            ).fetchone()
        return {
            "schema": PROVIDER_SETTING_SCHEMA,
            "scope_kind": normalized_scope,
            "scope_id": normalized_id,
            "provider": str(row["provider"]) if row is not None else "AUTO",
            "updated_at": str(row["updated_at"]) if row is not None else None,
        }

    def set_provider_setting(
        self,
        scope_kind: str,
        scope_id: str,
        value: Any,
    ) -> dict[str, Any]:
        request = _exact_object_fields(
            value,
            field="provider_setting",
            required=frozenset({"provider"}),
            optional=frozenset(),
        )
        provider = _required_text(request["provider"], "provider").upper()
        if provider not in PROVIDER_SETTING_CHOICES:
            raise UniverseError(
                "PROVIDER_SETTING_INVALID",
                "provider must be AUTO, GROK, or CODEX",
            )
        current = self.provider_setting(scope_kind, scope_id)
        updated_at = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO cli_provider_setting(
                    scope_kind, scope_id, provider, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(scope_kind, scope_id) DO UPDATE SET
                    provider = excluded.provider,
                    updated_at = excluded.updated_at
                """,
                (
                    current["scope_kind"],
                    current["scope_id"],
                    provider,
                    updated_at,
                ),
            )
        return self.provider_setting(current["scope_kind"], current["scope_id"])

    def list_provider_settings(self) -> dict[str, Any]:
        return {
            "schema": PROVIDER_SETTING_SCHEMA,
            "universe_conductor": self.provider_setting(
                "UNIVERSE_CONDUCTOR",
                "CONDUCTOR",
            ),
            "project_masters": [
                self.provider_setting("PROJECT_MASTER", project["project_id"])
                for project in self.list_projects()
            ],
        }

    def get_service_settings(self) -> dict[str, Any]:
        """Return durable local-service settings. interval_hours 0 disables batch."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT setting_key, setting_value, updated_at
                FROM service_setting
                """
            ).fetchall()
        values = {row["setting_key"]: row["setting_value"] for row in rows}
        raw = values.get("memory_maintain_interval_hours", "0")
        try:
            interval_hours = int(raw)
        except (TypeError, ValueError):
            interval_hours = 0
        if interval_hours < 0:
            interval_hours = 0
        if interval_hours > 24 * 30:
            interval_hours = 24 * 30
        return {
            "schema": "universe.service-settings.v1",
            "status": "SERVICE_SETTINGS_COLLECTED",
            "memory_maintain": {
                "interval_hours": interval_hours,
                "enabled": interval_hours > 0,
                "scorer": "HEURISTIC",
                "apply_proposals": True,
                "note": "0 hours disables the in-process maintain worker",
            },
            "updated_at": next(
                (
                    row["updated_at"]
                    for row in rows
                    if row["setting_key"] == "memory_maintain_interval_hours"
                ),
                None,
            ),
        }

    def set_service_settings(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise UniverseError(
                "SERVICE_SETTINGS_INVALID",
                "service settings body must be an object",
            )
        maintain = value.get("memory_maintain")
        if maintain is None and "interval_hours" in value:
            maintain = value
        if not isinstance(maintain, dict):
            raise UniverseError(
                "SERVICE_SETTINGS_INVALID",
                "memory_maintain must be an object",
            )
        raw = maintain.get("interval_hours", 0)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise UniverseError(
                "SERVICE_SETTINGS_INVALID",
                "memory_maintain.interval_hours must be an integer >= 0",
            )
        if raw < 0 or raw > 24 * 30:
            raise UniverseError(
                "SERVICE_SETTINGS_INVALID",
                "memory_maintain.interval_hours must be 0..720",
            )
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO service_setting(setting_key, setting_value, updated_at)
                VALUES ('memory_maintain_interval_hours', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (str(raw), now),
            )
        return self.get_service_settings()


    def register_project(self, value: Any) -> tuple[dict[str, Any], bool]:
        project = normalize_registration(value)
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT project_root FROM project_connection WHERE project_id = ?",
                (project["project_id"],),
            ).fetchone()
            if (
                existing is not None
                and existing["project_root"] != project["project_root"]
            ):
                raise UniverseError(
                    "PROJECT_ID_ALREADY_BOUND",
                    "project_id is already attached to another root",
                    HTTPStatus.CONFLICT,
                )
            root_owner = connection.execute(
                "SELECT project_id FROM project_connection WHERE project_root = ?",
                (project["project_root"],),
            ).fetchone()
            if (
                root_owner is not None
                and root_owner["project_id"] != project["project_id"]
            ):
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
                    json.dumps(
                        project["metadata"], sort_keys=True, separators=(",", ":")
                    ),
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
        payload_json = json.dumps(
            event["payload"], sort_keys=True, separators=(",", ":")
        )
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
                    event["event_id"],
                    event["project_id"],
                    event["event_type"],
                    payload_json,
                    event["created_at"],
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

    def create_todo(self, value: Any) -> dict[str, Any]:
        todo = normalize_todo(value)
        if todo["project_id"] is not None:
            self.get_project(todo["project_id"])
        todo_id = todo.get("todo_id") or "todo_" + uuid.uuid4().hex
        now = utc_now()
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO project_todo(
                        todo_id, scope_kind, project_id, node_ref, title, detail,
                        priority, state, source_kind, sort_order, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        todo_id,
                        todo["scope_kind"],
                        todo["project_id"],
                        todo["node_ref"],
                        todo["title"],
                        todo["detail"],
                        todo["priority"],
                        todo["state"],
                        todo["source_kind"],
                        todo["sort_order"],
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise UniverseError(
                    "TODO_ID_CONFLICT",
                    "todo_id already exists",
                    HTTPStatus.CONFLICT,
                ) from error
        return self.get_todo(todo_id)

    def list_todos(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT todo_id, scope_kind, project_id, node_ref, title, detail,
                       priority, state, source_kind, sort_order, revision,
                       created_at, updated_at
                FROM project_todo
                ORDER BY
                    CASE state
                        WHEN 'IN_PROGRESS' THEN 0
                        WHEN 'READY' THEN 1
                        WHEN 'BLOCKED' THEN 2
                        WHEN 'BACKLOG' THEN 3
                        ELSE 4
                    END,
                    CASE priority
                        WHEN 'P0' THEN 0
                        WHEN 'P1' THEN 1
                        WHEN 'P2' THEN 2
                        ELSE 3
                    END,
                    sort_order, updated_at DESC, todo_id
                """
            ).fetchall()
        return [self._todo_row(row) for row in rows]

    def get_todo(self, todo_id: str) -> dict[str, Any]:
        normalized = _identifier(todo_id, "todo_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT todo_id, scope_kind, project_id, node_ref, title, detail,
                       priority, state, source_kind, sort_order, revision,
                       created_at, updated_at
                FROM project_todo
                WHERE todo_id = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "TODO_NOT_FOUND",
                f"Todo does not exist: {normalized}",
                HTTPStatus.NOT_FOUND,
            )
        return self._todo_row(row)

    def update_todo(self, todo_id: str, value: Any) -> dict[str, Any]:
        normalized_id = _identifier(todo_id, "todo_id")
        todo = normalize_todo(value, updating=True)
        if "todo_id" in todo and todo["todo_id"] != normalized_id:
            raise UniverseError(
                "TODO_ID_MISMATCH",
                "body todo_id does not match request path",
            )
        if todo["project_id"] is not None:
            self.get_project(todo["project_id"])
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE project_todo
                SET scope_kind = ?, project_id = ?, node_ref = ?, title = ?,
                    detail = ?, priority = ?, state = ?, source_kind = ?,
                    sort_order = ?, revision = revision + 1, updated_at = ?
                WHERE todo_id = ? AND revision = ?
                """,
                (
                    todo["scope_kind"],
                    todo["project_id"],
                    todo["node_ref"],
                    todo["title"],
                    todo["detail"],
                    todo["priority"],
                    todo["state"],
                    todo["source_kind"],
                    todo["sort_order"],
                    now,
                    normalized_id,
                    todo["revision"],
                ),
            )
        if cursor.rowcount != 1:
            current = self.get_todo(normalized_id)
            raise UniverseError(
                "TODO_REVISION_CONFLICT",
                f"Todo revision changed; current revision is {current['revision']}",
                HTTPStatus.CONFLICT,
            )
        return self.get_todo(normalized_id)

    def delete_todo(self, todo_id: str) -> dict[str, Any]:
        normalized = _identifier(todo_id, "todo_id")
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM project_todo WHERE todo_id = ?",
                (normalized,),
            )
        if cursor.rowcount != 1:
            raise UniverseError(
                "TODO_NOT_FOUND",
                f"Todo does not exist: {normalized}",
                HTTPStatus.NOT_FOUND,
            )
        return {"todo_id": normalized, "deleted": True}

    def ingest_skill_observations(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_skill_observation_candidate(project["project_id"], value)
        candidate = request["candidate"]
        now = utc_now()
        rows: list[dict[str, Any]] = []
        with self._connection() as connection:
            existing_candidate = connection.execute(
                """
                SELECT DISTINCT candidate_digest
                FROM skill_run_observation
                WHERE project_id = ? AND candidate_id = ?
                """,
                (project["project_id"], request["candidate_id"]),
            ).fetchall()
            if existing_candidate and any(
                row["candidate_digest"] != request["candidate_digest"]
                for row in existing_candidate
            ):
                raise UniverseError(
                    "SKILL_OBSERVATION_CANDIDATE_CONFLICT",
                    "candidate_id already refers to different redacted content",
                    HTTPStatus.CONFLICT,
                )
            for observation in candidate["observations"]:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM skill_run_observation
                    WHERE project_id = ?
                      AND candidate_id = ?
                      AND observation_digest = ?
                    """,
                    (
                        project["project_id"],
                        request["candidate_id"],
                        observation["observation_digest"],
                    ),
                ).fetchone()
                if existing is not None:
                    rows.append(self._skill_observation_row(existing))
                    continue
                skill = observation["skill"]
                observation_id = (
                    "skillrun_"
                    + _json_sha256(
                        {
                            "project_id": project["project_id"],
                            "candidate_id": request["candidate_id"],
                            "observation_digest": observation["observation_digest"],
                        }
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT INTO skill_run_observation(
                        observation_id, project_id, candidate_id, candidate_digest,
                        task_frame_ref, source_ref, observation_digest,
                        skill_binding_digest, skill_id, skill_version,
                        operation_class, context_pack_digest, model_ref, outcome,
                        validation_state, evidence_refs_json, metrics_json,
                        observed_at, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        project["project_id"],
                        request["candidate_id"],
                        request["candidate_digest"],
                        candidate["task_frame_ref"],
                        candidate["source_ref"],
                        observation["observation_digest"],
                        observation["skill_binding_digest"],
                        skill["skill_id"],
                        skill["skill_version"],
                        skill["operation_class"],
                        skill["context_pack_digest"],
                        observation["model_ref"],
                        observation["outcome"],
                        observation["validation_state"],
                        _canonical_json(observation["evidence_refs"]),
                        _canonical_json(observation["metrics"]),
                        candidate["observed_at"],
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO skill_catalog(
                        skill_id, skill_version, operation_class,
                        first_observed_at, last_observed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(skill_id, skill_version, operation_class)
                    DO UPDATE SET
                        first_observed_at = MIN(
                            skill_catalog.first_observed_at,
                            excluded.first_observed_at
                        ),
                        last_observed_at = MAX(
                            skill_catalog.last_observed_at,
                            excluded.last_observed_at
                        )
                    """,
                    (
                        skill["skill_id"],
                        skill["skill_version"],
                        skill["operation_class"],
                        candidate["observed_at"],
                        candidate["observed_at"],
                    ),
                )
                rows.append(
                    {
                        "schema": SKILL_RUN_OBSERVATION_SCHEMA,
                        "observation_id": observation_id,
                        "project_id": project["project_id"],
                        "candidate_id": request["candidate_id"],
                        "candidate_digest": request["candidate_digest"],
                        "task_frame_ref": candidate["task_frame_ref"],
                        "source_ref": candidate["source_ref"],
                        **observation,
                        "observed_at": candidate["observed_at"],
                        "recorded_at": now,
                    }
                )
        created = not existing_candidate
        return {
            "project_id": project["project_id"],
            "candidate_id": request["candidate_id"],
            "candidate_digest": request["candidate_digest"],
            "redaction_state": "REDACTED",
            "observations": rows,
        }, created

    def enqueue_skill_observations(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        """Persist a redacted Project observation before asynchronous ingest."""

        project = self.get_project(project_id)
        request = normalize_skill_observation_publication(project["project_id"], value)
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT queue_id, candidate_id, candidate_digest,
                       publication_approval_json, status, queued_at, ingested_at
                FROM project_skill_observation_queue
                WHERE project_id = ? AND candidate_id = ?
                """,
                (project["project_id"], request["candidate_id"]),
            ).fetchone()
            if existing is not None:
                if existing["candidate_digest"] != request["candidate_digest"]:
                    raise UniverseError(
                        "SKILL_OBSERVATION_CANDIDATE_CONFLICT",
                        "candidate_id already refers to different redacted content",
                        HTTPStatus.CONFLICT,
                    )
                return self._skill_observation_queue_row(
                    existing, project["project_id"]
                ), False
            queue_id = (
                "observation_queue_"
                + _json_sha256(
                    {
                        "project_id": project["project_id"],
                        "candidate_id": request["candidate_id"],
                        "candidate_digest": request["candidate_digest"],
                    }
                )[:24]
            )
            connection.execute(
                """
                INSERT INTO project_skill_observation_queue(
                    queue_id, project_id, candidate_id, candidate_digest,
                    candidate_json, publication_approval_json,
                    status, queued_at, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, NULL)
                """,
                (
                    queue_id,
                    project["project_id"],
                    request["candidate_id"],
                    request["candidate_digest"],
                    _canonical_json(
                        {
                            "candidate_id": request["candidate_id"],
                            "candidate": request["candidate"],
                        }
                    ),
                    _canonical_json(request["publication_approval"]),
                    now,
                ),
            )
        return {
            "schema": SKILL_OBSERVATION_QUEUE_SCHEMA,
            "queue_id": queue_id,
            "project_id": project["project_id"],
            "candidate_id": request["candidate_id"],
            "candidate_digest": request["candidate_digest"],
            "publication_approval": request["publication_approval"],
            "publication_approval_digest": request["publication_approval_digest"],
            "status": "QUEUED",
            "queued_at": now,
            "ingested_at": None,
        }, True

    def list_skill_observation_queue(
        self, project_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT queue_id, candidate_id, candidate_digest,
                       publication_approval_json, status, queued_at, ingested_at
                FROM project_skill_observation_queue
                WHERE project_id = ?
                ORDER BY queued_at, queue_id
                LIMIT ?
                """,
                (project["project_id"], max(1, min(int(limit), 500))),
            ).fetchall()
        return [
            self._skill_observation_queue_row(row, project["project_id"])
            for row in rows
        ]

    def drain_skill_observation_queue(self, *, limit: int = 100) -> dict[str, Any]:
        """Consume Universe-owned queue items without touching Project files."""

        bounded_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT queue_id, project_id, candidate_json
                FROM project_skill_observation_queue
                WHERE status = 'QUEUED'
                ORDER BY queued_at, queue_id
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        ingested: list[dict[str, Any]] = []
        for row in rows:
            envelope = json.loads(row["candidate_json"])
            result, created = self.ingest_skill_observations(
                row["project_id"], envelope
            )
            now = utc_now()
            with self._connection() as connection:
                connection.execute(
                    """
                    UPDATE project_skill_observation_queue
                    SET status = 'INGESTED', ingested_at = ?
                    WHERE queue_id = ? AND status = 'QUEUED'
                    """,
                    (now, row["queue_id"]),
                )
            ingested.append(
                {
                    "queue_id": row["queue_id"],
                    "project_id": row["project_id"],
                    "status": "INGESTED",
                    "ingested_at": now,
                    "new_observations": created,
                    "candidate_digest": result["candidate_digest"],
                }
            )
        return {
            "schema": "universe.skill-observation-queue-drain.v1",
            "status": "SKILL_OBSERVATION_QUEUE_DRAINED",
            "items": ingested,
            "project_source_write": "NONE",
            "project_runtime_state_write": "NONE",
        }

    @staticmethod
    def _skill_observation_queue_row(
        row: sqlite3.Row, project_id: str
    ) -> dict[str, Any]:
        publication_approval = json.loads(row["publication_approval_json"])
        return {
            "schema": SKILL_OBSERVATION_QUEUE_SCHEMA,
            "queue_id": row["queue_id"],
            "project_id": project_id,
            "candidate_id": row["candidate_id"],
            "candidate_digest": row["candidate_digest"],
            "publication_approval": publication_approval,
            "publication_approval_digest": (
                _json_sha256(publication_approval)
                if publication_approval
                else "UNKNOWN"
            ),
            "status": row["status"],
            "queued_at": row["queued_at"],
            "ingested_at": row["ingested_at"],
        }

    def list_skill_observations(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM skill_run_observation
                WHERE project_id = ?
                ORDER BY observed_at DESC, observation_id DESC
                LIMIT ?
                """,
                (project["project_id"], limit),
            ).fetchall()
        return [self._skill_observation_row(row) for row in rows]

    def list_skill_bench(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM skill_run_observation
                ORDER BY skill_id, skill_version, operation_class, model_ref,
                         observed_at, observation_id
                """
            ).fetchall()
        grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            item = self._skill_observation_row(row)
            skill = item["skill"]
            provider_ref = provider_ref_from_model_ref(item["model_ref"])
            key = (
                skill["skill_id"],
                skill["skill_version"],
                skill["operation_class"],
                item["model_ref"],
                provider_ref,
            )
            group = grouped.setdefault(
                key,
                {
                    "schema": SKILL_BENCH_SCHEMA,
                    "skill": skill,
                    "model_ref": item["model_ref"],
                    "provider_ref": provider_ref,
                    "observation_count": 0,
                    "outcomes": {state: 0 for state in sorted(SKILL_OUTCOMES)},
                    "validation_states": {
                        state: 0 for state in sorted(SKILL_VALIDATION_STATES)
                    },
                    "metric_totals": {},
                    "first_observed_at": item["observed_at"],
                    "last_observed_at": item["observed_at"],
                },
            )
            group["observation_count"] += 1
            group["outcomes"][item["outcome"]] += 1
            group["validation_states"][item["validation_state"]] += 1
            group["first_observed_at"] = min(
                group["first_observed_at"], item["observed_at"]
            )
            group["last_observed_at"] = max(
                group["last_observed_at"], item["observed_at"]
            )
            for metric_key, metric_value in item["metrics"].items():
                group["metric_totals"][metric_key] = (
                    group["metric_totals"].get(metric_key, 0) + metric_value
                )
        return list(grouped.values())[:limit]

    def create_context_pack(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_context_pack_request(value)
        seed = self.get_project_seed(project["project_id"])
        known_nodes = {node["node_id"]: node for node in seed["nodes"]}
        unknown_nodes = set(request["node_ids"]) - set(known_nodes)
        if unknown_nodes:
            raise UniverseError(
                "CONTEXT_PACK_NODE_UNKNOWN",
                f"node_ids are not present in the current Project Seed: {', '.join(sorted(unknown_nodes))}",
                HTTPStatus.CONFLICT,
            )
        selected_nodes = [known_nodes[node_id] for node_id in request["node_ids"]]
        selected_node_ids = set(request["node_ids"])
        selected_edges = [
            edge
            for edge in seed["edges"]
            if edge["from_node"] in selected_node_ids
            or edge["to_node"] in selected_node_ids
        ]
        selected_documents = [
            document
            for document in seed["documents"]
            if document.get("project_wide", False)
            or selected_node_ids.intersection(document["node_ids"])
        ]
        selected_bindings = [
            binding
            for binding in seed.get("implementation_bindings", [])
            if binding["functional_node_id"] in selected_node_ids
        ]
        selected_implementation_ids = {
            binding["implementation_node_id"] for binding in selected_bindings
        }
        selected_implementation = [
            item
            for item in seed.get("implementation", {}).get("nodes", [])
            if item["implementation_id"] in selected_implementation_ids
        ]
        bench_observations = (
            self.list_skill_observations(
                project["project_id"], limit=request["bench_limit"]
            )
            if request["bench_limit"]
            else []
        )
        material = {
            "schema": PROJECT_CONTEXT_PACK_SCHEMA,
            "project_id": project["project_id"],
            "seed": {
                "seed_id": seed["seed_id"],
                "seed_digest": seed["seed_digest"],
                "source": seed["source"],
            },
            "purpose": request["purpose"],
            "node_ids": request["node_ids"],
            "project": seed["project"],
            "functional_nodes": selected_nodes,
            "functional_edges": selected_edges,
            "implementation": {"nodes": selected_implementation},
            "implementation_bindings": selected_bindings,
            "documents": selected_documents,
            "bench": {
                "scope": "PROJECT_LOCAL_ONLY",
                "observations": bench_observations,
                "observation_count": len(bench_observations),
            },
            "effects": {
                "project_source_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
                "task_frame": "NONE",
            },
        }
        material["context_pack_digest"] = _json_sha256(material)
        material["context_pack_id"] = "context_" + material["context_pack_digest"][:24]
        material["status"] = "CONTEXT_PACK_READY"
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT pack_json, created_at
                FROM project_context_pack
                WHERE context_pack_digest = ?
                """,
                (material["context_pack_digest"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["pack_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO project_context_pack(
                    context_pack_id, project_id, seed_id, seed_digest,
                    context_pack_digest, pack_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    material["context_pack_id"],
                    project["project_id"],
                    seed["seed_id"],
                    seed["seed_digest"],
                    material["context_pack_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def get_context_pack(self, project_id: str, context_pack_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        normalized_id = _identifier(context_pack_id, "context_pack_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT pack_json, created_at
                FROM project_context_pack
                WHERE project_id = ? AND context_pack_id = ?
                """,
                (project["project_id"], normalized_id),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "CONTEXT_PACK_NOT_FOUND",
                "project has no matching Context Pack",
                HTTPStatus.NOT_FOUND,
            )
        pack = json.loads(row["pack_json"])
        pack["created_at"] = row["created_at"]
        return pack

    def list_context_packs(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT pack_json, created_at
                FROM project_context_pack
                WHERE project_id = ?
                ORDER BY created_at DESC, context_pack_id DESC
                LIMIT ?
                """,
                (project["project_id"], limit),
            ).fetchall()
        packs = []
        for row in rows:
            pack = json.loads(row["pack_json"])
            pack["created_at"] = row["created_at"]
            packs.append(pack)
        return packs

    def create_skill_plan_proposal(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_skill_plan_request(value)
        pack = self.get_context_pack(project["project_id"], request["context_pack_id"])
        grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
        for observation in pack["bench"]["observations"]:
            skill = observation["skill"]
            provider_ref = provider_ref_from_model_ref(observation["model_ref"])
            key = (
                skill["skill_id"],
                skill["skill_version"],
                skill["operation_class"],
                skill["context_pack_digest"],
                observation["model_ref"],
                provider_ref,
            )
            candidate = grouped.setdefault(
                key,
                {
                    "candidate_id": "skill_" + _json_sha256(key)[:24],
                    "skill": skill,
                    "model_ref": observation["model_ref"],
                    "provider_ref": provider_ref,
                    "bench_rationale": {
                        "scope": "PROJECT_LOCAL_ONLY",
                        "observation_count": 0,
                        "validated_success_count": 0,
                        "successful_count": 0,
                        "failed_count": 0,
                        "duration_observation_count": 0,
                        "duration_total_ms": 0,
                        "average_duration_ms": None,
                        "outcomes": {state: 0 for state in sorted(SKILL_OUTCOMES)},
                        "validation_states": {
                            state: 0 for state in sorted(SKILL_VALIDATION_STATES)
                        },
                        "rank_basis": [
                            "validated_success_count DESC",
                            "validation_fail_count ASC",
                            "successful_count DESC",
                            "observation_count DESC",
                            "average_duration_ms ASC",
                            "candidate_id ASC",
                        ],
                    },
                    "recommendation_state": "CANDIDATE_ONLY",
                    "selection_state": "USER_SELECTION_REQUIRED",
                    "binding_state": "PROJECT_MASTER_BINDING_REQUIRED",
                },
            )
            rationale = candidate["bench_rationale"]
            rationale["observation_count"] += 1
            rationale["outcomes"][observation["outcome"]] += 1
            rationale["validation_states"][observation["validation_state"]] += 1
            if observation["outcome"] == "SUCCEEDED":
                rationale["successful_count"] += 1
            if observation["outcome"] == "FAILED":
                rationale["failed_count"] += 1
            if (
                observation["outcome"] == "SUCCEEDED"
                and observation["validation_state"] == "PASS"
            ):
                rationale["validated_success_count"] += 1
            duration_ms = observation["metrics"].get("duration_ms")
            if duration_ms is not None:
                rationale["duration_observation_count"] += 1
                rationale["duration_total_ms"] += duration_ms
        for candidate in grouped.values():
            rationale = candidate["bench_rationale"]
            if rationale["duration_observation_count"]:
                rationale["average_duration_ms"] = round(
                    rationale["duration_total_ms"]
                    / rationale["duration_observation_count"],
                    3,
                )
            rationale["validation_fail_count"] = rationale["validation_states"]["FAIL"]
        candidates = sorted(
            grouped.values(),
            key=lambda item: (
                -item["bench_rationale"]["validated_success_count"],
                item["bench_rationale"]["validation_fail_count"],
                -item["bench_rationale"]["successful_count"],
                -item["bench_rationale"]["observation_count"],
                (
                    item["bench_rationale"]["average_duration_ms"]
                    if item["bench_rationale"]["average_duration_ms"] is not None
                    else math.inf
                ),
                item["candidate_id"],
            ),
        )[: request["max_candidates"]]
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
        material = {
            "schema": PROJECT_SKILL_PLAN_SCHEMA,
            "project_id": project["project_id"],
            "context_pack_id": pack["context_pack_id"],
            "context_pack_digest": pack["context_pack_digest"],
            "purpose": request["purpose"],
            "candidates": candidates,
            "evidence_state": (
                "PROJECT_LOCAL_BENCH_AVAILABLE"
                if candidates
                else "NO_PROJECT_LOCAL_BENCH_MATCHES"
            ),
            "effects": {
                "project_source_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
                "task_frame": "NONE",
            },
            "next_operation": "USER_SELECTION_REQUIRED",
        }
        material["proposal_digest"] = _json_sha256(material)
        material["proposal_id"] = "skillplan_" + material["proposal_digest"][:24]
        material["status"] = "SKILL_PLAN_PROPOSAL_READY"
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM project_skill_plan_proposal
                WHERE proposal_digest = ?
                """,
                (material["proposal_digest"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["proposal_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO project_skill_plan_proposal(
                    proposal_id, project_id, context_pack_id, proposal_digest,
                    proposal_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    material["proposal_id"],
                    project["project_id"],
                    pack["context_pack_id"],
                    material["proposal_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def list_skill_plan_proposals(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM project_skill_plan_proposal
                WHERE project_id = ?
                ORDER BY created_at DESC, proposal_id DESC
                LIMIT ?
                """,
                (project["project_id"], limit),
            ).fetchall()
        proposals = []
        for row in rows:
            proposal = json.loads(row["proposal_json"])
            proposal["created_at"] = row["created_at"]
            proposals.append(proposal)
        return proposals

    def adopt_skill_plan(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_skill_plan_adoption_request(value)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT proposal_json
                FROM project_skill_plan_proposal
                WHERE project_id = ? AND proposal_id = ?
                """,
                (project["project_id"], request["proposal_id"]),
            ).fetchone()
            if row is None:
                raise UniverseError(
                    "SKILL_PLAN_PROPOSAL_NOT_FOUND",
                    "project has no matching Skill Plan proposal",
                    HTTPStatus.NOT_FOUND,
                )
            proposal = json.loads(row["proposal_json"])
            candidates = {
                candidate["candidate_id"]: candidate
                for candidate in proposal["candidates"]
            }
            unknown = set(request["candidate_ids"]) - set(candidates)
            if unknown:
                raise UniverseError(
                    "SKILL_PLAN_SELECTION_INVALID",
                    f"candidate_ids are not in the Skill Plan proposal: {', '.join(sorted(unknown))}",
                    HTTPStatus.CONFLICT,
                )
            selected = [
                candidates[candidate_id] for candidate_id in request["candidate_ids"]
            ]
            material = {
                "schema": PROJECT_SKILL_PLAN_ADOPTION_SCHEMA,
                "project_id": project["project_id"],
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["proposal_digest"],
                "context_pack_id": proposal["context_pack_id"],
                "selected_candidates": selected,
                "binding_state": "PROJECT_MASTER_BINDING_REQUIRED",
                "effects": {
                    "project_source_write": "NONE",
                    "authority": "NONE",
                    "execution_assignment": "NONE",
                    "task_frame": "NONE",
                },
                "next_operation": "PROJECT_MASTER_HANDOFF_CANDIDATE",
            }
            material["selection_digest"] = _json_sha256(material)
            material["adoption_id"] = "skilladopt_" + material["selection_digest"][:24]
            material["status"] = "SKILL_PLAN_ADOPTED"
            existing = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM project_skill_plan_adoption
                WHERE proposal_id = ? AND selection_digest = ?
                """,
                (proposal["proposal_id"], material["selection_digest"]),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["adoption_json"])
                stored["adopted_at"] = existing["adopted_at"]
                return stored, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO project_skill_plan_adoption(
                    adoption_id, project_id, proposal_id, selection_digest,
                    adoption_json, adopted_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    material["adoption_id"],
                    project["project_id"],
                    proposal["proposal_id"],
                    material["selection_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["adopted_at"] = now
        return material, True

    def list_skill_plan_adoptions(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM project_skill_plan_adoption
                WHERE project_id = ?
                ORDER BY adopted_at DESC, adoption_id DESC
                LIMIT ?
                """,
                (project["project_id"], limit),
            ).fetchall()
        adoptions = []
        for row in rows:
            adoption = json.loads(row["adoption_json"])
            adoption["adopted_at"] = row["adopted_at"]
            adoptions.append(adoption)
        return adoptions

    def create_fresh_project_composition(
        self, value: Any
    ) -> tuple[dict[str, Any], bool]:
        request = normalize_fresh_project_composition_request(value)
        intent = request["intent"]
        try:
            seed_result = suggest_paths(
                OFFICIAL_SEED_DATABASE,
                project=intent["project"],
                kind=intent["kind"],
                technologies=intent["technologies"],
                goal=intent["goal"],
                limit=10,
            )
        except SeedError as error:
            raise UniverseError(
                "FRESH_PROJECT_COMPOSITION_INVALID", str(error)
            ) from error
        route = next(
            (
                candidate
                for candidate in seed_result["candidates"]
                if candidate["route_id"] == request["route_id"]
            ),
            None,
        )
        if route is None:
            raise UniverseError(
                "FRESH_PROJECT_ROUTE_NOT_SELECTABLE",
                "route_id is not a candidate for the supplied Fresh Project intent",
                HTTPStatus.CONFLICT,
            )
        composition = build_fresh_project_composition(intent, route, seed_result)
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT composition_json, created_at
                FROM fresh_project_composition
                WHERE composition_digest = ?
                """,
                (composition["composition_digest"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["composition_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO fresh_project_composition(
                    composition_id, composition_digest, intent_json,
                    composition_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    composition["composition_id"],
                    composition["composition_digest"],
                    _canonical_json(composition["intent"]),
                    _canonical_json(composition),
                    now,
                ),
            )
        composition["created_at"] = now
        return composition, True

    def get_fresh_project_composition(self, composition_id: str) -> dict[str, Any]:
        normalized_id = _identifier(composition_id, "composition_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT composition_json, created_at
                FROM fresh_project_composition
                WHERE composition_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "FRESH_PROJECT_COMPOSITION_NOT_FOUND",
                "Universe has no matching Fresh Project Composition",
                HTTPStatus.NOT_FOUND,
            )
        composition = json.loads(row["composition_json"])
        composition["created_at"] = row["created_at"]
        return composition

    def list_fresh_project_compositions(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT composition_json, created_at
                FROM fresh_project_composition
                ORDER BY created_at DESC, composition_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        compositions = []
        for row in rows:
            composition = json.loads(row["composition_json"])
            composition["created_at"] = row["created_at"]
            compositions.append(composition)
        return compositions

    def adopt_fresh_project_composition(
        self, value: Any
    ) -> tuple[dict[str, Any], bool]:
        request = normalize_fresh_project_composition_adoption_request(value)
        composition = self.get_fresh_project_composition(request["composition_id"])
        material: dict[str, Any] = {
            "schema": FRESH_PROJECT_COMPOSITION_ADOPTION_SCHEMA,
            "composition_id": composition["composition_id"],
            "composition_digest": composition["composition_digest"],
            "selected_route_id": composition["selected_route"]["route_id"],
            "next_operation": "PROJECT_MASTER_HANDOFF_CANDIDATE",
            "effects": {
                "project_source_write": "NONE",
                "project_seed_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
                "task_frame": "NONE",
            },
        }
        if "user_notes" in request:
            material["user_notes"] = request["user_notes"]
        material["selection_digest"] = _json_sha256(material)
        material["adoption_id"] = (
            "compositionadopt_" + material["selection_digest"][:24]
        )
        material["status"] = "FRESH_PROJECT_COMPOSITION_ADOPTED"
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM fresh_project_composition_adoption
                WHERE composition_id = ? AND selection_digest = ?
                """,
                (composition["composition_id"], material["selection_digest"]),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["adoption_json"])
                stored["adopted_at"] = existing["adopted_at"]
                return stored, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO fresh_project_composition_adoption(
                    adoption_id, composition_id, selection_digest,
                    adoption_json, adopted_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    material["adoption_id"],
                    composition["composition_id"],
                    material["selection_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["adopted_at"] = now
        return material, True

    def list_fresh_project_composition_adoptions(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM fresh_project_composition_adoption
                ORDER BY adopted_at DESC, adoption_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        adoptions = []
        for row in rows:
            adoption = json.loads(row["adoption_json"])
            adoption["adopted_at"] = row["adopted_at"]
            adoptions.append(adoption)
        return adoptions

    def create_fresh_project_refinement_request(
        self, value: Any
    ) -> tuple[dict[str, Any], bool]:
        request = normalize_fresh_project_refinement_request(value)
        composition = self.get_fresh_project_composition(request["composition_id"])
        material = build_fresh_project_refinement_request(
            composition,
            purpose=request["purpose"],
        )
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT request_json, created_at
                FROM fresh_project_refinement_request
                WHERE request_digest = ?
                """,
                (material["request_digest"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["request_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO fresh_project_refinement_request(
                    request_id, composition_id, composition_digest, request_digest,
                    request_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    material["request_id"],
                    composition["composition_id"],
                    composition["composition_digest"],
                    material["request_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def get_fresh_project_refinement_request(self, request_id: str) -> dict[str, Any]:
        normalized_id = _identifier(request_id, "request_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT request_json, created_at
                FROM fresh_project_refinement_request
                WHERE request_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "FRESH_PROJECT_REFINEMENT_REQUEST_NOT_FOUND",
                "Universe has no matching Fresh Project refinement request",
                HTTPStatus.NOT_FOUND,
            )
        request = json.loads(row["request_json"])
        request["created_at"] = row["created_at"]
        return request

    def list_fresh_project_refinement_requests(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT request_json, created_at
                FROM fresh_project_refinement_request
                ORDER BY created_at DESC, request_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        requests = []
        for row in rows:
            request = json.loads(row["request_json"])
            request["created_at"] = row["created_at"]
            requests.append(request)
        return requests

    def record_fresh_project_refinement_run(
        self,
        request: dict[str, Any],
        planning: dict[str, Any],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        material = build_fresh_project_refinement_run(
            request,
            planning,
            run_id=run_id,
        )
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO fresh_project_refinement_run(
                    run_id, request_id, run_digest, proposal_id, plan_digest,
                    state, run_json, candidate_id, error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?, NULL, NULL, ?, ?)
                """,
                (
                    material["run_id"],
                    request["request_id"],
                    material["run_digest"],
                    material["proposal_id"],
                    material["plan_digest"],
                    _canonical_json(material),
                    now,
                    now,
                ),
            )
        material["created_at"] = now
        material["updated_at"] = now
        return material

    def get_fresh_project_refinement_run(self, run_id: str) -> dict[str, Any]:
        normalized_id = _identifier(run_id, "run_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_json, state, candidate_id, error_code, created_at, updated_at
                FROM fresh_project_refinement_run
                WHERE run_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "FRESH_PROJECT_REFINEMENT_RUN_NOT_FOUND",
                "Universe has no matching Fresh Project refinement run",
                HTTPStatus.NOT_FOUND,
            )
        run = json.loads(row["run_json"])
        run["state"] = row["state"]
        run["created_at"] = row["created_at"]
        run["updated_at"] = row["updated_at"]
        if row["candidate_id"] is not None:
            run["candidate_id"] = row["candidate_id"]
        if row["error_code"] is not None:
            run["error_code"] = row["error_code"]
        return run

    def list_fresh_project_refinement_runs(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_json, state, candidate_id, error_code, created_at, updated_at
                FROM fresh_project_refinement_run
                ORDER BY created_at DESC, run_id DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        runs = []
        for row in rows:
            run = json.loads(row["run_json"])
            run["state"] = row["state"]
            run["created_at"] = row["created_at"]
            run["updated_at"] = row["updated_at"]
            if row["candidate_id"] is not None:
                run["candidate_id"] = row["candidate_id"]
            if row["error_code"] is not None:
                run["error_code"] = row["error_code"]
            runs.append(run)
        return runs

    def claim_fresh_project_refinement_run(
        self,
        run_id: str,
        approval: dict[str, str],
    ) -> tuple[dict[str, Any], bool]:
        normalized_id = _identifier(run_id, "run_id")
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT run_json, state, candidate_id, created_at, updated_at
                FROM fresh_project_refinement_run
                WHERE run_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise UniverseError(
                    "FRESH_PROJECT_REFINEMENT_RUN_NOT_FOUND",
                    "Universe has no matching Fresh Project refinement run",
                    HTTPStatus.NOT_FOUND,
                )
            run = json.loads(row["run_json"])
            if (
                approval["proposal_id"] != run["proposal_id"]
                or approval["plan_digest"] != run["plan_digest"]
            ):
                raise UniverseError(
                    "FRESH_PROJECT_REFINEMENT_RUN_APPROVAL_MISMATCH",
                    "approval must match the exact stored Planning Frame proposal",
                    HTTPStatus.CONFLICT,
                )
            if row["state"] == "COMPLETED":
                run["state"] = "COMPLETED"
                run["candidate_id"] = row["candidate_id"]
                run["created_at"] = row["created_at"]
                run["updated_at"] = row["updated_at"]
                return run, False
            if row["state"] != "PROPOSED":
                raise UniverseError(
                    "FRESH_PROJECT_REFINEMENT_RUN_STATE_INVALID",
                    f"refinement run is already {row['state']}",
                    HTTPStatus.CONFLICT,
                )
            run["state"] = "RUNNING"
            run["approved_at"] = now
            run["approval"] = {
                "status": "APPROVED",
                "proposal_id": approval["proposal_id"],
                "plan_digest": approval["plan_digest"],
            }
            updated = connection.execute(
                """
                UPDATE fresh_project_refinement_run
                SET state = 'RUNNING', run_json = ?, error_code = NULL, updated_at = ?
                WHERE run_id = ? AND state = 'PROPOSED'
                """,
                (_canonical_json(run), now, normalized_id),
            )
            if updated.rowcount != 1:
                raise UniverseError(
                    "FRESH_PROJECT_REFINEMENT_RUN_STATE_CHANGED",
                    "refinement run changed before execution claim",
                    HTTPStatus.CONFLICT,
                )
        run["created_at"] = row["created_at"]
        run["updated_at"] = now
        return run, True

    def complete_fresh_project_refinement_run(
        self,
        run_id: str,
        *,
        candidate: dict[str, Any],
        result_receipt_ref: str,
    ) -> dict[str, Any]:
        normalized_id = _identifier(run_id, "run_id")
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT run_json, state, candidate_id, created_at, updated_at
                FROM fresh_project_refinement_run
                WHERE run_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise UniverseError(
                    "FRESH_PROJECT_REFINEMENT_RUN_NOT_FOUND",
                    "Universe has no matching Fresh Project refinement run",
                    HTTPStatus.NOT_FOUND,
                )
            if row["state"] == "COMPLETED":
                if row["candidate_id"] != candidate["candidate_id"]:
                    raise UniverseError(
                        "FRESH_PROJECT_REFINEMENT_RUN_RESULT_CONFLICT",
                        "refinement run already completed with another candidate",
                        HTTPStatus.CONFLICT,
                    )
                run = json.loads(row["run_json"])
                run["state"] = "COMPLETED"
                run["candidate_id"] = row["candidate_id"]
                run["created_at"] = row["created_at"]
                run["updated_at"] = row["updated_at"]
                return run
            if row["state"] != "RUNNING":
                raise UniverseError(
                    "FRESH_PROJECT_REFINEMENT_RUN_STATE_INVALID",
                    "refinement run must be RUNNING before completion",
                    HTTPStatus.CONFLICT,
                )
            run = json.loads(row["run_json"])
            run["state"] = "COMPLETED"
            run["candidate_id"] = candidate["candidate_id"]
            run["candidate_digest"] = candidate["candidate_digest"]
            run["result_receipt_ref"] = result_receipt_ref
            run["completed_at"] = now
            connection.execute(
                """
                UPDATE fresh_project_refinement_run
                SET state = 'COMPLETED', run_json = ?, candidate_id = ?,
                    error_code = NULL, updated_at = ?
                WHERE run_id = ? AND state = 'RUNNING'
                """,
                (
                    _canonical_json(run),
                    candidate["candidate_id"],
                    now,
                    normalized_id,
                ),
            )
        run["created_at"] = row["created_at"]
        run["updated_at"] = now
        return run

    def fail_fresh_project_refinement_run(
        self, run_id: str, *, error_code: str
    ) -> dict[str, Any]:
        run = self.get_fresh_project_refinement_run(run_id)
        if run["state"] != "RUNNING":
            return run
        now = utc_now()
        run["state"] = "FAILED"
        run["error_code"] = _required_text(error_code, "error_code")
        run["failed_at"] = now
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE fresh_project_refinement_run
                SET state = 'FAILED', run_json = ?, error_code = ?, updated_at = ?
                WHERE run_id = ? AND state = 'RUNNING'
                """,
                (
                    _canonical_json(run),
                    run["error_code"],
                    now,
                    run["run_id"],
                ),
            )
        run["updated_at"] = now
        return run

    def record_fresh_project_refinement_candidate(
        self, value: Any
    ) -> tuple[dict[str, Any], bool]:
        normalized = normalize_fresh_project_refinement_candidate(value)
        request = self.get_fresh_project_refinement_request(normalized["request_id"])
        if (
            normalized["request_digest"] != request["request_digest"]
            or normalized["composition_id"] != request["composition_id"]
            or normalized["composition_digest"] != request["composition_digest"]
        ):
            raise UniverseError(
                "FRESH_PROJECT_REFINEMENT_REQUEST_MISMATCH",
                "candidate must match its exact prepared refinement request",
                HTTPStatus.CONFLICT,
            )
        composition = self.get_fresh_project_composition(normalized["composition_id"])
        if composition["composition_digest"] != normalized["composition_digest"]:
            raise UniverseError(
                "FRESH_PROJECT_REFINEMENT_COMPOSITION_MISMATCH",
                "candidate does not match the current stored composition digest",
                HTTPStatus.CONFLICT,
            )
        material = build_fresh_project_refinement_candidate(normalized)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT candidate_json, created_at
                FROM fresh_project_refinement_candidate
                WHERE candidate_digest = ?
                """,
                (material["candidate_digest"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["candidate_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO fresh_project_refinement_candidate(
                    candidate_id, request_id, candidate_digest, candidate_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    material["candidate_id"],
                    request["request_id"],
                    material["candidate_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def get_fresh_project_refinement_candidate(
        self, candidate_id: str
    ) -> dict[str, Any]:
        normalized_id = _identifier(candidate_id, "candidate_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT candidate_json, created_at
                FROM fresh_project_refinement_candidate
                WHERE candidate_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "FRESH_PROJECT_REFINEMENT_CANDIDATE_NOT_FOUND",
                "Universe has no matching Fresh Project refinement candidate",
                HTTPStatus.NOT_FOUND,
            )
        candidate = json.loads(row["candidate_json"])
        candidate["created_at"] = row["created_at"]
        return candidate

    def list_fresh_project_refinement_candidates(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT candidate_json, created_at
                FROM fresh_project_refinement_candidate
                ORDER BY created_at DESC, candidate_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        candidates = []
        for row in rows:
            candidate = json.loads(row["candidate_json"])
            candidate["created_at"] = row["created_at"]
            candidates.append(candidate)
        return candidates

    def adopt_fresh_project_refinement(
        self, value: Any
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        selection = normalize_fresh_project_refinement_adoption_request(value)
        candidate = self.get_fresh_project_refinement_candidate(
            selection["candidate_id"]
        )
        request = self.get_fresh_project_refinement_request(candidate["request_id"])
        composition = self.get_fresh_project_composition(candidate["composition_id"])
        refined = build_refined_fresh_project_composition(composition, candidate)
        adoption = build_fresh_project_refinement_adoption(
            candidate,
            refined,
            request,
            user_notes=selection.get("user_notes"),
        )
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT adoption_json, adopted_at, refined_composition_id
                FROM fresh_project_refinement_adoption
                WHERE candidate_id = ? AND selection_digest = ?
                """,
                (candidate["candidate_id"], adoption["selection_digest"]),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["adoption_json"])
                stored["adopted_at"] = existing["adopted_at"]
                return (
                    stored,
                    self.get_fresh_project_composition(
                        existing["refined_composition_id"]
                    ),
                    False,
                )
            now = utc_now()
            stored_refined = connection.execute(
                """
                SELECT composition_json, created_at
                FROM fresh_project_composition
                WHERE composition_digest = ?
                """,
                (refined["composition_digest"],),
            ).fetchone()
            if stored_refined is None:
                connection.execute(
                    """
                    INSERT INTO fresh_project_composition(
                        composition_id, composition_digest, intent_json,
                        composition_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        refined["composition_id"],
                        refined["composition_digest"],
                        _canonical_json(refined["intent"]),
                        _canonical_json(refined),
                        now,
                    ),
                )
                refined["created_at"] = now
            else:
                refined = json.loads(stored_refined["composition_json"])
                refined["created_at"] = stored_refined["created_at"]
            connection.execute(
                """
                INSERT INTO fresh_project_refinement_adoption(
                    adoption_id, candidate_id, refined_composition_id,
                    selection_digest, adoption_json, adopted_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    adoption["adoption_id"],
                    candidate["candidate_id"],
                    refined["composition_id"],
                    adoption["selection_digest"],
                    _canonical_json(adoption),
                    now,
                ),
            )
        adoption["adopted_at"] = now
        return adoption, refined, True

    def list_fresh_project_refinement_adoptions(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM fresh_project_refinement_adoption
                ORDER BY adopted_at DESC, adoption_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        adoptions = []
        for row in rows:
            adoption = json.loads(row["adoption_json"])
            adoption["adopted_at"] = row["adopted_at"]
            adoptions.append(adoption)
        return adoptions

    def _get_fresh_project_composition_adoption(
        self, adoption_id: str
    ) -> dict[str, Any]:
        normalized_id = _identifier(adoption_id, "adoption_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM fresh_project_composition_adoption
                WHERE adoption_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "MASTER_HANDOFF_SOURCE_NOT_FOUND",
                "Fresh Project Composition adoption does not exist",
                HTTPStatus.NOT_FOUND,
            )
        adoption = json.loads(row["adoption_json"])
        adoption["adopted_at"] = row["adopted_at"]
        return adoption

    def _get_skill_plan_adoption(
        self, project_id: str, adoption_id: str
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        normalized_id = _identifier(adoption_id, "adoption_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT adoption_json, adopted_at
                FROM project_skill_plan_adoption
                WHERE project_id = ? AND adoption_id = ?
                """,
                (project["project_id"], normalized_id),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "MASTER_HANDOFF_SOURCE_NOT_FOUND",
                "Skill Plan adoption does not exist for this Project",
                HTTPStatus.NOT_FOUND,
            )
        adoption = json.loads(row["adoption_json"])
        adoption["adopted_at"] = row["adopted_at"]
        return adoption

    def _master_handoff_source(
        self, project_id: str, source: dict[str, str]
    ) -> dict[str, Any]:
        if source["kind"] == "FRESH_PROJECT_COMPOSITION":
            adoption = self._get_fresh_project_composition_adoption(
                source["adoption_id"]
            )
            composition = self.get_fresh_project_composition(adoption["composition_id"])
            return {
                "kind": source["kind"],
                "adoption": adoption,
                "composition": composition,
            }
        adoption = self._get_skill_plan_adoption(project_id, source["adoption_id"])
        return {"kind": source["kind"], "adoption": adoption}

    def create_master_handoff(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_master_handoff_proposal_request(value)
        source = self._master_handoff_source(project["project_id"], request["source"])
        material: dict[str, Any] = {
            "schema": PROJECT_MASTER_HANDOFF_SCHEMA,
            "project_id": project["project_id"],
            "source": source,
            "delivery_state": "PROPOSAL_ONLY",
            "effects": {
                "project_source_write": "NONE",
                "project_runtime_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
                "task_frame": "NONE",
            },
            "next_operation": "USER_APPROVAL_REQUIRED_FOR_MASTER_DELIVERY",
        }
        if "purpose" in request:
            material["purpose"] = request["purpose"]
        material["handoff_digest"] = _json_sha256(material)
        material["handoff_id"] = "handoff_" + material["handoff_digest"][:24]
        material["status"] = "PROJECT_MASTER_HANDOFF_PROPOSAL_READY"
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT handoff_json, delivery_state, room_message_id, created_at, delivered_at
                FROM project_master_handoff
                WHERE project_id = ? AND source_kind = ? AND source_adoption_id = ?
                """,
                (
                    project["project_id"],
                    request["source"]["kind"],
                    request["source"]["adoption_id"],
                ),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["handoff_json"])
                if stored["handoff_digest"] != material["handoff_digest"]:
                    raise UniverseError(
                        "MASTER_HANDOFF_SOURCE_CONFLICT",
                        "the selected source now produces a different handoff",
                        HTTPStatus.CONFLICT,
                    )
                return self._master_handoff_row(existing), False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO project_master_handoff(
                    handoff_id, project_id, source_kind, source_adoption_id,
                    handoff_digest, handoff_json, delivery_state, room_message_id,
                    created_at, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    material["handoff_id"],
                    project["project_id"],
                    request["source"]["kind"],
                    request["source"]["adoption_id"],
                    material["handoff_digest"],
                    _canonical_json(material),
                    "PROPOSAL_ONLY",
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def get_master_handoff(self, project_id: str, handoff_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        normalized_id = _identifier(handoff_id, "handoff_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT handoff_json, delivery_state, room_message_id, created_at, delivered_at
                FROM project_master_handoff
                WHERE project_id = ? AND handoff_id = ?
                """,
                (project["project_id"], normalized_id),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "MASTER_HANDOFF_NOT_FOUND",
                "Project Master handoff proposal does not exist",
                HTTPStatus.NOT_FOUND,
            )
        return self._master_handoff_row(row)

    def list_master_handoffs(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT handoff_json, delivery_state, room_message_id, created_at, delivered_at
                FROM project_master_handoff
                WHERE project_id = ?
                ORDER BY created_at DESC, handoff_id DESC
                LIMIT ?
                """,
                (project["project_id"], max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._master_handoff_row(row) for row in rows]

    def get_skill_plan_master_application(
        self,
        project_id: str,
        handoff_id: str,
    ) -> dict[str, Any] | None:
        project = self.get_project(project_id)
        normalized_handoff_id = _identifier(handoff_id, "handoff_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT application_json, applied_at
                FROM project_skill_plan_master_application
                WHERE project_id = ? AND handoff_id = ?
                """,
                (project["project_id"], normalized_handoff_id),
            ).fetchone()
        if row is None:
            return None
        application = json.loads(row["application_json"])
        application["applied_at"] = row["applied_at"]
        return application

    def record_skill_plan_master_application(
        self,
        *,
        project_id: str,
        handoff: Mapping[str, Any],
        approval: Mapping[str, Any],
        delivery: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        source = handoff.get("source")
        if (
            not isinstance(source, Mapping)
            or source.get("kind") != "SKILL_PLAN"
            or handoff.get("project_id") != project["project_id"]
        ):
            raise UniverseError(
                "PROJECT_SKILL_PLAN_APPLICATION_INVALID",
                "handoff is not an adopted Skill Plan for this Project",
                HTTPStatus.CONFLICT,
            )
        adoption = source.get("adoption")
        if not isinstance(adoption, Mapping):
            raise UniverseError(
                "PROJECT_SKILL_PLAN_APPLICATION_INVALID",
                "handoff has no adopted Skill Plan payload",
                HTTPStatus.CONFLICT,
            )
        host_response = delivery.get("host_response")
        binding_proposal = (
            host_response.get("binding_proposal")
            if isinstance(host_response, Mapping)
            else None
        )
        if (
            not isinstance(host_response, Mapping)
            or host_response.get("status")
            != "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT"
            or host_response.get("project_id") != project["project_id"]
            or host_response.get("handoff_id") != handoff.get("handoff_id")
            or host_response.get("adoption_id") != adoption.get("adoption_id")
            or not isinstance(binding_proposal, Mapping)
            or binding_proposal.get("status") != "PROJECT_SKILL_BINDING_PROPOSAL_READY"
            or binding_proposal.get("project_id") != project["project_id"]
            or binding_proposal.get("handoff_id") != handoff.get("handoff_id")
            or binding_proposal.get("adoption_id") != adoption.get("adoption_id")
            or binding_proposal.get("task_frame_started") is not False
        ):
            raise UniverseError(
                "PROJECT_SKILL_PLAN_APPLICATION_RECEIPT_INVALID",
                "Project Master did not return a matching Skill Plan receipt",
                HTTPStatus.CONFLICT,
            )
        approval_digest = _json_sha256(dict(approval))
        material = {
            "schema": PROJECT_SKILL_PLAN_MASTER_APPLICATION_SCHEMA,
            "project_id": project["project_id"],
            "handoff_id": handoff["handoff_id"],
            "handoff_digest": handoff["handoff_digest"],
            "adoption_id": adoption["adoption_id"],
            "selection_digest": adoption["selection_digest"],
            "approval_digest": approval_digest,
            "delivery": dict(delivery),
            "status": "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
        }
        material["application_digest"] = _json_sha256(material)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT application_json, application_digest, applied_at
                FROM project_skill_plan_master_application
                WHERE handoff_id = ?
                """,
                (handoff["handoff_id"],),
            ).fetchone()
            if existing is not None:
                if existing["application_digest"] != material["application_digest"]:
                    raise UniverseError(
                        "PROJECT_SKILL_PLAN_APPLICATION_CONFLICT",
                        "handoff already has a different Master application",
                        HTTPStatus.CONFLICT,
                    )
                stored = json.loads(existing["application_json"])
                stored["applied_at"] = existing["applied_at"]
                return stored, False
            applied_at = utc_now()
            connection.execute(
                """
                INSERT INTO project_skill_plan_master_application(
                    handoff_id, project_id, adoption_id, approval_digest,
                    application_digest, application_json, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff["handoff_id"],
                    project["project_id"],
                    adoption["adoption_id"],
                    approval_digest,
                    material["application_digest"],
                    _canonical_json(material),
                    applied_at,
                ),
            )
        material["applied_at"] = applied_at
        return material, True

    def create_experience_case(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_experience_case_request(value)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observation_id, project_id, candidate_id, candidate_digest, task_frame_ref,
                       source_ref, observation_digest, skill_binding_digest,
                       skill_id, skill_version, operation_class, context_pack_digest,
                       model_ref, outcome, validation_state, evidence_refs_json,
                       metrics_json, observed_at, recorded_at
                FROM skill_run_observation
                WHERE project_id = ?
                  AND observation_id IN (SELECT value FROM json_each(?))
                """,
                (
                    project["project_id"],
                    _canonical_json(request["observation_ids"]),
                ),
            ).fetchall()
        observations_by_id = {
            row["observation_id"]: self._skill_observation_row(row) for row in rows
        }
        missing = sorted(set(request["observation_ids"]) - set(observations_by_id))
        if missing:
            raise UniverseError(
                "EXPERIENCE_CASE_OBSERVATION_NOT_FOUND",
                "observation_ids are not recorded for this Project: "
                + ", ".join(missing),
                HTTPStatus.CONFLICT,
            )
        observations = [
            observations_by_id[observation_id]
            for observation_id in request["observation_ids"]
        ]
        material: dict[str, Any] = {
            "schema": EXPERIENCE_CASE_SCHEMA,
            "project_id": project["project_id"],
            "observation_ids": request["observation_ids"],
            "observations": observations,
            "case_state": "OBSERVED",
            "causal_state": "NOT_INFERRED",
            "pattern_state": "NOT_EVALUATED",
            "effects": {
                "project_source_write": "NONE",
                "project_runtime_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
            },
            "next_operation": "USER_REVIEW_OR_EXPERIENCE_MATCH",
        }
        if "title" in request:
            material["title"] = request["title"]
        material["case_digest"] = _json_sha256(material)
        material["case_id"] = "case_" + material["case_digest"][:24]
        material["status"] = "EXPERIENCE_CASE_RECORDED"
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT case_json, created_at
                FROM experience_case
                WHERE project_id = ? AND case_digest = ?
                """,
                (project["project_id"], material["case_digest"]),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["case_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO experience_case(
                    case_id, project_id, case_digest, case_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    material["case_id"],
                    project["project_id"],
                    material["case_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO experience_case_observation(case_id, observation_id)
                VALUES (?, ?)
                """,
                [
                    (material["case_id"], observation_id)
                    for observation_id in request["observation_ids"]
                ],
            )
        material["created_at"] = now
        return material, True

    def create_experience_cases_from_unlinked_observations(
        self, project_id: str, value: Any = None
    ) -> dict[str, Any]:
        """Create one Experience Case per observation not already in a case."""

        project = self.get_project(project_id)
        body = value if isinstance(value, dict) else {}
        limit = body.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise UniverseError(
                "EXPERIENCE_CASE_REQUEST_INVALID",
                "limit must be an integer from 1 through 200",
            )
        observations = self.list_skill_observations(project["project_id"], limit=500)
        linked_observation_ids: set[str] = set()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observation_id
                FROM experience_case_observation
                WHERE case_id IN (
                    SELECT case_id FROM experience_case WHERE project_id = ?
                )
                """,
                (project["project_id"],),
            ).fetchall()
            linked_observation_ids = {row["observation_id"] for row in rows}
        created_cases: list[dict[str, Any]] = []
        reused_cases: list[dict[str, Any]] = []
        for observation in observations:
            observation_id = observation["observation_id"]
            if observation_id in linked_observation_ids:
                continue
            if len(created_cases) + len(reused_cases) >= limit:
                break
            skill = observation.get("skill") or {}
            title = (
                f"Case for {skill.get('skill_id') or observation_id}"
            )
            case, created = self.create_experience_case(
                project["project_id"],
                {
                    "observation_ids": [observation_id],
                    "title": title,
                },
            )
            if created:
                created_cases.append(case)
            else:
                reused_cases.append(case)
        return {
            "schema": "universe.experience-cases-from-observations.v1",
            "status": "EXPERIENCE_CASES_FROM_OBSERVATIONS_COMPLETED",
            "project_id": project["project_id"],
            "created_count": len(created_cases),
            "reused_count": len(reused_cases),
            "created": created_cases,
            "reused": reused_cases,
            "effects": {
                "project_source_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
                "causal_inference": "NONE",
            },
            "next_operation": "USER_REVIEW_OR_EXPERIENCE_MATCH",
        }

    def auto_experience_pattern_proposals(
        self, project_id: str, value: Any = None
    ) -> dict[str, Any]:
        """Attempt pattern proposals for each case with local match support."""

        project = self.get_project(project_id)
        body = value if isinstance(value, dict) else {}
        minimum_support = body.get("minimum_support", 2)
        if (
            isinstance(minimum_support, bool)
            or not isinstance(minimum_support, int)
            or not 2 <= minimum_support <= 20
        ):
            raise UniverseError(
                "EXPERIENCE_PATTERN_REQUEST_INVALID",
                "minimum_support must be an integer from 2 through 20",
            )
        cases = self.list_experience_cases(project["project_id"], limit=200)
        recorded: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for case in cases:
            try:
                proposal, created = self.create_experience_pattern_proposal(
                    project["project_id"],
                    {
                        "case_id": case["case_id"],
                        "minimum_support": minimum_support,
                    },
                )
                recorded.append(
                    {
                        "case_id": case["case_id"],
                        "proposal_id": proposal.get("proposal_id"),
                        "created": created,
                        "support_case_count": proposal.get("support_case_count"),
                    }
                )
            except UniverseError as error:
                skipped.append(
                    {
                        "case_id": case["case_id"],
                        "error_code": error.code,
                        "message": error.message,
                    }
                )
        return {
            "schema": "universe.experience-pattern-auto.v1",
            "status": "EXPERIENCE_PATTERN_AUTO_COMPLETED",
            "project_id": project["project_id"],
            "recorded_count": len(recorded),
            "skipped_count": len(skipped),
            "recorded": recorded,
            "skipped": skipped,
            "effects": {
                "career_governance_write": "NONE",
                "promotion_state": "PROPOSAL_ONLY",
                "authority": "NONE",
            },
            "next_operation": "USER_REVIEW_OR_PATTERN_ADOPTION",
        }

    def list_experience_cases(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT case_json, created_at
                FROM experience_case
                WHERE project_id = ?
                ORDER BY created_at DESC, case_id DESC
                LIMIT ?
                """,
                (project["project_id"], max(1, min(int(limit), 500))),
            ).fetchall()
        cases = []
        for row in rows:
            case = json.loads(row["case_json"])
            case["created_at"] = row["created_at"]
            cases.append(case)
        return cases

    def get_experience_case(self, project_id: str, case_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        normalized_id = _identifier(case_id, "case_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT case_json, created_at
                FROM experience_case
                WHERE project_id = ? AND case_id = ?
                """,
                (project["project_id"], normalized_id),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "EXPERIENCE_CASE_NOT_FOUND",
                "Experience Case does not exist for this Project",
                HTTPStatus.NOT_FOUND,
            )
        case = json.loads(row["case_json"])
        case["created_at"] = row["created_at"]
        return case

    def match_experience_case(self, project_id: str, value: Any) -> dict[str, Any]:
        project = self.get_project(project_id)
        request = normalize_experience_match_request(value)
        subject = self.get_experience_case(project["project_id"], request["case_id"])
        candidates = [
            case
            for case in self.list_experience_cases(project["project_id"], limit=500)
            if case["case_id"] != subject["case_id"]
        ]
        subject_observations = subject["observations"]
        subject_bindings = {
            observation["skill_binding_digest"] for observation in subject_observations
        }
        subject_skills = {
            (
                observation["skill"]["skill_id"],
                observation["skill"]["skill_version"],
                observation["skill"]["operation_class"],
            )
            for observation in subject_observations
        }
        subject_outcomes = {
            observation["outcome"] for observation in subject_observations
        }
        subject_validation = {
            observation["validation_state"] for observation in subject_observations
        }
        matches = []
        for candidate in candidates:
            observations = candidate["observations"]
            bindings = {item["skill_binding_digest"] for item in observations}
            skills = {
                (
                    item["skill"]["skill_id"],
                    item["skill"]["skill_version"],
                    item["skill"]["operation_class"],
                )
                for item in observations
            }
            outcomes = {item["outcome"] for item in observations}
            validation_states = {item["validation_state"] for item in observations}
            shared_bindings = sorted(subject_bindings & bindings)
            shared_skills = sorted(subject_skills & skills)
            shared_outcomes = sorted(subject_outcomes & outcomes)
            shared_validation = sorted(subject_validation & validation_states)
            observed_dimension_count = sum(
                bool(value)
                for value in (
                    shared_bindings,
                    shared_skills,
                    shared_outcomes,
                    shared_validation,
                )
            )
            if not observed_dimension_count:
                continue
            matches.append(
                {
                    "case_id": candidate["case_id"],
                    "title": candidate.get("title"),
                    "relation": "OBSERVED_SIMILARITY",
                    "observed_dimension_count": observed_dimension_count,
                    "shared_skill_binding_digests": shared_bindings,
                    "shared_skills": [
                        {
                            "skill_id": skill_id,
                            "skill_version": skill_version,
                            "operation_class": operation_class,
                        }
                        for skill_id, skill_version, operation_class in shared_skills
                    ],
                    "shared_outcomes": shared_outcomes,
                    "shared_validation_states": shared_validation,
                    "causal_state": "NOT_INFERRED",
                    "pattern_state": "NOT_EVALUATED",
                }
            )
        matches.sort(
            key=lambda item: (
                -item["observed_dimension_count"],
                item["case_id"],
            )
        )
        return {
            "schema": "universe.experience-case-match.v1",
            "status": "EXPERIENCE_CASE_MATCHES_COLLECTED",
            "project_id": project["project_id"],
            "subject_case_id": subject["case_id"],
            "matches": matches[: request["limit"]],
            "match_scope": "PROJECT_LOCAL_OBSERVED_CASES",
            "effects": {
                "project_source_write": "NONE",
                "project_runtime_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
            },
            "next_operation": "USER_REVIEW_OR_PATTERN_PROPOSAL",
        }

    def create_experience_pattern_proposal(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        request = normalize_experience_pattern_proposal_request(value)
        subject = self.get_experience_case(project["project_id"], request["case_id"])
        match_result = self.match_experience_case(
            project["project_id"],
            {"case_id": subject["case_id"], "limit": 100},
        )
        supporting_ids = [subject["case_id"]] + [
            match["case_id"] for match in match_result["matches"]
        ]
        if len(supporting_ids) < request["minimum_support"]:
            raise UniverseError(
                "EXPERIENCE_PATTERN_INSUFFICIENT_SUPPORT",
                "not enough observed Cases share a supported similarity dimension",
                HTTPStatus.CONFLICT,
            )
        by_id = {
            case["case_id"]: case
            for case in self.list_experience_cases(project["project_id"], limit=500)
        }
        supporting_cases = [by_id[case_id] for case_id in supporting_ids]

        common_bindings = sorted(
            set.intersection(
                *[
                    {
                        observation["skill_binding_digest"]
                        for observation in case["observations"]
                    }
                    for case in supporting_cases
                ]
            )
        )
        common_outcomes = sorted(
            set.intersection(
                *[
                    {observation["outcome"] for observation in case["observations"]}
                    for case in supporting_cases
                ]
            )
        )
        common_validation_states = sorted(
            set.intersection(
                *[
                    {
                        observation["validation_state"]
                        for observation in case["observations"]
                    }
                    for case in supporting_cases
                ]
            )
        )
        skill_signatures = [
            {
                _canonical_json(
                    {
                        "skill_id": observation["skill"]["skill_id"],
                        "skill_version": observation["skill"]["skill_version"],
                        "operation_class": observation["skill"]["operation_class"],
                    }
                )
                for observation in case["observations"]
            }
            for case in supporting_cases
        ]
        common_skills = (
            set.intersection(*skill_signatures) if skill_signatures else set()
        )
        if not any(
            (common_bindings, common_skills, common_outcomes, common_validation_states)
        ):
            raise UniverseError(
                "EXPERIENCE_PATTERN_SIGNATURE_EMPTY",
                "supporting Cases do not have an exact observed signature in common",
                HTTPStatus.CONFLICT,
            )
        material = {
            "schema": EXPERIENCE_PATTERN_PROPOSAL_SCHEMA,
            "project_id": project["project_id"],
            "subject_case_id": subject["case_id"],
            "support_case_ids": sorted(supporting_ids),
            "support_case_count": len(supporting_ids),
            "observed_signature": {
                "skill_binding_digests": common_bindings,
                "skills": [
                    json.loads(signature) for signature in sorted(common_skills)
                ],
                "outcomes": common_outcomes,
                "validation_states": common_validation_states,
            },
            "causal_state": "NOT_INFERRED",
            "predictive_state": "NOT_EVALUATED",
            "promotion_state": "PROPOSAL_ONLY",
            "effects": {
                "career_governance_write": "NONE",
                "project_source_write": "NONE",
                "project_runtime_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
            },
            "next_operation": "USER_REVIEW_OR_PATTERN_ADOPTION",
        }
        material["proposal_digest"] = _json_sha256(material)
        material["proposal_id"] = "patternproposal_" + material["proposal_digest"][:24]
        material["status"] = "EXPERIENCE_PATTERN_PROPOSAL_READY"
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM experience_pattern_proposal
                WHERE project_id = ? AND proposal_digest = ?
                """,
                (project["project_id"], material["proposal_digest"]),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["proposal_json"])
                stored["created_at"] = existing["created_at"]
                return stored, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO experience_pattern_proposal(
                    proposal_id, project_id, proposal_digest, proposal_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    material["proposal_id"],
                    project["project_id"],
                    material["proposal_digest"],
                    _canonical_json(material),
                    now,
                ),
            )
        material["created_at"] = now
        return material, True

    def list_experience_pattern_proposals(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM experience_pattern_proposal
                WHERE project_id = ?
                ORDER BY created_at DESC, proposal_id DESC
                LIMIT ?
                """,
                (project["project_id"], max(1, min(int(limit), 500))),
            ).fetchall()
        proposals = []
        for row in rows:
            proposal = json.loads(row["proposal_json"])
            proposal["created_at"] = row["created_at"]
            proposals.append(proposal)
        return proposals

    def create_project_memory(
        self, project_id: str, value: Any
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        try:
            request = normalize_memory_create(value)
        except MemoryError as error:
            raise UniverseError(error.code, error.message) from error
        now = utc_now()
        material = {
            "schema": MEMORY_SCHEMA,
            "project_id": project["project_id"],
            "title": request["title"],
            "body": request["body"],
            "state": request["state"],
            "link_state": request["link_state"],
            "node_ref": request["node_ref"],
            "graph": request["graph"],
            "origin_ref": request["origin_ref"],
            "effects": {
                "seed_write": "NONE",
                "candidate": "NONE",
                "queue_publication": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
            },
            "next_operation": "USER_REVIEW_OR_NODE_LINK",
        }
        material["memory_digest"] = _json_sha256(material)
        material["memory_id"] = "memory_" + material["memory_digest"][:24]
        material["created_at"] = now
        material["updated_at"] = now
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO project_memory(
                    memory_id, project_id, title, body, state, link_state,
                    node_ref, graph, origin_ref, memory_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    material["memory_id"],
                    project["project_id"],
                    material["title"],
                    material["body"],
                    material["state"],
                    material["link_state"],
                    material["node_ref"],
                    material["graph"],
                    material["origin_ref"],
                    _canonical_json(material),
                    now,
                    now,
                ),
            )
        return material

    def list_project_memories(
        self,
        project_id: str,
        *,
        link_state: str | None = None,
        node_ref: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        clauses = ["project_id = ?"]
        params: list[Any] = [project["project_id"]]
        if link_state:
            clauses.append("link_state = ?")
            params.append(link_state.upper())
        if node_ref:
            clauses.append("node_ref = ?")
            params.append(node_ref)
        if query:
            clauses.append("(title LIKE ? OR body LIKE ?)")
            needle = f"%{query}%"
            params.extend([needle, needle])
        params.append(max(1, min(int(limit), 500)))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT memory_json, created_at, updated_at
                FROM project_memory
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC, memory_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        items = []
        for row in rows:
            item = json.loads(row["memory_json"])
            item["created_at"] = row["created_at"]
            item["updated_at"] = row["updated_at"]
            items.append(item)
        return items

    def get_project_memory(self, project_id: str, memory_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        normalized = _identifier(memory_id, "memory_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT memory_json, created_at, updated_at
                FROM project_memory
                WHERE project_id = ? AND memory_id = ?
                """,
                (project["project_id"], normalized),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "MEMORY_NOT_FOUND",
                "project memory does not exist",
                HTTPStatus.NOT_FOUND,
            )
        item = json.loads(row["memory_json"])
        item["created_at"] = row["created_at"]
        item["updated_at"] = row["updated_at"]
        return item

    def link_project_memory(
        self, project_id: str, memory_id: str, value: Any
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        try:
            request = normalize_memory_link(value)
        except MemoryError as error:
            raise UniverseError(error.code, error.message) from error
        current = self.get_project_memory(project["project_id"], memory_id)
        now = utc_now()
        current["node_ref"] = request["node_ref"]
        current["graph"] = request["graph"]
        current["link_state"] = request["link_state"]
        current["updated_at"] = now
        current["next_operation"] = "USER_REVIEW_ONLY"
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE project_memory
                SET node_ref = ?, graph = ?, link_state = ?,
                    memory_json = ?, updated_at = ?
                WHERE project_id = ? AND memory_id = ?
                """,
                (
                    current["node_ref"],
                    current["graph"],
                    current["link_state"],
                    _canonical_json(current),
                    now,
                    project["project_id"],
                    current["memory_id"],
                ),
            )
        return current

    def propose_memory_links(
        self, project_id: str, *, limit: int = 20
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        unlinked = self.list_project_memories(
            project["project_id"], link_state="UNLINKED", limit=200
        )
        nodes: list[dict[str, Any]] = []
        try:
            projection = self.get_project_projection(project["project_id"])
            for item in projection.get("nodes") or []:
                nodes.append(item if isinstance(item, dict) else {})
        except UniverseError:
            nodes = []
        proposals = propose_node_links(memories=unlinked, nodes=nodes, limit=limit)
        return {
            "schema": "universe.project-memory-link-proposals.v1",
            "project_id": project["project_id"],
            "proposals": proposals,
            "effects": {
                "seed_write": "NONE",
                "candidate": "NONE",
                "authority": "NONE",
            },
            "next_operation": "USER_CONFIRM_LINK",
        }

    def maintain_project_memories(
        self, project_id: str, value: Any = None
    ) -> dict[str, Any]:
        """Memory maintenance batch with scorer modes.

        DETERMINISTIC / HEURISTIC run locally. LLM accepts validated
        ``llm_proposals`` from an offline nightly job; live model calls remain
        outside this service. AUTO prefers LLM proposals when present, else
        HEURISTIC. Apply only writes PROPOSED (never auto-LINKED / Seed write).
        """

        project = self.get_project(project_id)
        try:
            request = normalize_memory_maintain(value)
        except MemoryError as error:
            raise UniverseError(error.code, error.message) from error

        unlinked = self.list_project_memories(
            project["project_id"], link_state="UNLINKED", limit=200
        )
        nodes: list[dict[str, Any]] = []
        try:
            projection = self.get_project_projection(project["project_id"])
            for item in projection.get("nodes") or []:
                if isinstance(item, dict):
                    nodes.append(item)
        except UniverseError:
            nodes = []

        scorer = request["scorer"]
        llm_status = "NOT_RUN"
        batch_kind = "DETERMINISTIC_TOKEN_OVERLAP"
        proposals: list[dict[str, Any]] = []

        if scorer in {"LLM", "AUTO"} and request.get("llm_proposals"):
            accepted = filter_llm_proposals(
                llm_proposals=request["llm_proposals"] or [],
                memories=unlinked,
                nodes=nodes,
            )
            if accepted:
                proposals = accepted
                batch_kind = "LLM_BATCH"
                llm_status = "APPLIED_EXTERNAL_PROPOSALS"
            elif scorer == "LLM":
                llm_status = "UNAVAILABLE_FALLBACK_DETERMINISTIC"
                proposals = propose_node_links(
                    memories=unlinked, nodes=nodes, limit=request["limit"]
                )
                batch_kind = "DETERMINISTIC_TOKEN_OVERLAP"
            else:
                llm_status = "UNAVAILABLE_FALLBACK_HEURISTIC"
                proposals = propose_node_links_heuristic(
                    memories=unlinked, nodes=nodes, limit=request["limit"]
                )
                batch_kind = "HEURISTIC_WEIGHTED"
        elif scorer == "LLM":
            # No external proposals and no in-process model: fall back.
            llm_status = "UNAVAILABLE_FALLBACK_DETERMINISTIC"
            proposals = propose_node_links(
                memories=unlinked, nodes=nodes, limit=request["limit"]
            )
            batch_kind = "DETERMINISTIC_TOKEN_OVERLAP"
        elif scorer in {"HEURISTIC", "AUTO"}:
            proposals = propose_node_links_heuristic(
                memories=unlinked, nodes=nodes, limit=request["limit"]
            )
            batch_kind = "HEURISTIC_WEIGHTED"
            if scorer == "AUTO":
                llm_status = "NOT_RUN_HEURISTIC_DEFAULT"
        else:
            proposals = propose_node_links(
                memories=unlinked, nodes=nodes, limit=request["limit"]
            )
            batch_kind = "DETERMINISTIC_TOKEN_OVERLAP"

        selected = select_best_proposals(
            proposals,
            per_memory=request["per_memory"],
            min_score=request["min_score"],
        )
        applied: list[dict[str, Any]] = []
        if request["apply_proposals"]:
            for proposal in selected:
                memory = self.link_project_memory(
                    project["project_id"],
                    str(proposal["memory_id"]),
                    {
                        "node_ref": proposal["node_ref"],
                        "graph": proposal["graph"],
                        "link_state": "PROPOSED",
                    },
                )
                applied.append(
                    {
                        "memory_id": memory["memory_id"],
                        "node_ref": memory.get("node_ref"),
                        "graph": memory.get("graph"),
                        "link_state": memory.get("link_state"),
                        "score": proposal.get("score"),
                        "reason": proposal.get("reason"),
                        "proposal_kind": proposal.get("proposal_kind"),
                    }
                )
        return {
            "schema": "universe.project-memory-maintain.v1",
            "status": "PROJECT_MEMORY_MAINTAIN_COMPLETED",
            "project_id": project["project_id"],
            "batch_kind": batch_kind,
            "scorer": scorer,
            "llm_batch": llm_status,
            "apply_proposals": request["apply_proposals"],
            "proposal_count": len(proposals),
            "selected_count": len(selected),
            "applied_count": len(applied),
            "proposals": proposals,
            "selected": selected,
            "applied": applied,
            "effects": {
                "seed_write": "NONE",
                "candidate": "NONE",
                "authority": "NONE",
                "auto_linked": False,
            },
            "next_operation": (
                "USER_CONFIRM_LINK" if applied else "USER_REVIEW_PROPOSALS"
            ),
        }

    def compare_skill_bench(
        self, *, group_by: str = "skill", limit: int = 50
    ) -> dict[str, Any]:
        """Compare Skill observations by skill, model, provider, or project."""

        normalized = str(group_by or "skill").strip().lower()
        if normalized not in {"skill", "model", "provider", "project"}:
            raise UniverseError(
                "BENCH_COMPARE_GROUP_INVALID",
                "group_by must be skill, model, provider, or project",
            )
        bounded = max(1, min(int(limit), 200))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM skill_run_observation
                ORDER BY observed_at DESC, observation_id DESC
                """
            ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = self._skill_observation_row(row)
            skill = item["skill"]
            provider_ref = provider_ref_from_model_ref(item["model_ref"])
            if normalized == "skill":
                key = f"{skill['skill_id']}@{skill['skill_version']}"
                label = {
                    "skill_id": skill["skill_id"],
                    "skill_version": skill["skill_version"],
                    "operation_class": skill["operation_class"],
                }
            elif normalized == "model":
                key = str(item["model_ref"] or "UNKNOWN")
                label = {"model_ref": item["model_ref"], "provider_ref": provider_ref}
            elif normalized == "provider":
                key = provider_ref
                label = {"provider_ref": provider_ref}
            else:
                key = str(item.get("project_id") or "UNKNOWN")
                label = {"project_id": item.get("project_id")}
            group = groups.setdefault(
                key,
                {
                    "group_key": key,
                    "group_by": normalized,
                    "label": label,
                    "observation_count": 0,
                    "outcomes": {state: 0 for state in sorted(SKILL_OUTCOMES)},
                    "validation_states": {
                        state: 0 for state in sorted(SKILL_VALIDATION_STATES)
                    },
                    "skills": set(),
                    "models": set(),
                    "providers": set(),
                    "projects": set(),
                    "metric_totals": {},
                    "duration_samples": [],
                },
            )
            group["observation_count"] += 1
            group["outcomes"][item["outcome"]] += 1
            group["validation_states"][item["validation_state"]] += 1
            group["skills"].add(
                f"{skill['skill_id']}@{skill['skill_version']}"
            )
            group["models"].add(str(item["model_ref"] or "UNKNOWN"))
            group["providers"].add(provider_ref)
            group["projects"].add(str(item.get("project_id") or "UNKNOWN"))
            for metric_key, metric_value in item["metrics"].items():
                if isinstance(metric_value, (int, float)) and not isinstance(
                    metric_value, bool
                ):
                    group["metric_totals"][metric_key] = (
                        group["metric_totals"].get(metric_key, 0) + metric_value
                    )
                    if metric_key == "duration_ms":
                        group["duration_samples"].append(float(metric_value))
        comparisons: list[dict[str, Any]] = []
        for group in groups.values():
            succeeded = int(group["outcomes"].get("SUCCEEDED") or 0)
            failed = int(group["outcomes"].get("FAILED") or 0)
            decided = succeeded + failed
            success_rate = (
                round(succeeded / decided, 4) if decided else None
            )
            samples = group.pop("duration_samples")
            avg_duration = (
                round(sum(samples) / len(samples), 2) if samples else None
            )
            comparisons.append(
                {
                    "group_key": group["group_key"],
                    "group_by": group["group_by"],
                    "label": group["label"],
                    "observation_count": group["observation_count"],
                    "outcomes": group["outcomes"],
                    "validation_states": group["validation_states"],
                    "success_rate": success_rate,
                    "avg_duration_ms": avg_duration,
                    "metric_totals": group["metric_totals"],
                    "distinct_skills": len(group["skills"]),
                    "distinct_models": len(group["models"]),
                    "distinct_providers": len(group["providers"]),
                    "distinct_projects": len(group["projects"]),
                }
            )
        comparisons.sort(
            key=lambda item: (
                -(item["success_rate"] if item["success_rate"] is not None else -1),
                -item["observation_count"],
                item["group_key"],
            )
        )
        return {
            "schema": "universe.skill-bench-compare.v1",
            "status": "SKILL_BENCH_COMPARE_COLLECTED",
            "group_by": normalized,
            "comparisons": comparisons[:bounded],
            "effects": {
                "authority": "NONE",
                "execution_assignment": "NONE",
                "career_promotion": "NONE",
            },
            "next_operation": "USER_REVIEW_ONLY",
        }

    def queue_career_promotion_candidate(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        """Queue a Career candidate derived only from a recorded pattern proposal."""

        project = self.get_project(project_id)
        request = _exact_object_fields(
            value,
            field="career_promotion_request",
            required=frozenset({"pattern_proposal_id"}),
        )
        proposal_id = _identifier(request["pattern_proposal_id"], "pattern_proposal_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM experience_pattern_proposal
                WHERE project_id = ? AND proposal_id = ?
                """,
                (project["project_id"], proposal_id),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "EXPERIENCE_PATTERN_PROPOSAL_NOT_FOUND",
                "Career promotion requires a recorded Universe pattern proposal",
                HTTPStatus.NOT_FOUND,
            )
        pattern = json.loads(row["proposal_json"])
        if pattern.get("promotion_state") != "PROPOSAL_ONLY":
            raise UniverseError(
                "EXPERIENCE_PATTERN_PROMOTION_STATE_INVALID",
                "Career candidate source must remain a proposal-only pattern",
                HTTPStatus.CONFLICT,
            )
        universe_id = self.identity()["universe_id"]
        material = {
            "schema": CAREER_PROMOTION_CANDIDATE_SCHEMA,
            "universe_ref": f"universe://{universe_id}",
            "project_ref": f"project://{project['project_id']}",
            "promotion_kind": "OBSERVED_EXPERIENCE_PATTERN",
            "source": {
                "pattern_proposal_id": pattern["proposal_id"],
                "pattern_proposal_digest": pattern["proposal_digest"],
                "support_case_ids": pattern["support_case_ids"],
                "support_case_count": pattern["support_case_count"],
            },
            "observed_signature": pattern["observed_signature"],
            "redaction_state": "REDACTED",
            "evidence_state": "OBSERVED_AGGREGATE",
            "promotion_state": "CANDIDATE_ONLY",
            "effects": {
                "career_governance_write": "NONE",
                "project_source_write": "NONE",
                "project_runtime_write": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
            },
            "next_operation": "CAREER_CARRIER_INTAKE",
        }
        candidate_digest = _json_sha256(material)
        candidate = {
            **material,
            "candidate_digest": candidate_digest,
            "candidate_id": "careerpromotion_" + candidate_digest[:24],
        }
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT queue_id, candidate_json, queued_at
                FROM career_promotion_queue
                WHERE project_id = ? AND source_proposal_id = ?
                """,
                (project["project_id"], proposal_id),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["candidate_json"])
                if stored["candidate_digest"] != candidate_digest:
                    raise UniverseError(
                        "CAREER_PROMOTION_SOURCE_CONFLICT",
                        "pattern proposal already maps to another Career candidate",
                        HTTPStatus.CONFLICT,
                    )
                return self._career_promotion_queue_row(existing, stored), False
            queue_id = "career_queue_" + candidate_digest[:24]
            connection.execute(
                """
                INSERT INTO career_promotion_queue(
                    queue_id, project_id, source_proposal_id, candidate_digest,
                    candidate_json, status, queued_at
                ) VALUES (?, ?, ?, ?, ?, 'QUEUED', ?)
                """,
                (
                    queue_id,
                    project["project_id"],
                    proposal_id,
                    candidate_digest,
                    _canonical_json(candidate),
                    now,
                ),
            )
        return {
            "schema": CAREER_PROMOTION_QUEUE_SCHEMA,
            "queue_id": queue_id,
            "candidate": candidate,
            "status": "QUEUED",
            "queued_at": now,
        }, True

    def list_career_promotion_queue(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT queue_id, candidate_json, queued_at
                FROM career_promotion_queue
                ORDER BY queued_at, queue_id
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            self._career_promotion_queue_row(row, json.loads(row["candidate_json"]))
            for row in rows
        ]

    @staticmethod
    def _career_promotion_queue_row(
        row: sqlite3.Row, candidate: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "schema": CAREER_PROMOTION_QUEUE_SCHEMA,
            "queue_id": row["queue_id"],
            "candidate": candidate,
            "status": "QUEUED",
            "queued_at": row["queued_at"],
        }

    def deliver_master_handoff(
        self, project_id: str, handoff_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        normalize_master_handoff_delivery_request(value)
        handoff = self.get_master_handoff(project_id, handoff_id)
        if handoff["delivery_state"] != "PROPOSAL_ONLY":
            return handoff, False
        message, _ = self.send_room_message(
            handoff["project_id"],
            {
                "kind": "TASK_DRAFT",
                "sender": "UNIVERSE_CONDUCTOR",
                "idempotency_key": "master-handoff-" + handoff["handoff_id"],
                "body": _canonical_json(
                    {
                        "schema": PROJECT_MASTER_HANDOFF_SCHEMA,
                        "handoff_id": handoff["handoff_id"],
                        "handoff_digest": handoff["handoff_digest"],
                        "source": handoff["source"],
                        "purpose": handoff.get("purpose", "PROJECT_MASTER_REVIEW"),
                        "next_operation": "PROJECT_MASTER_REVIEW_OR_TASK_FRAME_PROPOSAL",
                    }
                ),
            },
        )
        delivered_at = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE project_master_handoff
                SET delivery_state = ?, room_message_id = ?, delivered_at = ?
                WHERE project_id = ? AND handoff_id = ? AND delivery_state = 'PROPOSAL_ONLY'
                """,
                (
                    message["delivery_state"],
                    message["message_id"],
                    delivered_at,
                    handoff["project_id"],
                    handoff["handoff_id"],
                ),
            )
        if cursor.rowcount != 1:
            return self.get_master_handoff(project_id, handoff_id), False
        return self.get_master_handoff(project_id, handoff_id), True

    @staticmethod
    def _master_handoff_row(row: sqlite3.Row) -> dict[str, Any]:
        handoff = json.loads(row["handoff_json"])
        handoff["delivery_state"] = row["delivery_state"]
        handoff["room_message_id"] = row["room_message_id"]
        handoff["created_at"] = row["created_at"]
        handoff["delivered_at"] = row["delivered_at"]
        return handoff

    @staticmethod
    def _skill_observation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": SKILL_RUN_OBSERVATION_SCHEMA,
            "observation_id": row["observation_id"],
            "project_id": row["project_id"],
            "candidate_id": row["candidate_id"],
            "candidate_digest": row["candidate_digest"],
            "task_frame_ref": row["task_frame_ref"],
            "source_ref": row["source_ref"],
            "observation_digest": row["observation_digest"],
            "skill_binding_digest": row["skill_binding_digest"],
            "skill": {
                "skill_id": row["skill_id"],
                "skill_version": row["skill_version"],
                "operation_class": row["operation_class"],
                "context_pack_digest": row["context_pack_digest"],
            },
            "model_ref": row["model_ref"],
            "outcome": row["outcome"],
            "validation_state": row["validation_state"],
            "evidence_refs": json.loads(row["evidence_refs_json"]),
            "metrics": json.loads(row["metrics_json"]),
            "observed_at": row["observed_at"],
            "recorded_at": row["recorded_at"],
        }

    def register_master_bridge(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        bridge = normalize_master_bridge(project["project_id"], value)
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT bridge_id FROM project_master_bridge WHERE project_id = ?",
                (project["project_id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO project_master_bridge(
                    project_id, bridge_id, endpoint, credential_env,
                    master_session_ref, binding_evidence_ref, status,
                    registered_at, updated_at, last_delivery_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(project_id) DO UPDATE SET
                    bridge_id = excluded.bridge_id,
                    endpoint = excluded.endpoint,
                    credential_env = excluded.credential_env,
                    master_session_ref = excluded.master_session_ref,
                    binding_evidence_ref = excluded.binding_evidence_ref,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_delivery_at = NULL
                """,
                (
                    project["project_id"],
                    bridge["bridge_id"],
                    bridge["endpoint"],
                    bridge["credential_env"],
                    bridge["master_session_ref"],
                    bridge["binding_evidence_ref"],
                    bridge["status"],
                    now,
                    now,
                ),
            )
        return self.get_master_bridge(project["project_id"]), existing is None

    def get_master_bridge(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT project_id, bridge_id, endpoint, credential_env,
                       master_session_ref, binding_evidence_ref, status,
                       registered_at, updated_at, last_delivery_at
                FROM project_master_bridge
                WHERE project_id = ?
                """,
                (project["project_id"],),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "MASTER_BRIDGE_NOT_REGISTERED",
                "project has no registered Project Master Bridge",
                HTTPStatus.NOT_FOUND,
            )
        return self._master_bridge_row(row)

    def create_room_message(
        self,
        project_id: str,
        value: Any,
        *,
        delivery_state: str = "RECORDED",
        delivery: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        message = normalize_room_message(project["project_id"], value)
        delivery_json = _canonical_json(delivery or {})
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT message_json, delivery_state, delivery_json, created_at, updated_at
                FROM project_room_message
                WHERE project_id = ? AND idempotency_key = ?
                """,
                (project["project_id"], message["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["message_json"])
                if stored["content_digest"] != message["content_digest"]:
                    raise UniverseError(
                        "ROOM_MESSAGE_IDEMPOTENCY_CONFLICT",
                        "idempotency_key already refers to another message",
                        HTTPStatus.CONFLICT,
                    )
                stored["delivery_state"] = existing["delivery_state"]
                stored["delivery"] = json.loads(existing["delivery_json"])
                stored["created_at"] = existing["created_at"]
                stored["updated_at"] = existing["updated_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO project_room_message(
                    message_id, project_id, idempotency_key, message_json,
                    delivery_state, delivery_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["message_id"],
                    message["project_id"],
                    message["idempotency_key"],
                    _canonical_json(message),
                    delivery_state,
                    delivery_json,
                    message["created_at"],
                    message["created_at"],
                ),
            )
        message["delivery_state"] = delivery_state
        message["delivery"] = delivery or {}
        message["updated_at"] = message["created_at"]
        return message, True

    def create_conductor_room_message(self, value: Any) -> tuple[dict[str, Any], bool]:
        message = normalize_conductor_room_message(value)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT message_json, created_at
                FROM conductor_room_message
                WHERE idempotency_key = ?
                """,
                (message["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                stored = json.loads(existing["message_json"])
                if stored["content_digest"] != message["content_digest"]:
                    raise UniverseError(
                        "CONDUCTOR_ROOM_MESSAGE_IDEMPOTENCY_CONFLICT",
                        "idempotency_key already refers to another conductor message",
                        HTTPStatus.CONFLICT,
                    )
                stored["created_at"] = existing["created_at"]
                return stored, False
            connection.execute(
                """
                INSERT INTO conductor_room_message(
                    message_id, idempotency_key, message_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    message["message_id"],
                    message["idempotency_key"],
                    _canonical_json(message),
                    message["created_at"],
                ),
            )
        return message, True

    def list_conductor_room_messages(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT message_json, created_at
                FROM conductor_room_message
                ORDER BY created_at, message_id
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        messages = []
        for row in rows:
            message = json.loads(row["message_json"])
            message["created_at"] = row["created_at"]
            messages.append(message)
        return messages

    def get_conductor_room_message(self, message_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT message_json, created_at
                FROM conductor_room_message
                WHERE message_id = ?
                """,
                (_required_text(message_id, "message_id"),),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "CONDUCTOR_ROOM_MESSAGE_NOT_FOUND",
                "conductor room message is not registered",
                HTTPStatus.NOT_FOUND,
            )
        message = json.loads(row["message_json"])
        message["created_at"] = row["created_at"]
        return message

    def recover_conductor_room_messages(self) -> list[str]:
        pending: list[str] = []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT message_id, message_json
                FROM conductor_room_message
                ORDER BY created_at, message_id
                """
            ).fetchall()
            for row in rows:
                message = json.loads(row["message_json"])
                state = message.get("delivery_state")
                if state == "PROCESSING":
                    message["delivery_state"] = "QUEUED"
                    message["updated_at"] = utc_now()
                    message.pop("failure", None)
                    connection.execute(
                        """
                        UPDATE conductor_room_message
                        SET message_json = ?
                        WHERE message_id = ?
                        """,
                        (_canonical_json(message), row["message_id"]),
                    )
                    state = "QUEUED"
                if state in {"QUEUED", "WAITING_FOR_RUNTIME_BINDING"}:
                    pending.append(row["message_id"])
        return pending

    def wait_conductor_room_message(self, message_id: str) -> None:
        self._transition_conductor_room_message(
            message_id,
            expected_states={"QUEUED", "WAITING_FOR_RUNTIME_BINDING"},
            delivery_state="WAITING_FOR_RUNTIME_BINDING",
        )

    def claim_conductor_room_message(
        self, message_id: str, *, provider: str
    ) -> dict[str, Any] | None:
        return self._transition_conductor_room_message(
            message_id,
            expected_states={"QUEUED", "WAITING_FOR_RUNTIME_BINDING"},
            delivery_state="PROCESSING",
            updates={"provider": provider, "started_at": utc_now()},
            required=False,
        )

    def fail_conductor_room_message(
        self, message_id: str, *, code: str, reason: str
    ) -> None:
        self._transition_conductor_room_message(
            message_id,
            expected_states={
                "QUEUED",
                "WAITING_FOR_RUNTIME_BINDING",
                "PROCESSING",
            },
            delivery_state="FAILED",
            updates={
                "failure": {
                    "code": _required_text(code, "failure.code")[:160],
                    "reason": _required_text(reason, "failure.reason")[:1000],
                },
                "completed_at": utc_now(),
            },
        )

    def complete_conductor_room_message(
        self,
        message_id: str,
        *,
        provider: str,
        body: str,
        result_receipt_ref: str,
        ui_action: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reply = normalize_conductor_room_message(
            {
                "kind": "RESULT",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": _required_text(body, "reply.body")[:20000],
                "provider": provider,
                "idempotency_key": f"conductor-reply:{message_id}",
                "in_reply_to": message_id,
            }
        )
        completed_at = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT message_json
                FROM conductor_room_message
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
            if row is None:
                raise UniverseError(
                    "CONDUCTOR_ROOM_MESSAGE_NOT_FOUND",
                    "conductor room message is not registered",
                    HTTPStatus.NOT_FOUND,
                )
            original = json.loads(row["message_json"])
            if original.get("delivery_state") != "PROCESSING":
                raise UniverseError(
                    "CONDUCTOR_ROOM_STATE_CONFLICT",
                    "conductor room message is not processing",
                    HTTPStatus.CONFLICT,
                )
            original.update(
                {
                    "delivery_state": "ANSWERED",
                    "provider": provider,
                    "result_receipt_ref": result_receipt_ref,
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                }
            )
            reply.update(
                {
                    "delivery_state": "ANSWERED",
                    "provider": provider,
                    "result_receipt_ref": result_receipt_ref,
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                }
            )
            if ui_action is not None and ui_action.get("kind") != "NONE":
                reply["ui_action"] = dict(ui_action)
            connection.execute(
                """
                UPDATE conductor_room_message
                SET message_json = ?
                WHERE message_id = ?
                """,
                (_canonical_json(original), message_id),
            )
            connection.execute(
                """
                INSERT INTO conductor_room_message(
                    message_id, idempotency_key, message_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    reply["message_id"],
                    reply["idempotency_key"],
                    _canonical_json(reply),
                    reply["created_at"],
                ),
            )
        return original, reply

    def _transition_conductor_room_message(
        self,
        message_id: str,
        *,
        expected_states: set[str],
        delivery_state: str,
        updates: dict[str, Any] | None = None,
        required: bool = True,
    ) -> dict[str, Any] | None:
        if delivery_state not in CONDUCTOR_ROOM_DELIVERY_STATES:
            raise UniverseError(
                "CONDUCTOR_ROOM_STATE_INVALID",
                "unsupported conductor room delivery state",
            )
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT message_json
                FROM conductor_room_message
                WHERE message_id = ?
                """,
                (_required_text(message_id, "message_id"),),
            ).fetchone()
            if row is None:
                if not required:
                    return None
                raise UniverseError(
                    "CONDUCTOR_ROOM_MESSAGE_NOT_FOUND",
                    "conductor room message is not registered",
                    HTTPStatus.NOT_FOUND,
                )
            message = json.loads(row["message_json"])
            if message.get("delivery_state") not in expected_states:
                if not required:
                    return None
                raise UniverseError(
                    "CONDUCTOR_ROOM_STATE_CONFLICT",
                    "conductor room message state transition is not allowed",
                    HTTPStatus.CONFLICT,
                )
            message["delivery_state"] = delivery_state
            message["updated_at"] = utc_now()
            if updates:
                message.update(updates)
            connection.execute(
                """
                UPDATE conductor_room_message
                SET message_json = ?
                WHERE message_id = ?
                """,
                (_canonical_json(message), message_id),
            )
        return message

    def send_room_message(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], bool]:
        message, created = self.create_room_message(
            project_id,
            value,
            delivery_state="RECORDED",
        )
        if not created:
            return message, False
        try:
            bridge = self.get_master_bridge(project_id)
        except UniverseError as error:
            if error.code == "MASTER_BRIDGE_NOT_REGISTERED":
                return message, True
            raise
        try:
            receipt = HttpProjectMasterBridge(
                endpoint=bridge["endpoint"],
                credential_env=bridge["credential_env"],
            ).deliver(bridge=bridge, message=message)
        except DispatchError as error:
            self._set_master_bridge_status(
                project_id,
                status="UNAVAILABLE",
                last_delivery_at=None,
            )
            return self._update_room_delivery(
                message,
                delivery_state="DELIVERY_FAILED",
                delivery={"status": "FAILED", "reason": str(error)},
            ), True
        self._set_master_bridge_status(
            project_id,
            status="AVAILABLE",
            last_delivery_at=receipt["delivered_at"],
        )
        return self._update_room_delivery(
            message,
            delivery_state="DELIVERED_TO_MASTER",
            delivery=receipt,
        ), True

    def append_master_bridge_reply(
        self,
        project_id: str,
        value: Any,
        credential: str | None,
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        reply = normalize_master_bridge_reply(project["project_id"], value)
        bridge = self.validate_master_bridge_credential(
            project["project_id"],
            reply["bridge_id"],
            credential,
        )
        with self._connection() as connection:
            parent = connection.execute(
                """
                SELECT 1 FROM project_room_message
                WHERE project_id = ? AND message_id = ?
                """,
                (project["project_id"], reply["message"]["in_reply_to"]),
            ).fetchone()
        if parent is None:
            raise UniverseError(
                "ROOM_MESSAGE_NOT_FOUND",
                "reply target does not exist in this project room",
                HTTPStatus.CONFLICT,
            )
        message, created = self.create_room_message(
            project["project_id"],
            reply["message"],
            delivery_state="MASTER_REPLY_RECORDED",
            delivery={"bridge_id": bridge["bridge_id"], "status": "REPLIED"},
        )
        self._set_master_bridge_status(
            project["project_id"], status="AVAILABLE", last_delivery_at=utc_now()
        )
        return message, created

    def validate_master_bridge_credential(
        self,
        project_id: str,
        bridge_id: str,
        credential: str | None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        bridge = self.get_master_bridge(project["project_id"])
        expected = os.environ.get(bridge["credential_env"])
        if not expected:
            raise UniverseError(
                "MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE",
                "registered bridge credential is unavailable on this Host",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if not credential or not hmac.compare_digest(expected, credential):
            raise UniverseError(
                "MASTER_BRIDGE_UNAUTHORIZED",
                "bridge reply credential does not match the registered binding",
                HTTPStatus.FORBIDDEN,
            )
        if bridge_id != bridge["bridge_id"]:
            raise UniverseError(
                "MASTER_BRIDGE_BINDING_MISMATCH",
                "reply bridge_id does not match the registered binding",
                HTTPStatus.CONFLICT,
            )
        return bridge

    def list_room_messages(
        self, project_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT message_json, delivery_state, delivery_json, created_at, updated_at
                FROM project_room_message
                WHERE project_id = ?
                ORDER BY created_at, message_id
                LIMIT ?
                """,
                (project["project_id"], max(1, min(int(limit), 500))),
            ).fetchall()
        messages = []
        for row in rows:
            message = json.loads(row["message_json"])
            message["delivery_state"] = row["delivery_state"]
            message["delivery"] = json.loads(row["delivery_json"])
            message["created_at"] = row["created_at"]
            message["updated_at"] = row["updated_at"]
            messages.append(message)
        return messages

    def record_agent_permission(
        self,
        project_id: str,
        in_reply_to: str,
        permission: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        try:
            request = normalize_permission_request(permission)
        except AgentSessionError as error:
            raise UniverseError(
                "AGENT_PERMISSION_REQUEST_INVALID",
                str(error),
            ) from error
        with self._connection() as connection:
            parent = connection.execute(
                """
                SELECT 1 FROM project_room_message
                WHERE project_id = ? AND message_id = ?
                """,
                (project["project_id"], in_reply_to),
            ).fetchone()
            if parent is None:
                raise UniverseError(
                    "ROOM_MESSAGE_NOT_FOUND",
                    "permission request target does not exist in this project room",
                    HTTPStatus.CONFLICT,
                )
            existing = connection.execute(
                """
                SELECT project_id, in_reply_to, provider, session_id,
                       tool_call_json, options_json, state,
                       selected_option_id, requested_at, resolved_at
                FROM agent_permission_request
                WHERE request_id = ?
                """,
                (request["request_id"],),
            ).fetchone()
            if existing is not None:
                current = self._agent_permission_row(existing, request["request_id"])
                comparable = {
                    key: current[key]
                    for key in (
                        "project_id",
                        "in_reply_to",
                        "provider",
                        "session_id",
                        "tool_call",
                        "options",
                    )
                }
                expected = {
                    "project_id": project["project_id"],
                    "in_reply_to": in_reply_to,
                    "provider": request["provider"],
                    "session_id": request["session_id"],
                    "tool_call": request["tool_call"],
                    "options": request["options"],
                }
                if comparable != expected:
                    raise UniverseError(
                        "AGENT_PERMISSION_REQUEST_CONFLICT",
                        "permission request id is already bound to another request",
                        HTTPStatus.CONFLICT,
                    )
                return current, False
            now = utc_now()
            connection.execute(
                """
                INSERT INTO agent_permission_request(
                    request_id, project_id, in_reply_to, provider, session_id,
                    tool_call_json, options_json, state, selected_option_id,
                    requested_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?, NULL)
                """,
                (
                    request["request_id"],
                    project["project_id"],
                    in_reply_to,
                    request["provider"],
                    request["session_id"],
                    _canonical_json(request["tool_call"]),
                    _canonical_json(request["options"]),
                    now,
                ),
            )
        return self.get_agent_permission(
            project["project_id"], request["request_id"]
        ), True

    def get_agent_permission(
        self,
        project_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT project_id, in_reply_to, provider, session_id,
                       tool_call_json, options_json, state,
                       selected_option_id, requested_at, resolved_at
                FROM agent_permission_request
                WHERE project_id = ? AND request_id = ?
                """,
                (project["project_id"], _required_text(request_id, "request_id")),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "AGENT_PERMISSION_NOT_FOUND",
                "agent permission request does not exist",
                HTTPStatus.NOT_FOUND,
            )
        return self._agent_permission_row(row, request_id)

    def list_agent_permissions(
        self,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT request_id, project_id, in_reply_to, provider, session_id,
                       tool_call_json, options_json, state,
                       selected_option_id, requested_at, resolved_at
                FROM agent_permission_request
                WHERE project_id = ?
                ORDER BY requested_at, request_id
                LIMIT ?
                """,
                (project["project_id"], max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._agent_permission_row(row, row["request_id"]) for row in rows]

    def resolve_agent_permission(
        self,
        project_id: str,
        request_id: str,
        option_id: str,
    ) -> tuple[dict[str, Any], bool]:
        current = self.get_agent_permission(project_id, request_id)
        normalized_option = _required_text(option_id, "option_id")
        if normalized_option not in {
            option["optionId"] for option in current["options"]
        }:
            raise UniverseError(
                "AGENT_PERMISSION_OPTION_UNKNOWN",
                "selected option is not offered by this request",
                HTTPStatus.CONFLICT,
            )
        if current["state"] != "PENDING":
            if current["selected_option_id"] != normalized_option:
                raise UniverseError(
                    "AGENT_PERMISSION_ALREADY_RESOLVED",
                    "permission request already has another decision",
                    HTTPStatus.CONFLICT,
                )
            return current, False
        resolved_at = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_permission_request
                SET state = 'RESOLVED', selected_option_id = ?, resolved_at = ?
                WHERE project_id = ? AND request_id = ? AND state = 'PENDING'
                """,
                (
                    normalized_option,
                    resolved_at,
                    _project_id(project_id),
                    _required_text(request_id, "request_id"),
                ),
            )
        if cursor.rowcount != 1:
            return self.get_agent_permission(project_id, request_id), False
        return self.get_agent_permission(project_id, request_id), True

    @staticmethod
    def _agent_permission_row(
        row: sqlite3.Row,
        request_id: str,
    ) -> dict[str, Any]:
        return {
            "schema": PERMISSION_REQUEST_SCHEMA,
            "request_id": request_id,
            "project_id": row["project_id"],
            "in_reply_to": row["in_reply_to"],
            "provider": row["provider"],
            "session_id": row["session_id"],
            "tool_call": json.loads(row["tool_call_json"]),
            "options": json.loads(row["options_json"]),
            "state": row["state"],
            "selected_option_id": row["selected_option_id"],
            "requested_at": row["requested_at"],
            "resolved_at": row["resolved_at"],
        }

    def _update_room_delivery(
        self,
        message: dict[str, Any],
        *,
        delivery_state: str,
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        updated_at = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE project_room_message
                SET delivery_state = ?, delivery_json = ?, updated_at = ?
                WHERE message_id = ? AND project_id = ?
                """,
                (
                    delivery_state,
                    _canonical_json(delivery),
                    updated_at,
                    message["message_id"],
                    message["project_id"],
                ),
            )
        updated = dict(message)
        updated["delivery_state"] = delivery_state
        updated["delivery"] = delivery
        updated["updated_at"] = updated_at
        return updated

    def _set_master_bridge_status(
        self,
        project_id: str,
        *,
        status: str,
        last_delivery_at: str | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE project_master_bridge
                SET status = ?, last_delivery_at = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (status, last_delivery_at, utc_now(), _project_id(project_id)),
            )

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
            raise UniverseError(
                error.args[0], error.args[0], HTTPStatus.CONFLICT
            ) from error
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

    def get_project_seed(self, project_id: str, seed_id: str = "") -> dict[str, Any]:
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

    def prepare_project_seed_asset_proposal(self, project_id: str) -> dict[str, Any]:
        """Prepare assets for a Project Master without modifying the Project."""

        seed = self.get_project_seed(project_id)
        if not seed["is_current"]:
            raise UniverseError(
                "PROJECT_SEED_NOT_CURRENT",
                "asset proposal may only be prepared from the current Project Seed",
                HTTPStatus.CONFLICT,
            )
        proposal = build_project_seed_asset_proposal(seed)
        if proposal["schema"] != PROJECT_SEED_ASSET_PROPOSAL_SCHEMA:
            raise UniverseError(
                "PROJECT_SEED_ASSET_PROPOSAL_INVALID",
                "prepared Project Seed asset proposal has an unexpected schema",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return proposal

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
        if (
            expected_digest is not None
            and _sha256(expected_digest, "expected_seed_digest") != seed["seed_digest"]
        ):
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
        if (
            expected is not None
            and _sha256(expected, "expected_projection_digest")
            != projection["projection_digest"]
        ):
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
            source_database = (
                Path(_required_text(request["database_path"], "database_path"))
                .expanduser()
                .resolve(strict=True)
            )
            source_manifest = (
                Path(_required_text(request["manifest_path"], "manifest_path"))
                .expanduser()
                .resolve(strict=True)
            )
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
        stored_manifest = artifact_directory / (manifest_sha256 + ".manifest.json")
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
                if (
                    hashlib.sha256(stored_path.read_bytes()).hexdigest()
                    != expected_sha256
                ):
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
                plan = plan_project_release_lifecycle(
                    project_root=Path(project["project_root"]),
                    project_id=project["project_id"],
                    release_id=runtime.release_id,
                    source_commit=runtime.metadata["source_commit"],
                )
        except (
            CoreReleaseError,
            ProjectReleaseApplyError,
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
            "next_operation": "USER_APPROVAL_AND_PROJECT_HOST_APPLY",
        }
        material["proposal_digest"] = _json_sha256(material)
        material["proposal_id"] = "release_proposal_" + material["proposal_digest"][:20]
        material["status"] = "PROJECT_RELEASE_PROPOSAL_READY"
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

    def get_project_release_proposal(
        self,
        project_id: str,
        proposal_id: str,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        normalized_proposal = _identifier(proposal_id, "proposal_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT proposal_json, created_at
                FROM project_release_proposal
                WHERE project_id = ? AND proposal_id = ?
                """,
                (project["project_id"], normalized_proposal),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "PROJECT_RELEASE_PROPOSAL_NOT_FOUND",
                f"release proposal is not recorded: {normalized_proposal}",
                HTTPStatus.NOT_FOUND,
            )
        proposal = json.loads(row["proposal_json"])
        proposal["created_at"] = row["created_at"]
        return proposal

    def get_release_artifact_binding(self, release_id: str) -> dict[str, Any]:
        normalized = _identifier(release_id, "release_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT release_id, database_path, manifest_path, database_sha256
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
        return {
            "release_id": row["release_id"],
            "database_path": row["database_path"],
            "manifest_path": row["manifest_path"],
            "database_sha256": row["database_sha256"],
        }

    def get_project_release_application(
        self,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        normalized = _identifier(proposal_id, "proposal_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT application_id, approval_digest, receipt_json, applied_at
                FROM project_release_application
                WHERE proposal_id = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        receipt = json.loads(row["receipt_json"])
        receipt["application_id"] = row["application_id"]
        receipt["approval_digest"] = row["approval_digest"]
        receipt["applied_at"] = row["applied_at"]
        return receipt

    def record_project_release_application(
        self,
        *,
        project_id: str,
        proposal: Mapping[str, Any],
        approval_digest: str,
        receipt: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        proposal_id = _identifier(proposal.get("proposal_id"), "proposal_id")
        release_id = _identifier(proposal.get("release_id"), "release_id")
        normalized_approval = _sha256(approval_digest, "approval_digest")
        application_id = (
            "release_application_"
            + _json_sha256(
                {
                    "proposal_id": proposal_id,
                    "approval_digest": normalized_approval,
                }
            )[:24]
        )
        applied_at = utc_now()
        stored_receipt = dict(receipt)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT application_id, approval_digest, receipt_json, applied_at
                FROM project_release_application
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                if existing["approval_digest"] != normalized_approval:
                    raise UniverseError(
                        "PROJECT_RELEASE_ALREADY_APPLIED",
                        "release proposal already has a different durable approval",
                        HTTPStatus.CONFLICT,
                    )
                existing_receipt = json.loads(existing["receipt_json"])
                existing_receipt["application_id"] = existing["application_id"]
                existing_receipt["approval_digest"] = existing["approval_digest"]
                existing_receipt["applied_at"] = existing["applied_at"]
                return existing_receipt, False
            connection.execute(
                """
                INSERT INTO project_release_application(
                    application_id, proposal_id, project_id, release_id,
                    approval_digest, receipt_json, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    proposal_id,
                    project["project_id"],
                    release_id,
                    normalized_approval,
                    _canonical_json(stored_receipt),
                    applied_at,
                ),
            )
        stored_receipt["application_id"] = application_id
        stored_receipt["approval_digest"] = normalized_approval
        stored_receipt["applied_at"] = applied_at
        return stored_receipt, True

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

    def list_runtime_worker_invocations(
        self, project_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        bounded_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT invocation_json, status, result_json, created_at, completed_at
                FROM runtime_worker_invocation
                WHERE project_id = ?
                ORDER BY created_at DESC, invocation_id DESC
                LIMIT ?
                """,
                (project["project_id"], bounded_limit),
            ).fetchall()
        return [self._runtime_worker_invocation_row(row) for row in rows]

    def invoke_runtime_worker(
        self,
        project_id: str,
        value: Any,
        runtime_host: UniverseRuntimeHost,
    ) -> tuple[dict[str, Any], bool]:
        project = self.get_project(project_id)
        try:
            redacted = redacted_invocation_record(value)
        except RuntimeHostError as error:
            raise UniverseError(error.code, error.detail) from error
        now = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT invocation_json, status, result_json, created_at, completed_at
                FROM runtime_worker_invocation
                WHERE project_id = ? AND invocation_id = ?
                """,
                (project["project_id"], redacted["invocation_id"]),
            ).fetchone()
            if existing is not None:
                record = self._runtime_worker_invocation_row(existing)
                if record["invocation"]["request_digest"] != redacted["request_digest"]:
                    raise UniverseError(
                        "RUNTIME_WORKER_INVOCATION_CONFLICT",
                        "invocation_id already refers to a different read-only request",
                        HTTPStatus.CONFLICT,
                    )
                return record, False
            connection.execute(
                """
                INSERT INTO runtime_worker_invocation(
                    invocation_id, project_id, request_digest, invocation_json,
                    status, result_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, 'REQUESTED', '{}', ?, NULL)
                """,
                (
                    redacted["invocation_id"],
                    project["project_id"],
                    redacted["request_digest"],
                    _canonical_json(redacted),
                    now,
                ),
            )
        try:
            result = runtime_host.invoke_read_only(value)
        except RuntimeHostError as error:
            result = {
                "status": "RUNTIME_HOST_UNAVAILABLE",
                "reason": error.code,
            }
        observation_count = result.get("skill_run_observation_count", 0)
        if (
            isinstance(observation_count, bool)
            or not isinstance(observation_count, int)
            or observation_count < 0
        ):
            observation_count = 0
        result_record = {
            "status": _required_text(result.get("status"), "runtime result status"),
            "provider": redacted["provider"],
            "worker_id": str(result.get("worker_id") or "UNKNOWN"),
            "result_receipt_ref": str(result.get("result_receipt_ref") or "UNKNOWN"),
            "skill_run_observation_count": observation_count,
            "repository_write": False,
        }
        for field in ("reason", "stage"):
            if isinstance(result.get(field), str) and result[field]:
                result_record[field] = result[field]
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE runtime_worker_invocation
                SET status = ?, result_json = ?, completed_at = ?
                WHERE project_id = ? AND invocation_id = ?
                """,
                (
                    result_record["status"],
                    _canonical_json(result_record),
                    utc_now(),
                    project["project_id"],
                    redacted["invocation_id"],
                ),
            )
            row = connection.execute(
                """
                SELECT invocation_json, status, result_json, created_at, completed_at
                FROM runtime_worker_invocation
                WHERE project_id = ? AND invocation_id = ?
                """,
                (project["project_id"], redacted["invocation_id"]),
            ).fetchone()
        if row is None:
            raise UniverseError(
                "RUNTIME_WORKER_INVOCATION_UNAVAILABLE", "invocation record disappeared"
            )
        return self._runtime_worker_invocation_row(row), True

    @staticmethod
    def _runtime_worker_invocation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": RUNTIME_WORKER_INVOCATION_SCHEMA,
            "invocation": json.loads(row["invocation_json"]),
            "status": row["status"],
            "result": json.loads(row["result_json"]),
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
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
                # Deliver uses the registered project ref; keep the envelope
                # aligned so discovery dispatches match Project Master inbox layout.
                "inbox_ref": project["refs"]["master_inbox"],
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
        return [self._dispatch_summary(row) for row in rows]

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

    @staticmethod
    def _todo_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": TODO_SCHEMA,
            "todo_id": row["todo_id"],
            "scope_kind": row["scope_kind"],
            "project_id": row["project_id"],
            "node_ref": row["node_ref"],
            "title": row["title"],
            "detail": row["detail"],
            "priority": row["priority"],
            "state": row["state"],
            "source_kind": row["source_kind"],
            "sort_order": row["sort_order"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _master_bridge_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": PROJECT_MASTER_BRIDGE_SCHEMA,
            "project_id": row["project_id"],
            "bridge_id": row["bridge_id"],
            "endpoint": row["endpoint"],
            "credential_env": row["credential_env"],
            "master_session_ref": row["master_session_ref"],
            "binding_evidence_ref": row["binding_evidence_ref"],
            "status": row["status"],
            "registered_at": row["registered_at"],
            "updated_at": row["updated_at"],
            "last_delivery_at": row["last_delivery_at"],
        }


class ProjectRoomEventHub:
    def __init__(self, *, retained_events: int = 512) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._retained_events = max(32, int(retained_events))
        self._events: dict[str, deque[dict[str, Any]]] = {}

    def cursor(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized_project = _project_id(project_id)
        with self._condition:
            self._sequence += 1
            event = {
                "schema": PROJECT_ROOM_STREAM_SCHEMA,
                "event_id": self._sequence,
                "project_id": normalized_project,
                "emitted_at": utc_now(),
                "payload": dict(payload),
            }
            events = self._events.setdefault(
                normalized_project,
                deque(maxlen=self._retained_events),
            )
            events.append(event)
            self._condition.notify_all()
            return dict(event)

    def wait(
        self,
        project_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        normalized_project = _project_id(project_id)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._condition:
            while True:
                events = [
                    dict(event)
                    for event in self._events.get(normalized_project, ())
                    if int(event["event_id"]) > after_event_id
                ]
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


class UniverseHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        store: UniverseStore,
        token: str,
        runtime_host: UniverseRuntimeHost | None = None,
        mode_contract: dict[str, Any] | None = None,
        *,
        auto_start_conductor_runtime: bool = False,
        conductor_runtime_factory: Any = None,
        conductor_session_provider_factory: Any = None,
        auto_start_project_masters: bool = True,
        project_master_provider_factory: Any = None,
        host_profile: HostProfileStore | None = None,
    ):
        self.store = store
        self.token = token
        self.host_profile = host_profile or HostProfileStore()
        try:
            self.host_profile.ensure_initialized()
        except HostProfileError as error:
            raise UniverseError(error.code, str(error)) from error
        self.runtime_host = runtime_host or UniverseRuntimeHost(
            Path(__file__).resolve().parents[1]
        )
        self.mode_contract = dict(mode_contract or unknown_universe_mode_contract())
        self._planning_binding: dict[str, Any] | None = None
        self._planning_binding_error: dict[str, str] | None = None
        self._planning_binding_lock = threading.RLock()
        self.planning_execution_lock = threading.Lock()
        self.project_release_application_lock = threading.Lock()
        self.project_skill_plan_application_lock = threading.Lock()
        self._conductor_queue: queue.Queue[str | None] = queue.Queue()
        self._conductor_queued_ids: set[str] = set()
        self._conductor_queue_lock = threading.RLock()
        self._conductor_stop = threading.Event()
        self._conductor_session_error: dict[str, str] | None = None
        self.project_room_events = ProjectRoomEventHub()
        super().__init__(address, UniverseRequestHandler)
        self.conductor_runtime: UniverseConductorRuntime | None = None
        if auto_start_conductor_runtime:
            factory = conductor_runtime_factory or UniverseConductorRuntime
            try:
                self.conductor_runtime = factory(Path(__file__).resolve().parents[1])
                self._planning_binding = normalize_planning_runtime_binding(
                    self.conductor_runtime.start()
                )
                self._planning_binding["bound_at"] = utc_now()
            except (OSError, UniverseConductorRuntimeError, UniverseError) as error:
                self.conductor_runtime = None
                self._planning_binding_error = {
                    "error_code": getattr(
                        error,
                        "code",
                        str(error) or type(error).__name__,
                    ),
                    "reason": f"{type(error).__name__}: {error}",
                }
        self.conductor_session_host = (
            ResidentModeSessionHost(
                Path(__file__).resolve().parents[1],
                "CONDUCTOR",
                "CONDUCTOR",
                self.store.database_path.parent / "conductor-mode-session.sqlite",
                actor_label="Universe Conductor",
                provider_factory=conductor_session_provider_factory,
            )
            if auto_start_conductor_runtime
            else None
        )
        host, port = self.server_address[:2]
        host_text = host.decode("ascii") if isinstance(host, bytes) else host
        self.connection_profile = local_connection_profile(f"http://{host_text}:{port}")
        self.interface_profiles = (local_http_interface_profile(),)
        self.project_master_hosts = (
            ResidentProjectMasterHostManager(
                universe_endpoint=self.connection_profile.endpoint,
                bridge_registrar=self.store.register_master_bridge,
                provider_factory=project_master_provider_factory,
                provider_resolver=self._resolve_project_master_provider,
            )
            if auto_start_project_masters
            else None
        )
        if self.conductor_session_host is not None:
            self.prepare_conductor_session()
        self._conductor_worker = threading.Thread(
            target=self._conductor_worker_loop,
            name="universe-conductor-room-worker",
            daemon=True,
        )
        self._conductor_worker.start()
        self._maintain_stop = threading.Event()
        self._maintain_wake = threading.Event()
        self._maintain_last_run: dict[str, Any] | None = None
        self._maintain_worker = threading.Thread(
            target=self._memory_maintain_worker_loop,
            name="universe-memory-maintain-worker",
            daemon=True,
        )
        self._maintain_worker.start()
        for message_id in self.store.recover_conductor_room_messages():
            self.enqueue_conductor_message(message_id)

    def bind_planning_runtime(self, value: Any) -> dict[str, Any]:
        binding = normalize_planning_runtime_binding(value)
        binding["bound_at"] = utc_now()
        with self._planning_binding_lock:
            if self.conductor_runtime is not None:
                self.conductor_runtime.stop()
                self.conductor_runtime = None
            self._planning_binding = binding
            self._planning_binding_error = None
        for message_id in self.store.recover_conductor_room_messages():
            self.enqueue_conductor_message(message_id)
        return self.planning_binding_status()

    def planning_binding_status(self) -> dict[str, Any]:
        with self._planning_binding_lock:
            binding = (
                dict(self._planning_binding)
                if self._planning_binding is not None
                else None
            )
        if binding is None:
            result: dict[str, Any] = {
                "schema": PLANNING_RUNTIME_BINDING_SCHEMA,
                "status": "UNBOUND",
                "persistence": "PROCESS_LOCAL",
                "provider_execution": "BLOCKED",
            }
            if self._planning_binding_error is not None:
                result.update(
                    {
                        "status": "START_FAILED",
                        **self._planning_binding_error,
                    }
                )
            return result
        evidence = {
            "session_id": binding["session_id"],
            "origin_anchor_ref": binding["origin_anchor_ref"],
            "origin_frame_id": binding["origin_frame_id"],
            "parent_actor_ref": binding["parent_actor_ref"],
            "parent_evidence_ref": binding["parent_evidence_ref"],
            "binding_evidence_ref": binding["binding_evidence_ref"],
        }
        return {
            "schema": PLANNING_RUNTIME_BINDING_SCHEMA,
            "status": "BOUND",
            "persistence": "PROCESS_LOCAL",
            "provider_execution": "USER_APPROVAL_REQUIRED",
            **evidence,
            "binding_digest": _json_sha256(evidence),
            "bound_at": binding["bound_at"],
        }

    def require_planning_binding(self) -> dict[str, Any]:
        with self._planning_binding_lock:
            if self._planning_binding is None:
                raise UniverseError(
                    "PLANNING_RUNTIME_BINDING_REQUIRED",
                    "Planning Frame execution requires a process-local Runtime binding",
                    HTTPStatus.CONFLICT,
                )
            return dict(self._planning_binding)

    def enqueue_conductor_message(self, message_id: str) -> None:
        normalized_id = _required_text(message_id, "message_id")
        with self._conductor_queue_lock:
            if normalized_id in self._conductor_queued_ids:
                return
            self._conductor_queued_ids.add(normalized_id)
        self._conductor_queue.put(normalized_id)

    def ensure_project_master(self, project_id: str) -> dict[str, Any]:
        if self.project_master_hosts is None:
            return {"status": "AUTO_START_DISABLED", "project_id": project_id}
        project = self.store.get_project(project_id)
        try:
            bridge = self.store.get_master_bridge(project_id)
        except UniverseError as error:
            if error.code != "MASTER_BRIDGE_NOT_REGISTERED":
                raise
            bridge = None
        if bridge is not None:
            managed = str(bridge.get("binding_evidence_ref") or "").startswith(
                "universe://resident-project-master/"
            )
            if bridge.get("status") in {"REGISTERED", "AVAILABLE"} and (
                (not managed and _loopback_endpoint_reachable(str(bridge["endpoint"])))
                or (managed and self.project_master_hosts.is_resident(project_id))
            ):
                return {
                    "status": "EXISTING_BRIDGE",
                    "project_id": project_id,
                    "bridge": bridge,
                }
        return self.project_master_hosts.ensure(project)

    def apply_project_seed_assets(
        self,
        project_id: str,
        value: Any,
    ) -> dict[str, Any]:
        request = normalize_project_seed_asset_apply_request(value)
        proposal = self.store.prepare_project_seed_asset_proposal(project_id)
        if (
            request["proposal_id"] != proposal["proposal_id"]
            or request["proposal_digest"] != proposal["proposal_digest"]
        ):
            raise UniverseError(
                "PROJECT_SEED_ASSET_APPROVAL_STALE",
                "approval does not match the current Project Seed asset proposal",
                HTTPStatus.CONFLICT,
            )
        try:
            self.ensure_project_master(project_id)
            bridge = self.store.get_master_bridge(project_id)
            approval_evidence_ref = (
                "universe://projects/"
                + quote(project_id, safe="")
                + "/seed-asset-proposals/"
                + quote(proposal["proposal_id"], safe="")
                + "/approvals/"
                + _json_sha256(request)[:24]
            )
            approval = build_project_seed_asset_approval(
                project_id=project_id,
                proposal=proposal,
                evidence_ref=approval_evidence_ref,
            )
            delivery = HttpProjectMasterBridge(
                endpoint=bridge["endpoint"],
                credential_env=bridge["credential_env"],
                timeout_seconds=60,
            ).apply_seed_assets(
                bridge=bridge,
                proposal=proposal,
                approval=approval,
            )
        except (DispatchError, OSError, ProjectMasterHostError) as error:
            raise UniverseError(
                "PROJECT_SEED_ASSET_APPLICATION_BLOCKED",
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        return {
            "schema": API_SCHEMA,
            "status": "PROJECT_SEED_ASSET_APPLICATION_DELIVERED",
            "project_id": project_id,
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "approval": approval,
            "delivery": delivery,
        }

    def deliver_master_handoff(
        self,
        project_id: str,
        handoff_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
        request = normalize_master_handoff_delivery_request(value)
        with self.project_skill_plan_application_lock:
            handoff = self.store.get_master_handoff(project_id, handoff_id)
            application = None
            source = handoff.get("source")
            if isinstance(source, Mapping) and source.get("kind") == "SKILL_PLAN":
                application = self.store.get_skill_plan_master_application(
                    project_id,
                    handoff_id,
                )
                if application is None:
                    try:
                        self.ensure_project_master(project_id)
                        bridge = self.store.get_master_bridge(project_id)
                        approval_evidence_ref = (
                            "universe://projects/"
                            + quote(project_id, safe="")
                            + "/master-handoffs/"
                            + quote(handoff_id, safe="")
                            + "/approvals/"
                            + _json_sha256(request)[:24]
                        )
                        approval = build_project_skill_plan_approval(
                            project_id=project_id,
                            handoff=handoff,
                            evidence_ref=approval_evidence_ref,
                        )
                        delivery = HttpProjectMasterBridge(
                            endpoint=bridge["endpoint"],
                            credential_env=bridge["credential_env"],
                            timeout_seconds=60,
                        ).apply_skill_plan(
                            bridge=bridge,
                            handoff=handoff,
                            approval=approval,
                        )
                        application, _ = (
                            self.store.record_skill_plan_master_application(
                                project_id=project_id,
                                handoff=handoff,
                                approval=approval,
                                delivery=delivery,
                            )
                        )
                    except (
                        DispatchError,
                        OSError,
                        ProjectMasterHostError,
                        ProjectSkillPlanApplyError,
                    ) as error:
                        raise UniverseError(
                            "PROJECT_SKILL_PLAN_APPLICATION_BLOCKED",
                            str(error),
                            HTTPStatus.CONFLICT,
                        ) from error
            delivered_handoff, delivered = self.store.deliver_master_handoff(
                project_id,
                handoff_id,
                request,
            )
        return delivered_handoff, delivered, application

    def apply_project_release(
        self,
        project_id: str,
        value: Any,
    ) -> dict[str, Any]:
        request = normalize_project_release_apply_request(value)
        with self.project_release_application_lock:
            return self._apply_project_release(project_id, request)

    def _apply_project_release(
        self,
        project_id: str,
        request: Mapping[str, str],
    ) -> dict[str, Any]:
        proposal = self.store.get_project_release_proposal(
            project_id,
            request["proposal_id"],
        )
        if request["proposal_digest"] != proposal["proposal_digest"]:
            raise UniverseError(
                "PROJECT_RELEASE_APPROVAL_STALE",
                "approval does not match the recorded Project Release proposal",
                HTTPStatus.CONFLICT,
            )
        approval_evidence_ref = (
            "universe://projects/"
            + quote(project_id, safe="")
            + "/release-proposals/"
            + quote(proposal["proposal_id"], safe="")
            + "/approvals/"
            + _json_sha256(request)[:24]
        )
        try:
            approval = build_project_release_approval(
                project_id=project_id,
                proposal=proposal,
                evidence_ref=approval_evidence_ref,
            )
        except ProjectReleaseApplyError as error:
            raise UniverseError(
                error.code,
                str(error),
                HTTPStatus.CONFLICT,
            ) from error
        approval_digest = _json_sha256(approval)
        existing = self.store.get_project_release_application(proposal["proposal_id"])
        if existing is not None:
            if existing["approval_digest"] != approval_digest:
                raise UniverseError(
                    "PROJECT_RELEASE_ALREADY_APPLIED",
                    "release proposal already has a different durable approval",
                    HTTPStatus.CONFLICT,
                )
            return {
                "schema": API_SCHEMA,
                "status": "PROJECT_RELEASE_APPLICATION_ALREADY_COMPLETED",
                "project_id": project_id,
                "proposal_id": proposal["proposal_id"],
                "approval": approval,
                "receipt": existing,
            }

        project = self.store.get_project(project_id)
        artifact = self.store.get_release_artifact_binding(proposal["release_id"])
        resident_stopped = False
        if self.project_master_hosts is not None:
            resident_stopped = self.project_master_hosts.stop(project_id)
        try:
            receipt = apply_project_release_proposal(
                project_root=Path(project["project_root"]),
                project_id=project_id,
                proposal=proposal,
                approval=approval,
                database_path=Path(artifact["database_path"]),
                manifest_path=Path(artifact["manifest_path"]),
            )
        except ProjectReleaseApplyError as error:
            master_host = {"status": "NOT_RESTARTED"}
            if resident_stopped:
                try:
                    master_host = self.ensure_project_master(project_id)
                except (OSError, ProjectMasterHostError, UniverseError) as restart:
                    master_host = {
                        "status": "PROJECT_MASTER_RESTART_FAILED",
                        "detail": str(restart),
                    }
            raise UniverseError(
                error.code,
                f"{error}; master_host={master_host['status']}",
                HTTPStatus.CONFLICT,
            ) from error

        stored_receipt, created = self.store.record_project_release_application(
            project_id=project_id,
            proposal=proposal,
            approval_digest=approval_digest,
            receipt=receipt,
        )
        try:
            master_host = self.ensure_project_master(project_id)
        except (OSError, ProjectMasterHostError, UniverseError) as error:
            master_host = {
                "status": "PROJECT_MASTER_START_FAILED",
                "detail": str(error),
            }
        return {
            "schema": API_SCHEMA,
            "status": (
                "PROJECT_RELEASE_APPLICATION_COMPLETED"
                if created
                else "PROJECT_RELEASE_APPLICATION_ALREADY_COMPLETED"
            ),
            "project_id": project_id,
            "proposal_id": proposal["proposal_id"],
            "approval": approval,
            "receipt": stored_receipt,
            "master_host": master_host,
        }

    def provider_settings(self) -> dict[str, Any]:
        settings = self.store.list_provider_settings()
        capabilities = {
            item["provider"]: item
            for item in self.runtime_host.provider_capabilities()
            if isinstance(item, Mapping) and isinstance(item.get("provider"), str)
        }
        settings["available_providers"] = [
            capabilities.get(
                provider,
                {
                    "provider": provider,
                    "status": "UNAVAILABLE",
                    "reason": f"{provider}_CLI_UNAVAILABLE",
                },
            )
            for provider in ("GROK", "CODEX")
        ]
        settings["universe_conductor"]["resolved_provider"] = (
            self._resolve_configured_provider(
                settings["universe_conductor"]["provider"],
                capabilities=capabilities,
                strict=False,
            )
        )
        settings["universe_conductor"]["session_connection"] = (
            self.conductor_session_status()
        )
        for project in settings["project_masters"]:
            project["resolved_provider"] = self._resolve_configured_provider(
                project["provider"],
                capabilities=capabilities,
                strict=False,
            )
            project["session_connection"] = (
                self.project_master_hosts.connection_status(project["scope_id"])
                if self.project_master_hosts is not None
                else {
                    "schema": "universe.provider-session-connection.v1",
                    "target_kind": "PROJECT_MASTER",
                    "target_id": project["scope_id"],
                    "requested_mode": "MASTER",
                    "last_provider": "UNKNOWN",
                    "last_session_ref": "UNKNOWN",
                    "connection_state": "NOT_OPENED",
                    "session_persistence": "LAST_COORDINATE",
                    "resident": False,
                }
            )
        settings["status"] = "CLI_PROVIDER_SETTINGS_COLLECTED"
        return settings

    def conductor_session_status(self) -> dict[str, Any]:
        if self.conductor_session_host is None:
            return {
                "schema": "universe.provider-session-connection.v1",
                "target_kind": "UNIVERSE_CONDUCTOR",
                "target_id": "CONDUCTOR",
                "requested_mode": "CONDUCTOR",
                "last_provider": "UNKNOWN",
                "last_session_ref": "UNKNOWN",
                "connection_state": "UNAVAILABLE",
                "session_persistence": "LAST_COORDINATE",
                "resident": False,
                "reason": "CONDUCTOR_SESSION_HOST_DISABLED",
            }
        status = self.conductor_session_host.status()
        if self._conductor_session_error is not None and not status["resident"]:
            status["connection_state"] = "UNAVAILABLE"
            status["reason"] = self._conductor_session_error["reason"]
        return status

    def prepare_conductor_session(self) -> dict[str, Any]:
        if self.conductor_session_host is None:
            return self.conductor_session_status()
        try:
            provider = self._resolve_conductor_provider(
                {"requested_provider": "AUTO"}
            )
            status = self.conductor_session_host.prepare(provider)
            self._conductor_session_error = None
            return status
        except (AgentSessionError, OSError, ProjectMasterHostError, UniverseError) as error:
            self._conductor_session_error = {
                "error_code": getattr(error, "code", type(error).__name__),
                "reason": str(error),
            }
            return self.conductor_session_status()

    def host_tool_settings(self) -> dict[str, Any]:
        try:
            return self.host_profile.snapshot()
        except HostProfileError as error:
            raise UniverseError(error.code, str(error)) from error

    def discover_host_tools(self) -> dict[str, Any]:
        try:
            return self.host_profile.discover()
        except HostProfileError as error:
            raise UniverseError(error.code, str(error)) from error

    def set_host_tool(self, tool: str, value: Any) -> dict[str, Any]:
        request = _exact_object_fields(
            value,
            field="host_tool_setting",
            required=frozenset({"executable"}),
            optional=frozenset(),
        )
        try:
            return self.host_profile.set_tool(
                tool,
                _required_text(request["executable"], "executable"),
            )
        except HostProfileError as error:
            raise UniverseError(error.code, str(error)) from error

    def verify_host_tool(self, tool: str) -> dict[str, Any]:
        try:
            return self.host_profile.verify_tool(tool)
        except HostProfileError as error:
            raise UniverseError(error.code, str(error)) from error

    def set_universe_provider_setting(self, value: Any) -> dict[str, Any]:
        setting = self.store.set_provider_setting(
            "UNIVERSE_CONDUCTOR",
            "CONDUCTOR",
            value,
        )
        return {
            "schema": API_SCHEMA,
            "status": "CLI_PROVIDER_SETTING_UPDATED",
            "setting": setting,
            "session_connection": self.prepare_conductor_session(),
        }

    def set_project_provider_setting(
        self,
        project_id: str,
        value: Any,
    ) -> dict[str, Any]:
        setting = self.store.set_provider_setting(
            "PROJECT_MASTER",
            project_id,
            value,
        )
        if self.project_master_hosts is not None:
            self.project_master_hosts.invalidate(project_id)
        return {
            "schema": API_SCHEMA,
            "status": "CLI_PROVIDER_SETTING_UPDATED",
            "setting": setting,
            "resident_host": "PREPARE_REQUIRED",
            "session_connection": (
                self.project_master_hosts.connection_status(project_id)
                if self.project_master_hosts is not None
                else None
            ),
        }

    def prepare_project_master_session(self, project_id: str) -> dict[str, Any]:
        host = self.ensure_project_master(project_id)
        return {
            "schema": API_SCHEMA,
            "status": "PROJECT_MASTER_SESSION_PREPARED",
            "project_id": project_id,
            "master_host": host,
            "session_connection": (
                self.project_master_hosts.connection_status(project_id)
                if self.project_master_hosts is not None
                else None
            ),
        }

    def send_project_room_message(
        self,
        project_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], bool]:
        try:
            self.ensure_project_master(project_id)
        except (OSError, ProjectMasterHostError):
            pass
        message, created = self.store.send_room_message(project_id, value)
        self.publish_project_room_changed(project_id)
        return message, created

    def publish_project_room_changed(self, project_id: str) -> None:
        self.project_room_events.publish(
            project_id,
            {
                "type": "ROOM_CHANGED",
                "messages": self.store.list_room_messages(project_id),
                "permissions": self.store.list_agent_permissions(project_id),
            },
        )

    def publish_agent_permission(
        self,
        project_id: str,
        permission: Mapping[str, Any],
    ) -> None:
        self.project_room_events.publish(
            project_id,
            {
                "type": "AGENT_PERMISSION",
                "permission": dict(permission),
            },
        )

    def resolve_agent_permission(
        self,
        project_id: str,
        request_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], bool]:
        decision = normalize_permission_decision(value)
        current = self.store.get_agent_permission(project_id, request_id)
        if current["state"] != "PENDING":
            if current["selected_option_id"] == decision["option_id"]:
                return current, False
            raise UniverseError(
                "AGENT_PERMISSION_ALREADY_RESOLVED",
                "permission request already has another decision",
                HTTPStatus.CONFLICT,
            )
        if self.project_master_hosts is None or not (
            self.project_master_hosts.resolve_permission(
                project_id,
                request_id,
                decision["option_id"],
            )
        ):
            raise UniverseError(
                "AGENT_PERMISSION_SESSION_UNAVAILABLE",
                "resident agent session cannot accept this permission decision",
                HTTPStatus.CONFLICT,
            )
        permission, changed = self.store.resolve_agent_permission(
            project_id,
            request_id,
            decision["option_id"],
        )
        self.publish_agent_permission(project_id, permission)
        return permission, changed

    def _conductor_worker_loop(self) -> None:
        while not self._conductor_stop.is_set():
            message_id = self._conductor_queue.get()
            try:
                if message_id is None:
                    return
                try:
                    self._process_conductor_message(message_id)
                except (RuntimeHostError, UniverseError) as error:
                    self._record_conductor_worker_failure(message_id, error)
                except Exception as error:
                    self._record_conductor_worker_failure(message_id, error)
            finally:
                if message_id is not None:
                    with self._conductor_queue_lock:
                        self._conductor_queued_ids.discard(message_id)
                self._conductor_queue.task_done()

    def _record_conductor_worker_failure(
        self, message_id: str, error: Exception
    ) -> None:
        try:
            self.store.fail_conductor_room_message(
                message_id,
                code=getattr(error, "code", "CONDUCTOR_WORKER_FAILED"),
                reason=f"{type(error).__name__}: {error}",
            )
        except UniverseError:
            pass

    def _process_conductor_message(self, message_id: str) -> None:
        with self._planning_binding_lock:
            binding = (
                dict(self._planning_binding)
                if self._planning_binding is not None
                else None
            )
        if binding is None:
            if self._planning_binding_error is not None:
                self.store.fail_conductor_room_message(
                    message_id,
                    code=self._planning_binding_error["error_code"],
                    reason=self._planning_binding_error["reason"],
                )
                return
            self.store.wait_conductor_room_message(message_id)
            return
        message = self.store.get_conductor_room_message(message_id)
        provider = self._resolve_conductor_provider(message)
        claimed = self.store.claim_conductor_room_message(message_id, provider=provider)
        if claimed is None:
            return
        if self.conductor_runtime is not None:
            self.conductor_runtime.observe(message_id)
            binding["parent_evidence_ref"] = (
                f"universe://conductor-room/messages/{message_id}"
            )
        history = [
            item
            for item in self.store.list_conductor_room_messages(limit=200)
            if item.get("message_id") != message_id
        ]
        try:
            worker_message = dict(claimed)
            worker_message["available_projects"] = [
                {
                    "project_id": project["project_id"],
                    "summary": str(
                        project.get("metadata", {}).get("summary")
                        or project.get("metadata", {}).get("goal")
                        or ""
                    )[:500],
                }
                for project in self.store.list_projects()[:50]
            ]
            if self.conductor_session_host is not None:
                worker_message["runtime_context"] = {
                    "requested_mode": "CONDUCTOR",
                    "session_id": binding.get("session_id", "UNKNOWN"),
                    "origin_anchor_ref": binding.get(
                        "origin_anchor_ref", "UNKNOWN"
                    ),
                    "origin_frame_id": binding.get("origin_frame_id", "UNKNOWN"),
                    "commander_surface": "UNIVERSE_UI",
                    "history": history[-50:],
                }
                session_result = self.conductor_session_host.reply(
                    provider,
                    worker_message,
                )
                self.store.complete_conductor_room_message(
                    message_id,
                    provider=provider,
                    body=str(session_result["text"]).strip()[:12000],
                    result_receipt_ref=str(session_result["session_ref"]),
                    ui_action=normalize_conductor_ui_action({"kind": "NONE"}),
                )
                return
            invocation = self.runtime_host.invoke_conductor_message(
                runtime_binding=binding,
                message=worker_message,
                history=history,
                provider=provider,
            )
            returned = invocation.get("structured_result")
            if not isinstance(returned, dict):
                raise RuntimeHostError(
                    "WORKER_RESULT_INVALID",
                    "Conductor Worker did not return a structured result object",
                )
            reply_text = returned.get("reply")
            if not isinstance(reply_text, str) or not reply_text.strip():
                raise RuntimeHostError(
                    "WORKER_RESULT_INVALID",
                    "Conductor Worker did not return bounded response text",
                )
            ui_action = normalize_conductor_ui_action(returned.get("action"))
            if ui_action["kind"] == "TODO_DRAFT":
                todo = ui_action["todo"]
                if todo["project_id"] is not None:
                    self.store.get_project(todo["project_id"])
                if todo["scope_kind"] == "NODE":
                    ui_context = claimed.get("ui_context")
                    if (
                        not isinstance(ui_context, dict)
                        or todo["project_id"] != ui_context.get("selected_project_id")
                        or todo["node_ref"] != ui_context.get("selected_node_ref")
                    ):
                        raise RuntimeHostError(
                            "CONDUCTOR_TODO_DRAFT_SCOPE_INVALID",
                            "Node Todo draft must match the selected UI node",
                        )
            self.store.complete_conductor_room_message(
                message_id,
                provider=provider,
                body=reply_text.strip()[:12000],
                result_receipt_ref=str(
                    invocation.get("result_receipt_ref") or "UNKNOWN"
                ),
                ui_action=ui_action,
            )
        except (RuntimeHostError, UniverseError) as error:
            self.store.fail_conductor_room_message(
                message_id,
                code=getattr(error, "code", type(error).__name__),
                reason=str(error),
            )
        except Exception as error:
            self.store.fail_conductor_room_message(
                message_id,
                code="CONDUCTOR_WORKER_FAILED",
                reason=f"{type(error).__name__}: {error}",
            )

    def _resolve_conductor_provider(self, message: dict[str, Any]) -> str:
        requested = str(message.get("requested_provider") or "AUTO").upper()
        configured = self.store.provider_setting(
            "UNIVERSE_CONDUCTOR",
            "CONDUCTOR",
        )["provider"]
        if configured == "AUTO":
            configured = os.environ.get("UNIVERSE_CONDUCTOR_PROVIDER", "AUTO").upper()
        selected = requested if requested in {"GROK", "CODEX"} else configured
        return self._resolve_configured_provider(selected, strict=True)

    def _resolve_project_master_provider(self, project_id: str) -> str:
        selected = self.store.provider_setting(
            "PROJECT_MASTER",
            project_id,
        )["provider"]
        return self._resolve_configured_provider(selected, strict=True)

    def _resolve_configured_provider(
        self,
        selected: str,
        *,
        capabilities: Mapping[str, Mapping[str, Any]] | None = None,
        strict: bool,
    ) -> str:
        normalized = str(selected or "AUTO").strip().upper()
        candidates = (
            [normalized] if normalized in {"GROK", "CODEX"} else ["GROK", "CODEX"]
        )
        unavailable: list[str] = []
        for provider in candidates:
            capability = (
                capabilities.get(provider)
                if capabilities is not None
                else self.runtime_host.provider_capability(provider)
            )
            if capability is None:
                capability = {
                    "status": "UNAVAILABLE",
                    "reason": f"{provider}_CLI_UNAVAILABLE",
                }
            if capability.get("status") == "AVAILABLE":
                return provider
            unavailable.append(f"{provider}:{capability.get('reason', 'UNAVAILABLE')}")
        if not strict:
            return "UNAVAILABLE"
        raise RuntimeHostError(
            "WORKER_PROVIDER_UNAVAILABLE",
            "; ".join(unavailable) or "no Conductor provider is available",
        )

    def notify_maintain_settings_changed(self) -> None:
        """Wake the maintain worker so interval 0/non-zero changes apply soon."""

        self._maintain_wake.set()

    def memory_maintain_worker_status(self) -> dict[str, Any]:
        settings = self.store.get_service_settings()["memory_maintain"]
        return {
            "schema": "universe.memory-maintain-worker.v1",
            "status": "ACTIVE" if settings["enabled"] else "DISABLED",
            "interval_hours": settings["interval_hours"],
            "last_run": self._maintain_last_run,
        }

    def _memory_maintain_worker_loop(self) -> None:
        """In-process maintain batch. interval_hours 0 => idle recheck only."""

        while not self._maintain_stop.is_set():
            settings = self.store.get_service_settings()["memory_maintain"]
            interval_hours = int(settings.get("interval_hours") or 0)
            if interval_hours <= 0:
                # Disabled: wake every 30s or on settings change.
                self._maintain_wake.wait(timeout=30.0)
                self._maintain_wake.clear()
                continue
            try:
                projects = self.store.list_projects()
                results: list[dict[str, Any]] = []
                for project in projects:
                    project_id = project.get("project_id")
                    if not project_id:
                        continue
                    try:
                        outcome = self.store.maintain_project_memories(
                            str(project_id),
                            {
                                "scorer": settings.get("scorer") or "HEURISTIC",
                                "apply_proposals": bool(
                                    settings.get("apply_proposals", True)
                                ),
                                "limit": 50,
                                "per_memory": 1,
                            },
                        )
                        results.append(
                            {
                                "project_id": project_id,
                                "status": outcome.get("status"),
                                "proposal_count": outcome.get("proposal_count"),
                                "applied_count": outcome.get("applied_count"),
                                "batch_kind": outcome.get("batch_kind"),
                            }
                        )
                    except UniverseError as error:
                        results.append(
                            {
                                "project_id": project_id,
                                "status": "FAILED",
                                "error_code": error.code,
                            }
                        )
                self._maintain_last_run = {
                    "ran_at": utc_now(),
                    "interval_hours": interval_hours,
                    "project_count": len(results),
                    "results": results,
                }
            except Exception as error:  # noqa: BLE001 - keep worker alive
                self._maintain_last_run = {
                    "ran_at": utc_now(),
                    "status": "WORKER_ERROR",
                    "error": f"{type(error).__name__}: {error}",
                }
            # Wait interval, but allow early wake on settings change.
            deadline = time.monotonic() + max(60.0, interval_hours * 3600.0)
            while not self._maintain_stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self._maintain_wake.wait(timeout=min(30.0, remaining)):
                    self._maintain_wake.clear()
                    # Settings may have disabled or shortened interval.
                    break

    def server_close(self) -> None:
        if not self._maintain_stop.is_set():
            self._maintain_stop.set()
            self._maintain_wake.set()
            self._maintain_worker.join(timeout=5)
        if not self._conductor_stop.is_set():
            self._conductor_stop.set()
            self._conductor_queue.put(None)
            self._conductor_worker.join(timeout=5)
        if self.project_master_hosts is not None:
            self.project_master_hosts.close()
        if self.conductor_session_host is not None:
            self.conductor_session_host.close()
            self.conductor_session_host = None
        if self.conductor_runtime is not None:
            self.conductor_runtime.stop()
            self.conductor_runtime = None
        super().server_close()


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
                    "mode_contract": self.server.mode_contract,
                    "connection": self.server.connection_profile.as_dict(),
                    "interfaces": [
                        profile.as_dict() for profile in self.server.interface_profiles
                    ],
                },
            )
            return
        if not self._authorize():
            return
        if path == "/v1/runtime/providers":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "RUNTIME_PROVIDER_CAPABILITIES_COLLECTED",
                    "providers": self.server.runtime_host.provider_capabilities(),
                },
            )
            return
        if path == "/v1/settings/providers":
            self._send(
                HTTPStatus.OK,
                self.server.provider_settings(),
            )
            return
        if path == "/v1/settings/service":
            settings = self.server.store.get_service_settings()
            settings["worker"] = self.server.memory_maintain_worker_status()
            self._send(HTTPStatus.OK, settings)
            return
        if path == "/v1/settings/host-tools":
            try:
                self._send(
                    HTTPStatus.OK,
                    self.server.host_tool_settings(),
                )
            except UniverseError as error:
                self._send_error(error)
            return
        if path == "/v1/runtime/planning-binding":
            self._send(
                HTTPStatus.OK,
                self.server.planning_binding_status(),
            )
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
        if path == "/v1/todos":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "TODOS_COLLECTED",
                    "todos": self.server.store.list_todos(),
                    "task_frame_created": False,
                    "execution_assignment_created": False,
                },
            )
            return
        if path == "/v1/conductor-room/messages":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "CONDUCTOR_ROOM_MESSAGES_COLLECTED",
                    "messages": self.server.store.list_conductor_room_messages(),
                    "runtime_binding": self.server.planning_binding_status(),
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
        if path == "/v1/fresh-project-compositions":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "FRESH_PROJECT_COMPOSITIONS_COLLECTED",
                    "compositions": self.server.store.list_fresh_project_compositions(),
                },
            )
            return
        if path == "/v1/fresh-project-composition-adoptions":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "FRESH_PROJECT_COMPOSITION_ADOPTIONS_COLLECTED",
                    "adoptions": (
                        self.server.store.list_fresh_project_composition_adoptions()
                    ),
                },
            )
            return
        if path == "/v1/fresh-project-refinement-requests":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "FRESH_PROJECT_REFINEMENT_REQUESTS_COLLECTED",
                    "requests": self.server.store.list_fresh_project_refinement_requests(),
                },
            )
            return
        if path == "/v1/fresh-project-refinement-runs":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "FRESH_PROJECT_REFINEMENT_RUNS_COLLECTED",
                    "runs": self.server.store.list_fresh_project_refinement_runs(),
                },
            )
            return
        if path == "/v1/fresh-project-refinement-candidates":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "FRESH_PROJECT_REFINEMENT_CANDIDATES_COLLECTED",
                    "candidates": self.server.store.list_fresh_project_refinement_candidates(),
                },
            )
            return
        if path == "/v1/fresh-project-refinement-adoptions":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "FRESH_PROJECT_REFINEMENT_ADOPTIONS_COLLECTED",
                    "adoptions": self.server.store.list_fresh_project_refinement_adoptions(),
                },
            )
            return
        if path == "/v1/bench/skills":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "SKILL_BENCH_COLLECTED",
                    "bench": self.server.store.list_skill_bench(),
                },
            )
            return
        if path == "/v1/bench/compare":
            query_map = parse_qs(urlsplit(self.path).query)
            group_by = (query_map.get("group_by") or ["skill"])[0]
            limit_raw = (query_map.get("limit") or ["50"])[0]
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError):
                limit = 50
            try:
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        **self.server.store.compare_skill_bench(
                            group_by=group_by, limit=limit
                        ),
                    },
                )
            except UniverseError as error:
                self._send_error(error)
            return
        if path == "/v1/career-promotion-queue":
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "CAREER_PROMOTION_QUEUE_COLLECTED",
                    "items": self.server.store.list_career_promotion_queue(),
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
            if suffix == "/room/messages":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_ROOM_MESSAGES_COLLECTED",
                        "project_id": project_id,
                        "messages": self.server.store.list_room_messages(project_id),
                    },
                )
                return
            if suffix == "/room/stream":
                self._stream_project_room(project_id)
                return
            if suffix == "/agent-session/permissions":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "AGENT_PERMISSIONS_COLLECTED",
                        "project_id": project_id,
                        "permissions": self.server.store.list_agent_permissions(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/skill-observations":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_SKILL_OBSERVATIONS_COLLECTED",
                        "project_id": project_id,
                        "observations": self.server.store.list_skill_observations(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/skill-observation-queue":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_SKILL_OBSERVATION_QUEUE_COLLECTED",
                        "project_id": project_id,
                        "items": self.server.store.list_skill_observation_queue(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/context-packs":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_CONTEXT_PACKS_COLLECTED",
                        "project_id": project_id,
                        "context_packs": self.server.store.list_context_packs(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/skill-plan-proposals":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_SKILL_PLAN_PROPOSALS_COLLECTED",
                        "project_id": project_id,
                        "proposals": self.server.store.list_skill_plan_proposals(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/skill-plan-adoptions":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_SKILL_PLAN_ADOPTIONS_COLLECTED",
                        "project_id": project_id,
                        "adoptions": self.server.store.list_skill_plan_adoptions(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/master-handoffs":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MASTER_HANDOFFS_COLLECTED",
                        "project_id": project_id,
                        "handoffs": self.server.store.list_master_handoffs(project_id),
                    },
                )
                return
            if suffix == "/experience-cases":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_EXPERIENCE_CASES_COLLECTED",
                        "project_id": project_id,
                        "cases": self.server.store.list_experience_cases(project_id),
                    },
                )
                return
            if suffix == "/experience-pattern-proposals":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "EXPERIENCE_PATTERN_PROPOSALS_COLLECTED",
                        "project_id": project_id,
                        "proposals": self.server.store.list_experience_pattern_proposals(
                            project_id
                        ),
                    },
                )
                return
            if suffix == "/memories":
                query_map = parse_qs(urlsplit(self.path).query)
                query = (query_map.get("q") or [None])[0]
                link_state = (query_map.get("link_state") or [None])[0]
                node_ref = (query_map.get("node_ref") or [None])[0]
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MEMORIES_COLLECTED",
                        "project_id": project_id,
                        "memories": self.server.store.list_project_memories(
                            project_id,
                            link_state=link_state,
                            node_ref=node_ref,
                            query=query,
                        ),
                    },
                )
                return
            if suffix == "/memories/propose-links":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MEMORY_LINK_PROPOSALS_COLLECTED",
                        **self.server.store.propose_memory_links(project_id),
                    },
                )
                return
            if suffix == "/master-bridge":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MASTER_BRIDGE_COLLECTED",
                        "bridge": self.server.store.get_master_bridge(project_id),
                    },
                )
                return
            if suffix == "/provider-setting":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "CLI_PROVIDER_SETTING_COLLECTED",
                        "setting": self.server.store.provider_setting(
                            "PROJECT_MASTER",
                            project_id,
                        ),
                    },
                )
                return
            if suffix == "/runtime-worker-invocations":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "RUNTIME_WORKER_INVOCATIONS_COLLECTED",
                        "project_id": project_id,
                        "invocations": self.server.store.list_runtime_worker_invocations(
                            project_id
                        ),
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
                        "dispatches": self.server.store.list_dispatches(project_id),
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
                            self.server.store.list_project_release_proposals(project_id)
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
            if suffix == "/seed-asset-proposal":
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_SEED_ASSET_PROPOSAL_READY",
                        "proposal": self.server.store.prepare_project_seed_asset_proposal(
                            project_id
                        ),
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
            if path == "/v1/settings/service":
                settings = self.server.store.set_service_settings(body)
                self.server.notify_maintain_settings_changed()
                self._send(HTTPStatus.OK, settings)
                return
            if path == "/v1/settings/providers/universe":
                self._send(
                    HTTPStatus.OK,
                    self.server.set_universe_provider_setting(body),
                )
                return
            if path == "/v1/settings/host-tools/discover":
                self._send(
                    HTTPStatus.OK,
                    self.server.discover_host_tools(),
                )
                return
            host_tool_match = re.fullmatch(
                r"/v1/settings/host-tools/([^/]+)/(select|verify)",
                path,
            )
            if host_tool_match is not None:
                tool, operation = host_tool_match.groups()
                self._send(
                    HTTPStatus.OK,
                    (
                        self.server.set_host_tool(unquote(tool), body)
                        if operation == "select"
                        else self.server.verify_host_tool(unquote(tool))
                    ),
                )
                return
            if path == "/v1/runtime/planning-binding":
                self._send(
                    HTTPStatus.OK,
                    self.server.bind_planning_runtime(body),
                )
                return
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
                        "status": "PROJECT_REGISTERED"
                        if created
                        else "PROJECT_REFRESHED",
                        "project": project,
                    },
                )
                return
            if path == "/v1/todos":
                todo = self.server.store.create_todo(body)
                self._send(
                    HTTPStatus.CREATED,
                    {
                        "schema": API_SCHEMA,
                        "status": "TODO_RECORDED",
                        "todo": todo,
                        "task_frame_created": False,
                        "execution_assignment_created": False,
                    },
                )
                return
            if path == "/v1/conductor-room/messages":
                message, created = self.server.store.create_conductor_room_message(body)
                if created:
                    self.server.enqueue_conductor_message(message["message_id"])
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "CONDUCTOR_ROOM_MESSAGE_RECORDED"
                            if created
                            else "CONDUCTOR_ROOM_MESSAGE_ALREADY_RECORDED"
                        ),
                        "message": message,
                        "runtime_binding": self.server.planning_binding_status(),
                    },
                )
                return
            if path == "/v1/skill-observation-queue/drain":
                request = _exact_object_fields(
                    body,
                    field="skill_observation_queue_drain",
                    required=frozenset(),
                    optional=frozenset({"limit"}),
                )
                limit = request.get("limit", 100)
                if isinstance(limit, bool) or not isinstance(limit, int):
                    raise UniverseError(
                        "SKILL_OBSERVATION_QUEUE_LIMIT_INVALID",
                        "limit must be an integer",
                    )
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        **self.server.store.drain_skill_observation_queue(limit=limit),
                    },
                )
                return
            if path == "/v1/future-paths":
                intent = normalize_fresh_project_intent(body)
                try:
                    proposal = suggest_paths(
                        OFFICIAL_SEED_DATABASE,
                        project=intent["project"],
                        kind=intent["kind"],
                        technologies=intent["technologies"],
                        goal=intent["goal"],
                        limit=intent["limit"],
                    )
                except SeedError as error:
                    raise UniverseError(
                        "FRESH_PROJECT_INTENT_INVALID", str(error)
                    ) from error
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "FRESH_PROJECT_ROUTE_CANDIDATES",
                        "intent": intent,
                        "proposal": proposal,
                    },
                )
                return
            if path == "/v1/fresh-project-compositions":
                composition, created = (
                    self.server.store.create_fresh_project_composition(body)
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "FRESH_PROJECT_COMPOSITION_PROPOSAL_READY"
                            if created
                            else "FRESH_PROJECT_COMPOSITION_ALREADY_RECORDED"
                        ),
                        "composition": composition,
                    },
                )
                return
            if path == "/v1/fresh-project-composition-adoptions":
                adoption, created = self.server.store.adopt_fresh_project_composition(
                    body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "FRESH_PROJECT_COMPOSITION_ADOPTED"
                            if created
                            else "FRESH_PROJECT_COMPOSITION_ADOPTION_ALREADY_RECORDED"
                        ),
                        "adoption": adoption,
                    },
                )
                return
            if path == "/v1/fresh-project-refinement-requests":
                request, created = (
                    self.server.store.create_fresh_project_refinement_request(body)
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "FRESH_PROJECT_REFINEMENT_REQUEST_READY"
                            if created
                            else "FRESH_PROJECT_REFINEMENT_REQUEST_ALREADY_RECORDED"
                        ),
                        "request": request,
                    },
                )
                return
            if path == "/v1/fresh-project-refinement-runs":
                run_request = normalize_fresh_project_refinement_run_request(body)
                binding = self.server.require_planning_binding()
                refinement_request = (
                    self.server.store.get_fresh_project_refinement_request(
                        run_request["request_id"]
                    )
                )
                run_id = "planningrun_" + uuid.uuid4().hex[:24]
                try:
                    planning = self.server.runtime_host.build_planning_proposal(
                        runtime_binding=binding,
                        refinement_request=refinement_request,
                        provider=run_request["provider"],
                        run_id=run_id,
                    )
                except RuntimeHostError as error:
                    raise UniverseError(
                        error.code,
                        error.detail,
                        HTTPStatus.CONFLICT,
                    ) from error
                run = self.server.store.record_fresh_project_refinement_run(
                    refinement_request,
                    planning,
                    run_id=run_id,
                )
                self._send(
                    HTTPStatus.CREATED,
                    {
                        "schema": API_SCHEMA,
                        "status": "FRESH_PROJECT_REFINEMENT_RUN_PROPOSED",
                        "run": run,
                    },
                )
                return
            refinement_run_id = self._fresh_project_refinement_run_path(path)
            if refinement_run_id is not None:
                approval = normalize_fresh_project_refinement_run_approval(body)
                binding = self.server.require_planning_binding()
                with self.server.planning_execution_lock:
                    run, execute = self.server.store.claim_fresh_project_refinement_run(
                        refinement_run_id,
                        approval,
                    )
                    if not execute:
                        candidate = (
                            self.server.store.get_fresh_project_refinement_candidate(
                                run["candidate_id"]
                            )
                        )
                        self._send(
                            HTTPStatus.OK,
                            {
                                "schema": API_SCHEMA,
                                "status": (
                                    "FRESH_PROJECT_REFINEMENT_RUN_ALREADY_COMPLETED"
                                ),
                                "run": run,
                                "candidate": candidate,
                            },
                        )
                        return
                    refinement_request = (
                        self.server.store.get_fresh_project_refinement_request(
                            run["request_id"]
                        )
                    )
                    runtime_approval = {
                        "status": "APPROVED",
                        "proposal_id": run["proposal_id"],
                        "plan_digest": run["plan_digest"],
                        "commander_surface": "universe-ui",
                        "evidence_ref": (
                            "universe-ui://fresh-project-refinement-runs/"
                            + run["run_id"]
                            + "/approval"
                        ),
                    }
                    try:
                        result = self.server.runtime_host.invoke_structured_planning(
                            runtime_binding=binding,
                            run=run,
                            refinement_request=refinement_request,
                            approval=runtime_approval,
                        )
                        worker_output = (
                            normalize_fresh_project_refinement_worker_output(
                                result.get("structured_result")
                            )
                        )
                        candidate, candidate_created = (
                            self.server.store.record_fresh_project_refinement_candidate(
                                {
                                    "schema": (
                                        FRESH_PROJECT_REFINEMENT_CANDIDATE_SCHEMA
                                    ),
                                    "request_id": refinement_request["request_id"],
                                    "request_digest": (
                                        refinement_request["request_digest"]
                                    ),
                                    "composition_id": (
                                        refinement_request["composition_id"]
                                    ),
                                    "composition_digest": (
                                        refinement_request["composition_digest"]
                                    ),
                                    "producer": {
                                        "provider": run["provider"],
                                        "model_ref": run["model_ref"],
                                        "worker_id": _required_text(
                                            result.get("worker_id"),
                                            "worker result worker_id",
                                        ),
                                        "result_receipt_ref": _required_text(
                                            result.get("result_receipt_ref"),
                                            "worker result result_receipt_ref",
                                        ),
                                    },
                                    "refinement": worker_output["refinement"],
                                }
                            )
                        )
                        run = self.server.store.complete_fresh_project_refinement_run(
                            run["run_id"],
                            candidate=candidate,
                            result_receipt_ref=candidate["producer"][
                                "result_receipt_ref"
                            ],
                        )
                    except RuntimeHostError as error:
                        self.server.store.fail_fresh_project_refinement_run(
                            run["run_id"],
                            error_code=error.code,
                        )
                        raise UniverseError(
                            error.code,
                            error.detail,
                            HTTPStatus.CONFLICT,
                        ) from error
                    except UniverseError as error:
                        self.server.store.fail_fresh_project_refinement_run(
                            run["run_id"],
                            error_code=error.code,
                        )
                        raise
                self._send(
                    HTTPStatus.CREATED if candidate_created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "FRESH_PROJECT_REFINEMENT_RUN_COMPLETED",
                        "run": run,
                        "candidate": candidate,
                    },
                )
                return
            if path == "/v1/fresh-project-refinement-candidates":
                candidate, created = (
                    self.server.store.record_fresh_project_refinement_candidate(body)
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "FRESH_PROJECT_REFINEMENT_CANDIDATE_READY"
                            if created
                            else "FRESH_PROJECT_REFINEMENT_CANDIDATE_ALREADY_RECORDED"
                        ),
                        "candidate": candidate,
                    },
                )
                return
            if path == "/v1/fresh-project-refinement-adoptions":
                adoption, composition, created = (
                    self.server.store.adopt_fresh_project_refinement(body)
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "FRESH_PROJECT_REFINEMENT_ADOPTED"
                            if created
                            else "FRESH_PROJECT_REFINEMENT_ADOPTION_ALREADY_RECORDED"
                        ),
                        "adoption": adoption,
                        "composition": composition,
                    },
                )
                return
            handoff_parts = self._master_handoff_path(path)
            if handoff_parts is not None and handoff_parts[1] == "/deliver":
                handoff, delivered, application = self.server.deliver_master_handoff(
                    handoff_parts[0], handoff_parts[1 + 1], body
                )
                result = {
                    "schema": API_SCHEMA,
                    "status": (
                        "PROJECT_MASTER_HANDOFF_DELIVERED"
                        if delivered
                        else "PROJECT_MASTER_HANDOFF_ALREADY_DELIVERED"
                    ),
                    "handoff": handoff,
                }
                if application is not None:
                    result["skill_plan_application"] = application
                self._send(
                    HTTPStatus.OK,
                    result,
                )
                return
            permission_parts = self._agent_permission_path(path)
            if permission_parts is not None:
                permission, changed = self.server.resolve_agent_permission(
                    permission_parts[0],
                    permission_parts[1],
                    body,
                )
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "AGENT_PERMISSION_RESOLVED"
                            if changed
                            else "AGENT_PERMISSION_ALREADY_RESOLVED"
                        ),
                        "permission": permission,
                    },
                )
                return
            parts = self._project_path(path)
            if parts is not None and parts[1] == "/master-session/prepare":
                self._send(
                    HTTPStatus.OK,
                    self.server.prepare_project_master_session(parts[0]),
                )
                return
            if parts is not None and parts[1] == "/provider-setting":
                self._send(
                    HTTPStatus.OK,
                    self.server.set_project_provider_setting(parts[0], body),
                )
                return
            if parts is not None and parts[1] == "/seed-asset-proposal/apply":
                self._send(
                    HTTPStatus.OK,
                    self.server.apply_project_seed_assets(parts[0], body),
                )
                return
            if parts is not None and parts[1] == "/release-proposals/apply":
                self._send(
                    HTTPStatus.OK,
                    self.server.apply_project_release(parts[0], body),
                )
                return
            if parts is not None and parts[1] == "/release-proposals":
                proposal, created = self.server.store.create_project_release_proposal(
                    parts[0],
                    body,
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
                dispatch, created = (
                    self.server.store.create_project_seed_discovery_dispatch(parts[0])
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
            if parts is not None and parts[1] == "/runtime-worker-invocations":
                invocation, created = self.server.store.invoke_runtime_worker(
                    parts[0], body, self.server.runtime_host
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "RUNTIME_WORKER_INVOCATION_RECORDED"
                            if created
                            else "RUNTIME_WORKER_INVOCATION_ALREADY_RECORDED"
                        ),
                        "invocation": invocation,
                    },
                )
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
                            "DISPATCH_QUEUED" if created else "DISPATCH_ALREADY_QUEUED"
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
                        "status": "PROJECT_EVENT_APPENDED"
                        if created
                        else "PROJECT_EVENT_ALREADY_RECORDED",
                        "event": event,
                    },
                )
                return
            if parts is not None and parts[1] == "/room/messages":
                message, created = self.server.send_project_room_message(parts[0], body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_ROOM_MESSAGE_RECORDED"
                        if created
                        else "PROJECT_ROOM_MESSAGE_ALREADY_RECORDED",
                        "message": message,
                    },
                )
                return
            if parts is not None and parts[1] == "/master-bridge/stream":
                credential = self.headers.get("X-Universe-Bridge-Token")
                stream_event = normalize_master_bridge_stream(parts[0], body)
                self.server.store.validate_master_bridge_credential(
                    parts[0],
                    stream_event["bridge_id"],
                    credential,
                )
                published = self.server.project_room_events.publish(
                    parts[0],
                    {
                        "type": "MASTER_STREAM",
                        **stream_event,
                    },
                )
                self._send(
                    HTTPStatus.ACCEPTED,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MASTER_STREAM_EVENT_ACCEPTED",
                        "event_id": published["event_id"],
                    },
                )
                return
            if parts is not None and parts[1] == "/master-bridge/permissions":
                credential = self.headers.get("X-Universe-Bridge-Token")
                request = normalize_master_bridge_permission(parts[0], body)
                self.server.store.validate_master_bridge_credential(
                    parts[0],
                    request["bridge_id"],
                    credential,
                )
                permission, created = self.server.store.record_agent_permission(
                    parts[0],
                    request["in_reply_to"],
                    request["permission"],
                )
                self.server.publish_agent_permission(parts[0], permission)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "AGENT_PERMISSION_REQUESTED"
                            if created
                            else "AGENT_PERMISSION_ALREADY_REQUESTED"
                        ),
                        "permission": permission,
                    },
                )
                return
            if parts is not None and parts[1] == "/skill-observations":
                result, created = self.server.store.ingest_skill_observations(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "SKILL_OBSERVATIONS_INGESTED"
                            if created
                            else "SKILL_OBSERVATIONS_ALREADY_INGESTED"
                        ),
                        **result,
                    },
                )
                return
            if parts is not None and parts[1] == "/skill-observation-queue":
                item, created = self.server.store.enqueue_skill_observations(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "SKILL_OBSERVATION_QUEUED"
                            if created
                            else "SKILL_OBSERVATION_ALREADY_QUEUED"
                        ),
                        "item": item,
                    },
                )
                return
            if parts is not None and parts[1] == "/context-packs":
                pack, created = self.server.store.create_context_pack(parts[0], body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "CONTEXT_PACK_READY"
                            if created
                            else "CONTEXT_PACK_ALREADY_RECORDED"
                        ),
                        "context_pack": pack,
                    },
                )
                return
            if parts is not None and parts[1] == "/skill-plan-proposals":
                proposal, created = self.server.store.create_skill_plan_proposal(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "SKILL_PLAN_PROPOSAL_READY"
                            if created
                            else "SKILL_PLAN_PROPOSAL_ALREADY_RECORDED"
                        ),
                        "proposal": proposal,
                    },
                )
                return
            if parts is not None and parts[1] == "/skill-plan-adoptions":
                adoption, created = self.server.store.adopt_skill_plan(parts[0], body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "SKILL_PLAN_ADOPTED"
                            if created
                            else "SKILL_PLAN_ADOPTION_ALREADY_RECORDED"
                        ),
                        "adoption": adoption,
                    },
                )
                return
            if parts is not None and parts[1] == "/master-handoffs":
                handoff, created = self.server.store.create_master_handoff(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_MASTER_HANDOFF_PROPOSAL_RECORDED"
                            if created
                            else "PROJECT_MASTER_HANDOFF_PROPOSAL_ALREADY_RECORDED"
                        ),
                        "handoff": handoff,
                    },
                )
                return
            if parts is not None and parts[1] == "/experience-cases":
                case, created = self.server.store.create_experience_case(parts[0], body)
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "EXPERIENCE_CASE_RECORDED"
                            if created
                            else "EXPERIENCE_CASE_ALREADY_RECORDED"
                        ),
                        "case": case,
                    },
                )
                return
            if parts is not None and parts[1] == "/memories":
                memory = self.server.store.create_project_memory(parts[0], body)
                self._send(
                    HTTPStatus.CREATED,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MEMORY_RECORDED",
                        "memory": memory,
                    },
                )
                return
            if parts is not None and parts[1] == "/memories/link":
                memory_id = _identifier(body.get("memory_id"), "memory_id")
                memory = self.server.store.link_project_memory(
                    parts[0], memory_id, body
                )
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "PROJECT_MEMORY_LINKED",
                        "memory": memory,
                    },
                )
                return
            if parts is not None and parts[1] == "/memories/maintain":
                result = self.server.store.maintain_project_memories(parts[0], body)
                self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
                return
            if parts is not None and parts[1] == "/experience-cases/from-observations":
                result = self.server.store.create_experience_cases_from_unlinked_observations(
                    parts[0], body
                )
                self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
                return
            if parts is not None and parts[1] == "/experience-patterns/auto":
                result = self.server.store.auto_experience_pattern_proposals(
                    parts[0], body
                )
                self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
                return
            if parts is not None and parts[1] == "/experience-matches":
                result = self.server.store.match_experience_case(parts[0], body)
                self._send(HTTPStatus.OK, {"schema": API_SCHEMA, **result})
                return
            if parts is not None and parts[1] == "/experience-pattern-proposals":
                proposal, created = (
                    self.server.store.create_experience_pattern_proposal(parts[0], body)
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "EXPERIENCE_PATTERN_PROPOSAL_RECORDED"
                            if created
                            else "EXPERIENCE_PATTERN_PROPOSAL_ALREADY_RECORDED"
                        ),
                        "proposal": proposal,
                    },
                )
                return
            if parts is not None and parts[1] == "/career-promotion-queue":
                item, created = self.server.store.queue_career_promotion_candidate(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "CAREER_PROMOTION_CANDIDATE_QUEUED"
                            if created
                            else "CAREER_PROMOTION_CANDIDATE_ALREADY_QUEUED"
                        ),
                        "item": item,
                    },
                )
                return
            if parts is not None and parts[1] == "/master-bridge":
                bridge, created = self.server.store.register_master_bridge(
                    parts[0], body
                )
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_MASTER_BRIDGE_REGISTERED"
                            if created
                            else "PROJECT_MASTER_BRIDGE_REFRESHED"
                        ),
                        "bridge": bridge,
                    },
                )
                return
            if parts is not None and parts[1] == "/master-bridge/replies":
                credential = self.headers.get("X-Universe-Bridge-Token")
                message, created = self.server.store.append_master_bridge_reply(
                    parts[0], body, credential
                )
                self.server.publish_project_room_changed(parts[0])
                self._send(
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": (
                            "PROJECT_MASTER_REPLY_RECORDED"
                            if created
                            else "PROJECT_MASTER_REPLY_ALREADY_RECORDED"
                        ),
                        "message": message,
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
            if parts is not None and parts[1] == "/document-incorporation-proposals":
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

    def do_PATCH(self) -> None:
        if not self._authorize():
            return
        todo_id = self._todo_path(urlsplit(self.path).path)
        if todo_id is None:
            self._not_found()
            return
        try:
            todo = self.server.store.update_todo(todo_id, self._read_json())
            self._send(
                HTTPStatus.OK,
                {
                    "schema": API_SCHEMA,
                    "status": "TODO_UPDATED",
                    "todo": todo,
                    "task_frame_created": False,
                    "execution_assignment_created": False,
                },
            )
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
        path = urlsplit(self.path).path
        todo_id = self._todo_path(path)
        if todo_id is not None:
            try:
                result = self.server.store.delete_todo(todo_id)
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema": API_SCHEMA,
                        "status": "TODO_DELETED",
                        **result,
                        "task_frame_created": False,
                        "execution_assignment_created": False,
                    },
                )
            except UniverseError as error:
                self._send_error(error)
            return
        parts = self._project_path(path)
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
        try:
            if ipaddress.ip_address(self.client_address[0]).is_loopback:
                return True
        except ValueError:
            pass
        self._send(
            HTTPStatus.FORBIDDEN,
            {
                "schema": API_SCHEMA,
                "status": "FORBIDDEN",
                "error_code": "LOOPBACK_CLIENT_REQUIRED",
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
        remainder = path[len(prefix) :]
        for suffix in (
            "/master-bridge/permissions",
            "/master-bridge/stream",
            "/master-bridge/replies",
            "/master-bridge",
            "/master-session/prepare",
            "/agent-session/permissions",
            "/provider-setting",
            "/room/stream",
            "/room/messages",
            "/document-incorporation-proposals",
            "/skill-plan-proposals",
            "/skill-plan-adoptions",
            "/master-handoffs",
            "/experience-cases/from-observations",
            "/experience-cases",
            "/experience-matches",
            "/experience-patterns/auto",
            "/experience-pattern-proposals",
            "/memories/propose-links",
            "/memories/maintain",
            "/memories/link",
            "/memories",
            "/career-promotion-queue",
            "/context-packs",
            "/release-proposals/apply",
            "/release-proposals",
            "/runtime-worker-invocations",
            "/dispatches",
            "/discovery-dispatch",
            "/projection",
            "/events",
            "/skill-observations",
            "/skill-observation-queue",
            "/seed-asset-proposal/apply",
            "/seed-asset-proposal",
            "/seed",
            "/sync",
        ):
            if remainder.endswith(suffix):
                return unquote(remainder[: -len(suffix)]), suffix
        return unquote(remainder), ""

    @staticmethod
    def _agent_permission_path(path: str) -> tuple[str, str] | None:
        prefix = "/v1/projects/"
        marker = "/agent-session/permissions/"
        suffix = "/decision"
        if (
            not path.startswith(prefix)
            or marker not in path
            or not path.endswith(suffix)
        ):
            return None
        remainder = path[len(prefix) :]
        project_id, request_path = remainder.split(marker, 1)
        request_id = request_path[: -len(suffix)]
        if not project_id or "/" in project_id or not request_id or "/" in request_id:
            return None
        return unquote(project_id), unquote(request_id)

    @staticmethod
    def _release_path(path: str) -> str | None:
        prefix = "/v1/releases/"
        if not path.startswith(prefix):
            return None
        release_id = unquote(path[len(prefix) :])
        if not release_id or "/" in release_id:
            return None
        return release_id

    @staticmethod
    def _todo_path(path: str) -> str | None:
        prefix = "/v1/todos/"
        if not path.startswith(prefix):
            return None
        todo_id = unquote(path[len(prefix) :])
        if not todo_id or "/" in todo_id:
            return None
        return todo_id

    @staticmethod
    def _fresh_project_refinement_run_path(path: str) -> str | None:
        prefix = "/v1/fresh-project-refinement-runs/"
        suffix = "/execute"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        run_id = unquote(path[len(prefix) : -len(suffix)])
        if not run_id or "/" in run_id:
            return None
        return run_id

    @staticmethod
    def _dispatch_path(path: str) -> tuple[str, str] | None:
        prefix = "/v1/dispatches/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix) :]
        for suffix in (
            "/acknowledge",
            "/deliver",
            "/result",
            "/start",
            "/wake",
        ):
            if remainder.endswith(suffix):
                return unquote(remainder[: -len(suffix)]), suffix
        return unquote(remainder), ""

    @staticmethod
    def _master_handoff_path(path: str) -> tuple[str, str, str] | None:
        prefix = "/v1/projects/"
        suffix = "/master-handoffs/"
        if not path.startswith(prefix) or suffix not in path:
            return None
        project_id, remainder = path[len(prefix) :].split(suffix, 1)
        if not project_id or not remainder.endswith("/deliver"):
            return None
        handoff_id = remainder[: -len("/deliver")]
        if not handoff_id or "/" in handoff_id:
            return None
        return unquote(project_id), "/deliver", unquote(handoff_id)

    def _not_found(self) -> None:
        self._send(
            HTTPStatus.NOT_FOUND,
            {
                "schema": API_SCHEMA,
                "status": "NOT_FOUND",
                "error_code": "ROUTE_NOT_FOUND",
            },
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

    def _stream_project_room(self, project_id: str) -> None:
        self.server.store.get_project(project_id)
        cursor = self.server.project_room_events.cursor()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self._write_sse(
                cursor,
                {
                    "schema": PROJECT_ROOM_STREAM_SCHEMA,
                    "event_id": cursor,
                    "project_id": project_id,
                    "emitted_at": utc_now(),
                    "payload": {
                        "type": "SNAPSHOT",
                        "messages": self.server.store.list_room_messages(project_id),
                        "permissions": self.server.store.list_agent_permissions(
                            project_id
                        ),
                    },
                },
            )
            while True:
                events = self.server.project_room_events.wait(
                    project_id,
                    after_event_id=cursor,
                    timeout_seconds=15.0,
                )
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    cursor = int(event["event_id"])
                    self._write_sse(cursor, event)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _write_sse(self, event_id: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        frame = f"id: {event_id}\nevent: project-room\ndata: {body}\n\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

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
        content_type = (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
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
    *,
    database_path: Path,
    token: str,
    host: str = "127.0.0.1",
    port: int = 0,
    runtime_host: UniverseRuntimeHost | None = None,
    mode_contract: dict[str, Any] | None = None,
    auto_start_conductor_runtime: bool = False,
    conductor_runtime_factory: Any = None,
    conductor_session_provider_factory: Any = None,
    auto_start_project_masters: bool = True,
    project_master_provider_factory: Any = None,
    host_profile: HostProfileStore | None = None,
) -> UniverseHTTPServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise UniverseError(
            "SERVER_HOST_INVALID", "host must be a literal loopback IP address"
        ) from error
    if not address.is_loopback:
        raise UniverseError(
            "SERVER_HOST_FORBIDDEN",
            "Universe local service may listen only on loopback",
        )
    return UniverseHTTPServer(
        (host, port),
        UniverseStore(database_path),
        _required_text(token, "token"),
        runtime_host,
        mode_contract,
        auto_start_conductor_runtime=auto_start_conductor_runtime,
        conductor_runtime_factory=conductor_runtime_factory,
        conductor_session_provider_factory=conductor_session_provider_factory,
        auto_start_project_masters=auto_start_project_masters,
        project_master_provider_factory=project_master_provider_factory,
        host_profile=host_profile,
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
    *,
    endpoint: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    profile = local_connection_profile(endpoint)
    transport: UniverseTransport = HttpUniverseTransport(
        profile,
        auth_provider_for(profile, token),
    )
    return transport.request_json(method=method, path=path, payload=payload)


def publish_skill_observation(
    *,
    project_id: str,
    prepared: Any,
    publication_approval: Any,
    endpoint: str,
    token: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(prepared, dict):
        raise UniverseError(
            "SKILL_OBSERVATION_PREPARED_INVALID",
            "prepared Skill observation must be an object",
        )
    if (
        prepared.get("status") != "PREPARED"
        or prepared.get("command") != "SKILL_OBSERVATION"
    ):
        raise UniverseError(
            "SKILL_OBSERVATION_PREPARED_INVALID",
            "candidate must be a PREPARED SKILL_OBSERVATION artifact",
        )
    envelope = {
        "candidate_id": prepared.get("candidate_id"),
        "candidate": prepared.get("candidate"),
        "publication_approval": publication_approval,
    }
    normalized_project = _project_id(project_id)
    normalized = normalize_skill_observation_publication(normalized_project, envelope)
    _, health = request_json(
        endpoint=endpoint,
        token=token,
        method="GET",
        path="/health",
    )
    universe = health.get("universe")
    if not isinstance(universe, dict):
        raise UniverseError(
            "UNIVERSE_IDENTITY_UNAVAILABLE",
            "Universe service health response did not contain an identity",
        )
    universe_id = _required_text(universe.get("universe_id"), "universe.universe_id")
    status, response = request_json(
        endpoint=endpoint,
        token=token,
        method="POST",
        path=(
            "/v1/projects/"
            + quote(normalized_project, safe="")
            + "/skill-observation-queue"
        ),
        payload=envelope,
    )
    if not 200 <= status < 300:
        return status, response
    item = response.get("item")
    if not isinstance(item, dict):
        raise UniverseError(
            "SKILL_OBSERVATION_QUEUE_RESPONSE_INVALID",
            "Universe queue response did not contain an item",
        )
    candidate_digest = _required_text(
        item.get("candidate_digest"), "response.item.candidate_digest"
    )
    queue_id = _required_text(item.get("queue_id"), "response.item.queue_id")
    result_ref = (
        f"universe://{universe_id}/projects/{normalized_project}/"
        f"skill-observation-queue/{queue_id}"
    )
    receipt = {
        "schema": "universe.skill-observation-publication-receipt.v1",
        "status": (
            "UNIVERSE_SKILL_OBSERVATION_QUEUED"
            if response.get("status") == "SKILL_OBSERVATION_QUEUED"
            else "UNIVERSE_SKILL_OBSERVATION_ALREADY_QUEUED"
        ),
        "operation_class": "UNIVERSE_OBSERVATION_QUEUE",
        "provider": "UNIVERSE_LOCAL_HTTP",
        "selection_ref": normalized["publication_approval"]["selection_ref"],
        "approval_evidence_ref": normalized["publication_approval"]["evidence_ref"],
        "publication_approval_digest": normalized["publication_approval_digest"],
        "approved_by": normalized["publication_approval"]["approver"],
        "base_source_ref": normalized["candidate"]["source_ref"],
        "candidate_id": normalized["candidate_id"],
        "candidate_digest": candidate_digest,
        "queue_id": queue_id,
        "queue_state": _required_text(item.get("status"), "response.item.status"),
        "result_ref": result_ref,
        "provider_receipt_ref": result_ref + "/receipt",
        "durability": "UNIVERSE_LOCAL_SQLITE",
        "project_archive_write": "NOT_PERFORMED",
    }
    return status, receipt


def prepare_skill_observation_archive(
    *,
    project_id: str,
    receipt: Any,
    selection_ref: str,
    archive_path: str,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise UniverseError(
            "PROJECT_ARCHIVE_RECEIPT_INVALID",
            "Universe Skill observation receipt must be an object",
        )
    if (
        receipt.get("schema") != "universe.skill-observation-publication-receipt.v1"
        or receipt.get("operation_class")
        not in {"UNIVERSE_BENCH_INGEST", "UNIVERSE_OBSERVATION_QUEUE"}
        or receipt.get("project_archive_write") != "NOT_PERFORMED"
    ):
        raise UniverseError(
            "PROJECT_ARCHIVE_RECEIPT_INVALID",
            "receipt must be an unarchived Universe Skill observation ingest receipt",
        )
    normalized_project = _project_id(project_id)
    normalized_path = archive_path.replace("\\", "/")
    parts = normalized_path.split("/")
    if (
        len(parts) < 3
        or parts[:2] != [".ai", "archive"]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise UniverseError(
            "PROJECT_ARCHIVE_PATH_INVALID",
            "archive_path must be a normalized file path below .ai/archive/",
        )
    result_ref = _required_text(receipt.get("result_ref"), "receipt.result_ref")
    expected_project_segment = f"/projects/{normalized_project}/"
    if expected_project_segment not in result_ref:
        raise UniverseError(
            "PROJECT_ARCHIVE_PROJECT_MISMATCH",
            "receipt result_ref is not bound to the requested Project",
            HTTPStatus.CONFLICT,
        )
    material: dict[str, Any] = {
        "schema": PROJECT_ARCHIVE_RECEIPT_CANDIDATE_SCHEMA,
        "status": "PROJECT_ARCHIVE_RECEIPT_CANDIDATE_READY",
        "operation_class": "HANDOFF_APPEND",
        "project_ref": f"project://{normalized_project}",
        "archive_path": normalized_path,
        "selection_ref": _required_text(selection_ref, "selection_ref"),
        "source": {
            "universe_result_ref": result_ref,
            "universe_provider_receipt_ref": _required_text(
                receipt.get("provider_receipt_ref"), "receipt.provider_receipt_ref"
            ),
            "candidate_id": _identifier(
                receipt.get("candidate_id"), "receipt.candidate_id"
            ),
            "candidate_digest": _sha256(
                receipt.get("candidate_digest"), "receipt.candidate_digest"
            ),
            "base_source_ref": _required_text(
                receipt.get("base_source_ref"), "receipt.base_source_ref"
            ),
        },
        "project_archive_write": "NOT_PERFORMED",
        "provider_write_evidence": "NOT_OBSERVED",
        "effects": {
            "project_archive_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
        },
        "next_operation": "PROJECT_OWNED_HANDOFF_APPEND",
    }
    material["candidate_digest"] = _json_sha256(material)
    material["candidate_id"] = "archivecandidate_" + material["candidate_digest"][:24]
    return material


def load_prepared_skill_observation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UniverseError(
            "SKILL_OBSERVATION_PREPARED_UNAVAILABLE", str(error)
        ) from error
    if not isinstance(value, dict):
        raise UniverseError(
            "SKILL_OBSERVATION_PREPARED_INVALID",
            "prepared Skill observation file must contain an object",
        )
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Universe local application service")
    commands = root.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--database", type=Path, default=default_database_path())
    serve.add_argument("--state-file", type=Path, default=default_state_path())
    serve.add_argument("--token", default="")
    serve.add_argument(
        "--open-ui",
        dest="open_ui",
        action="store_true",
        help="Open the local UI with its one-time session token (default).",
    )
    serve.add_argument(
        "--no-open-ui",
        dest="open_ui",
        action="store_false",
        help="Keep the local service headless.",
    )
    serve.set_defaults(open_ui=True)
    serve.add_argument(
        "--mode-registry", type=Path, default=default_mode_registry_path()
    )

    status_command = commands.add_parser(
        "status",
        help="Show local service PID/health from server.json",
    )
    status_command.add_argument(
        "--state-file", type=Path, default=default_state_path()
    )

    start_command = commands.add_parser(
        "start",
        help="Start the local service in the background if it is not READY",
    )
    start_command.add_argument(
        "--state-file", type=Path, default=default_state_path()
    )
    start_command.add_argument(
        "--database", type=Path, default=default_database_path()
    )
    start_command.add_argument(
        "--mode-registry", type=Path, default=default_mode_registry_path()
    )
    start_command.add_argument(
        "--open-ui",
        dest="open_ui",
        action="store_true",
        help="Open the local UI after start (default).",
    )
    start_command.add_argument(
        "--no-open-ui",
        dest="open_ui",
        action="store_false",
        help="Start headless without opening a browser.",
    )
    start_command.set_defaults(open_ui=True)

    stop_command = commands.add_parser(
        "stop",
        help="Stop the local service process recorded in server.json",
    )
    stop_command.add_argument("--state-file", type=Path, default=default_state_path())

    restart_command = commands.add_parser(
        "restart",
        help="Stop then start the local service",
    )
    restart_command.add_argument(
        "--state-file", type=Path, default=default_state_path()
    )
    restart_command.add_argument(
        "--database", type=Path, default=default_database_path()
    )
    restart_command.add_argument(
        "--mode-registry", type=Path, default=default_mode_registry_path()
    )
    restart_command.add_argument(
        "--open-ui",
        dest="open_ui",
        action="store_true",
        help="Open the local UI after restart.",
    )
    restart_command.add_argument(
        "--no-open-ui",
        dest="open_ui",
        action="store_false",
        help="Restart headless (default).",
    )
    restart_command.set_defaults(open_ui=False)

    tray_command = commands.add_parser(
        "tray",
        help="Start the Windows system-tray host (packaging/windows/Universe-Tray.ps1)",
    )
    tray_command.add_argument(
        "--start-service",
        action="store_true",
        help="Start the local service when the tray opens",
    )

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

    publish = commands.add_parser("publish-skill-observation")
    publish.add_argument("--project-id", required=True)
    publish.add_argument("--candidate-file", type=Path, required=True)
    publish.add_argument("--approval-file", type=Path, required=True)
    publish.add_argument("--endpoint", default="")
    publish.add_argument("--token", default="")
    publish.add_argument("--state-file", type=Path, default=default_state_path())

    drain_observation_queue = commands.add_parser("drain-skill-observation-queue")
    drain_observation_queue.add_argument("--limit", type=int, default=100)
    drain_observation_queue.add_argument("--endpoint", default="")
    drain_observation_queue.add_argument("--token", default="")
    drain_observation_queue.add_argument(
        "--state-file", type=Path, default=default_state_path()
    )

    archive = commands.add_parser("prepare-skill-observation-archive")
    archive.add_argument("--project-id", required=True)
    archive.add_argument("--receipt-file", type=Path, required=True)
    archive.add_argument("--selection-ref", required=True)
    archive.add_argument("--archive-path", required=True)
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
        if args.command in {"status", "start", "stop", "restart", "tray"}:
            from universe_service_control import (
                restart_service,
                service_status,
                start_service,
                stop_service,
            )

            if args.command == "tray":
                if os.name != "nt":
                    print(
                        json.dumps(
                            {
                                "schema": "universe.local-service-control.v1",
                                "status": "TRAY_UNSUPPORTED",
                                "detail": "System tray host is Windows-only in this slice",
                            },
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    return 1
                tray_script = (
                    Path(__file__).resolve().parents[1]
                    / "packaging"
                    / "windows"
                    / "Universe-Tray.ps1"
                )
                if not tray_script.is_file():
                    raise UniverseError(
                        "TRAY_SCRIPT_UNAVAILABLE",
                        f"missing tray script: {tray_script}",
                    )
                tray_args = [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(tray_script),
                    "-UniverseRoot",
                    str(Path(__file__).resolve().parents[1]),
                ]
                if args.start_service:
                    tray_args.append("-StartService")
                # Detach tray so this CLI can return after launch.
                creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
                process = subprocess.Popen(
                    tray_args,
                    cwd=str(Path(__file__).resolve().parents[1]),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                result = {
                    "schema": "universe.local-service-control.v1",
                    "status": "TRAY_STARTED",
                    "tray_pid": process.pid,
                    "script": str(tray_script),
                }
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 0

            if args.command == "status":
                result = service_status(args.state_file)
            elif args.command == "start":
                result = start_service(
                    state_path=args.state_file,
                    database_path=args.database,
                    mode_registry=args.mode_registry,
                    open_ui=bool(args.open_ui),
                )
            elif args.command == "stop":
                result = stop_service(args.state_file)
            else:
                result = restart_service(
                    state_path=args.state_file,
                    database_path=args.database,
                    mode_registry=args.mode_registry,
                    open_ui=bool(args.open_ui),
                )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            status_text = str(result.get("status") or "")
            if status_text in {
                "STOP_FAILED",
                "STOP_TIMEOUT",
                "START_FAILED",
            }:
                return 1
            return 0

        if args.command == "serve":
            mode_registry = load_universe_mode_registry(args.mode_registry)
            mode_contract = universe_mode_contract(mode_registry)
            token = (
                args.token
                or os.environ.get("UNIVERSE_TOKEN")
                or secrets.token_urlsafe(32)
            )
            server = create_server(
                database_path=args.database,
                token=token,
                host=args.host,
                port=args.port,
                mode_contract=mode_contract,
                auto_start_conductor_runtime=True,
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
                        "mode_contract": mode_contract,
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

        if args.command == "prepare-skill-observation-archive":
            result = prepare_skill_observation_archive(
                project_id=args.project_id,
                receipt=load_prepared_skill_observation(args.receipt_file),
                selection_ref=args.selection_ref,
                archive_path=args.archive_path,
            )
            status: int = HTTPStatus.OK
        else:
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
            elif args.command == "publish-skill-observation":
                status, result = publish_skill_observation(
                    project_id=args.project_id,
                    prepared=load_prepared_skill_observation(args.candidate_file),
                    publication_approval=load_prepared_skill_observation(
                        args.approval_file
                    ),
                    endpoint=endpoint,
                    token=token,
                )
            elif args.command == "drain-skill-observation-queue":
                status, result = request_json(
                    endpoint=endpoint,
                    token=token,
                    method="POST",
                    path="/v1/skill-observation-queue/drain",
                    payload={"limit": args.limit},
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
