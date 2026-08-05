"""Universe Rendezvous client for local Universe app registration and join approval.

The rendezvous server is a broker only. This client:
  - signs every request with a host-local Ed25519 identity
  - reuses the same identity and universe_id across app restarts
  - registers the local universe with optional public endpoint_url
  - keeps presence ONLINE via a background heartbeat
  - polls the owner pending inbox and supports approve/deny

Private key material is stored only in a host-local state file (mode 0600) and
is never logged or returned in public status snapshots.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError as error:  # pragma: no cover - host dependency
    raise RuntimeError(
        "RENDEZVOUS_CRYPTOGRAPHY_UNAVAILABLE: install the cryptography package"
    ) from error


CLIENT_SCHEMA = "universe.rendezvous-client.v1"
CONFIG_SCHEMA = "universe.rendezvous-client-config.v1"
STATE_SCHEMA = "universe.rendezvous-client-state.v1"
DEFAULT_PRESENCE_INTERVAL_SECONDS = 60
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0

HEADER_PUBLIC_KEY = "x-ur-public-key"
HEADER_TIMESTAMP = "x-ur-timestamp"
HEADER_NONCE = "x-ur-nonce"
HEADER_SIGNATURE = "x-ur-signature"


class RendezvousClientError(RuntimeError):
    def __init__(
        self, code: str, detail: str, *, status: int | None = None
    ) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _required_text(value: Any, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID", f"{field} is required"
        )
    text = value.strip()
    if len(text) > limit:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID", f"{field} exceeds {limit} characters"
        )
    return text


def _https_origin(value: Any) -> str:
    text = _required_text(value, "base_url", limit=2048).rstrip("/")
    parsed = urlsplit(text)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "base_url must be an HTTP(S) origin without credentials, query, or path",
        )
    if parsed.scheme != "https" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "non-loopback base_url must use https",
        )
    return text


def _endpoint_url(value: Any) -> str:
    text = _required_text(value, "endpoint_url", limit=512)
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "endpoint_url must be an absolute http(s) URL",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "endpoint_url must not include credentials, query, or fragment",
        )
    return text.rstrip("/")


def _default_data_dir() -> Path:
    override = os.environ.get("UNIVERSE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def default_rendezvous_config_path() -> Path:
    override = os.environ.get("UNIVERSE_RENDEZVOUS_CONFIG")
    if override:
        return Path(override).expanduser()
    return _default_data_dir() / "rendezvous-client-config.json"


def default_rendezvous_state_path() -> Path:
    override = os.environ.get("UNIVERSE_RENDEZVOUS_STATE")
    if override:
        return Path(override).expanduser()
    return _default_data_dir() / "rendezvous-client-state.json"


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


def normalize_rendezvous_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID", "rendezvous configuration is required"
        )
    display_name = _required_text(value.get("display_name"), "display_name", limit=64)
    visibility = str(value.get("visibility") or "PUBLIC").strip().upper()
    if visibility not in {"PUBLIC", "UNLISTED", "PRIVATE"}:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "visibility must be PUBLIC, UNLISTED, or PRIVATE",
        )
    presence_interval = int(value.get("presence_interval_seconds") or DEFAULT_PRESENCE_INTERVAL_SECONDS)
    poll_interval = int(value.get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS)
    if presence_interval < 15 or presence_interval > 300:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "presence_interval_seconds must be 15..300",
        )
    if poll_interval < 2 or poll_interval > 60:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "poll_interval_seconds must be 2..60",
        )
    config = {
        "schema": CONFIG_SCHEMA,
        "base_url": _https_origin(value.get("base_url")),
        "display_name": display_name,
        "visibility": visibility,
        "endpoint_url": _endpoint_url(value.get("endpoint_url")),
        "presence_interval_seconds": presence_interval,
        "poll_interval_seconds": poll_interval,
        "enabled": bool(value.get("enabled", True)),
    }
    return config


def load_rendezvous_config(
    path: Path | None = None,
) -> dict[str, Any]:
    config_path = path or default_rendezvous_config_path()
    try:
        raw = json.loads(config_path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_UNAVAILABLE", str(error)
        ) from error
    return normalize_rendezvous_config(raw)


def config_from_environ(
    *,
    endpoint_url: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any] | None:
    """Build config from env when present. Returns None if not configured."""
    base_url = os.environ.get("UNIVERSE_RENDEZVOUS_URL", "").strip()
    if not base_url:
        return None
    resolved_endpoint = (
        endpoint_url
        or os.environ.get("UNIVERSE_RENDEZVOUS_ENDPOINT_URL", "").strip()
        or None
    )
    if not resolved_endpoint:
        raise RendezvousClientError(
            "RENDEZVOUS_CONFIG_INVALID",
            "endpoint_url is required (UNIVERSE_RENDEZVOUS_ENDPOINT_URL or argument)",
        )
    return normalize_rendezvous_config(
        {
            "base_url": base_url,
            "display_name": display_name
            or os.environ.get("UNIVERSE_RENDEZVOUS_DISPLAY_NAME", "Universe").strip()
            or "Universe",
            "visibility": os.environ.get("UNIVERSE_RENDEZVOUS_VISIBILITY", "PUBLIC"),
            "endpoint_url": resolved_endpoint,
            "presence_interval_seconds": int(
                os.environ.get(
                    "UNIVERSE_RENDEZVOUS_PRESENCE_INTERVAL",
                    str(DEFAULT_PRESENCE_INTERVAL_SECONDS),
                )
            ),
            "poll_interval_seconds": int(
                os.environ.get(
                    "UNIVERSE_RENDEZVOUS_POLL_INTERVAL",
                    str(DEFAULT_POLL_INTERVAL_SECONDS),
                )
            ),
            "enabled": os.environ.get("UNIVERSE_RENDEZVOUS_ENABLED", "1")
            not in {"0", "false", "False", "no"},
        }
    )


def _b64u_decode(value: str, *, expected_len: int) -> bytes:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding)
    if len(raw) != expected_len:
        raise RendezvousClientError(
            "RENDEZVOUS_STATE_INVALID",
            f"expected {expected_len} bytes, got {len(raw)}",
        )
    return raw


@dataclass
class _Identity:
    private_key: Ed25519PrivateKey
    public_key: str

    @classmethod
    def generate(cls) -> "_Identity":
        private = Ed25519PrivateKey.generate()
        return cls.from_private_key(private)

    @classmethod
    def from_private_key(cls, private: Ed25519PrivateKey) -> "_Identity":
        public = _b64u_encode(
            private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )
        return cls(private_key=private, public_key=public)

    @classmethod
    def from_private_raw(cls, raw: bytes) -> "_Identity":
        if len(raw) != 32:
            raise RendezvousClientError(
                "RENDEZVOUS_STATE_INVALID", "private key must be 32 bytes"
            )
        return cls.from_private_key(Ed25519PrivateKey.from_private_bytes(raw))

    def private_raw(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )


def load_rendezvous_state(
    path: Path | None = None,
) -> dict[str, Any] | None:
    state_path = path or default_rendezvous_state_path()
    if not state_path.is_file():
        return None
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RendezvousClientError(
            "RENDEZVOUS_STATE_INVALID", str(error)
        ) from error
    if not isinstance(value, dict) or value.get("schema") != STATE_SCHEMA:
        raise RendezvousClientError(
            "RENDEZVOUS_STATE_INVALID", "state schema is invalid"
        )
    private_raw = _b64u_decode(
        _required_text(value.get("private_key"), "private_key", limit=128),
        expected_len=32,
    )
    identity = _Identity.from_private_raw(private_raw)
    if identity.public_key != _required_text(
        value.get("public_key"), "public_key", limit=128
    ):
        raise RendezvousClientError(
            "RENDEZVOUS_STATE_INVALID", "public_key does not match private_key"
        )
    universe_id = str(value.get("universe_id") or "").strip() or None
    return {
        "schema": STATE_SCHEMA,
        "identity": identity,
        "universe_id": universe_id,
        "base_url": str(value.get("base_url") or "").strip() or None,
        "path": state_path,
    }


def save_rendezvous_state(
    *,
    identity: _Identity,
    universe_id: str,
    base_url: str,
    path: Path | None = None,
) -> Path:
    state_path = path or default_rendezvous_state_path()
    payload = {
        "schema": STATE_SCHEMA,
        "private_key": _b64u_encode(identity.private_raw()),
        "public_key": identity.public_key,
        "universe_id": _required_text(universe_id, "universe_id", limit=64),
        "base_url": _https_origin(base_url),
        "updated_at": utc_now(),
    }
    _write_json_atomic(state_path, payload)
    return state_path


class RendezvousHttpClient:
    """Signed HTTP client for one rendezvous origin."""

    def __init__(
        self,
        base_url: str,
        *,
        identity: _Identity | None = None,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        transport: Callable[..., tuple[int, bytes]] | None = None,
    ) -> None:
        self.base_url = _https_origin(base_url)
        self.identity = identity or _Identity.generate()
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def signed_headers(
        self, method: str, path: str, body: bytes
    ) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        canonical = "\n".join(
            (
                "v1",
                method.upper(),
                path,
                timestamp,
                nonce,
                hashlib.sha256(body).hexdigest(),
            )
        ).encode("utf-8")
        signature = self.identity.private_key.sign(canonical)
        return {
            HEADER_PUBLIC_KEY: self.identity.public_key,
            HEADER_TIMESTAMP: timestamp,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: _b64u_encode(signature),
        }

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise RendezvousClientError(
                "RENDEZVOUS_PATH_INVALID", "path must start with /"
            )
        body = (
            b""
            if payload is None
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        headers = self.signed_headers(method, path, body)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self._send(method, path, body=body, headers=headers)
        if not raw:
            if 200 <= status < 300:
                return {}
            raise RendezvousClientError(
                "RENDEZVOUS_HTTP_ERROR",
                f"empty error body status={status}",
                status=status,
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RendezvousClientError(
                "RENDEZVOUS_RESPONSE_INVALID",
                f"non-json response status={status}",
                status=status,
            ) from error
        if not (200 <= status < 300):
            error_body = data.get("error") if isinstance(data, dict) else None
            code = "RENDEZVOUS_HTTP_ERROR"
            detail = f"status={status}"
            if isinstance(error_body, dict):
                code = str(error_body.get("code") or code)
                detail = str(error_body.get("message") or detail)
            raise RendezvousClientError(code, detail, status=status)
        if not isinstance(data, dict):
            raise RendezvousClientError(
                "RENDEZVOUS_RESPONSE_INVALID", "response must be a JSON object"
            )
        return data

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        headers: Mapping[str, str],
    ) -> tuple[int, bytes]:
        if self._transport is not None:
            return self._transport(method, path, body, dict(headers))
        url = self.base_url + path
        request = Request(  # noqa: S310 - scheme validated on base_url
            url,
            data=body if method.upper() not in {"GET", "HEAD"} else None,
            headers=dict(headers),
            method=method.upper(),
        )
        if method.upper() in {"GET", "HEAD"} and body:
            # urllib ignores body on GET; path-only signed empty body is required.
            pass
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                return int(response.status), response.read()
        except HTTPError as error:
            return int(error.code), error.read() or b""
        except URLError as error:
            raise RendezvousClientError(
                "RENDEZVOUS_TRANSPORT_FAILED", str(error.reason or error)
            ) from error


class UniverseRendezvousService:
    """Background registration, presence, and pending-join poller."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        http: RendezvousHttpClient | None = None,
        on_pending: Callable[[list[dict[str, Any]]], None] | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.config = normalize_rendezvous_config(config)
        self.state_path = state_path or default_rendezvous_state_path()
        if http is None:
            saved = None
            try:
                saved = load_rendezvous_state(self.state_path)
            except RendezvousClientError:
                saved = None
            identity = (
                saved["identity"]
                if saved is not None
                and (
                    not saved.get("base_url")
                    or saved["base_url"] == self.config["base_url"]
                )
                else _Identity.generate()
            )
            self.http = RendezvousHttpClient(self.config["base_url"], identity=identity)
            self._saved_universe_id = (
                saved.get("universe_id")
                if saved is not None
                and (
                    not saved.get("base_url")
                    or saved["base_url"] == self.config["base_url"]
                )
                else None
            )
        else:
            self.http = http
            self._saved_universe_id = None
        self._on_pending = on_pending
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._presence_thread: threading.Thread | None = None
        self._poll_thread: threading.Thread | None = None
        self._started = False
        self._universe_id: str | None = None
        self._invite_code: str | None = None
        self._registered_at: str | None = None
        self._last_presence_at: str | None = None
        self._last_poll_at: str | None = None
        self._last_error: dict[str, str] | None = None
        self._pending: list[dict[str, Any]] = []
        self._bound_existing = False

    @property
    def universe_id(self) -> str | None:
        with self._lock:
            return self._universe_id

    def _persist_state(self, universe_id: str) -> None:
        save_rendezvous_state(
            identity=self.http.identity,
            universe_id=universe_id,
            base_url=self.config["base_url"],
            path=self.state_path,
        )

    def _register(self) -> str:
        registration = self.http.request(
            "POST",
            "/v1/universes/register",
            {
                "display_name": self.config["display_name"],
                "visibility": self.config["visibility"],
                "endpoint_url": self.config["endpoint_url"],
            },
        )
        universe_id = str(registration.get("universe_id") or "").strip()
        if not universe_id:
            raise RendezvousClientError(
                "RENDEZVOUS_REGISTER_INVALID",
                "register response missing universe_id",
            )
        invite = registration.get("invite_code")
        self._invite_code = invite if isinstance(invite, str) else None
        self._persist_state(universe_id)
        return universe_id

    def _resume_or_register(self) -> str:
        saved_id = str(self._saved_universe_id or "").strip()
        if saved_id:
            try:
                self.http.request(
                    "POST",
                    f"/v1/universes/{saved_id}/presence",
                    {"status": "ONLINE"},
                )
                self._bound_existing = True
                self._persist_state(saved_id)
                return saved_id
            except RendezvousClientError:
                # Identity/universe no longer valid on the broker: register again.
                pass
        self._bound_existing = False
        return self._register()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if not self.config.get("enabled", True):
                return self.public_status()
            if self._started:
                return self.public_status()
            universe_id = self._resume_or_register()
            self._universe_id = universe_id
            self._registered_at = utc_now()
            self._started = True
            self._stop.clear()
            self._last_error = None
            if self._bound_existing:
                self._last_presence_at = utc_now()
            self._presence_thread = threading.Thread(
                target=self._presence_loop,
                name="universe-rendezvous-presence",
                daemon=True,
            )
            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                name="universe-rendezvous-poll",
                daemon=True,
            )
            self._presence_thread.start()
            self._poll_thread.start()
            return self.public_status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop.set()
            presence = self._presence_thread
            poll = self._poll_thread
            self._presence_thread = None
            self._poll_thread = None
            self._started = False
        if presence is not None and presence.is_alive():
            presence.join(timeout=2)
        if poll is not None and poll.is_alive():
            poll.join(timeout=2)
        return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": CLIENT_SCHEMA,
                "status": (
                    "RENDEZVOUS_ACTIVE"
                    if self._started and self._universe_id
                    else "RENDEZVOUS_INACTIVE"
                ),
                "enabled": bool(self.config.get("enabled", True)),
                "base_url": self.config["base_url"],
                "display_name": self.config["display_name"],
                "visibility": self.config["visibility"],
                "endpoint_url": self.config["endpoint_url"],
                "universe_id": self._universe_id,
                "registered_at": self._registered_at,
                "last_presence_at": self._last_presence_at,
                "last_poll_at": self._last_poll_at,
                "pending_count": len(self._pending),
                "pending": list(self._pending),
                "last_error": self._last_error,
                # Never expose private key or invite code material here.
            }

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._pending)

    def approve(self, request_id: str) -> dict[str, Any]:
        return self._decide(request_id, approve=True)

    def deny(self, request_id: str) -> dict[str, Any]:
        return self._decide(request_id, approve=False)

    def refresh_pending(self) -> list[dict[str, Any]]:
        universe_id = self.universe_id
        if not universe_id:
            raise RendezvousClientError(
                "RENDEZVOUS_NOT_REGISTERED", "register before listing connect requests"
            )
        data = self.http.request(
            "GET", f"/v1/universes/{universe_id}/connect-requests"
        )
        items = data.get("connect_requests")
        if not isinstance(items, list):
            raise RendezvousClientError(
                "RENDEZVOUS_RESPONSE_INVALID",
                "connect_requests must be a list",
            )
        pending: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("state") != "PENDING":
                continue
            request_id = str(item.get("connect_request_id") or "").strip()
            if not request_id:
                continue
            pending.append(
                {
                    "connect_request_id": request_id,
                    "universe_id": str(item.get("universe_id") or universe_id),
                    "state": "PENDING",
                    "created_at": item.get("created_at"),
                    "expires_at": item.get("expires_at"),
                }
            )
        with self._lock:
            self._pending = pending
            self._last_poll_at = utc_now()
            self._last_error = None
        if self._on_pending is not None:
            self._on_pending(list(pending))
        return list(pending)

    def _decide(self, request_id: str, *, approve: bool) -> dict[str, Any]:
        normalized = _required_text(request_id, "connect_request_id", limit=64)
        action = "approve" if approve else "deny"
        result = self.http.request(
            "POST", f"/v1/connect-requests/{normalized}/{action}", {}
        )
        with self._lock:
            self._pending = [
                item
                for item in self._pending
                if item.get("connect_request_id") != normalized
            ]
            self._last_error = None
        return result

    def _presence_loop(self) -> None:
        interval = float(self.config["presence_interval_seconds"])
        while not self._stop.is_set():
            try:
                universe_id = self.universe_id
                if universe_id:
                    self.http.request(
                        "POST",
                        f"/v1/universes/{universe_id}/presence",
                        {"status": "ONLINE"},
                    )
                    with self._lock:
                        self._last_presence_at = utc_now()
                        self._last_error = None
            except RendezvousClientError as error:
                with self._lock:
                    self._last_error = {
                        "code": error.code,
                        "detail": error.detail,
                        "at": utc_now(),
                    }
            self._stop.wait(interval)

    def _poll_loop(self) -> None:
        interval = float(self.config["poll_interval_seconds"])
        while not self._stop.is_set():
            try:
                if self.universe_id:
                    self.refresh_pending()
            except RendezvousClientError as error:
                with self._lock:
                    self._last_error = {
                        "code": error.code,
                        "detail": error.detail,
                        "at": utc_now(),
                    }
            self._stop.wait(interval)


def start_rendezvous_service(
    config: Mapping[str, Any],
    *,
    on_pending: Callable[[list[dict[str, Any]]], None] | None = None,
) -> UniverseRendezvousService:
    service = UniverseRendezvousService(config, on_pending=on_pending)
    service.start()
    return service
