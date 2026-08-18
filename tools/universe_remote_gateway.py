from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from remote_access import RemoteAccessError, RemoteAccessStore


GATEWAY_SCHEMA = "universe.remote-gateway.v1"
MAX_BODY_BYTES = 1024 * 1024
SESSION_COOKIE = "universe_remote_session"
PAIRING_COOKIE = "universe_pair_request"
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "set-cookie",
    }
)


class GatewayError(Exception):
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


def default_gateway_state_path() -> Path:
    override = os.environ.get("UNIVERSE_REMOTE_GATEWAY_STATE")
    return (
        Path(override).expanduser()
        if override
        else default_data_dir() / "remote-gateway.json"
    )


def default_upstream_state_path() -> Path:
    override = os.environ.get("UNIVERSE_STATE_FILE")
    return (
        Path(override).expanduser()
        if override
        else default_data_dir() / "server.json"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
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


def load_gateway_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GatewayError(
            "REMOTE_GATEWAY_STATE_UNAVAILABLE", str(error), HTTPStatus.NOT_FOUND
        ) from error
    if not isinstance(value, dict) or value.get("schema") != GATEWAY_SCHEMA:
        raise GatewayError(
            "REMOTE_GATEWAY_STATE_INVALID", "remote gateway state is invalid"
        )
    return value


def _read_http_head(sock: socket.socket) -> tuple[bytes, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return buf, b""
        buf += chunk
    idx = buf.index(b"\r\n\r\n") + 4
    return buf[:idx], buf[idx:]


def _load_upstream_state(path: Path) -> tuple[str, Path]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GatewayError("HOST_OFFLINE", "local Universe service is offline", 503) from error
    if not isinstance(value, dict):
        raise GatewayError("HOST_OFFLINE", "local Universe service state is invalid", 503)
    endpoint = str(value.get("endpoint") or "")
    database = str(value.get("database") or "")
    parsed = urlsplit(endpoint)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as error:
        raise GatewayError("UPSTREAM_INVALID", "upstream must be loopback", 503) from error
    if parsed.scheme != "http" or not address.is_loopback or parsed.port is None:
        raise GatewayError("UPSTREAM_INVALID", "upstream must be fixed loopback HTTP", 503)
    database_path = Path(database).expanduser().resolve()
    if not database_path.is_file():
        raise GatewayError("HOST_OFFLINE", "Universe database is unavailable", 503)
    return endpoint.rstrip("/"), database_path


def _loopback_request(
    endpoint: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 2,
) -> tuple[int, bytes]:
    parsed = urlsplit(endpoint)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as error:
        raise GatewayError(
            "CONTROL_ENDPOINT_INVALID", "control endpoint must be loopback", 503
        ) from error
    if parsed.scheme != "http" or not address.is_loopback or parsed.port is None:
        raise GatewayError(
            "CONTROL_ENDPOINT_INVALID", "control endpoint must be loopback HTTP", 503
        )
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _discover_lan_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        candidate = str(probe.getsockname()[0])
        if ipaddress.ip_address(candidate).is_private:
            return candidate
    except OSError:
        pass
    finally:
        probe.close()
    for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        candidate = str(item[4][0])
        address = ipaddress.ip_address(candidate)
        if address.is_private and not address.is_loopback:
            return candidate
    raise GatewayError(
        "SAFE_LAN_ADDRESS_UNAVAILABLE",
        "no private IPv4 address is available; use a trusted HTTPS tunnel",
        409,
    )


def _validate_listen_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise GatewayError(
            "REMOTE_LISTEN_HOST_INVALID", "listen host must be a literal IPv4 address"
        ) from error
    if address.version != 4 or address.is_unspecified or not (
        address.is_loopback or address.is_private
    ):
        raise GatewayError(
            "REMOTE_LISTEN_HOST_UNSAFE",
            "LAN gateway may bind only to loopback or a private IPv4 address",
            409,
        )
    return str(address)


class AttemptLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[identity]
            while attempts and now - attempts[0] > self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                return False
            attempts.append(now)
            return True


class RemoteGatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        upstream_state: Path,
        database_path: Path,
        public_base_url: str,
        control_token: str,
    ) -> None:
        self.upstream_state = upstream_state.expanduser().resolve()
        self.remote_access = RemoteAccessStore(database_path)
        self.public_base_url = public_base_url.rstrip("/")
        self.control_token = control_token
        self.pairing_limiter = AttemptLimiter()
        super().__init__(server_address, RemoteGatewayHandler)


class RemoteGatewayHandler(BaseHTTPRequestHandler):
    server: RemoteGatewayServer
    server_version = "UniverseRemoteGateway/1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/_universe/health":
            self._gateway_health()
            return
        if path == "/pair":
            self._pairing_form()
            return
        if path == "/pair/wait":
            self._pairing_wait()
            return
        if path == "/pair/status":
            self._pairing_status()
            return
        if path == "/" and SESSION_COOKIE not in self._cookies():
            # Unpaired browsers land on the public universe list (rendezvous
            # broker at /join), not device-pair. Device pair is still /pair.
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/join")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        device = self._authorize_remote_browser(path=path)
        if device is None:
            return
        self._proxy(device)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/_universe/control/stop":
            if not self._authorize_control():
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "REMOTE_GATEWAY_STOPPING"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/pair/request":
            self._request_pairing()
            return
        device = self._authorize_remote_browser(path=path)
        if device is not None:
            self._proxy(device)

    def do_PATCH(self) -> None:
        device = self._authorize_remote_browser()
        if device is not None:
            self._proxy(device)

    def do_DELETE(self) -> None:
        device = self._authorize_remote_browser()
        if device is not None:
            self._proxy(device)

    def _gateway_health(self) -> None:
        try:
            upstream, _ = _load_upstream_state(self.server.upstream_state)
            status, _ = _loopback_request(upstream, "/health")
            host_ready = status == HTTPStatus.OK
        except (GatewayError, OSError):
            host_ready = False
        self._send_json(
            HTTPStatus.OK,
            {
                "schema": GATEWAY_SCHEMA,
                "status": "READY" if host_ready else "HOST_OFFLINE",
                "public_base_url": self.server.public_base_url,
                "upstream": "LOOPBACK_FIXED",
                "transport_kind": "LAN_DOGFOOD_GATEWAY",
                "auth_type": "DEVICE_PAIRING",
            },
        )

    def _pairing_form(self) -> None:
        code = parse_qs(urlsplit(self.path).query).get("code", [""])[0]
        reason = parse_qs(urlsplit(self.path).query).get("reason", [""])[0]
        safe_code = html.escape(code, quote=True)
        reason_note = ""
        if reason in {
            "REMOTE_DEVICE_SESSION_INVALID",
            "REMOTE_DEVICE_SESSION_EXPIRED",
            "REMOTE_DEVICE_SESSION_MISMATCH",
            "session_invalid",
        }:
            reason_note = (
                "<p class='warn'>Previous remote session expired or was reset "
                "(common after tunnel restart). Pair again to open the main app.</p>"
            )
        self._send_html(
            HTTPStatus.OK,
            f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Pair Universe</title>
<style>{PAIRING_STYLE}</style></head><body><main>
<span class="kicker">UNIVERSE REMOTE</span><h1>Pair this browser</h1>
<p>This pairs <strong>this browser to this PC's Universe app</strong>. After
desktop approval you get the main UI. To browse or join <strong>other
universes</strong>, use the public list.</p>
{reason_note}
<p><a class="link" href="/join">← 유니버스 목록 (다른 유니버스 가입)</a></p>
<ol class="steps">
<li>On desktop Universe: Settings → Remote access → create pairing code</li>
<li>Enter the code here and wait for desktop Approve</li>
<li>This browser opens the main Universe UI automatically</li>
</ol>
<form method="post" action="/pair/request"><label>Pairing code
<input name="code" value="{safe_code}" autocomplete="one-time-code" required></label>
<label>Device name<input name="device_name" value="Mobile browser" maxlength="120" required></label>
<button type="submit">Request access</button></form>
<small>Pairing grants access to the Universe UI only. It creates no execution authority.</small>
</main></body></html>""",
        )

    def _request_pairing(self) -> None:
        if not self.server.pairing_limiter.allow(self.client_address[0]):
            self._send_error_payload(429, "REMOTE_PAIRING_RATE_LIMITED", "try later")
            return
        try:
            body = self._read_body()
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            result = self.server.remote_access.request_pairing(
                code=form.get("code", [""])[0],
                device_name=form.get("device_name", [""])[0],
                user_agent=self.headers.get("User-Agent") or "UNKNOWN_BROWSER",
            )
        except (UnicodeError, RemoteAccessError) as error:
            if isinstance(error, RemoteAccessError):
                self._send_error_payload(error.status, error.code, error.detail)
            else:
                self._send_error_payload(400, "REMOTE_PAIRING_REQUEST_INVALID", str(error))
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header(
            "Set-Cookie",
            self._cookie(PAIRING_COOKIE, result["request_token"], max_age=600),
        )
        self.send_header(
            "Location", "/pair/wait?" + urlencode({"id": result["pairing_id"]})
        )
        self.end_headers()

    def _pairing_wait(self) -> None:
        pairing_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
        safe_id = html.escape(pairing_id, quote=True)
        self._send_html(
            HTTPStatus.OK,
            f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve Universe device</title><style>{PAIRING_STYLE}</style></head>
<body><main><span class="kicker">APPROVAL REQUIRED</span><h1>Check your desktop</h1>
<p id="state">Waiting for the Universe desktop to approve this browser.</p>
<small>You may close this page if you did not request access.</small></main>
<script>const id={json.dumps(safe_id)};async function poll(){{const r=await fetch('/pair/status?id='+encodeURIComponent(id),{{cache:'no-store',credentials:'same-origin'}});const p=await r.json();if(p.state==='CONSUMED'){{location.replace('/?paired=1');return}}if(p.state==='DENIED'||p.state==='EXPIRED'){{document.querySelector('#state').textContent=p.state;return}}setTimeout(poll,1200)}}poll();</script>
</body></html>""",
        )

    def _pairing_status(self) -> None:
        pairing_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
        request_token = self._cookies().get(PAIRING_COOKIE, "")
        try:
            result = self.server.remote_access.pairing_status(
                pairing_id,
                request_token=request_token,
                user_agent=self.headers.get("User-Agent") or "UNKNOWN_BROWSER",
            )
        except RemoteAccessError as error:
            self._send_error_payload(error.status, error.code, error.detail)
            return
        if result["state"] == "CONSUMED":
            secure = urlsplit(self.server.public_base_url).scheme == "https"
            self._send_json(
                HTTPStatus.OK,
                {"status": result["status"], "state": result["state"]},
                cookies=[
                    self._cookie(
                        SESSION_COOKIE,
                        result["session_token"],
                        max_age=30 * 24 * 60 * 60,
                        secure=secure,
                    ),
                    self._cookie(PAIRING_COOKIE, "", max_age=0, secure=secure),
                ],
            )
            return
        self._send_json(
            HTTPStatus.OK, {"status": "REMOTE_PAIRING_STATUS", "state": result["state"]}
        )

    def _authorize_remote_browser(
        self, *, path: str | None = None
    ) -> dict[str, Any] | None:
        token = self._cookies().get(SESSION_COOKIE, "")
        if not token:
            # Browser navigations should re-enter pairing, not a raw JSON error.
            if self._wants_html_navigation():
                self._redirect_to_pair(clear_session=True)
                return None
            self._send_error_payload(
                401, "REMOTE_DEVICE_PAIRING_REQUIRED", "pair this browser first"
            )
            return None
        try:
            return self.server.remote_access.authorize_device(
                token,
                user_agent=self.headers.get("User-Agent") or "UNKNOWN_BROWSER",
            )
        except RemoteAccessError as error:
            # Stale cookies after tunnel/gateway restart are common: clear and re-pair.
            recoverable = error.code in {
                "REMOTE_DEVICE_SESSION_INVALID",
                "REMOTE_DEVICE_SESSION_EXPIRED",
                "REMOTE_DEVICE_SESSION_MISMATCH",
            }
            if recoverable and self._wants_html_navigation():
                self._redirect_to_pair(clear_session=True)
                return None
            if recoverable:
                secure = urlsplit(self.server.public_base_url).scheme == "https"
                self._send_json(
                    error.status,
                    {
                        "status": "ERROR",
                        "error_code": error.code,
                        "detail": error.detail,
                        "recovery": "CLEAR_COOKIE_AND_REPAIR",
                        "pair_path": "/pair",
                    },
                    cookies=[
                        self._cookie(SESSION_COOKIE, "", max_age=0, secure=secure),
                        self._cookie(PAIRING_COOKIE, "", max_age=0, secure=secure),
                    ],
                )
                return None
            self._send_error_payload(error.status, error.code, error.detail)
            return None

    def _wants_html_navigation(self) -> bool:
        accept = (self.headers.get("Accept") or "").lower()
        if "text/html" in accept:
            return True
        # Top-level navigations often omit Accept or use */*.
        if self.command == "GET" and (
            "application/json" not in accept or accept.strip() in {"", "*/*"}
        ):
            path = urlsplit(self.path).path
            if path.startswith("/v1/") or path.startswith("/_universe/"):
                return False
            return True
        return False

    def _redirect_to_pair(self, *, clear_session: bool = False) -> None:
        secure = urlsplit(self.server.public_base_url).scheme == "https"
        location = "/pair"
        if clear_session:
            location = "/pair?reason=session_invalid"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if clear_session:
            self.send_header(
                "Set-Cookie",
                self._cookie(SESSION_COOKIE, "", max_age=0, secure=secure),
            )
            self.send_header(
                "Set-Cookie",
                self._cookie(PAIRING_COOKIE, "", max_age=0, secure=secure),
            )
        self.end_headers()

    def _authorize_control(self) -> bool:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise ValueError
        except ValueError:
            self._send_error_payload(403, "LOOPBACK_CLIENT_REQUIRED", "local control only")
            return False
        scheme, separator, token = (self.headers.get("Authorization") or "").partition(
            " "
        )
        if (
            not separator
            or scheme.lower() != "bearer"
            or not secrets.compare_digest(token, self.server.control_token)
        ):
            self._send_error_payload(401, "REMOTE_GATEWAY_CONTROL_TOKEN_REQUIRED", "")
            return False
        return True

    def _proxy(self, device: dict[str, Any]) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/_universe") or path.startswith("/pair"):
            self._send_error_payload(404, "NOT_FOUND", "")
            return
        if path == "/v1/service/shutdown":
            self._send_error_payload(
                403, "REMOTE_SERVICE_CONTROL_FORBIDDEN", "desktop control is required"
            )
            return
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            self._proxy_websocket(device)
            self.close_connection = True
            return
        connection: HTTPConnection | None = None
        response_started = False
        try:
            upstream, _ = _load_upstream_state(self.server.upstream_state)
            parsed = urlsplit(upstream)
            body = self._read_body(optional=True)
            headers = {
                "Accept": self.headers.get("Accept") or "*/*",
                "User-Agent": self.headers.get("User-Agent") or "UniverseRemote/1",
                "X-Universe-Access-Surface": "REMOTE_BROWSER",
                "X-Universe-Remote-Device": str(device["device_id"]),
            }
            if self.headers.get("Content-Type"):
                headers["Content-Type"] = str(self.headers["Content-Type"])
            if self.headers.get("Last-Event-ID"):
                headers["Last-Event-ID"] = str(self.headers["Last-Event-ID"])
            connection = HTTPConnection(parsed.hostname, parsed.port, timeout=120)
            connection.request(self.command, self.path, body=body or None, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            response_started = True
            for name, value in response.getheaders():
                lowered = name.lower()
                if lowered in HOP_BY_HOP_HEADERS or lowered == "content-length":
                    continue
                self.send_header(name, value)
            if response.getheader("Content-Length"):
                self.send_header("Content-Length", str(response.getheader("Content-Length")))
            else:
                self.send_header("Connection", "close")
            self.send_header("X-Universe-Access-Surface", "REMOTE_BROWSER")
            self.send_header("X-Universe-Remote-Device", str(device["device_id"]))
            self.end_headers()
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            stream_reader = getattr(response, "read1", None) if content_type == "text/event-stream" else None
            while True:
                # read1 keeps SSE deltas live instead of waiting for a large buffer.
                chunk = (
                    stream_reader(64 * 1024)
                    if callable(stream_reader)
                    else response.read(64 * 1024)
                )
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (GatewayError, OSError) as error:
            if not response_started and not self.wfile.closed:
                try:
                    code = error.code if isinstance(error, GatewayError) else "HOST_OFFLINE"
                    detail = error.detail if isinstance(error, GatewayError) else str(error)
                    self._send_error_payload(503, code, detail)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if connection is not None:
                connection.close()

    def _proxy_websocket(self, device: dict[str, Any]) -> None:
        try:
            upstream, _ = _load_upstream_state(self.server.upstream_state)
        except GatewayError as error:
            self._send_error_payload(error.status, error.code, error.detail)
            return
        parsed = urlsplit(upstream)
        try:
            upstream_sock = socket.create_connection(
                (parsed.hostname, parsed.port), timeout=10
            )
        except OSError as error:
            self._send_error_payload(503, "HOST_OFFLINE", str(error))
            return
        request_parts = [f"{self.command} {self.path} HTTP/1.1\r\n"]
        request_parts.append(f"Host: {parsed.hostname}:{parsed.port}\r\n")
        for name, value in self.headers.items():
            if name.lower() == "host":
                continue
            request_parts.append(f"{name}: {value}\r\n")
        request_parts.append("X-Universe-Access-Surface: REMOTE_BROWSER\r\n")
        request_parts.append(f"X-Universe-Remote-Device: {device['device_id']}\r\n")
        request_parts.append("\r\n")
        try:
            upstream_sock.sendall("".join(request_parts).encode("latin-1"))
        except OSError as error:
            upstream_sock.close()
            self._send_error_payload(503, "HOST_OFFLINE", str(error))
            return
        head, leftover = _read_http_head(upstream_sock)
        if not head:
            upstream_sock.close()
            self.log_message("WS-PROXY: empty response from upstream for %s", self.path)
            self._send_error_payload(502, "UPSTREAM_EMPTY", "no response from upstream")
            return
        first_line = head.split(b"\r\n", 1)[0]
        self.log_message("WS-PROXY: upstream first line: %r leftover=%d bytes", first_line, len(leftover))
        if b" 101 " not in first_line:
            self.log_message("WS-PROXY: non-101 from upstream, forwarding to browser")
            self.wfile.write(head)
            self.wfile.flush()
            upstream_sock.close()
            return
        self.wfile.write(head)
        if leftover:
            self.wfile.write(leftover)
        self.wfile.flush()
        client_sock = self.connection
        client_sock.settimeout(0.5)
        upstream_sock.settimeout(0.5)
        stop = threading.Event()

        def relay(src: socket.socket, dst: socket.socket) -> None:
            try:
                while not stop.is_set():
                    try:
                        data = src.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                stop.set()

        t_up = threading.Thread(target=relay, args=(client_sock, upstream_sock), daemon=True)
        t_down = threading.Thread(target=relay, args=(upstream_sock, client_sock), daemon=True)
        t_up.start()
        t_down.start()
        stop.wait()
        t_up.join(timeout=2)
        t_down.join(timeout=2)
        try:
            upstream_sock.close()
        except OSError:
            pass

    def _read_body(self, *, optional: bool = False) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as error:
            raise GatewayError("REQUEST_SIZE_INVALID", "invalid Content-Length") from error
        if length == 0 and optional:
            return b""
        if length <= 0 or length > MAX_BODY_BYTES:
            raise GatewayError(
                "REQUEST_SIZE_INVALID", f"body must be 1..{MAX_BODY_BYTES} bytes"
            )
        return self.rfile.read(length)

    def _cookies(self) -> dict[str, str]:
        parsed = SimpleCookie()
        try:
            parsed.load(self.headers.get("Cookie") or "")
        except Exception:
            return {}
        return {name: morsel.value for name, morsel in parsed.items()}

    @staticmethod
    def _cookie(name: str, value: str, *, max_age: int, secure: bool = False) -> str:
        cookie = SimpleCookie()
        cookie[name] = value
        cookie[name]["path"] = "/"
        cookie[name]["httponly"] = True
        # Lax: allow top-level navigation after /pair/wait → / so the session
        # cookie is actually sent and the Universe main UI can load.
        cookie[name]["samesite"] = "Lax"
        cookie[name]["max-age"] = str(max_age)
        if secure:
            cookie[name]["secure"] = True
        return cookie[name].OutputString()

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        cookies: list[str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_error_payload(self, status: int, code: str, detail: str) -> None:
        self._send_json(
            status,
            {"status": "ERROR", "error_code": code, "detail": detail},
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


PAIRING_STYLE = """
:root{color-scheme:dark;font-family:Inter,Segoe UI,sans-serif;background:#05070a;color:#f3f6f8}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;
background:radial-gradient(circle at 50% 0,#163047 0,transparent 48%),#05070a}
main{width:min(440px,100%);border:1px solid #33414d;background:rgba(10,15,20,.94);padding:28px;
box-shadow:0 24px 80px rgba(0,0,0,.48)}.kicker{font-size:11px;color:#55d6be;letter-spacing:.14em}
h1{font-size:28px;margin:10px 0 8px}p{color:#b7c0c8;line-height:1.55}form{display:grid;gap:16px;margin:24px 0}
label{display:grid;gap:7px;font-size:13px;color:#cad2d8}input{width:100%;border:1px solid #40505c;
background:#0d141a;color:#fff;padding:13px 14px;font:inherit}button{border:0;background:#55d6be;color:#05100e;
padding:13px 16px;font-weight:700;cursor:pointer}small{display:block;color:#77838c;line-height:1.5}
.steps{color:#b7c0c8;line-height:1.55;padding-left:1.2rem;margin:12px 0 0}
.steps li{margin:6px 0}.warn{color:#f0b45d;border:1px solid rgba(240,180,74,.35);
background:rgba(240,180,74,.08);padding:10px 12px;border-radius:8px}
a.link{color:#55d6be;font-size:.9rem}
"""


def create_gateway(
    *,
    listen_host: str,
    port: int,
    upstream_state: Path,
    database_path: Path,
    public_base_url: str = "",
    control_token: str = "",
) -> RemoteGatewayServer:
    listen_host = _validate_listen_host(listen_host)
    server = RemoteGatewayServer(
        (listen_host, port),
        upstream_state=upstream_state,
        database_path=database_path,
        public_base_url=public_base_url or "http://127.0.0.1",
        control_token=control_token or secrets.token_urlsafe(32),
    )
    host, bound_port = server.server_address[:2]
    if not public_base_url:
        server.public_base_url = f"http://{host}:{bound_port}"
    return server


def gateway_status(state_path: Path = default_gateway_state_path()) -> dict[str, Any]:
    try:
        state = load_gateway_state(state_path)
        status, payload = _loopback_request(
            str(state["control_endpoint"]), "/_universe/health"
        )
        if status != HTTPStatus.OK:
            raise GatewayError("REMOTE_GATEWAY_UNHEALTHY", f"health status {status}")
        health = json.loads(payload.decode("utf-8"))
        return {**state, "status": health.get("status", "UNKNOWN")}
    except (GatewayError, OSError, UnicodeError, json.JSONDecodeError):
        return {
            "schema": GATEWAY_SCHEMA,
            "status": "OFFLINE",
            "state_file": str(state_path.expanduser().resolve()),
        }


def start_gateway(
    *,
    upstream_state: Path,
    database_path: Path,
    state_path: Path = default_gateway_state_path(),
    listen_host: str = "",
    port: int = 0,
    public_base_url: str = "",
    python_executable: str = "",
) -> dict[str, Any]:
    current = gateway_status(state_path)
    if current.get("status") in {"READY", "HOST_OFFLINE"}:
        return current
    bind_host = _validate_listen_host(listen_host or _discover_lan_ipv4())
    python = python_executable or sys.executable
    command = [
        python,
        str(Path(__file__).resolve()),
        "serve",
        "--listen-host",
        bind_host,
        "--port",
        str(port),
        "--upstream-state",
        str(upstream_state.expanduser().resolve()),
        "--database",
        str(database_path.expanduser().resolve()),
        "--state-file",
        str(state_path.expanduser().resolve()),
    ]
    if public_base_url:
        command.extend(["--public-base-url", public_base_url])
    log_path = default_data_dir() / "remote-gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open("ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    # The executable and argv are assembled from local fixed paths and typed values.
    subprocess.Popen(  # nosec B603
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=output,
        close_fds=True,
        creationflags=creationflags,
    )
    output.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = gateway_status(state_path)
        if status.get("status") in {"READY", "HOST_OFFLINE"}:
            return status
        time.sleep(0.1)
    raise GatewayError("REMOTE_GATEWAY_START_TIMEOUT", "gateway did not become ready", 503)


def stop_gateway(state_path: Path = default_gateway_state_path()) -> dict[str, Any]:
    try:
        state = load_gateway_state(state_path)
    except GatewayError:
        return {"schema": GATEWAY_SCHEMA, "status": "OFFLINE"}
    try:
        _loopback_request(
            str(state["control_endpoint"]),
            "/_universe/control/stop",
            method="POST",
            body=b"{}",
            headers={"Authorization": f"Bearer {state['control_token']}"},
            timeout=3,
        )
    except (GatewayError, OSError):
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if gateway_status(state_path).get("status") == "OFFLINE":
            state_path.expanduser().unlink(missing_ok=True)
            return {"schema": GATEWAY_SCHEMA, "status": "OFFLINE"}
        time.sleep(0.1)
    return {"schema": GATEWAY_SCHEMA, "status": "STOP_TIMEOUT"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Universe paired remote access gateway")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--listen-host", required=True)
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--upstream-state", type=Path, required=True)
    serve.add_argument("--database", type=Path, required=True)
    serve.add_argument("--state-file", type=Path, default=default_gateway_state_path())
    serve.add_argument("--public-base-url", default="")
    status = commands.add_parser("status")
    status.add_argument("--state-file", type=Path, default=default_gateway_state_path())
    start = commands.add_parser("start")
    start.add_argument(
        "--upstream-state", type=Path, default=default_upstream_state_path()
    )
    start.add_argument("--state-file", type=Path, default=default_gateway_state_path())
    start.add_argument("--listen-host", default="")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--public-base-url", default="")
    stop = commands.add_parser("stop")
    stop.add_argument("--state-file", type=Path, default=default_gateway_state_path())
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "status":
        print(json.dumps(gateway_status(args.state_file), ensure_ascii=False, indent=2))
        return 0
    if args.command == "stop":
        result = stop_gateway(args.state_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "OFFLINE" else 1
    if args.command == "start":
        try:
            _, database_path = _load_upstream_state(args.upstream_state)
            result = start_gateway(
                upstream_state=args.upstream_state,
                database_path=database_path,
                state_path=args.state_file,
                listen_host=args.listen_host,
                port=args.port,
                public_base_url=args.public_base_url,
            )
        except GatewayError as error:
            print(
                json.dumps(
                    {"status": "ERROR", "error_code": error.code, "detail": error.detail}
                )
            )
            return 1
        result.pop("control_token", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    control_token = secrets.token_urlsafe(32)
    gateway = create_gateway(
        listen_host=args.listen_host,
        port=args.port,
        upstream_state=args.upstream_state,
        database_path=args.database,
        public_base_url=args.public_base_url,
        control_token=control_token,
    )
    _, port = gateway.server_address[:2]
    state = {
        "schema": GATEWAY_SCHEMA,
        "status": "READY",
        "pid": os.getpid(),
        "started_at": utc_now(),
        "public_base_url": gateway.public_base_url,
        "control_endpoint": f"http://127.0.0.1:{port}",
        "control_token": control_token,
        "listen_host": args.listen_host,
        "port": port,
        "upstream_state": str(args.upstream_state.expanduser().resolve()),
        "database": str(args.database.expanduser().resolve()),
    }
    _write_json_atomic(args.state_file, state)
    public_state = {key: value for key, value in state.items() if key != "control_token"}
    public_state["control_token_present"] = True
    print(json.dumps(public_state), flush=True)
    try:
        gateway.serve_forever(poll_interval=0.2)
    finally:
        gateway.server_close()
        try:
            current = load_gateway_state(args.state_file)
        except GatewayError:
            current = {}
        if current.get("pid") == os.getpid():
            args.state_file.expanduser().unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
