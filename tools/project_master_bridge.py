from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


MASTER_BRIDGE_ENVELOPE_SCHEMA = "universe.project-master-bridge-envelope.v1"
MASTER_BRIDGE_INBOX_RECEIPT_SCHEMA = "universe.project-master-inbox-receipt.v1"
ROOM_MESSAGE_ID_PATTERN = re.compile(r"^room_[0-9a-f]{32}$")
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BRIDGE_ID_PATTERN = re.compile(r"^bridge_[0-9a-f]{20,64}$")
REPLY_KINDS = frozenset({"QUESTION", "REVIEW", "STATUS", "TASK_DRAFT", "RESULT"})
STREAM_EVENT_KINDS = frozenset({"STARTED", "DELTA", "COMPLETED", "FAILED"})
MAX_BODY_BYTES = 1024 * 1024


class ProjectMasterBridgeError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectMasterBridgeHost:
    """A deterministic loopback receiver for one separately running Master Host.

    It writes an authenticated room envelope to the project's existing MASTER
    inbox. It deliberately does not invoke an LLM, load project code, or grant
    source-mutation authority.
    """

    project_root: Path
    token: str
    inbox_ref: str = ".ai/inbox/MASTER"
    require_inbox: bool = True

    def __post_init__(self) -> None:
        _text(self.token, "token")
        if self.require_inbox:
            self._inbox()

    def record(self, envelope: Any) -> dict[str, Any]:
        normalize_bridge_envelope(envelope)
        raise ProjectMasterBridgeError("MASTER_CONVERSATION_HANDLER_UNAVAILABLE")

    def record_inbox_dispatch(self, envelope: Any) -> dict[str, Any]:
        normalized = normalize_bridge_envelope(envelope)
        inbox = self._inbox()
        message_id = normalized["message"]["message_id"]
        target = inbox / f"universe-room-{message_id}.json"
        content = (
            json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise ProjectMasterBridgeError("MASTER_INBOX_TARGET_INVALID")
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ProjectMasterBridgeError("MASTER_INBOX_DELIVERY_CONFLICT")
            status = "ALREADY_RECORDED"
        else:
            _create_file(target, content)
            status = "RECORDED"
        root = self.project_root.expanduser().resolve(strict=True)
        return {
            "schema": MASTER_BRIDGE_INBOX_RECEIPT_SCHEMA,
            "status": status,
            "bridge_id": normalized["bridge_id"],
            "project_id": normalized["project_id"],
            "message_id": message_id,
            "target_ref": target.relative_to(root).as_posix(),
            "content_sha256": digest,
            "recorded_at": utc_now(),
        }

    def apply_seed_assets(self, _request: Any) -> dict[str, Any]:
        raise ProjectMasterBridgeError(
            "PROJECT_SEED_ASSET_MUTATION_GATEWAY_UNAVAILABLE"
        )

    def apply_skill_plan(self, _request: Any) -> dict[str, Any]:
        raise ProjectMasterBridgeError("PROJECT_SKILL_PLAN_CONTEXT_GATEWAY_UNAVAILABLE")

    def _inbox(self) -> Path:
        root = self.project_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ProjectMasterBridgeError("PROJECT_ROOT_UNAVAILABLE")
        relative = Path(_text(self.inbox_ref, "inbox_ref").replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ProjectMasterBridgeError("MASTER_INBOX_REF_INVALID")
        inbox = root / relative
        try:
            resolved = inbox.resolve(strict=True)
        except OSError as error:
            raise ProjectMasterBridgeError("MASTER_INBOX_UNAVAILABLE") from error
        if not resolved.is_dir():
            raise ProjectMasterBridgeError("MASTER_INBOX_UNAVAILABLE")
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ProjectMasterBridgeError("MASTER_INBOX_BOUNDARY_VIOLATION") from error
        current = inbox
        while current != root:
            if current.is_symlink():
                raise ProjectMasterBridgeError("MASTER_INBOX_SYMLINK_FORBIDDEN")
            current = current.parent
        return resolved


class ProjectMasterBridgeHttpServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], host: ProjectMasterBridgeHost):
        if address[0] != "127.0.0.1":
            raise ProjectMasterBridgeError("MASTER_BRIDGE_LISTEN_MUST_BE_LOOPBACK")
        self.bridge_host = host
        super().__init__(address, ProjectMasterBridgeRequestHandler)


