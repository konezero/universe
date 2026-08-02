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
TOKEN_HEADER = "X-Universe-Claude-Permission-Token"
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

    @property
    def revoked(self) -> bool:
        return self._revoked.is_set()

    def revoke(self) -> None:
        self._revoked.set()

    def matches(self, presented: str | None) -> bool:
        if self._revoked.is_set() or not presented:
            return False
        return secrets.compare_digest(str(presented), self.value)

    def identity(self) -> dict[str, Any]:
        """Identity the token is bound to. Never includes the token value."""

        return {
            "provider": self.provider,
            "session_ref": self.session_ref,
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
        self._seen_lock = threading.Lock()
        self._seen: set[str] = set()
        self._server = HTTPServer((host, port), self._handler_type())
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

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

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self.token.revoke()
        self.bridge.close()
        if self._thread is not None:
            # shutdown() waits for serve_forever() to acknowledge; calling it on
            # a server that was never started would block forever.
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()

    def provider_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        """Strip permission-token names before handing env to the provider.

        The token must reach the MCP child only. If it were also in Claude's
        own environment, a Bash call inside Claude could read the capability
        that gates its approvals.
        """

        return {
            str(key): str(value)
            for key, value in environment.items()
            if str(key) not in {TOKEN_ENVIRONMENT, ENDPOINT_ENVIRONMENT}
        }

    def mcp_config(self) -> dict[str, Any]:
        """Config for ``--mcp-config``. The token rides in ``env``, not argv."""

        server_script = Path(__file__).with_name("claude_permission_mcp.py")
        return {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "command": _python_executable(),
                    "args": [str(server_script)],
                    "env": {
                        ENDPOINT_ENVIRONMENT: f"{self.endpoint}{BROKER_PATH}",
                        TOKEN_ENVIRONMENT: self.token.value,
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
        return path

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
            if str(claimed_session) != self.token.session_ref:
                return deny("CLAUDE_PERMISSION_SESSION_MISMATCH")
        request = dict(payload)
        request.setdefault("session_ref", self.token.session_ref)
        return self.bridge.handle(request)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != BROKER_PATH:
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
