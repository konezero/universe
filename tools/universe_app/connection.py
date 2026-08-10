"""Connection, authentication, and HTTP transport contracts for Universe."""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


CONNECTION_PROFILE_SCHEMA = "universe.connection-profile.v1"
AUTH_PROFILE_SCHEMA = "universe.auth-profile.v1"
INTERFACE_PROFILE_SCHEMA = "universe.interface-profile.v1"
CAPABILITY_PROFILE_SCHEMA = "universe.connection-capabilities.v1"
CONNECTION_KINDS = frozenset({"LOCAL", "REMOTE", "PEER"})
TRANSPORT_KINDS = frozenset({"HTTP", "GIT", "P2P"})
INTERFACE_KINDS = frozenset({"HTTP_API", "MCP", "CLI"})
ADAPTER_DIRECTIONS = frozenset({"INBOUND", "OUTBOUND"})
AUTH_TYPES = frozenset({"NONE", "LOCAL_TOKEN", "OAUTH2", "PEER_KEY"})


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


__all__ = [
    "AuthProfile",
    "AuthProvider",
    "ConnectionCapabilities",
    "ConnectionProfile",
    "HttpUniverseTransport",
    "InterfaceProfile",
    "LocalTokenAuthProvider",
    "LoopbackNoAuthProvider",
    "UniverseError",
    "UniverseTransport",
    "_loopback_endpoint_reachable",
    "_required_text",
    "auth_provider_for",
    "connection_profile",
    "interface_profile",
    "local_connection_profile",
    "local_http_interface_profile",
]