class ProjectMasterBridgeRequestHandler(BaseHTTPRequestHandler):
    server: ProjectMasterBridgeHttpServer

    def do_POST(self) -> None:  # noqa: N802
        routes = {
            "/v1/project-master/messages": self.server.bridge_host.record,
            (
                "/v1/project-master/inbox-dispatches"
            ): self.server.bridge_host.record_inbox_dispatch,
            (
                "/v1/project-master/seed-assets/apply"
            ): self.server.bridge_host.apply_seed_assets,
            (
                "/v1/project-master/skill-plans/apply"
            ): self.server.bridge_host.apply_skill_plan,
        }
        operation = routes.get(self.path)
        if operation is None:
            self._send_error(HTTPStatus.NOT_FOUND, "ROUTE_NOT_FOUND")
            return
        expected = f"Bearer {self.server.bridge_host.token}"
        provided = self.headers.get("Authorization")
        if not provided or not hmac.compare_digest(provided, expected):
            self._send_error(HTTPStatus.FORBIDDEN, "MASTER_BRIDGE_AUTH_REQUIRED")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ProjectMasterBridgeError("MASTER_BRIDGE_BODY_INVALID")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            receipt = operation(payload)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            ProjectMasterBridgeError,
        ) as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        created = receipt["status"] in {
            "RECORDED",
            "ACCEPTED",
            "PROJECT_SEED_ASSETS_APPLIED",
            "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
        } and not receipt.get("idempotent_replay", False)
        status = HTTPStatus.CREATED if created else HTTPStatus.OK
        self._send_json(status, receipt)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_error(self, status: HTTPStatus, code: str) -> None:
        self._send_json(status, {"status": "REJECTED", "error_code": code})

    def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def normalize_bridge_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "bridge_id",
        "project_id",
        "master_session_ref",
        "message",
    }:
        raise ProjectMasterBridgeError("MASTER_BRIDGE_ENVELOPE_INVALID")
    if value["schema"] != MASTER_BRIDGE_ENVELOPE_SCHEMA:
        raise ProjectMasterBridgeError("MASTER_BRIDGE_ENVELOPE_SCHEMA_UNSUPPORTED")
    bridge_id = _text(value["bridge_id"], "bridge_id")
    if BRIDGE_ID_PATTERN.fullmatch(bridge_id) is None:
        raise ProjectMasterBridgeError("MASTER_BRIDGE_ID_INVALID")
    project_id = _project_id(value["project_id"])
    message = value["message"]
    if not isinstance(message, dict):
        raise ProjectMasterBridgeError("MASTER_BRIDGE_MESSAGE_INVALID")
    message_id = _text(message.get("message_id"), "message.message_id")
    if ROOM_MESSAGE_ID_PATTERN.fullmatch(message_id) is None:
        raise ProjectMasterBridgeError("MASTER_BRIDGE_MESSAGE_ID_INVALID")
    if _project_id(message.get("project_id")) != project_id:
        raise ProjectMasterBridgeError("MASTER_BRIDGE_PROJECT_MISMATCH")
    return {
        "schema": MASTER_BRIDGE_ENVELOPE_SCHEMA,
        "bridge_id": bridge_id,
        "project_id": project_id,
        "master_session_ref": _text(value["master_session_ref"], "master_session_ref"),
        "message": message,
    }


