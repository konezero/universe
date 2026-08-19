"""RAM pull mailbox for live PTY sessions.

Bodies stay on the bus. The only optional stdin write is a one-line HEADER
pointer. Meeting-room fan-out is a Universe-side helper that copies one
thread into each live participant inbox.
"""

from __future__ import annotations

import secrets
import threading
import time
from http import HTTPStatus
from typing import Any, Mapping

from universe_app.terminal_host import TerminalHostError


BUS_SCHEMA = "universe.session-bus.v1"
MAX_BODY_BYTES = 32 * 1024
MAX_INBOX = 32
KINDS = frozenset({"NOTE", "INSTRUCTION"})
NOTIFY_MODES = frozenset({"NONE", "HEADER"})


class SessionBusError(ValueError):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(value: Any, field: str, *, required: bool = False, limit: int = 512) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise SessionBusError("BUS_FIELD_REQUIRED", f"{field} is required")
    if len(text) > limit:
        raise SessionBusError("BUS_FIELD_INVALID", f"{field} exceeds {limit} characters")
    return text


def _coord(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "project_id": _text(raw.get("project_id"), "project_id", limit=120),
        "mode": _text(raw.get("mode"), "mode", limit=64).upper(),
        "provider": _text(raw.get("provider"), "provider", limit=64).upper(),
        "terminal_id": _text(raw.get("terminal_id"), "terminal_id", limit=80),
    }


def _public_session(host: Any, terminal_id: str) -> dict[str, Any]:
    session = host.get(terminal_id)
    public = session.public() if hasattr(session, "public") else session
    if not isinstance(public, Mapping):
        raise SessionBusError("BUS_TARGET_NOT_FOUND", "terminal session does not exist", 404)
    return dict(public)


def live_sessions(host: Any) -> list[dict[str, Any]]:
    rows = []
    for item in host.list_sessions():
        if str(item.get("state") or "").upper() == "LIVE":
            rows.append(dict(item))
    return rows


def match_live_terminals(
    host: Any,
    *,
    project_id: str = "",
    mode: str = "",
    provider: str = "",
    terminal_id: str = "",
) -> list[dict[str, Any]]:
    wanted_id = _text(terminal_id, "terminal_id", limit=80)
    if wanted_id:
        try:
            row = _public_session(host, wanted_id)
        except (SessionBusError, TerminalHostError):
            return []
        return [row] if str(row.get("state") or "").upper() == "LIVE" else []
    wanted_project = _text(project_id, "project_id", limit=120)
    wanted_mode = _text(mode, "mode", limit=64).upper()
    wanted_provider = _text(provider, "provider", limit=64).upper()
    rows = []
    for item in live_sessions(host):
        if wanted_project and str(item.get("project_id") or "") != wanted_project:
            continue
        if wanted_mode and str(item.get("mode") or "").upper() != wanted_mode:
            continue
        if (
            wanted_provider
            and wanted_provider != "AUTO"
            and str(item.get("provider") or "").upper() != wanted_provider
        ):
            continue
        rows.append(item)
    return rows


def format_header(message: Mapping[str, Any]) -> bytes:
    source = message.get("from") if isinstance(message.get("from"), Mapping) else {}
    from_label = "/".join(
        part
        for part in (
            str(source.get("project_id") or "").strip() or "unknown",
            str(source.get("mode") or "").strip() or "UNKNOWN",
            str(source.get("provider") or "").strip() or "UNKNOWN",
        )
    )
    room_id = str(message.get("room_id") or "").strip()
    room = f" room {room_id}" if room_id else ""
    line = (
        f"[session-bus] 1 unread {message.get('message_id')}{room} from {from_label}\n"
    )
    return line.encode("utf-8")


def resolve_direct_targets(host: Any, to: Mapping[str, str]) -> list[dict[str, Any]]:
    if to.get("room_id"):
        raise SessionBusError(
            "BUS_ROOM_REQUIRES_UNIVERSE",
            "meeting-room fan-out is served by /v1/session-bus, not the supervisor",
            400,
        )
    rows = match_live_terminals(
        host,
        project_id=to.get("project_id") or "",
        mode=to.get("mode") or "",
        provider=to.get("provider") or "",
        terminal_id=to.get("terminal_id") or "",
    )
    if not rows:
        raise SessionBusError(
            "BUS_TARGET_NOT_FOUND",
            "no live terminal matches the destination",
            404,
        )
    if not to.get("terminal_id") and len(rows) > 1:
        raise SessionBusError(
            "BUS_TARGET_AMBIGUOUS",
            "destination matches more than one live terminal",
            409,
        )
    return rows


class SessionBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: dict[str, dict[str, Any]] = {}
        self._inbox: dict[str, list[str]] = {}

    def drop_terminal(self, terminal_id: str) -> None:
        tid = str(terminal_id or "").strip()
        if not tid:
            return
        with self._lock:
            ids = self._inbox.pop(tid, [])
            remaining = {item for bucket in self._inbox.values() for item in bucket}
            for message_id in ids:
                if message_id not in remaining:
                    self._messages.pop(message_id, None)

    def unread_map(self) -> dict[str, int]:
        with self._lock:
            return {key: len(ids) for key, ids in self._inbox.items() if ids}

    def unread_count(self, terminal_id: str) -> int:
        tid = str(terminal_id or "").strip()
        with self._lock:
            return len(self._inbox.get(tid, []))

    def directory(self, host: Any) -> dict[str, Any]:
        counts = self.unread_map()
        terminals = []
        for item in live_sessions(host):
            row = {
                "project_id": item.get("project_id"),
                "mode": item.get("mode"),
                "provider": item.get("provider"),
                "terminal_id": item.get("terminal_id"),
                "pid": item.get("pid"),
                "unread": int(counts.get(str(item.get("terminal_id") or ""), 0)),
            }
            terminals.append(row)
        return {
            "schema": BUS_SCHEMA,
            "status": "OK",
            "terminals": terminals,
        }

    def _public_message(
        self,
        message: Mapping[str, Any],
        *,
        headers_only: bool,
    ) -> dict[str, Any]:
        body = str(message.get("body_text") or "")
        payload = {
            "schema": BUS_SCHEMA,
            "message_id": message["message_id"],
            "thread_id": message["thread_id"],
            "room_id": message.get("room_id") or "",
            "kind": message["kind"],
            "from": dict(message.get("from") or {}),
            "to": dict(message.get("to") or {}),
            "created_at": message["created_at"],
            "bytes": int(message.get("bytes") or len(body.encode("utf-8"))),
        }
        if not headers_only:
            payload["body_text"] = body
        return payload

    def post(self, host: Any, value: Mapping[str, Any] | None) -> dict[str, Any]:
        payload = value if isinstance(value, Mapping) else {}
        to_raw = payload.get("to") if isinstance(payload.get("to"), Mapping) else {}
        if to_raw.get("room_id"):
            raise SessionBusError(
                "BUS_ROOM_REQUIRES_UNIVERSE",
                "meeting-room fan-out is served by /v1/session-bus, not the supervisor",
                400,
            )
        to = _coord(to_raw)
        source = _coord(payload.get("from"))
        kind = _text(payload.get("kind") or "NOTE", "kind", limit=32).upper()
        if kind not in KINDS:
            raise SessionBusError("BUS_KIND_INVALID", f"unsupported kind: {kind}")
        notify = _text(payload.get("notify") or "NONE", "notify", limit=16).upper()
        if notify not in NOTIFY_MODES:
            raise SessionBusError("BUS_NOTIFY_INVALID", f"unsupported notify: {notify}")
        body = str(payload.get("body_text") or payload.get("text") or "")
        if not body.strip():
            raise SessionBusError("BUS_BODY_REQUIRED", "body_text is required")
        encoded = body.encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            raise SessionBusError("BUS_BODY_TOO_LARGE", "body_text exceeds 32 KiB", 413)
        room_id = _text(payload.get("room_id"), "room_id", limit=80)
        thread_id = _text(payload.get("thread_id"), "thread_id", limit=80)
        targets = resolve_direct_targets(host, to)
        delivered = [
            self._deliver(
                host,
                terminal=item,
                source=source,
                to=to,
                kind=kind,
                notify=notify,
                body=body,
                room_id=room_id,
                thread_id=thread_id,
            )
            for item in targets
        ]
        first = delivered[0]
        return {
            "schema": BUS_SCHEMA,
            "status": "CREATED",
            "thread_id": first["thread_id"],
            "message_id": first["message_id"],
            "messages": delivered,
        }

    def deliver_to_terminal(
        self,
        host: Any,
        *,
        terminal: Mapping[str, Any],
        source: Mapping[str, str],
        to: Mapping[str, str],
        kind: str,
        notify: str,
        body: str,
        room_id: str = "",
        thread_id: str = "",
    ) -> dict[str, Any]:
        return self._deliver(
            host,
            terminal=terminal,
            source=source,
            to=to,
            kind=kind,
            notify=notify,
            body=body,
            room_id=room_id,
            thread_id=thread_id,
        )

    def _deliver(
        self,
        host: Any,
        *,
        terminal: Mapping[str, Any],
        source: Mapping[str, str],
        to: Mapping[str, str],
        kind: str,
        notify: str,
        body: str,
        room_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        terminal_id = str(terminal.get("terminal_id") or "").strip()
        if not terminal_id:
            raise SessionBusError("BUS_TARGET_NOT_FOUND", "terminal id missing", 404)
        message_id = "msg_" + secrets.token_hex(8)
        resolved_thread = thread_id or message_id
        message = {
            "message_id": message_id,
            "thread_id": resolved_thread,
            "room_id": room_id,
            "kind": kind,
            "from": dict(source),
            "to": {
                **dict(to),
                "terminal_id": terminal_id,
                "project_id": str(terminal.get("project_id") or to.get("project_id") or ""),
                "mode": str(terminal.get("mode") or to.get("mode") or ""),
                "provider": str(terminal.get("provider") or to.get("provider") or ""),
            },
            "body_text": body,
            "bytes": len(body.encode("utf-8")),
            "created_at": utc_now(),
        }
        with self._lock:
            inbox = self._inbox.setdefault(terminal_id, [])
            inbox.append(message_id)
            extra = len(inbox) - MAX_INBOX
            if extra > 0:
                dropped = inbox[:extra]
                del inbox[:extra]
                remaining = {item for bucket in self._inbox.values() for item in bucket}
                for old_id in dropped:
                    if old_id not in remaining:
                        self._messages.pop(old_id, None)
            self._messages[message_id] = message
        if notify == "HEADER":
            host.write(terminal_id, format_header(message))
        return self._public_message(message, headers_only=False)

    def inbox(
        self,
        host: Any,
        *,
        terminal_id: str = "",
        project_id: str = "",
        mode: str = "",
        provider: str = "",
        room_id: str = "",
        headers_only: bool = False,
    ) -> dict[str, Any]:
        targets = match_live_terminals(
            host,
            project_id=project_id,
            mode=mode,
            provider=provider,
            terminal_id=terminal_id,
        )
        if not terminal_id and not project_id and not room_id:
            raise SessionBusError("BUS_INBOX_TARGET_REQUIRED", "inbox requires terminal, coordinate, or room_id")
        if terminal_id or (project_id and mode):
            if not targets:
                raise SessionBusError(
                    "BUS_TARGET_NOT_FOUND",
                    "no live terminal matches the inbox query",
                    404,
                )
            if not terminal_id and len(targets) > 1:
                raise SessionBusError(
                    "BUS_TARGET_AMBIGUOUS",
                    "inbox coordinate matches more than one live terminal",
                    409,
                )
        wanted_room = _text(room_id, "room_id", limit=80)
        wanted_ids = {str(item.get("terminal_id") or "") for item in targets} if targets else None
        messages: list[dict[str, Any]] = []
        with self._lock:
            if wanted_ids is not None:
                scan = [(tid, list(self._inbox.get(tid, []))) for tid in wanted_ids]
            else:
                scan = [(tid, list(ids)) for tid, ids in self._inbox.items()]
            for tid, ids in scan:
                for message_id in ids:
                    message = self._messages.get(message_id)
                    if message is None:
                        continue
                    if wanted_room and str(message.get("room_id") or "") != wanted_room:
                        continue
                    row = self._public_message(message, headers_only=headers_only)
                    row["terminal_id"] = tid
                    messages.append(row)
        messages.sort(key=lambda item: str(item.get("created_at") or ""))
        return {
            "schema": BUS_SCHEMA,
            "status": "OK",
            "messages": messages,
        }

    def ack(self, message_id: str, terminal_id: str) -> dict[str, Any]:
        mid = _text(message_id, "message_id", required=True, limit=80)
        tid = _text(terminal_id, "terminal_id", required=True, limit=80)
        with self._lock:
            inbox = self._inbox.get(tid) or []
            if mid not in inbox:
                raise SessionBusError("BUS_MESSAGE_NOT_FOUND", "message is not in that inbox", 404)
            self._inbox[tid] = [item for item in inbox if item != mid]
            remaining = {item for bucket in self._inbox.values() for item in bucket}
            if mid not in remaining:
                self._messages.pop(mid, None)
        return {"schema": BUS_SCHEMA, "status": "OK", "message_id": mid, "terminal_id": tid}


def _mailbox(host: Any, bus: SessionBus | None) -> SessionBus:
    if bus is not None:
        return bus
    owned = getattr(host, "bus", None)
    if isinstance(owned, SessionBus):
        return owned
    raise SessionBusError("BUS_UNAVAILABLE", "session bus mailbox is unavailable", 409)


def fanout_meeting_bus(
    *,
    rooms: Any,
    host: Any,
    value: Mapping[str, Any] | None,
    bus: SessionBus | None = None,
) -> dict[str, Any]:
    mailbox = _mailbox(host, bus)
    payload = value if isinstance(value, Mapping) else {}
    to_raw = payload.get("to") if isinstance(payload.get("to"), Mapping) else {}
    room_id = _text(to_raw.get("room_id") or payload.get("room_id"), "room_id", required=True, limit=80)
    from universe_multi_room import MultiRoomError

    try:
        room = rooms.get_room(room_id)
    except MultiRoomError as error:
        raise SessionBusError(error.code, error.detail, getattr(error, "status", 404)) from error
    if str(room.get("room_type") or "").upper() != "MEETING":
        raise SessionBusError(
            "BUS_ROOM_TYPE_INVALID",
            "room targeting is limited to OPEN MEETING rooms",
            409,
        )
    if str(room.get("state") or "").upper() != "OPEN":
        raise SessionBusError("BUS_ROOM_CLOSED", "cannot post to a closed meeting room", 409)
    source = _coord(payload.get("from"))
    kind = _text(payload.get("kind") or "NOTE", "kind", limit=32).upper()
    if kind not in KINDS:
        raise SessionBusError("BUS_KIND_INVALID", f"unsupported kind: {kind}")
    notify = _text(payload.get("notify") or "NONE", "notify", limit=16).upper()
    if notify not in NOTIFY_MODES:
        raise SessionBusError("BUS_NOTIFY_INVALID", f"unsupported notify: {notify}")
    body = str(payload.get("body_text") or payload.get("text") or "")
    if not body.strip():
        raise SessionBusError("BUS_BODY_REQUIRED", "body_text is required")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise SessionBusError("BUS_BODY_TOO_LARGE", "body_text exceeds 32 KiB", 413)
    author_role = _text(payload.get("author_role") or "USER", "author_role", limit=32).upper()
    durable = rooms.post_message(
        room_id,
        {
            "author_role": author_role,
            "body_text": body,
            "idempotency_key": _text(payload.get("idempotency_key"), "idempotency_key", limit=120)
            or None,
        },
    )
    thread_id = str(durable.get("message_id") or "").strip() or ("msg_" + secrets.token_hex(8))
    skip_id = source.get("terminal_id") or ""
    deliveries: list[dict[str, Any]] = []
    for binding in rooms.list_bindings(room_id, active_only=True):
        provider = str(binding.get("provider") or "").strip()
        if not provider:
            continue
        targets = match_live_terminals(
            host,
            provider=provider,
            terminal_id=str(binding.get("supervisor_session_id") or ""),
        )
        if not targets:
            targets = match_live_terminals(
                host,
                project_id=str(room.get("project_id") or ""),
                provider=provider,
            )
        if not targets:
            targets = match_live_terminals(host, provider=provider)
        for terminal in targets:
            terminal_id = str(terminal.get("terminal_id") or "")
            if skip_id and terminal_id == skip_id:
                continue
            posted = mailbox.post(
                host,
                {
                    "to": {"terminal_id": terminal_id},
                    "from": source,
                    "kind": kind,
                    "notify": notify,
                    "body_text": body,
                    "room_id": room_id,
                    "thread_id": thread_id,
                },
            )
            deliveries.extend(list(posted.get("messages") or [posted]))
    return {
        "schema": BUS_SCHEMA,
        "status": "CREATED",
        "thread_id": thread_id,
        "room_id": room_id,
        "room_message": durable,
        "messages": deliveries,
    }


def bus_http_error(error: SessionBusError) -> tuple[int, dict[str, Any]]:
    return error.status, {
        "schema": BUS_SCHEMA,
        "status": "ERROR",
        "error_code": error.code,
        "detail": error.detail,
    }


def http_status_for(error: SessionBusError) -> int:
    return int(error.status or HTTPStatus.BAD_REQUEST)
