"""Durable pull mailbox for live PTY sessions.

Messages are persisted to SQLite when a database_path is supplied so that
inboxes and conversation threads survive PTY, provider, and service restarts.
The only optional terminal notification is a one-line HEADER pointer emitted
to the xterm display surface; it never crosses a provider's stdin boundary.
Meeting-room fan-out is a Universe-side helper that copies one thread into
each live participant inbox.
"""

from __future__ import annotations

import json as _json_mod
import secrets
import sqlite3
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Mapping

from universe_app.terminal_host import TerminalHostError


BUS_SCHEMA = "universe.session-bus.v1"
MAX_BODY_BYTES = 32 * 1024
MAX_INBOX = 32
KINDS = frozenset({"NOTE", "INSTRUCTION"})
NOTIFY_MODES = frozenset({"NONE", "HEADER"})
INSTRUCTION_DELIVERY_STATES = frozenset({"PENDING", "CLAIMED", "DISPATCHED"})


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


def _instruction_provenance(source: Mapping[str, str], kind: str) -> dict[str, Any]:
    """Derive trusted direct-user provenance only at the Universe UI boundary."""

    if kind == "INSTRUCTION" and str(source.get("provider") or "").upper() == "UI":
        return {
            "kind": "DIRECT_USER_INSTRUCTION",
            "verified_by": "UNIVERSE_UI",
            "user_authorized": True,
        }
    return {"kind": "SESSION_BUS_INSTRUCTION", "user_authorized": False}


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
    def __init__(self, database_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._messages: dict[str, dict[str, Any]] = {}
        self._inbox: dict[str, list[str]] = {}
        self._database_path: Path | None = (
            Path(database_path).resolve() if database_path else None
        )
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_store()
            self._hydrate()

    def _db_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._database_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_store(self) -> None:
        conn = self._db_connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_bus_message (
                    message_id TEXT PRIMARY KEY,
                    terminal_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    room_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'NOTE',
                    from_json TEXT NOT NULL DEFAULT '{}',
                    to_json TEXT NOT NULL DEFAULT '{}',
                    body_text TEXT NOT NULL DEFAULT '',
                    bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    delivery_state TEXT NOT NULL DEFAULT 'UNREAD',
                    session_anchor_ref TEXT NOT NULL DEFAULT '',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    claimed_at TEXT,
                    dispatched_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sbm_terminal "
                "ON session_bus_message (terminal_id)"
            )
            conn.commit()
        finally:
            conn.close()

    def _hydrate(self) -> None:
        conn = self._db_connect()
        try:
            rows = conn.execute(
                "SELECT * FROM session_bus_message ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            message = self._row_to_message(row)
            mid = message["message_id"]
            tid = message["_terminal_id"]
            self._messages[mid] = message
            self._inbox.setdefault(tid, []).append(mid)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        msg: dict[str, Any] = {
            "message_id": d["message_id"],
            "_terminal_id": d["terminal_id"],
            "thread_id": d["thread_id"],
            "room_id": d["room_id"],
            "kind": d["kind"],
            "from": _json_mod.loads(d["from_json"]),
            "to": _json_mod.loads(d["to_json"]),
            "body_text": d["body_text"],
            "bytes": d["bytes"],
            "created_at": d["created_at"],
            "delivery_state": d["delivery_state"],
            "session_anchor_ref": d["session_anchor_ref"],
            "provenance": _json_mod.loads(d["provenance_json"]),
        }
        if d.get("claimed_at"):
            msg["claimed_at"] = d["claimed_at"]
        if d.get("dispatched_at"):
            msg["dispatched_at"] = d["dispatched_at"]
        return msg

    def _persist_message(self, terminal_id: str, message: dict[str, Any]) -> None:
        if self._database_path is None:
            return
        conn = self._db_connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_bus_message (
                    message_id, terminal_id, thread_id, room_id, kind,
                    from_json, to_json, body_text, bytes, created_at,
                    delivery_state, session_anchor_ref, provenance_json,
                    claimed_at, dispatched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["message_id"],
                    terminal_id,
                    message["thread_id"],
                    message.get("room_id") or "",
                    message["kind"],
                    _json_mod.dumps(message.get("from") or {}),
                    _json_mod.dumps(message.get("to") or {}),
                    message.get("body_text") or "",
                    message.get("bytes") or 0,
                    message["created_at"],
                    message.get("delivery_state") or "UNREAD",
                    message.get("session_anchor_ref") or "",
                    _json_mod.dumps(message.get("provenance") or {}),
                    message.get("claimed_at"),
                    message.get("dispatched_at"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_message(self, message_id: str) -> None:
        if self._database_path is None:
            return
        conn = self._db_connect()
        try:
            conn.execute(
                "DELETE FROM session_bus_message WHERE message_id = ?",
                (message_id,),
            )
            conn.commit()
        finally:
            conn.close()

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
                    self._delete_message(message_id)

    def unread_map(self) -> dict[str, int]:
        with self._lock:
            return {
                terminal_id: sum(
                    1
                    for message_id in message_ids
                    if (self._messages.get(message_id) or {}).get("delivery_state")
                    != "DISPATCHED"
                )
                for terminal_id, message_ids in self._inbox.items()
                if any(
                    (self._messages.get(message_id) or {}).get("delivery_state")
                    != "DISPATCHED"
                    for message_id in message_ids
                )
            }

    def unread_count(self, terminal_id: str) -> int:
        tid = str(terminal_id or "").strip()
        with self._lock:
            return sum(
                1
                for message_id in self._inbox.get(tid, [])
                if (self._messages.get(message_id) or {}).get("delivery_state")
                != "DISPATCHED"
            )

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
            "delivery_state": str(message.get("delivery_state") or "UNREAD"),
            "session_anchor_ref": str(message.get("session_anchor_ref") or ""),
            "provenance": dict(message.get("provenance") or {}),
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
            "delivery_state": "PENDING" if kind == "INSTRUCTION" else "UNREAD",
            "session_anchor_ref": "",
            "provenance": _instruction_provenance(source, kind),
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
                        self._delete_message(old_id)
            self._messages[message_id] = message
        self._persist_message(terminal_id, message)
        if notify == "HEADER":
            # A bus notification is display-only.  Never send the header back
            # through the provider's stdin: Claude may interpret it as a user
            # prompt (and it can trigger the same prompt-injection path as the
            # instruction body).  TerminalHost emits it to xterm subscribers
            # without crossing the PTY input boundary.
            emitter = getattr(host, "emit_output", None)
            if callable(emitter):
                emitter(terminal_id, format_header(message))
            else:
                # Compatibility for small test/dummy hosts that predate the
                # display-only surface.
                host.write(terminal_id, format_header(message))
        return self._public_message(message, headers_only=False)

    def claim_instruction(
        self,
        host: Any,
        *,
        terminal_id: str,
        session_anchor_ref: str,
    ) -> dict[str, Any] | None:
        """Atomically bind one pending instruction to the live Session Anchor.

        The caller invokes this only from an observed provider lifecycle hook.
        It does not send bytes to the terminal; the product adapter owns the
        provider transport and must complete or release this claim.
        """

        terminal = _public_session(host, terminal_id)
        if str(terminal.get("state") or "").upper() != "LIVE":
            raise SessionBusError("BUS_TARGET_NOT_FOUND", "terminal is not live", 404)
        anchor = _text(
            session_anchor_ref,
            "session_anchor_ref",
            required=True,
            limit=256,
        )
        tid = _text(terminal_id, "terminal_id", required=True, limit=80)
        with self._lock:
            for message_id in self._inbox.get(tid, []):
                message = self._messages.get(message_id)
                if not isinstance(message, dict):
                    continue
                if message.get("kind") != "INSTRUCTION":
                    continue
                if message.get("delivery_state") != "PENDING":
                    continue
                message["delivery_state"] = "CLAIMED"
                message["session_anchor_ref"] = anchor
                message["claimed_at"] = utc_now()
                self._persist_message(tid, message)
                return dict(message)
        return None

    def complete_instruction_claim(
        self,
        *,
        terminal_id: str,
        message_id: str,
        session_anchor_ref: str,
    ) -> dict[str, Any]:
        """Record successful provider-adapter delivery for the exact claim."""

        tid = _text(terminal_id, "terminal_id", required=True, limit=80)
        mid = _text(message_id, "message_id", required=True, limit=80)
        anchor = _text(
            session_anchor_ref,
            "session_anchor_ref",
            required=True,
            limit=256,
        )
        with self._lock:
            message = self._messages.get(mid)
            if not isinstance(message, dict) or mid not in self._inbox.get(tid, []):
                raise SessionBusError("BUS_MESSAGE_NOT_FOUND", "message is not in that inbox", 404)
            if (
                message.get("kind") != "INSTRUCTION"
                or message.get("delivery_state") != "CLAIMED"
                or message.get("session_anchor_ref") != anchor
            ):
                raise SessionBusError(
                    "BUS_INSTRUCTION_CLAIM_INVALID",
                    "instruction claim does not match the Session Anchor",
                    409,
                )
            message["delivery_state"] = "DISPATCHED"
            message["dispatched_at"] = utc_now()
            self._persist_message(tid, message)
            return self._public_message(message, headers_only=False)

    def release_instruction_claim(
        self,
        *,
        terminal_id: str,
        message_id: str,
        session_anchor_ref: str,
    ) -> None:
        """Return a failed delivery to pending without changing its target."""

        tid = _text(terminal_id, "terminal_id", required=True, limit=80)
        mid = _text(message_id, "message_id", required=True, limit=80)
        anchor = _text(
            session_anchor_ref,
            "session_anchor_ref",
            required=True,
            limit=256,
        )
        with self._lock:
            message = self._messages.get(mid)
            if not isinstance(message, dict) or mid not in self._inbox.get(tid, []):
                return
            if (
                message.get("delivery_state") == "CLAIMED"
                and message.get("session_anchor_ref") == anchor
            ):
                message["delivery_state"] = "PENDING"
                message["session_anchor_ref"] = ""
                message.pop("claimed_at", None)
                self._persist_message(tid, message)

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
                self._delete_message(mid)
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