def post_master_reply(
    *,
    universe_endpoint: str,
    project_id: str,
    bridge_id: str,
    in_reply_to: str,
    kind: str,
    body: str,
    idempotency_key: str,
    bridge_token: str,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    origin = loopback_origin(universe_endpoint, label="universe")
    normalized_project = _project_id(project_id)
    normalized_kind = _text(kind, "kind").upper()
    if normalized_kind not in REPLY_KINDS:
        raise ProjectMasterBridgeError("MASTER_REPLY_KIND_INVALID")
    request_payload = {
        "bridge_id": _text(bridge_id, "bridge_id"),
        "in_reply_to": _text(in_reply_to, "in_reply_to"),
        "kind": normalized_kind,
        "body": _text(body, "body"),
        "idempotency_key": _text(idempotency_key, "idempotency_key"),
    }
    request = Request(
        f"{origin}/v1/projects/{quote(normalized_project, safe='')}/master-bridge/replies",
        data=json.dumps(request_payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        ),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Universe-Bridge-Token": _text(bridge_token, "bridge_token"),
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
            status_code = response.status
    except HTTPError as error:
        raise ProjectMasterBridgeError(f"UNIVERSE_REPLY_HTTP_{error.code}") from error
    except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectMasterBridgeError("UNIVERSE_REPLY_UNAVAILABLE") from error
    if not 200 <= status_code < 300 or not isinstance(payload, dict):
        raise ProjectMasterBridgeError("UNIVERSE_REPLY_REJECTED")
    return payload


def post_master_stream_event(
    *,
    universe_endpoint: str,
    project_id: str,
    bridge_id: str,
    in_reply_to: str,
    event: str,
    sequence: int,
    bridge_token: str,
    delta: str = "",
    detail: str = "",
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    origin = loopback_origin(universe_endpoint, label="universe")
    normalized_project = _project_id(project_id)
    normalized_event = _text(event, "event").upper()
    if normalized_event not in STREAM_EVENT_KINDS:
        raise ProjectMasterBridgeError("MASTER_STREAM_EVENT_INVALID")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ProjectMasterBridgeError("MASTER_STREAM_SEQUENCE_INVALID")
    request_payload = {
        "bridge_id": _text(bridge_id, "bridge_id"),
        "in_reply_to": _text(in_reply_to, "in_reply_to"),
        "event": normalized_event,
        "sequence": sequence,
        "delta": str(delta),
        "detail": str(detail),
    }
    request = Request(
        f"{origin}/v1/projects/{quote(normalized_project, safe='')}/master-bridge/stream",
        data=json.dumps(
            request_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Universe-Bridge-Token": _text(bridge_token, "bridge_token"),
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
            status_code = response.status
    except HTTPError as error:
        raise ProjectMasterBridgeError(f"UNIVERSE_STREAM_HTTP_{error.code}") from error
    except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectMasterBridgeError("UNIVERSE_STREAM_UNAVAILABLE") from error
    if not 200 <= status_code < 300 or not isinstance(payload, dict):
        raise ProjectMasterBridgeError("UNIVERSE_STREAM_REJECTED")
    return payload


def loopback_origin(value: Any, *, label: str) -> str:
    endpoint = _text(value, "endpoint").rstrip("/")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.hostname is None
    ):
        raise ProjectMasterBridgeError(f"{label.upper()}_ENDPOINT_INVALID")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ProjectMasterBridgeError(
            f"{label.upper()}_ENDPOINT_MUST_BE_LOOPBACK"
        ) from error
    if not address.is_loopback:
        raise ProjectMasterBridgeError(f"{label.upper()}_ENDPOINT_MUST_BE_LOOPBACK")
    return endpoint


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _project_id(value: Any) -> str:
    project_id = _text(value, "project_id")
    if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise ProjectMasterBridgeError("PROJECT_ID_INVALID")
    return project_id


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectMasterBridgeError(f"{field} must be non-empty text")
    return value.strip()


def _create_file(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if (
                hashlib.sha256(path.read_bytes()).hexdigest()
                != hashlib.sha256(content).hexdigest()
            ):
                raise ProjectMasterBridgeError("MASTER_INBOX_DELIVERY_CONFLICT")
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_token(environment_name: str) -> str:
    token = os.environ.get(_text(environment_name, "token_env"))
    if not token:
        raise ProjectMasterBridgeError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Project Master Bridge Host")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--project-root", required=True, type=Path)
    serve.add_argument("--token-env", required=True)
    serve.add_argument("--inbox-ref", default=".ai/inbox/MASTER")
    serve.add_argument("--port", default=0, type=int)
    reply = commands.add_parser("reply")
    reply.add_argument("--universe-endpoint", required=True)
    reply.add_argument("--project-id", required=True)
    reply.add_argument("--bridge-id", required=True)
    reply.add_argument("--in-reply-to", required=True)
    reply.add_argument("--kind", required=True)
    reply.add_argument("--body", required=True)
    reply.add_argument("--idempotency-key", required=True)
    reply.add_argument("--token-env", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        server = ProjectMasterBridgeHttpServer(
            ("127.0.0.1", args.port),
            ProjectMasterBridgeHost(
                args.project_root,
                _read_token(args.token_env),
                args.inbox_ref,
            ),
        )
        print(
            json.dumps(
                {
                    "endpoint": f"http://127.0.0.1:{server.server_port}",
                    "status": "LISTENING",
                }
            ),
            flush=True,
        )
        server.serve_forever()
        return 0
    print(
        json.dumps(
            post_master_reply(
                universe_endpoint=args.universe_endpoint,
                project_id=args.project_id,
                bridge_id=args.bridge_id,
                in_reply_to=args.in_reply_to,
                kind=args.kind,
                body=args.body,
                idempotency_key=args.idempotency_key,
                bridge_token=_read_token(args.token_env),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
