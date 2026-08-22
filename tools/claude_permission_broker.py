"""Loopback broker between the Claude permission MCP server and Universe.

Claude spawns the stdio MCP server (``claude_permission_mcp.py``) as its
``--permission-prompt-tool`` provider. That server has no access to Universe
state, so it forwards each prompt to this broker over loopback HTTP. The broker
validates a short-lived capability token, then hands the request to the
``ClaudePermissionBridge`` bound to one resident session.

Security boundary:

* the listener binds ``127.0.0.1`` only;
* the capability token is minted per resident session -- it is **not** the
  general Universe service token -- and is bound to provider, session, target,
  and the owning process;
* the token is passed to the MCP server through its environment, never through
  argv, stdout, or stderr;
* the token is revoked when the session closes;
* timeout, session mismatch, duplicate request, and shutdown all deny.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping

from claude_permission_bridge import ClaudePermissionBridge, deny


BROKER_PATH = "/v1/claude-permission/approve"
EXCHANGE_PATH = "/v1/claude-permission/exchange"
TOKEN_HEADER = "X-Universe-Claude-Permission-Token"
# Only the one-time bootstrap token is ever written to the MCP config. The
# real session token is handed back in the exchange response and lives in the
# MCP process memory only.
BOOTSTRAP_ENVIRONMENT = "UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"
TOKEN_ENVIRONMENT = "UNIVERSE_CLAUDE_PERMISSION_TOKEN"
ENDPOINT_ENVIRONMENT = "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT"
MCP_SERVER_NAME = "universe_permission"
MCP_TOOL_NAME = "approve"


class ClaudePermissionBrokerError(RuntimeError):
    pass


class CapabilityToken:
    """One short-lived token bound to a single resident session."""

    def __init__(self, *, provider: str, session_ref: str, target: str) -> None:
        self.provider = str(provider).upper()
        self.session_ref = str(session_ref)
        self.target = str(target)
        self.process_id = os.getpid()
        self.value = secrets.token_urlsafe(32)
        self._revoked = threading.Event()
        self._session_lock = threading.Lock()

    @property
    def revoked(self) -> bool:
        return self._revoked.is_set()

    def revoke(self) -> None:
        self._revoked.set()

    def bind_session_ref(self, session_ref: str) -> None:
        """Replace only the one-time pending provider coordinate."""

        normalized = str(session_ref or "").strip()
        if not normalized:
            raise ClaudePermissionBrokerError("CLAUDE_PERMISSION_SESSION_REF_INVALID")
        with self._session_lock:
            current = str(self.session_ref).strip()
            if current == normalized:
                return
            if not current.lower().startswith("claude-code:pending:"):
                raise ClaudePermissionBrokerError("CLAUDE_PERMISSION_SESSION_MISMATCH")
            self.session_ref = normalized

    def current_session_ref(self) -> str:
        with self._session_lock:
            return str(self.session_ref)

    def matches(self, presented: str | None) -> bool:
        if self._revoked.is_set() or not presented:
            return False
        return secrets.compare_digest(str(presented), self.value)

    def identity(self) -> dict[str, Any]:
        """Identity the token is bound to. Never includes the token value."""

        return {
            "provider": self.provider,
            "session_ref": self.current_session_ref(),
            "target": self.target,
            "process_id": self.process_id,
        }


class ClaudePermissionBroker:
    """Loopback HTTP host that serves exactly one resident session's prompts."""

    def __init__(
        self,
        *,
        bridge: ClaudePermissionBridge,
        provider: str = "CLAUDE",
        target: str = "UNKNOWN",
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ClaudePermissionBrokerError("CLAUDE_PERMISSION_BROKER_NOT_LOOPBACK")
        self.bridge = bridge
        self.token = CapabilityToken(
            provider=provider,
            session_ref=bridge.session_ref,
            target=target,
        )
        # One-time bootstrap credential. It is the only secret that reaches
        # disk, and it stops working the moment it is exchanged.
        self._bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_consumed = False
        self._registered = threading.Event()
        self._seen_lock = threading.Lock()
        self._seen: set[str] = set()
        self._server = HTTPServer((host, port), self._handler_type())
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self._mcp_config_path: Path | None = None
        self._mcp_config_lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "ClaudePermissionBroker":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="universe-claude-permission-broker",
            daemon=True,
        )
        self._thread.start()
        return self

    def bind_session_ref(self, session_ref: str) -> None:
        """Bind the token and bridge to Claude's observed session id."""

        self.token.bind_session_ref(session_ref)
        self.bridge.bind_session_ref(session_ref)

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._invalidate_bootstrap()
        self.token.revoke()
        # Closing the bridge cancels every pending request, including one whose
        # operator decision is still in flight.
        self.bridge.close()
        if self._thread is not None:
            # shutdown() waits for serve_forever() to acknowledge; calling it on
            # a server that was never started would block forever.
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()

    def abort_registration(self) -> None:
        """Invalidate an MCP registration that did not complete in time."""

        self._invalidate_bootstrap()

    @property
    def registered(self) -> bool:
        """True once the MCP server has exchanged its bootstrap token."""

        return self._registered.is_set()

    def wait_for_registration(self, timeout_seconds: float = 30.0) -> bool:
        """Block until the MCP server registers. A turn must not start first."""

        registered = self._registered.wait(timeout_seconds)
        if not registered:
            # A child that missed the registration window must not be able to
            # register later, and its one-time config must not remain on disk.
            self.abort_registration()
        return registered

    def exchange_bootstrap(self, presented: str | None) -> dict[str, Any]:
        """Trade the one-time bootstrap token for the session token.

        The bootstrap token is burned on the first successful exchange, so a
        replay -- including one by a Claude child that read the config file --
        gets nothing.
        """

        if self._stopped.is_set():
            return {"status": "DENIED", "reason": "BROKER_STOPPED"}
        with self._bootstrap_lock:
            if self._bootstrap_consumed:
                return {"status": "DENIED", "reason": "BOOTSTRAP_ALREADY_USED"}
            if not presented or not secrets.compare_digest(
                str(presented), self._bootstrap_token
            ):
                return {"status": "DENIED", "reason": "BOOTSTRAP_INVALID"}
            self._bootstrap_consumed = True
            self._bootstrap_token = ""
            self._registered.set()
            self._cleanup_mcp_config()
            return {"status": "REGISTERED", "session_token": self.token.value}

    def provider_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        """Strip permission-token names before handing env to the provider.

        The token must reach the MCP child only. If it were also in Claude's
        own environment, a Bash call inside Claude could read the capability
        that gates its approvals.
        """

        return {
            str(key): str(value)
            for key, value in environment.items()
            if str(key)
            not in {TOKEN_ENVIRONMENT, ENDPOINT_ENVIRONMENT, BOOTSTRAP_ENVIRONMENT}
        }

    def mcp_config(self) -> dict[str, Any]:
        """Config for ``--mcp-config``.

        Carries the endpoint and the **one-time bootstrap token** only. The
        session token never touches disk or argv.
        """

        server_script = Path(__file__).with_name("claude_permission_mcp.py")
        return {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "command": _python_executable(),
                    "args": [str(server_script)],
                    "env": {
                        ENDPOINT_ENVIRONMENT: self.endpoint,
                        BOOTSTRAP_ENVIRONMENT: self._bootstrap_token,
                    },
                }
            }
        }

    def write_mcp_config(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.mcp_config(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        with self._mcp_config_lock:
            self._mcp_config_path = path
        return path

    def _invalidate_bootstrap(self) -> None:
        with self._bootstrap_lock:
            # Burn the bootstrap credential so a late child cannot use it.
            self._bootstrap_consumed = True
            self._bootstrap_token = ""
        self._cleanup_mcp_config()

    def _cleanup_mcp_config(self) -> None:
        with self._mcp_config_lock:
            path = self._mcp_config_path
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Windows can keep the file locked briefly while the provider is
            # starting. Retain the path so close() can retry the cleanup.
            return
        with self._mcp_config_lock:
            if self._mcp_config_path == path:
                self._mcp_config_path = None
        # The host creates a private temporary directory for this one file.
        # Remove it only when empty; never remove a caller-owned non-empty dir.
        try:
            path.parent.rmdir()
        except OSError:
            return

    # -- request handling -----------------------------------------------

    def handle_payload(
        self, payload: Mapping[str, Any], *, presented_token: str | None
    ) -> dict[str, Any]:
        """Validate and dispatch one permission prompt. Every failure denies."""

        if self._stopped.is_set():
            return deny("Universe permission broker stopped")
        if not self.token.matches(presented_token):
            return deny("CLAUDE_PERMISSION_TOKEN_INVALID")
        if not isinstance(payload, Mapping):
            return deny("CLAUDE_PERMISSION_PAYLOAD_INVALID")

        request_key = payload.get("tool_use_id") or payload.get("request_id")
        if isinstance(request_key, str) and request_key.strip():
            key = request_key.strip()
            with self._seen_lock:
                if key in self._seen:
                    # A replayed prompt must never be answered twice.
                    return deny("CLAUDE_PERMISSION_DUPLICATE_REQUEST")
                self._seen.add(key)

        claimed_session = payload.get("session_ref")
        if claimed_session is not None:
            if str(claimed_session) != self.token.current_session_ref():
                return deny("CLAUDE_PERMISSION_SESSION_MISMATCH")
        request = dict(payload)
        request.setdefault("session_ref", self.token.current_session_ref())
        return self.bridge.handle(request)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path not in {BROKER_PATH, EXCHANGE_PATH}:
                    self._send(404, deny("CLAUDE_PERMISSION_ROUTE_NOT_FOUND"))
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, deny("CLAUDE_PERMISSION_PAYLOAD_INVALID"))
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(400, deny("CLAUDE_PERMISSION_PAYLOAD_INVALID"))
                    return
                if not isinstance(payload, Mapping):
                    self._send(400, deny("CLAUDE_PERMISSION_PAYLOAD_INVALID"))
                    return
                if self.path == EXCHANGE_PATH:
                    self._send(
                        200,
                        broker.exchange_bootstrap(
                            self.headers.get(TOKEN_HEADER)
                            or payload.get("bootstrap_token")
                        ),
                    )
                    return
                result = broker.handle_payload(
                    payload,
                    presented_token=self.headers.get(TOKEN_HEADER),
                )
                self._send(200, result)

            def _send(self, status: int, body: Mapping[str, Any]) -> None:
                encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: Any) -> None:
                # Never write request detail (or headers) to stderr.
                return

        return Handler


def _python_executable() -> str:
    import sys

    return sys.executable or "python"
