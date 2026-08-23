"""Loopback transport for Claude Code's official channel notifications.

The interactive Claude process remains attached to the existing ConPTY/xterm.
Universe does not write session-bus instruction bodies to that PTY.  Instead a
small MCP child, spawned by Claude Code, polls this loopback broker and emits
``notifications/claude/channel`` on the MCP stdio connection.

The broker is intentionally session-scoped.  Its one-time bootstrap token is
written only to the temporary MCP config; the live token stays in the child
and Universe process memory.  A valid session-bus claim is still required by
the caller before ``push`` is reached.
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping


CHANNEL_SCHEMA = "universe.claude-channel.v1"
MCP_SERVER_NAME = "universe_channel"
MCP_SOURCE = "universe"
CHANNEL_TOKEN_HEADER = "X-Universe-Claude-Channel-Token"
CHANNEL_EXCHANGE_PATH = "/v1/claude-channel/exchange"
CHANNEL_PUSH_PATH = "/v1/claude-channel/push"
CHANNEL_POLL_PATH = "/v1/claude-channel/poll"
TERMINAL_ID_ENVIRONMENT = "UNIVERSE_TERMINAL_ID"
MAX_CONTENT_BYTES = 32 * 1024
MAX_QUEUE = 64
MAX_META_KEYS = 16
_META_KEY = re.compile(r"^[A-Za-z0-9_]+$")
_REGISTRATION_LOCK = threading.Lock()


def _data_dir() -> Path:
    # Mirrors universe_app.pty_supervisor.default_data_dir() without
    # importing it - that module imports terminal_host, which imports this
    # one, so importing it back here would be circular.
    override = os.environ.get("UNIVERSE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe"
    return Path.home() / ".local" / "share" / "universe"


def session_lookup_path(terminal_id: str) -> Path:
    """Where a channel MCP child looks up its broker's endpoint/token.

    Claude Code's --channels allowlist only recognizes MCP servers that are
    persistently registered (enterprise/user/project/local scope), not ones
    supplied via a one-off --mcp-config file. So the registered server entry
    is now static per project, and each spawned MCP child instead discovers
    its own session's broker via UNIVERSE_TERMINAL_ID (inherited from the
    parent CLI process) plus this small per-terminal lookup file.
    """
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(terminal_id or "").strip())
    return _data_dir() / "channel-sessions" / f"{safe_id}.json"


def _channel_python_executable() -> str:
    # This script only ever talks over stdio to its parent (Claude Code), so
    # it never needs a console of its own - use the windowless interpreter on
    # Windows so launching it doesn't pop a console window per session.
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return sys.executable


def _claude_config_path() -> Path:
    override = os.environ.get("UNIVERSE_CLAUDE_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude.json"


def ensure_local_channel_server_registered(project_root: str) -> None:
    """Idempotently register universe_channel as a local-scope MCP server.

    This edits the user's shared ~/.claude.json, so it only writes when the
    entry is missing or stale, and does a fresh read-merge-atomic-replace
    right before writing to keep the window where a concurrent Claude Code
    write could be clobbered as small as possible.
    """
    root = str(Path(project_root or "").expanduser())
    if not root:
        return
    keys = {root, root.replace("\\", "/")}
    server_script = Path(__file__).with_name("claude_channel_mcp.py")
    desired = {"command": _channel_python_executable(), "args": [str(server_script)]}
    config_path = _claude_config_path()
    with _REGISTRATION_LOCK:
        try:
            raw = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError):
            return  # best effort - never clobber a file we cannot parse
        if not isinstance(data, dict):
            return
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            return
        changed = False
        for key in keys:
            entry = projects.get(key)
            if not isinstance(entry, dict):
                entry = {}
                projects[key] = entry
            servers = entry.get("mcpServers")
            if not isinstance(servers, dict):
                servers = {}
                entry["mcpServers"] = servers
            if servers.get(MCP_SERVER_NAME) != desired:
                servers[MCP_SERVER_NAME] = desired
                changed = True
        if not changed:
            return
        tmp = config_path.with_name(config_path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, config_path)


class ClaudeChannelError(RuntimeError):
    pass


def _text(value: Any, field: str, *, required: bool = False, limit: int = 512) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ClaudeChannelError(f"CLAUDE_CHANNEL_FIELD_REQUIRED:{field}")
    if len(text) > limit:
        raise ClaudeChannelError(f"CLAUDE_CHANNEL_FIELD_INVALID:{field}")
    return text


class ChannelCapabilityToken:
    """A token bound to one local terminal and its selected provider."""

    def __init__(
        self,
        *,
        terminal_id: str,
        project_id: str,
        mode: str,
        provider: str,
        supervisor_session_id: str,
    ) -> None:
        self.terminal_id = _text(terminal_id, "terminal_id", required=True)
        self.project_id = _text(project_id, "project_id", required=True)
        self.mode = _text(mode, "mode", required=True).upper()
        self.provider = _text(provider, "provider", required=True).upper()
        self.supervisor_session_id = _text(
            supervisor_session_id, "supervisor_session_id"
        )
        self.value = secrets.token_urlsafe(32)
        self._revoked = threading.Event()

    @property
    def revoked(self) -> bool:
        return self._revoked.is_set()

    def revoke(self) -> None:
        self._revoked.set()

    def matches(self, presented: str | None) -> bool:
        return bool(
            not self.revoked
            and presented
            and secrets.compare_digest(str(presented), self.value)
        )

    def identity(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "provider": self.provider,
            "supervisor_session_id": self.supervisor_session_id,
        }


class ClaudeChannelBroker:
    """One authenticated queue shared by Universe and one MCP child."""

    def __init__(
        self,
        *,
        terminal_id: str,
        project_id: str,
        mode: str,
        provider: str = "CLAUDE",
        supervisor_session_id: str = "",
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ClaudeChannelError("CLAUDE_CHANNEL_NOT_LOOPBACK")
        self.token = ChannelCapabilityToken(
            terminal_id=terminal_id,
            project_id=project_id,
            mode=mode,
            provider=provider,
            supervisor_session_id=supervisor_session_id,
        )
        self._bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_consumed = False
        self._bootstrap_lock = threading.Lock()
        self._registered = threading.Event()
        self._stopped = threading.Event()
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=MAX_QUEUE)
        self._seen: set[str] = set()
        self._seen_lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler_type())
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def registered(self) -> bool:
        return self._registered.is_set()

    @property
    def enabled(self) -> bool:
        return not self._stopped.is_set()

    def start(self) -> "ClaudeChannelBroker":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="universe-claude-channel-broker",
            daemon=True,
        )
        self._thread.start()
        self._write_session_lookup()
        return self

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self.token.revoke()
        with self._bootstrap_lock:
            self._bootstrap_consumed = True
            self._bootstrap_token = ""
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()
        self._cleanup_session_lookup()

    def exchange_bootstrap(self, presented: str | None) -> dict[str, Any]:
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
        # Claude Code can re-read --mcp-config after the initial channel
        # handshake (for example from its MCP inspector). Keep the ephemeral
        # config until the terminal broker closes; the bootstrap is already
        # one-time and cannot be reused.
        return {"status": "REGISTERED", "session_token": self.token.value}

    def _write_session_lookup(self) -> None:
        path = session_lookup_path(self.token.terminal_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"endpoint": self.endpoint, "bootstrap_token": self._bootstrap_token}
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)

    def push(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._stopped.is_set():
            raise ClaudeChannelError("CLAUDE_CHANNEL_STOPPED")
        if not self.registered:
            raise ClaudeChannelError("CLAUDE_CHANNEL_NOT_REGISTERED")
        if not isinstance(payload, Mapping):
            raise ClaudeChannelError("CLAUDE_CHANNEL_PAYLOAD_INVALID")
        message_id = _text(payload.get("message_id"), "message_id", required=True, limit=80)
        content = str(payload.get("content") or "")
        if not content.strip():
            raise ClaudeChannelError("CLAUDE_CHANNEL_CONTENT_REQUIRED")
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ClaudeChannelError("CLAUDE_CHANNEL_CONTENT_TOO_LARGE")
        meta = self._meta(payload.get("meta"))
        if meta.get("message_id") not in {None, "", message_id}:
            raise ClaudeChannelError("CLAUDE_CHANNEL_MESSAGE_ID_MISMATCH")
        meta.setdefault("message_id", message_id)
        anchor = _text(payload.get("session_anchor_ref"), "session_anchor_ref", required=True, limit=256)
        if meta.get("session_anchor_ref") not in {None, "", anchor}:
            raise ClaudeChannelError("CLAUDE_CHANNEL_ANCHOR_MISMATCH")
        meta.setdefault("session_anchor_ref", anchor)
        sender = meta.get("sender_id")
        if sender and sender not in {"UNIVERSE_UI", "UNIVERSE_SESSION_BUS"}:
            raise ClaudeChannelError("CLAUDE_CHANNEL_SENDER_INVALID")
        with self._seen_lock:
            if message_id in self._seen:
                return {"status": "DUPLICATE", "message_id": message_id}
            self._seen.add(message_id)
        event = {
            "schema": CHANNEL_SCHEMA,
            "message_id": message_id,
            "content": content,
            "meta": meta,
        }
        try:
            self._queue.put_nowait(event)
        except queue.Full as error:
            with self._seen_lock:
                self._seen.discard(message_id)
            raise ClaudeChannelError("CLAUDE_CHANNEL_QUEUE_FULL") from error
        return {"status": "QUEUED", "message_id": message_id}

    def poll(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        if self._stopped.is_set():
            return {"status": "STOPPED"}
        if not self.registered:
            return {"status": "NOT_REGISTERED"}
        try:
            event = self._queue.get(timeout=max(0.05, min(float(timeout_seconds), 5.0)))
        except queue.Empty:
            return {"status": "EMPTY"}
        return {"status": "EVENT", "event": event}

    def public(self) -> dict[str, Any]:
        return {
            "transport": "CLAUDE_CODE_CHANNEL",
            "server": MCP_SERVER_NAME,
            "registered": self.registered,
            "identity": self.token.identity(),
        }

    @staticmethod
    def _meta(value: Any) -> dict[str, str]:
        raw = value if isinstance(value, Mapping) else {}
        if len(raw) > MAX_META_KEYS:
            raise ClaudeChannelError("CLAUDE_CHANNEL_META_INVALID")
        result: dict[str, str] = {}
        for key, item in raw.items():
            name = _text(key, "meta_key", required=True, limit=64)
            if not _META_KEY.fullmatch(name):
                raise ClaudeChannelError("CLAUDE_CHANNEL_META_KEY_INVALID")
            result[name] = _text(item, f"meta.{name}", limit=512)
        return result

    def _handler_type(self):
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                try:
                    payload = self._json_body()
                    path = self.path.split("?", 1)[0]
                    token = self.headers.get(CHANNEL_TOKEN_HEADER)
                    if path == CHANNEL_EXCHANGE_PATH:
                        result = broker.exchange_bootstrap(token)
                    elif path == CHANNEL_PUSH_PATH:
                        if not broker.token.matches(token):
                            result = {"status": "DENIED", "reason": "TOKEN_INVALID"}
                        else:
                            result = broker.push(payload)
                    elif path == CHANNEL_POLL_PATH:
                        if not broker.token.matches(token):
                            result = {"status": "DENIED", "reason": "TOKEN_INVALID"}
                        else:
                            timeout = payload.get("timeout_seconds", 2.0)
                            result = broker.poll(timeout_seconds=float(timeout))
                    else:
                        self._send(404, {"status": "NOT_FOUND"})
                        return
                    self._send(200, result)
                except (ClaudeChannelError, ValueError, TypeError) as error:
                    self._send(400, {"status": "DENIED", "reason": str(error)})

            def _json_body(self) -> dict[str, Any]:
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError as error:
                    raise ClaudeChannelError("CLAUDE_CHANNEL_BODY_INVALID") from error
                if length < 0 or length > MAX_CONTENT_BYTES * 2:
                    raise ClaudeChannelError("CLAUDE_CHANNEL_BODY_TOO_LARGE")
                raw = self.rfile.read(length)
                try:
                    decoded = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ClaudeChannelError("CLAUDE_CHANNEL_BODY_INVALID") from error
                if not isinstance(decoded, dict):
                    raise ClaudeChannelError("CLAUDE_CHANNEL_BODY_INVALID")
                return decoded

            def _send(self, status: int, value: Mapping[str, Any]) -> None:
                body = json.dumps(dict(value), ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        return Handler

    def _cleanup_session_lookup(self) -> None:
        try:
            session_lookup_path(self.token.terminal_id).unlink(missing_ok=True)
        except OSError:
            pass
