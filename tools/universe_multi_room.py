"""Multi-room chat foundation (function-first).

Implements product slices S1–S5 as durable room/session bindings and control
events. Does not invent authority or Execution Assignment. Full Host
session_ref invalidate remains coordinated with project_master_host; this
module owns room identity, membership, messages, attach, inject, and events.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

ROOM_SCHEMA = "universe.chat-room.v1"
ROOM_MESSAGE_SCHEMA = "universe.chat-room-message.v1"
ROOM_ATTACH_SCHEMA = "universe.chat-room-session-attach.v1"
ROOM_EVENT_SCHEMA = "universe.chat-room-event.v1"
ROOM_STREAM_SCHEMA = "universe.chat-room-stream.v1"
INJECT_SCHEMA = "universe.session-ref-inject.v1"

ROOM_TYPES = frozenset({"PROJECT", "BOSS", "MEETING"})
ROOM_STATES = frozenset({"OPEN", "CLOSED"})
ATTACH_STATES = frozenset({"ACTIVE", "DETACHED", "STALE"})
MEMBER_ROLES = frozenset(
    {
        "USER",
        "CONDUCTOR",
        "MASTER",
        "BOSS",
        "WORKER",
        "MODEL",
        "OBSERVER",
    }
)
# Who may POST chat messages in each room type (product write matrix).
WRITE_ROLES: dict[str, frozenset[str]] = {
    "PROJECT": frozenset({"USER", "CONDUCTOR", "MASTER"}),
    "BOSS": frozenset({"BOSS", "WORKER", "MASTER"}),  # user observe-only
    "MEETING": frozenset({"USER", "CONDUCTOR", "MODEL", "MASTER"}),
}


class MultiRoomError(ValueError):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _text(value: Any, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MultiRoomError("MULTI_ROOM_FIELD_INVALID", f"{field} is required")
    text = value.strip()
    if len(text) > limit:
        raise MultiRoomError(
            "MULTI_ROOM_FIELD_INVALID", f"{field} exceeds {limit} characters"
        )
    return text


def _optional_text(value: Any, field: str, *, limit: int = 512) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, field, limit=limit)


class MultiRoomEventHub:
    """Per-room SSE-style event hub (same pattern as ProjectRoomEventHub)."""

    def __init__(self, *, retained_events: int = 512) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._retained = max(32, int(retained_events))
        self._events: dict[str, deque[dict[str, Any]]] = {}

    def cursor(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, room_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        with self._condition:
            self._sequence += 1
            event = {
                "schema": ROOM_STREAM_SCHEMA,
                "event_id": self._sequence,
                "room_id": rid,
                "emitted_at": utc_now(),
                "payload": dict(payload),
            }
            bucket = self._events.setdefault(rid, deque(maxlen=self._retained))
            bucket.append(event)
            self._condition.notify_all()
            return dict(event)

    def wait(
        self,
        room_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        import time

        rid = _text(room_id, "room_id", limit=80)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._condition:
            while True:
                events = [
                    dict(event)
                    for event in self._events.get(rid, ())
                    if int(event["event_id"]) > after_event_id
                ]
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


class MultiRoomStore:
    """SQLite-backed multi-room store. Shares Universe DB path."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self.hub = MultiRoomEventHub()
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Shares universe.sqlite3 with the main service, remote gateway, and
        # session supervisor. DELETE journal fights WAL writers on Windows and
        # surfaces as "database is locked" when remote UI switches rooms.
        connection = sqlite3.connect(
            self.database_path, timeout=30, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_room (
                    room_id TEXT PRIMARY KEY,
                    room_type TEXT NOT NULL
                        CHECK(room_type IN ('PROJECT', 'BOSS', 'MEETING')),
                    project_id TEXT,
                    task_frame_id TEXT,
                    title TEXT NOT NULL,
                    host_role TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK(state IN ('OPEN', 'CLOSED')),
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS chat_room_project_type
                ON chat_room(project_id, room_type, state, created_at);

                CREATE TABLE IF NOT EXISTS chat_room_session (
                    binding_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    slot_role TEXT NOT NULL,
                    provider TEXT,
                    provider_session_ref TEXT,
                    supervisor_session_id TEXT,
                    display_name TEXT,
                    state TEXT NOT NULL
                        CHECK(state IN ('ACTIVE', 'DETACHED', 'STALE')),
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS chat_room_session_room
                ON chat_room_session(room_id, state, slot_role);

                CREATE TABLE IF NOT EXISTS chat_room_message (
                    message_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    author_role TEXT NOT NULL,
                    author_binding_id TEXT,
                    body_text TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(room_id, idempotency_key)
                );

                CREATE INDEX IF NOT EXISTS chat_room_message_room_time
                ON chat_room_message(room_id, created_at, message_id);

                CREATE TABLE IF NOT EXISTS chat_room_control_event (
                    event_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS chat_room_control_event_room_time
                ON chat_room_control_event(room_id, created_at, event_id);
                """
            )
            connection.commit()

    def create_room(
        self,
        *,
        room_type: str,
        title: str,
        host_role: str,
        project_id: str | None = None,
        task_frame_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        rtype = _text(room_type, "room_type").upper()
        if rtype not in ROOM_TYPES:
            raise MultiRoomError("ROOM_TYPE_INVALID", f"unsupported room_type: {rtype}")
        host = _text(host_role, "host_role").upper()
        if host not in MEMBER_ROLES:
            raise MultiRoomError("ROOM_HOST_ROLE_INVALID", f"unsupported host_role: {host}")
        rid = room_id or ("room_" + secrets.token_hex(12))
        now = utc_now()
        meta = dict(metadata or {})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_room(
                    room_id, room_type, project_id, task_frame_id, title,
                    host_role, state, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
                """,
                (
                    rid,
                    rtype,
                    project_id,
                    task_frame_id,
                    _text(title, "title", limit=200),
                    host,
                    json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            connection.commit()
        room = self.get_room(rid)
        self.hub.publish(rid, {"type": "ROOM_CREATED", "room": room})
        return room

    def get_room(self, room_id: str) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_room WHERE room_id = ?", (rid,)
            ).fetchone()
            if row is None:
                raise MultiRoomError(
                    "ROOM_NOT_FOUND", "chat room does not exist", 404
                )
            return self._room_row(row)

    def list_rooms(
        self,
        *,
        project_id: str | None = None,
        room_type: str | None = None,
        state: str = "OPEN",
    ) -> list[dict[str, Any]]:
        clauses = ["state = ?"]
        params: list[Any] = [state if state in ROOM_STATES else "OPEN"]
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if room_type:
            clauses.append("room_type = ?")
            params.append(room_type.upper())
        sql = (
            "SELECT * FROM chat_room WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, room_id DESC"
        )
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [self._room_row(row) for row in rows]

    def close_room(self, room_id: str) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE chat_room SET state = 'CLOSED', updated_at = ? WHERE room_id = ?",
                (now, rid),
            )
            connection.commit()
        room = self.get_room(rid)
        self.hub.publish(rid, {"type": "ROOM_CLOSED", "room": room})
        return room

    def attach_session(
        self,
        room_id: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        room = self.get_room(room_id)
        if room["state"] != "OPEN":
            raise MultiRoomError("ROOM_CLOSED", "cannot attach to a closed room", 409)
        slot_role = _text(value.get("slot_role") or value.get("role"), "slot_role").upper()
        if slot_role not in MEMBER_ROLES:
            raise MultiRoomError("SLOT_ROLE_INVALID", f"unsupported slot_role: {slot_role}")
        # Product: user cannot take a write role on BOSS rooms via attach-as-USER writing later
        provider = _optional_text(value.get("provider"), "provider", limit=64)
        provider_session_ref = _optional_text(
            value.get("provider_session_ref") or value.get("session_ref"),
            "provider_session_ref",
            limit=256,
        )
        supervisor_session_id = _optional_text(
            value.get("supervisor_session_id") or value.get("session_id"),
            "supervisor_session_id",
            limit=128,
        )
        display_name = _optional_text(value.get("display_name"), "display_name", limit=120)
        now = utc_now()
        binding_id = "bind_" + secrets.token_hex(12)
        # Detach previous ACTIVE same role for this room (one primary slot per role in v0)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE chat_room_session
                SET state = 'DETACHED', updated_at = ?
                WHERE room_id = ? AND slot_role = ? AND state = 'ACTIVE'
                """,
                (now, room["room_id"], slot_role),
            )
            connection.execute(
                """
                INSERT INTO chat_room_session(
                    binding_id, room_id, slot_role, provider, provider_session_ref,
                    supervisor_session_id, display_name, state, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '{}', ?, ?)
                """,
                (
                    binding_id,
                    room["room_id"],
                    slot_role,
                    provider,
                    provider_session_ref,
                    supervisor_session_id,
                    display_name,
                    now,
                    now,
                ),
            )
            connection.commit()
        binding = self.get_binding(binding_id)
        bridge = self._bridge_line(room, binding)
        self.hub.publish(
            room["room_id"],
            {
                "type": "SESSION_ATTACHED",
                "binding": binding,
                "bridge_line": bridge,
            },
        )
        return {
            "schema": ROOM_ATTACH_SCHEMA,
            "status": "ROOM_SESSION_ATTACHED",
            "room": room,
            "binding": binding,
            "bridge_line": bridge,
            # Trust deny-list: never claim authority
            "authority": "UNASSIGNED",
            "execution_assignment": "UNASSIGNED",
        }

    def inject_session_ref(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Harness boot inject: bind provider_session_ref into a room slot."""
        room_id = _optional_text(value.get("room_id"), "room_id", limit=80)
        project_id = _optional_text(value.get("project_id"), "project_id", limit=120)
        room_type = (_optional_text(value.get("room_type"), "room_type") or "PROJECT").upper()
        if room_id is None:
            if not project_id:
                raise MultiRoomError(
                    "INJECT_TARGET_REQUIRED",
                    "room_id or project_id is required",
                )
            rooms = self.list_rooms(project_id=project_id, room_type=room_type)
            if not rooms:
                rooms = [
                    self.create_room(
                        room_type=room_type if room_type in ROOM_TYPES else "PROJECT",
                        title=f"{project_id} {room_type}",
                        host_role="MASTER" if room_type == "PROJECT" else "BOSS",
                        project_id=project_id,
                    )
                ]
            room_id = rooms[0]["room_id"]
        result = self.attach_session(
            room_id,
            {
                "slot_role": value.get("slot_role") or value.get("role") or "MASTER",
                "provider": value.get("provider"),
                "provider_session_ref": value.get("provider_session_ref")
                or value.get("session_ref"),
                "supervisor_session_id": value.get("supervisor_session_id")
                or value.get("session_id"),
                "display_name": value.get("display_name"),
            },
        )
        return {
            **result,
            "schema": INJECT_SCHEMA,
            "status": "SESSION_REF_INJECTED",
        }

    def get_binding(self, binding_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_room_session WHERE binding_id = ?",
                (_text(binding_id, "binding_id", limit=80),),
            ).fetchone()
            if row is None:
                raise MultiRoomError("BINDING_NOT_FOUND", "session binding not found", 404)
            return self._binding_row(row)

    def list_bindings(self, room_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        rid = _text(room_id, "room_id", limit=80)
        with self._connect() as connection:
            if active_only:
                rows = connection.execute(
                    """
                    SELECT * FROM chat_room_session
                    WHERE room_id = ? AND state = 'ACTIVE'
                    ORDER BY created_at ASC
                    """,
                    (rid,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM chat_room_session
                    WHERE room_id = ?
                    ORDER BY created_at ASC
                    """,
                    (rid,),
                ).fetchall()
            return [self._binding_row(row) for row in rows]

    def list_active_session_bindings(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Active room slot bindings with room metadata (Observatory side channel)."""
        cap = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT b.*, r.room_type, r.title AS room_title, r.project_id AS room_project_id,
                       r.host_role AS room_host_role, r.state AS room_state
                FROM chat_room_session b
                JOIN chat_room r ON r.room_id = b.room_id
                WHERE b.state = 'ACTIVE' AND r.state = 'OPEN'
                ORDER BY b.updated_at DESC, b.binding_id DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                binding = self._binding_row(row)
                binding["room_type"] = row["room_type"]
                binding["room_title"] = row["room_title"]
                binding["room_project_id"] = row["room_project_id"]
                binding["room_host_role"] = row["room_host_role"]
                binding["room_state"] = row["room_state"]
                result.append(binding)
            return result

    def post_message(self, room_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        room = self.get_room(room_id)
        if room["state"] != "OPEN":
            raise MultiRoomError("ROOM_CLOSED", "cannot post to a closed room", 409)
        author_role = _text(
            value.get("author_role") or value.get("role") or "USER", "author_role"
        ).upper()
        if author_role not in MEMBER_ROLES:
            raise MultiRoomError("AUTHOR_ROLE_INVALID", f"unsupported role: {author_role}")
        allowed = WRITE_ROLES.get(room["room_type"], frozenset())
        if author_role not in allowed:
            raise MultiRoomError(
                "ROOM_WRITE_FORBIDDEN",
                f"role {author_role} cannot write in {room['room_type']} rooms",
                403,
            )
        # Explicit product rule: USER cannot write BOSS rooms
        if room["room_type"] == "BOSS" and author_role == "USER":
            raise MultiRoomError(
                "ROOM_WRITE_FORBIDDEN",
                "user may only observe boss rooms; instruct the project master",
                403,
            )
        body_text = _text(value.get("body_text") or value.get("text") or value.get("body"), "body_text", limit=20000)
        idem = _optional_text(value.get("idempotency_key"), "idempotency_key", limit=120)
        if not idem:
            idem = "idem_" + secrets.token_hex(16)
        author_binding_id = _optional_text(
            value.get("author_binding_id"), "author_binding_id", limit=80
        )
        message_id = "msg_" + secrets.token_hex(12)
        now = utc_now()
        payload = {
            "schema": ROOM_MESSAGE_SCHEMA,
            "message_id": message_id,
            "room_id": room["room_id"],
            "author_role": author_role,
            "author_binding_id": author_binding_id,
            "body_text": body_text,
            "created_at": now,
        }
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT message_id FROM chat_room_message
                WHERE room_id = ? AND idempotency_key = ?
                """,
                (room["room_id"], idem),
            ).fetchone()
            if existing is not None:
                return self.get_message(str(existing["message_id"]))
            connection.execute(
                """
                INSERT INTO chat_room_message(
                    message_id, room_id, idempotency_key, author_role,
                    author_binding_id, body_text, message_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    room["room_id"],
                    idem,
                    author_role,
                    author_binding_id,
                    body_text,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            connection.commit()
        message = self.get_message(message_id)
        self.hub.publish(
            room["room_id"],
            {"type": "ROOM_MESSAGE", "message": message},
        )
        return message

    def get_message(self, message_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_room_message WHERE message_id = ?",
                (_text(message_id, "message_id", limit=80),),
            ).fetchone()
            if row is None:
                raise MultiRoomError("MESSAGE_NOT_FOUND", "message not found", 404)
            return json.loads(row["message_json"])

    def list_messages(self, room_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rid = _text(room_id, "room_id", limit=80)
        lim = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_json FROM chat_room_message
                WHERE room_id = ?
                ORDER BY created_at ASC, message_id ASC
                LIMIT ?
                """,
                (rid, lim),
            ).fetchall()
            return [json.loads(row["message_json"]) for row in rows]

    def list_recent_messages(
        self, room_id: str, *, limit: int = 2
    ) -> list[dict[str, Any]]:
        """Newest-first then reversed to chronological order for display."""
        rid = _text(room_id, "room_id", limit=80)
        lim = max(1, min(50, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_json FROM chat_room_message
                WHERE room_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (rid, lim),
            ).fetchall()
        messages = [json.loads(row["message_json"]) for row in rows]
        messages.reverse()
        return messages

    def preview_for_session(
        self,
        *,
        supervisor_session_id: str | None = None,
        provider_session_ref: str | None = None,
        project_id: str | None = None,
        limit: int = 2,
        allow_project_fallback: bool = False,
    ) -> dict[str, Any]:
        """Find multi-room lines tied to a *specific* session coordinate.

        Strict by default: only ACTIVE bindings that match supervisor_session_id
        and/or provider_session_ref. Project-level fallback is opt-in so
        Observatory does not paste the same project chat onto every sibling
        session with the same node.
        """
        sid = (supervisor_session_id or "").strip() or None
        pref = (provider_session_ref or "").strip() or None
        pid = (project_id or "").strip() or None
        lim = max(1, min(10, int(limit)))
        empty = {
            "lines": [],
            "source": "NONE",
            "room_id": None,
            "last_message_at": None,
            "match": "NONE",
        }
        if not sid and not pref and not (allow_project_fallback and pid):
            return empty
        with self._connect() as connection:
            rooms: list[Any] = []
            if sid or pref:
                # Prefer exact dual match, then single-key match. Never match by
                # project alone in the strict path.
                if sid and pref:
                    rooms = connection.execute(
                        """
                        SELECT b.room_id, b.updated_at AS binding_updated_at,
                               b.supervisor_session_id, b.provider_session_ref
                        FROM chat_room_session b
                        JOIN chat_room r ON r.room_id = b.room_id
                        WHERE b.state = 'ACTIVE' AND r.state = 'OPEN'
                          AND b.supervisor_session_id = ?
                          AND b.provider_session_ref = ?
                        ORDER BY b.updated_at DESC
                        LIMIT 5
                        """,
                        (sid, pref),
                    ).fetchall()
                    match = "SESSION_AND_REF"
                else:
                    match = "SESSION_ID" if sid else "PROVIDER_REF"
                    rooms = []
                if not rooms and sid:
                    rooms = connection.execute(
                        """
                        SELECT b.room_id, b.updated_at AS binding_updated_at,
                               b.supervisor_session_id, b.provider_session_ref
                        FROM chat_room_session b
                        JOIN chat_room r ON r.room_id = b.room_id
                        WHERE b.state = 'ACTIVE' AND r.state = 'OPEN'
                          AND b.supervisor_session_id = ?
                        ORDER BY b.updated_at DESC
                        LIMIT 5
                        """,
                        (sid,),
                    ).fetchall()
                    if rooms:
                        match = "SESSION_ID"
                if not rooms and pref:
                    rooms = connection.execute(
                        """
                        SELECT b.room_id, b.updated_at AS binding_updated_at,
                               b.supervisor_session_id, b.provider_session_ref
                        FROM chat_room_session b
                        JOIN chat_room r ON r.room_id = b.room_id
                        WHERE b.state = 'ACTIVE' AND r.state = 'OPEN'
                          AND b.provider_session_ref = ?
                        ORDER BY b.updated_at DESC
                        LIMIT 5
                        """,
                        (pref,),
                    ).fetchall()
                    if rooms:
                        match = "PROVIDER_REF"
            else:
                match = "NONE"
                rooms = []
        if not rooms and allow_project_fallback and pid:
            for room in self.list_rooms(project_id=pid, room_type="PROJECT"):
                rooms = [
                    {
                        "room_id": room["room_id"],
                        "binding_updated_at": room.get("updated_at"),
                    }
                ]
                match = "PROJECT_FALLBACK"
                break
        if not rooms:
            return empty
        room_id = str(rooms[0]["room_id"])
        messages = self.list_recent_messages(room_id, limit=lim)
        lines: list[dict[str, Any]] = []
        last_at = None
        for message in messages:
            text = str(
                message.get("body_text")
                or message.get("body")
                or message.get("text")
                or ""
            ).strip()
            if not text:
                continue
            created = message.get("created_at")
            if created:
                last_at = created
            lines.append(
                {
                    "author_role": message.get("author_role") or message.get("role") or "?",
                    "text": text[:240],
                    "created_at": created,
                }
            )
        return {
            "lines": lines,
            "source": "MULTI_ROOM" if lines else "MULTI_ROOM_EMPTY",
            "room_id": room_id,
            "last_message_at": last_at,
            "match": match,
        }

    def ensure_project_room(self, project_id: str) -> dict[str, Any]:
        pid = _text(project_id, "project_id", limit=120)
        existing = self.list_rooms(project_id=pid, room_type="PROJECT")
        if existing:
            return existing[0]
        return self.create_room(
            room_type="PROJECT",
            title=f"Project {pid}",
            host_role="MASTER",
            project_id=pid,
            metadata={"source": "ensure_project_room"},
        )

    def create_boss_room(
        self,
        *,
        project_id: str,
        task_frame_id: str,
        title: str | None = None,
        boss_session: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        pid = _text(project_id, "project_id", limit=120)
        tf = _text(task_frame_id, "task_frame_id", limit=120)
        # Idempotent: one OPEN boss room per task frame
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT room_id FROM chat_room
                WHERE room_type = 'BOSS' AND task_frame_id = ? AND state = 'OPEN'
                """,
                (tf,),
            ).fetchone()
            if row is not None:
                return self.get_room(str(row["room_id"]))
        room = self.create_room(
            room_type="BOSS",
            title=title or f"Boss / {tf}",
            host_role="BOSS",
            project_id=pid,
            task_frame_id=tf,
            metadata={"task_frame_id": tf},
        )
        if boss_session:
            self.attach_session(
                room["room_id"],
                {
                    "slot_role": "BOSS",
                    **dict(boss_session),
                    "display_name": boss_session.get("display_name") or "Boss",
                },
            )
        return self.get_room(room["room_id"])

    def create_meeting_room(
        self,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        title = _text(value.get("title") or "Meeting", "title", limit=200)
        project_id = _optional_text(value.get("project_id"), "project_id", limit=120)
        topic = _optional_text(value.get("topic"), "topic", limit=2000)
        models = value.get("models") or value.get("model_slots") or []
        if not isinstance(models, list):
            raise MultiRoomError("MEETING_MODELS_INVALID", "models must be a list")
        room = self.create_room(
            room_type="MEETING",
            title=title,
            host_role="CONDUCTOR",
            project_id=project_id,
            metadata={
                "topic": topic,
                "source": "meeting-room.create",
                "created_by": "skill_or_api",
            },
        )
        self.attach_session(
            room["room_id"],
            {
                "slot_role": "CONDUCTOR",
                "display_name": value.get("conductor_name") or "Conductor",
                "provider": value.get("conductor_provider"),
                "provider_session_ref": value.get("conductor_session_ref"),
            },
        )
        for index, model in enumerate(models):
            if not isinstance(model, Mapping):
                continue
            self.attach_session(
                room["room_id"],
                {
                    "slot_role": "MODEL",
                    "display_name": model.get("display_name")
                    or model.get("name")
                    or f"Model-{index + 1}",
                    "provider": model.get("provider"),
                    "provider_session_ref": model.get("provider_session_ref")
                    or model.get("session_ref"),
                    "supervisor_session_id": model.get("supervisor_session_id")
                    or model.get("session_id"),
                },
            )
        return {
            "schema": ROOM_SCHEMA,
            "status": "MEETING_ROOM_CREATED",
            "room": self.get_room(room["room_id"]),
            "bindings": self.list_bindings(room["room_id"]),
            "authority": "UNASSIGNED",
            "execution_assignment": "UNASSIGNED",
        }

    def call_master(self, room_id: str, value: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Boss room control: request Master attach/attention (does not grant authority)."""
        room = self.get_room(room_id)
        if room["room_type"] != "BOSS":
            raise MultiRoomError(
                "CALL_MASTER_ROOM_TYPE_INVALID",
                "only BOSS rooms can call master",
                409,
            )
        reason = _optional_text((value or {}).get("reason"), "reason", limit=2000)
        event_id = "evt_" + secrets.token_hex(12)
        now = utc_now()
        payload = {
            "schema": ROOM_EVENT_SCHEMA,
            "event_id": event_id,
            "event_type": "CALL_MASTER",
            "room_id": room["room_id"],
            "project_id": room.get("project_id"),
            "task_frame_id": room.get("task_frame_id"),
            "reason": reason,
            "created_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_room_control_event(
                    event_id, room_id, event_type, payload_json, created_at
                ) VALUES (?, ?, 'CALL_MASTER', ?, ?)
                """,
                (
                    event_id,
                    room["room_id"],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            connection.commit()
        # Optional: auto-open a MASTER slot placeholder for attach
        master_binding = None
        if (value or {}).get("auto_attach_master"):
            master_binding = self.attach_session(
                room["room_id"],
                {
                    "slot_role": "MASTER",
                    "display_name": "Master",
                    "provider": (value or {}).get("master_provider"),
                    "provider_session_ref": (value or {}).get("master_session_ref"),
                    "supervisor_session_id": (value or {}).get("master_supervisor_session_id"),
                },
            )
        self.hub.publish(
            room["room_id"],
            {"type": "CALL_MASTER", "event": payload, "master_attach": master_binding},
        )
        return {
            "schema": ROOM_EVENT_SCHEMA,
            "status": "MASTER_CALLED",
            "event": payload,
            "master_attach": master_binding,
            "authority": "UNASSIGNED",
            "execution_assignment": "UNASSIGNED",
        }

    def worker_report(self, room_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        """Worker → Boss problem/progress report (message + control event)."""
        room = self.get_room(room_id)
        if room["room_type"] != "BOSS":
            raise MultiRoomError(
                "WORKER_REPORT_ROOM_TYPE_INVALID",
                "worker reports only apply to BOSS rooms",
                409,
            )
        text = _text(
            value.get("body_text") or value.get("text") or value.get("report"),
            "body_text",
            limit=20000,
        )
        message = self.post_message(
            room_id,
            {
                "author_role": "WORKER",
                "body_text": text,
                "idempotency_key": value.get("idempotency_key"),
                "author_binding_id": value.get("author_binding_id"),
            },
        )
        event_id = "evt_" + secrets.token_hex(12)
        now = utc_now()
        payload = {
            "schema": ROOM_EVENT_SCHEMA,
            "event_id": event_id,
            "event_type": "WORKER_REPORT",
            "room_id": room["room_id"],
            "message_id": message["message_id"],
            "severity": (value.get("severity") or "INFO").upper(),
            "created_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_room_control_event(
                    event_id, room_id, event_type, payload_json, created_at
                ) VALUES (?, ?, 'WORKER_REPORT', ?, ?)
                """,
                (
                    event_id,
                    room["room_id"],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            connection.commit()
        self.hub.publish(room["room_id"], {"type": "WORKER_REPORT", "event": payload})
        return {
            "status": "WORKER_REPORT_RECORDED",
            "message": message,
            "event": payload,
        }

    def room_snapshot(self, room_id: str) -> dict[str, Any]:
        room = self.get_room(room_id)
        bindings = self.list_bindings(room_id)
        messages = self.list_messages(room_id, limit=200)
        master = next((b for b in bindings if b["slot_role"] == "MASTER"), None)
        host = next(
            (b for b in bindings if b["slot_role"] == room["host_role"]), None
        )
        bridge = self._bridge_line(room, master or host)
        return {
            "schema": ROOM_SCHEMA,
            "status": "ROOM_SNAPSHOT",
            "room": room,
            "bindings": bindings,
            "messages": messages,
            "bridge_line": bridge,
            "write_roles": sorted(WRITE_ROLES.get(room["room_type"], frozenset())),
            "user_may_write": "USER" in WRITE_ROLES.get(room["room_type"], frozenset()),
        }

    def _bridge_line(
        self, room: Mapping[str, Any], binding: Mapping[str, Any] | None
    ) -> str:
        if binding is None:
            return (
                f"Room {room.get('title')} ({room.get('room_type')}) — "
                "no session attached yet."
            )
        ref = binding.get("provider_session_ref") or binding.get("supervisor_session_id")
        ref_s = (str(ref)[:16] + "…") if ref else "no-ref"
        return (
            f"[{room.get('room_type')}] {room.get('title')} · "
            f"{binding.get('slot_role')} "
            f"{binding.get('display_name') or ''} · "
            f"{binding.get('provider') or 'PROVIDER?'} · "
            f"ref={ref_s} · "
            "meaning may resume via provider ref; timeline from durable room turns."
        )

    def _room_row(self, row: sqlite3.Row) -> dict[str, Any]:
        meta = {}
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        return {
            "schema": ROOM_SCHEMA,
            "room_id": row["room_id"],
            "room_type": row["room_type"],
            "project_id": row["project_id"],
            "task_frame_id": row["task_frame_id"],
            "title": row["title"],
            "host_role": row["host_role"],
            "state": row["state"],
            "metadata": meta,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _binding_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": ROOM_ATTACH_SCHEMA,
            "binding_id": row["binding_id"],
            "room_id": row["room_id"],
            "slot_role": row["slot_role"],
            "provider": row["provider"],
            "provider_session_ref": row["provider_session_ref"],
            "supervisor_session_id": row["supervisor_session_id"],
            "display_name": row["display_name"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
