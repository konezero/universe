"""Multi-room chat foundation (function-first).

Implements product slices S1–S5 as durable room/session bindings and control
events. Does not invent authority or Execution Assignment. Full Host
session_ref invalidate remains coordinated with project_master_host; this
module owns room identity, membership, messages, attach, inject, and events.
"""

from __future__ import annotations

import json
import hashlib
import secrets
import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

ROOM_SCHEMA = "universe.chat-room.v1"
ROOM_MESSAGE_SCHEMA = "universe.chat-room-message.v1"
ROOM_ATTACH_SCHEMA = "universe.chat-room-session-attach.v1"
ROOM_EVENT_SCHEMA = "universe.chat-room-event.v1"
ROOM_STREAM_SCHEMA = "universe.chat-room-stream.v1"
INJECT_SCHEMA = "universe.session-ref-inject.v1"
ROOM_DURABLE_EVENT_SCHEMA = "universe.chat-room-durable-event.v1"
ROOM_CURSOR_SCHEMA = "universe.chat-room-cursor.v1"
MEETING_COORDINATOR_SCHEMA = "universe.meeting-coordinator.v1"
MEETING_SUMMARY_SCHEMA = "universe.meeting-summary.v1"
MEETING_PROTOCOLS = frozenset(
    {"INCREMENTAL_DELTA_ONLY", "INDEPENDENT_PROPOSAL_REVIEW"}
)
ROOM_ARTIFACT_SCHEMA = "universe.chat-room-artifact.v1"
ROOM_ARTIFACT_REVISION_SCHEMA = "universe.chat-room-artifact-revision.v1"
ROOM_FINDING_SCHEMA = "universe.chat-room-finding.v1"

ROOM_TYPES = frozenset({"PROJECT", "BOSS", "MEETING"})
ROOM_STATES = frozenset({"OPEN", "CLOSED"})
ROOM_ARTIFACT_TYPES = frozenset(
    {"PROPOSAL", "SPECIFICATION", "COMPARISON", "DECISION_CANDIDATE"}
)
ROOM_ARTIFACT_STATES = frozenset({"DRAFT", "REVIEW", "CANDIDATE", "ARCHIVED"})
ROOM_FINDING_TYPES = frozenset(
    {"RAG_FINDING", "CROSS_FEATURE_DEPENDENCY", "ESCALATION_REQUEST"}
)
ROOM_FINDING_STATES = frozenset({"OPEN", "ACKNOWLEDGED", "RESOLVED"})
MESSAGE_KINDS = frozenset(
    {
        "MESSAGE",
        "DECISION",
        "TASK_DRAFT",
        "DOCUMENT_DRAFT",
        "FAILURE",
        "BENCH_OBSERVATION",
    }
)
ATTACH_STATES = frozenset({"ACTIVE", "DETACHED", "STALE"})
PARTICIPANT_STATES = frozenset(
    {"OBSERVED", "ATTACHED", "CONTROLLED", "LIVE", "DISCONNECTED"}
)
DELIVERY_STATES = frozenset(
    {
        "QUEUED",
        "ACCEPTED",
        "DEFERRED",
        "INTERRUPTED",
        "REJECTED",
        "DISCONNECTED",
        "UNCERTAIN",
    }
)
ACCEPTED_DELIVERY_STATES = frozenset({"ACCEPTED", "DEFERRED", "INTERRUPTED"})
SINGLETON_SLOT_ROLES = frozenset({"CONDUCTOR", "MASTER", "BOSS"})
MEMBER_ROLES = frozenset(
    {
        "USER",
        "CONDUCTOR",
        "MASTER",
        "BOSS",
        "WORKER",
        "REVIEWER",
        "MODEL",
        "OBSERVER",
    }
)
# Who may POST chat messages in each room type (product write matrix).
WRITE_ROLES: dict[str, frozenset[str]] = {
    "PROJECT": frozenset({"USER", "CONDUCTOR", "MASTER"}),
    "BOSS": frozenset({"BOSS", "WORKER", "REVIEWER", "MASTER"}),  # user observe-only
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

                CREATE TABLE IF NOT EXISTS chat_room_artifact (
                    artifact_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    artifact_type TEXT NOT NULL
                        CHECK(artifact_type IN (
                            'PROPOSAL', 'SPECIFICATION', 'COMPARISON',
                            'DECISION_CANDIDATE'
                        )),
                    title TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK(state IN ('DRAFT', 'REVIEW', 'CANDIDATE', 'ARCHIVED')),
                    current_revision INTEGER NOT NULL,
                    created_by_role TEXT NOT NULL,
                    created_by_binding_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS chat_room_artifact_room_time
                ON chat_room_artifact(room_id, updated_at, artifact_id);

                CREATE TABLE IF NOT EXISTS chat_room_artifact_revision (
                    artifact_id TEXT NOT NULL
                        REFERENCES chat_room_artifact(artifact_id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    author_role TEXT NOT NULL,
                    author_binding_id TEXT,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    source_message_id TEXT,
                    content_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, revision)
                );

                CREATE TABLE IF NOT EXISTS chat_room_finding (
                    finding_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    finding_type TEXT NOT NULL
                        CHECK(finding_type IN (
                            'RAG_FINDING', 'CROSS_FEATURE_DEPENDENCY',
                            'ESCALATION_REQUEST'
                        )),
                    summary TEXT NOT NULL,
                    detail_text TEXT NOT NULL,
                    reporter_role TEXT NOT NULL,
                    reporter_binding_id TEXT,
                    evidence_refs_json TEXT NOT NULL,
                    feature_refs_json TEXT NOT NULL,
                    requested_owner_role TEXT,
                    source_message_id TEXT,
                    content_digest TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK(state IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS chat_room_finding_room_time
                ON chat_room_finding(room_id, created_at, finding_id);

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

                CREATE TABLE IF NOT EXISTS chat_room_event (
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    room_sequence INTEGER NOT NULL,
                    room_event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    origin_binding_id TEXT,
                    provider_event_id TEXT,
                    correlation_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(room_id, room_sequence),
                    UNIQUE(room_id, room_event_id),
                    UNIQUE(room_id, provider_event_id)
                );

                CREATE INDEX IF NOT EXISTS chat_room_event_room_time
                ON chat_room_event(room_id, room_sequence);

                CREATE TABLE IF NOT EXISTS chat_room_participant_cursor (
                    binding_id TEXT PRIMARY KEY
                        REFERENCES chat_room_session(binding_id) ON DELETE CASCADE,
                    participant_state TEXT NOT NULL DEFAULT 'OBSERVED',
                    delivery_sequence INTEGER NOT NULL DEFAULT 0,
                    provider_observation_cursor TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_room_delivery (
                    binding_id TEXT NOT NULL
                        REFERENCES chat_room_session(binding_id) ON DELETE CASCADE,
                    room_event_id TEXT NOT NULL,
                    room_sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    provider_result_json TEXT NOT NULL DEFAULT '{}',
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(binding_id, room_event_id)
                );

                CREATE TABLE IF NOT EXISTS chat_room_viewer_cursor (
                    room_id TEXT NOT NULL
                        REFERENCES chat_room(room_id) ON DELETE CASCADE,
                    viewer_id TEXT NOT NULL,
                    room_sequence INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(room_id, viewer_id)
                );
                """
            )
            self._backfill_room_events(connection)
            now = utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_room_participant_cursor(
                    binding_id, participant_state, delivery_sequence,
                    provider_observation_cursor, updated_at
                )
                SELECT
                    binding.binding_id,
                    'OBSERVED',
                    COALESCE((
                        SELECT MAX(event.room_sequence)
                        FROM chat_room_event event
                        WHERE event.room_id = binding.room_id
                    ), 0),
                    NULL,
                    ?
                FROM chat_room_session binding
                """,
                (now,),
            )
            connection.commit()

    def _backfill_room_events(self, connection: sqlite3.Connection) -> None:
        """Project legacy room messages into the ordered event plane once."""
        rooms = connection.execute(
            """
            SELECT DISTINCT message.room_id
            FROM chat_room_message message
            LEFT JOIN chat_room_event event
              ON event.room_id = message.room_id
             AND json_extract(event.payload_json, '$.message.message_id') = message.message_id
            WHERE event.room_event_id IS NULL
            """
        ).fetchall()
        for room in rooms:
            room_id = str(room["room_id"])
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(room_sequence), 0)
                    FROM chat_room_event WHERE room_id = ?
                    """,
                    (room_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT message_id, author_binding_id, message_json, created_at
                FROM chat_room_message
                WHERE room_id = ?
                  AND message_id NOT IN (
                    SELECT json_extract(payload_json, '$.message.message_id')
                    FROM chat_room_event
                    WHERE room_id = ?
                  )
                ORDER BY created_at ASC, message_id ASC
                """,
                (room_id, room_id),
            ).fetchall()
            for row in rows:
                sequence += 1
                message = json.loads(row["message_json"])
                event_id = "room_evt_" + hashlib.sha256(
                    f"{room_id}\0{row['message_id']}".encode("utf-8")
                ).hexdigest()[:24]
                provider_event_id = message.get("provider_event_id")
                correlation_id = message.get("correlation_id")
                message.update(
                    {
                        "room_event_id": event_id,
                        "room_sequence": sequence,
                    }
                )
                if provider_event_id is not None:
                    duplicate_provider_event = connection.execute(
                        """
                        SELECT 1 FROM chat_room_event
                        WHERE room_id = ? AND provider_event_id = ?
                        """,
                        (room_id, provider_event_id),
                    ).fetchone()
                    if duplicate_provider_event is not None:
                        provider_event_id = None
                event_payload = {
                    "schema": ROOM_DURABLE_EVENT_SCHEMA,
                    "room_id": room_id,
                    "room_sequence": sequence,
                    "room_event_id": event_id,
                    "event_type": "MESSAGE",
                    "origin_binding_id": row["author_binding_id"],
                    "provider_event_id": provider_event_id,
                    "correlation_id": correlation_id,
                    "message": message,
                    "created_at": row["created_at"],
                }
                connection.execute(
                    """
                    INSERT INTO chat_room_event(
                        room_id, room_sequence, room_event_id, event_type,
                        origin_binding_id, provider_event_id, correlation_id,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, 'MESSAGE', ?, ?, ?, ?, ?)
                    """,
                    (
                        room_id,
                        sequence,
                        event_id,
                        row["author_binding_id"],
                        provider_event_id,
                        correlation_id,
                        json.dumps(
                            event_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        row["created_at"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE chat_room_message SET message_json = ?
                    WHERE message_id = ?
                    """,
                    (
                        json.dumps(message, ensure_ascii=False, separators=(",", ":")),
                        row["message_id"],
                    ),
                )

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
        state: str | None = "OPEN",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state if state in ROOM_STATES else "OPEN")
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if room_type:
            clauses.append("room_type = ?")
            params.append(room_type.upper())
        where_sql = (
            " WHERE " + " AND ".join(f"r.{clause}" for clause in clauses)
            if clauses
            else ""
        )
        sql = (
            "SELECT r.*, COUNT(b.binding_id) AS participant_count"
            " FROM chat_room r"
            " LEFT JOIN chat_room_session b ON b.room_id = r.room_id AND b.state = 'ACTIVE'"
            + where_sql
            + " GROUP BY r.room_id"
            + " ORDER BY r.created_at DESC, r.room_id DESC"
        )
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            result = []
            for row in rows:
                item = self._room_row(row)
                item["participant_count"] = row["participant_count"]
                result.append(item)
            return result

    def close_room(self, room_id: str) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE chat_room SET state = 'CLOSED', updated_at = ? WHERE room_id = ?",
                (now, rid),
            )
            connection.execute(
                "UPDATE chat_room_session SET state = 'DETACHED', updated_at = ? "
                "WHERE room_id = ? AND state = 'ACTIVE'",
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
        session_anchor_ref = _optional_text(
            value.get("session_anchor_ref") or value.get("anchor_ref"),
            "session_anchor_ref",
            limit=256,
        )
        raw_metadata = value.get("metadata")
        binding_metadata: dict[str, Any] = (
            dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        )
        if session_anchor_ref is not None:
            binding_metadata["session_anchor_ref"] = session_anchor_ref
        provider_chat_key = _optional_text(
            value.get("provider_chat_key"), "provider_chat_key", limit=160
        )
        if provider_chat_key is not None:
            binding_metadata["provider_chat_key"] = provider_chat_key
        resume_pending_delivery = value.get("resume_pending_delivery") is True
        participant_state = str(value.get("participant_state") or "OBSERVED").upper()
        if participant_state not in PARTICIPANT_STATES:
            raise MultiRoomError(
                "PARTICIPANT_STATE_INVALID",
                f"unsupported participant_state: {participant_state}",
            )
        now = utc_now()
        binding_id = "bind_" + secrets.token_hex(12)
        with self._connect() as connection:
            initial_delivery_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(room_sequence), 0)
                    FROM chat_room_event WHERE room_id = ?
                    """,
                    (room["room_id"],),
                ).fetchone()[0]
            )
            if slot_role in SINGLETON_SLOT_ROLES:
                if resume_pending_delivery:
                    retryable = connection.execute(
                        """
                        SELECT MIN(delivery.room_sequence)
                        FROM chat_room_delivery delivery
                        JOIN chat_room_session binding
                          ON binding.binding_id = delivery.binding_id
                        WHERE binding.room_id = ?
                          AND binding.slot_role = ?
                          AND delivery.status IN ('UNCERTAIN', 'DISCONNECTED')
                        """,
                        (room["room_id"], slot_role),
                    ).fetchone()[0]
                    if retryable is not None:
                        initial_delivery_sequence = min(
                            initial_delivery_sequence,
                            max(0, int(retryable) - 1),
                        )
                previous_cursor = connection.execute(
                    """
                    SELECT cursor.delivery_sequence
                    FROM chat_room_session binding
                    JOIN chat_room_participant_cursor cursor
                      ON cursor.binding_id = binding.binding_id
                    WHERE binding.room_id = ?
                      AND binding.slot_role = ?
                      AND binding.state = 'ACTIVE'
                    ORDER BY binding.updated_at DESC, binding.binding_id DESC
                    LIMIT 1
                    """,
                    (room["room_id"], slot_role),
                ).fetchone()
                if previous_cursor is not None:
                    # Replacement bindings resume only room work the previous
                    # singleton had not accepted; late joins still skip history.
                    initial_delivery_sequence = min(
                        initial_delivery_sequence,
                        int(previous_cursor[0]),
                    )
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    binding_id,
                    room["room_id"],
                    slot_role,
                    provider,
                    provider_session_ref,
                    supervisor_session_id,
                    display_name,
                    json.dumps(binding_metadata, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO chat_room_participant_cursor(
                    binding_id, participant_state, delivery_sequence,
                    provider_observation_cursor, updated_at
                ) VALUES (?, ?, ?, NULL, ?)
                """,
                (binding_id, participant_state, initial_delivery_sequence, now),
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
                "session_anchor_ref": value.get("session_anchor_ref")
                or value.get("anchor_ref"),
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
        message_kind = _text(
            value.get("kind") or value.get("message_kind") or "MESSAGE",
            "message_kind",
            limit=64,
        ).upper()
        if message_kind not in MESSAGE_KINDS:
            raise MultiRoomError(
                "MESSAGE_KIND_INVALID", f"unsupported message_kind: {message_kind}"
            )
        idem = _optional_text(value.get("idempotency_key"), "idempotency_key", limit=120)
        if not idem:
            idem = "idem_" + secrets.token_hex(16)
        author_binding_id = _optional_text(
            value.get("author_binding_id"), "author_binding_id", limit=80
        )
        provider_event_id = _optional_text(
            value.get("provider_event_id"), "provider_event_id", limit=256
        )
        correlation_id = _optional_text(
            value.get("correlation_id"), "correlation_id", limit=256
        )
        target_binding_ids_raw = value.get("target_binding_ids")
        target_binding_ids: list[str] | None = None
        if target_binding_ids_raw is not None:
            if not isinstance(target_binding_ids_raw, list):
                raise MultiRoomError(
                    "TARGET_BINDINGS_INVALID",
                    "target_binding_ids must be an array",
                )
            target_binding_ids = []
            for item in target_binding_ids_raw:
                binding_id = _text(item, "target_binding_id", limit=80)
                if binding_id not in target_binding_ids:
                    target_binding_ids.append(binding_id)
            if not target_binding_ids:
                raise MultiRoomError(
                    "TARGET_BINDINGS_REQUIRED",
                    "target_binding_ids must contain at least one binding",
                )
        message_id = "msg_" + secrets.token_hex(12)
        room_event_id = "room_evt_" + secrets.token_hex(12)
        now = utc_now()
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
            if author_binding_id is not None:
                binding = connection.execute(
                    """
                    SELECT binding_id FROM chat_room_session
                    WHERE binding_id = ? AND room_id = ?
                    """,
                    (author_binding_id, room["room_id"]),
                ).fetchone()
                if binding is None:
                    raise MultiRoomError(
                        "AUTHOR_BINDING_INVALID",
                        "author binding does not belong to the room",
                        409,
                    )
            for target_binding_id in target_binding_ids or []:
                target_binding = connection.execute(
                    """
                    SELECT binding_id FROM chat_room_session
                    WHERE binding_id = ? AND room_id = ? AND state = 'ACTIVE'
                    """,
                    (target_binding_id, room["room_id"]),
                ).fetchone()
                if target_binding is None:
                    raise MultiRoomError(
                        "TARGET_BINDING_INVALID",
                        "target binding is not an active participant in the room",
                        409,
                    )
            if provider_event_id is not None:
                observed = connection.execute(
                    """
                    SELECT payload_json FROM chat_room_event
                    WHERE room_id = ? AND provider_event_id = ?
                    """,
                    (room["room_id"], provider_event_id),
                ).fetchone()
                if observed is not None:
                    event_payload = json.loads(observed["payload_json"])
                    prior_message = event_payload.get("message")
                    if isinstance(prior_message, dict):
                        return prior_message
            room_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(room_sequence), 0) + 1
                    FROM chat_room_event WHERE room_id = ?
                    """,
                    (room["room_id"],),
                ).fetchone()[0]
            )
            payload = {
                "schema": ROOM_MESSAGE_SCHEMA,
                "message_id": message_id,
                "room_id": room["room_id"],
                "room_event_id": room_event_id,
                "room_sequence": room_sequence,
                "kind": message_kind,
                "author_role": author_role,
                "author_binding_id": author_binding_id,
                "provider_event_id": provider_event_id,
                "correlation_id": correlation_id,
                "target_binding_ids": target_binding_ids,
                "body_text": body_text,
                "created_at": now,
            }
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
            event_payload = {
                "schema": ROOM_DURABLE_EVENT_SCHEMA,
                "room_id": room["room_id"],
                "room_sequence": room_sequence,
                "room_event_id": room_event_id,
                "event_type": "MESSAGE",
                "origin_binding_id": author_binding_id,
                "provider_event_id": provider_event_id,
                "correlation_id": correlation_id,
                "message": payload,
                "created_at": now,
            }
            connection.execute(
                """
                INSERT INTO chat_room_event(
                    room_id, room_sequence, room_event_id, event_type,
                    origin_binding_id, provider_event_id, correlation_id,
                    payload_json, created_at
                ) VALUES (?, ?, ?, 'MESSAGE', ?, ?, ?, ?, ?)
                """,
                (
                    room["room_id"],
                    room_sequence,
                    room_event_id,
                    author_binding_id,
                    provider_event_id,
                    correlation_id,
                    json.dumps(
                        event_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
            connection.commit()
        message = self.get_message(message_id)
        self.hub.publish(
            room["room_id"],
            {
                "type": "ROOM_MESSAGE",
                "message": message,
                "room_event": event_payload,
            },
        )
        return message

    def _artifact_write_context(
        self,
        room: Mapping[str, Any],
        value: Mapping[str, Any],
        connection: sqlite3.Connection,
    ) -> tuple[str, str | None]:
        if room["state"] != "OPEN":
            raise MultiRoomError("ROOM_CLOSED", "cannot write artifacts in a closed room", 409)
        if room["room_type"] != "MEETING":
            raise MultiRoomError(
                "ROOM_ARTIFACT_UNSUPPORTED",
                "Room-native specification artifacts are currently limited to MEETING rooms",
                409,
            )
        author_role = _text(
            value.get("author_role") or value.get("role") or "USER",
            "author_role",
        ).upper()
        if author_role not in WRITE_ROLES["MEETING"]:
            raise MultiRoomError(
                "ROOM_WRITE_FORBIDDEN",
                f"role {author_role} cannot write artifacts in MEETING rooms",
                403,
            )
        author_binding_id = _optional_text(
            value.get("author_binding_id"), "author_binding_id", limit=80
        )
        if author_binding_id is not None:
            binding = connection.execute(
                "SELECT binding_id FROM chat_room_session WHERE binding_id = ? AND room_id = ?",
                (author_binding_id, room["room_id"]),
            ).fetchone()
            if binding is None:
                raise MultiRoomError(
                    "AUTHOR_BINDING_INVALID",
                    "author binding does not belong to the room",
                    409,
                )
        return author_role, author_binding_id

    def _artifact_evidence_refs(self, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise MultiRoomError(
                "ROOM_ARTIFACT_EVIDENCE_INVALID",
                "evidence_refs must be an array",
            )
        if len(value) > 50:
            raise MultiRoomError(
                "ROOM_ARTIFACT_EVIDENCE_INVALID",
                "evidence_refs exceeds 50 entries",
            )
        refs: list[str] = []
        for item in value:
            ref = _text(item, "evidence_ref", limit=512)
            if ref not in refs:
                refs.append(ref)
        return refs

    def _artifact_source_message(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        value: Any,
    ) -> str | None:
        source_message_id = _optional_text(value, "source_message_id", limit=80)
        if source_message_id is None:
            return None
        message = connection.execute(
            "SELECT message_id FROM chat_room_message WHERE message_id = ? AND room_id = ?",
            (source_message_id, room_id),
        ).fetchone()
        if message is None:
            raise MultiRoomError(
                "ROOM_ARTIFACT_SOURCE_INVALID",
                "source message does not belong to the room",
                409,
            )
        return source_message_id

    def create_artifact(
        self, room_id: str, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        room = self.get_room(room_id)
        artifact_type = _text(value.get("artifact_type"), "artifact_type", limit=64).upper()
        if artifact_type not in ROOM_ARTIFACT_TYPES:
            raise MultiRoomError(
                "ROOM_ARTIFACT_TYPE_INVALID",
                f"unsupported artifact_type: {artifact_type}",
            )
        title = _text(value.get("title"), "title", limit=300)
        body_text = _text(value.get("body_text") or value.get("body"), "body_text", limit=100000)
        state = _text(value.get("state") or "DRAFT", "state", limit=32).upper()
        if state not in ROOM_ARTIFACT_STATES:
            raise MultiRoomError(
                "ROOM_ARTIFACT_STATE_INVALID", f"unsupported artifact state: {state}"
            )
        evidence_refs = self._artifact_evidence_refs(value.get("evidence_refs"))
        artifact_id = "room_art_" + secrets.token_hex(12)
        now = utc_now()
        with self._connect() as connection:
            author_role, author_binding_id = self._artifact_write_context(
                room, value, connection
            )
            source_message_id = self._artifact_source_message(
                connection, room["room_id"], value.get("source_message_id")
            )
            content_digest = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO chat_room_artifact(
                    artifact_id, room_id, artifact_type, title, state,
                    current_revision, created_by_role, created_by_binding_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    artifact_id, room["room_id"], artifact_type, title, state,
                    author_role, author_binding_id, now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO chat_room_artifact_revision(
                    artifact_id, revision, title, state, body_text, author_role,
                    author_binding_id, evidence_refs_json, source_message_id,
                    content_digest, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id, title, state, body_text, author_role,
                    author_binding_id,
                    json.dumps(evidence_refs, ensure_ascii=False, separators=(",", ":")),
                    source_message_id, content_digest, now,
                ),
            )
            connection.commit()
        artifact = self.get_artifact(room["room_id"], artifact_id)
        self.hub.publish(
            room["room_id"], {"type": "ROOM_ARTIFACT_CREATED", "artifact": artifact}
        )
        return artifact

    def revise_artifact(
        self,
        room_id: str,
        artifact_id: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        room = self.get_room(room_id)
        aid = _text(artifact_id, "artifact_id", limit=80)
        if isinstance(value.get("expected_revision"), bool):
            raise MultiRoomError(
                "ROOM_ARTIFACT_REVISION_REQUIRED", "expected_revision must be an integer"
            )
        try:
            expected_revision = int(value.get("expected_revision"))
        except (TypeError, ValueError):
            raise MultiRoomError(
                "ROOM_ARTIFACT_REVISION_REQUIRED", "expected_revision must be an integer"
            ) from None
        body_text = _text(value.get("body_text") or value.get("body"), "body_text", limit=100000)
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT artifact.*, revision.evidence_refs_json
                FROM chat_room_artifact artifact
                JOIN chat_room_artifact_revision revision
                  ON revision.artifact_id = artifact.artifact_id
                 AND revision.revision = artifact.current_revision
                WHERE artifact.artifact_id = ? AND artifact.room_id = ?
                """,
                (aid, room["room_id"]),
            ).fetchone()
            if current is None:
                raise MultiRoomError("ROOM_ARTIFACT_NOT_FOUND", "artifact not found", 404)
            if int(current["current_revision"]) != expected_revision:
                raise MultiRoomError(
                    "ROOM_ARTIFACT_REVISION_CONFLICT",
                    "expected_revision does not match the current artifact revision",
                    409,
                )
            author_role, author_binding_id = self._artifact_write_context(
                room, value, connection
            )
            title = _text(value.get("title") or current["title"], "title", limit=300)
            state = _text(value.get("state") or current["state"], "state", limit=32).upper()
            if state not in ROOM_ARTIFACT_STATES:
                raise MultiRoomError(
                    "ROOM_ARTIFACT_STATE_INVALID",
                    f"unsupported artifact state: {state}",
                )
            if "evidence_refs" in value:
                evidence_refs = self._artifact_evidence_refs(value.get("evidence_refs"))
            else:
                evidence_refs = json.loads(current["evidence_refs_json"] or "[]")
            source_message_id = self._artifact_source_message(
                connection, room["room_id"], value.get("source_message_id")
            )
            revision = expected_revision + 1
            now = utc_now()
            content_digest = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
            updated = connection.execute(
                """
                UPDATE chat_room_artifact
                SET title = ?, state = ?, current_revision = ?, updated_at = ?
                WHERE artifact_id = ? AND room_id = ? AND current_revision = ?
                """,
                (title, state, revision, now, aid, room["room_id"], expected_revision),
            )
            if updated.rowcount != 1:
                raise MultiRoomError(
                    "ROOM_ARTIFACT_REVISION_CONFLICT",
                    "artifact revision changed while the update was being applied",
                    409,
                )
            connection.execute(
                """
                INSERT INTO chat_room_artifact_revision(
                    artifact_id, revision, title, state, body_text, author_role,
                    author_binding_id, evidence_refs_json, source_message_id,
                    content_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid, revision, title, state, body_text, author_role,
                    author_binding_id,
                    json.dumps(evidence_refs, ensure_ascii=False, separators=(",", ":")),
                    source_message_id, content_digest, now,
                ),
            )
            connection.commit()
        artifact = self.get_artifact(room["room_id"], aid)
        self.hub.publish(
            room["room_id"], {"type": "ROOM_ARTIFACT_REVISED", "artifact": artifact}
        )
        return artifact

    def get_artifact(self, room_id: str, artifact_id: str) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        aid = _text(artifact_id, "artifact_id", limit=80)
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT artifact.*, revision.body_text, revision.author_role,
                       revision.author_binding_id AS revision_author_binding_id,
                       revision.evidence_refs_json, revision.source_message_id,
                       revision.content_digest,
                       revision.created_at AS revision_created_at
                FROM chat_room_artifact artifact
                JOIN chat_room_artifact_revision revision
                  ON revision.artifact_id = artifact.artifact_id
                 AND revision.revision = artifact.current_revision
                WHERE artifact.artifact_id = ? AND artifact.room_id = ?
                """,
                (aid, rid),
            ).fetchone()
            if current is None:
                raise MultiRoomError("ROOM_ARTIFACT_NOT_FOUND", "artifact not found", 404)
            rows = connection.execute(
                """
                SELECT * FROM chat_room_artifact_revision
                WHERE artifact_id = ? ORDER BY revision ASC
                """,
                (aid,),
            ).fetchall()
        artifact = self._artifact_row(current)
        artifact["revisions"] = [self._artifact_revision_row(row) for row in rows]
        return artifact

    def list_artifacts(
        self, room_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        rid = _text(room_id, "room_id", limit=80)
        cap = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact.*, revision.body_text, revision.author_role,
                       revision.author_binding_id AS revision_author_binding_id,
                       revision.evidence_refs_json, revision.source_message_id,
                       revision.content_digest,
                       revision.created_at AS revision_created_at
                FROM chat_room_artifact artifact
                JOIN chat_room_artifact_revision revision
                  ON revision.artifact_id = artifact.artifact_id
                 AND revision.revision = artifact.current_revision
                WHERE artifact.room_id = ?
                ORDER BY artifact.updated_at ASC, artifact.artifact_id ASC
                LIMIT ?
                """,
                (rid, cap),
            ).fetchall()
        return [self._artifact_row(row) for row in rows]

    def _finding_refs(
        self,
        value: Any,
        *,
        field: str,
        error_code: str,
        limit: int,
    ) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise MultiRoomError(error_code, f"{field} must be an array")
        if len(value) > limit:
            raise MultiRoomError(error_code, f"{field} exceeds {limit} entries")
        refs: list[str] = []
        for item in value:
            ref = _text(item, field[:-1] if field.endswith("s") else field, limit=512)
            if ref not in refs:
                refs.append(ref)
        return refs

    def record_finding(
        self, room_id: str, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        room = self.get_room(room_id)
        finding_type = _text(
            value.get("finding_type") or value.get("kind"),
            "finding_type",
            limit=64,
        ).upper()
        if finding_type not in ROOM_FINDING_TYPES:
            raise MultiRoomError(
                "ROOM_FINDING_TYPE_INVALID",
                f"unsupported finding_type: {finding_type}",
            )
        summary = _text(value.get("summary"), "summary", limit=500)
        detail_text = _optional_text(
            value.get("detail_text") or value.get("detail"),
            "detail_text",
            limit=20000,
        ) or ""
        evidence_refs = self._finding_refs(
            value.get("evidence_refs"),
            field="evidence_refs",
            error_code="ROOM_FINDING_EVIDENCE_INVALID",
            limit=50,
        )
        if not evidence_refs:
            raise MultiRoomError(
                "ROOM_FINDING_EVIDENCE_REQUIRED",
                "structured room findings require at least one evidence reference",
            )
        feature_refs = self._finding_refs(
            value.get("feature_refs"),
            field="feature_refs",
            error_code="ROOM_FINDING_FEATURE_REFS_INVALID",
            limit=20,
        )
        if finding_type == "CROSS_FEATURE_DEPENDENCY" and len(feature_refs) < 2:
            raise MultiRoomError(
                "ROOM_FINDING_FEATURE_REFS_REQUIRED",
                "cross-feature dependencies require at least two feature_refs",
            )
        requested_owner_role = _optional_text(
            value.get("requested_owner_role"), "requested_owner_role", limit=64
        )
        if requested_owner_role is not None:
            requested_owner_role = requested_owner_role.upper()
            if requested_owner_role not in MEMBER_ROLES:
                raise MultiRoomError(
                    "ROOM_FINDING_OWNER_INVALID",
                    f"unsupported requested_owner_role: {requested_owner_role}",
                )
        if finding_type == "ESCALATION_REQUEST" and requested_owner_role is None:
            raise MultiRoomError(
                "ROOM_FINDING_OWNER_REQUIRED",
                "escalation requests require requested_owner_role",
            )
        state = _text(value.get("state") or "OPEN", "state", limit=32).upper()
        if state not in ROOM_FINDING_STATES:
            raise MultiRoomError(
                "ROOM_FINDING_STATE_INVALID", f"unsupported finding state: {state}"
            )
        finding_id = "room_find_" + secrets.token_hex(12)
        now = utc_now()
        with self._connect() as connection:
            reporter_role, reporter_binding_id = self._artifact_write_context(
                room, value, connection
            )
            source_message_id = self._artifact_source_message(
                connection, room["room_id"], value.get("source_message_id")
            )
            content_digest = hashlib.sha256(detail_text.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO chat_room_finding(
                    finding_id, room_id, finding_type, summary, detail_text,
                    reporter_role, reporter_binding_id, evidence_refs_json,
                    feature_refs_json, requested_owner_role, source_message_id,
                    content_digest, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id, room["room_id"], finding_type, summary, detail_text,
                    reporter_role, reporter_binding_id,
                    json.dumps(evidence_refs, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(feature_refs, ensure_ascii=False, separators=(",", ":")),
                    requested_owner_role, source_message_id, content_digest,
                    state, now, now,
                ),
            )
            connection.commit()
        finding = self.get_finding(room["room_id"], finding_id)
        self.hub.publish(
            room["room_id"], {"type": "ROOM_FINDING_RECORDED", "finding": finding}
        )
        return finding

    def get_finding(self, room_id: str, finding_id: str) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        fid = _text(finding_id, "finding_id", limit=80)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_room_finding WHERE room_id = ? AND finding_id = ?",
                (rid, fid),
            ).fetchone()
        if row is None:
            raise MultiRoomError("ROOM_FINDING_NOT_FOUND", "finding not found", 404)
        return self._finding_row(row)

    def list_findings(
        self, room_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        rid = _text(room_id, "room_id", limit=80)
        cap = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM chat_room_finding
                WHERE room_id = ?
                ORDER BY created_at ASC, finding_id ASC
                LIMIT ?
                """,
                (rid, cap),
            ).fetchall()
        return [self._finding_row(row) for row in rows]

    def update_finding_state(
        self, room_id: str, finding_id: str, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        room = self.get_room(room_id)
        fid = _text(finding_id, "finding_id", limit=80)
        requested_state = _text(value.get("state"), "state", limit=32).upper()
        if requested_state not in ROOM_FINDING_STATES:
            raise MultiRoomError(
                "ROOM_FINDING_STATE_INVALID",
                f"unsupported finding state: {requested_state}",
            )
        allowed = {
            "OPEN": {"OPEN", "ACKNOWLEDGED", "RESOLVED"},
            "ACKNOWLEDGED": {"ACKNOWLEDGED", "RESOLVED"},
            "RESOLVED": {"RESOLVED"},
        }
        with self._connect() as connection:
            self._artifact_write_context(room, value, connection)
            row = connection.execute(
                "SELECT state FROM chat_room_finding WHERE room_id = ? AND finding_id = ?",
                (room["room_id"], fid),
            ).fetchone()
            if row is None:
                raise MultiRoomError("ROOM_FINDING_NOT_FOUND", "finding not found", 404)
            current_state = str(row["state"])
            if requested_state not in allowed[current_state]:
                raise MultiRoomError(
                    "ROOM_FINDING_STATE_TRANSITION_INVALID",
                    f"cannot transition finding from {current_state} to {requested_state}",
                    409,
                )
            if requested_state != current_state:
                connection.execute(
                    "UPDATE chat_room_finding SET state = ?, updated_at = ? WHERE room_id = ? AND finding_id = ?",
                    (requested_state, utc_now(), room["room_id"], fid),
                )
                connection.commit()
        finding = self.get_finding(room["room_id"], fid)
        self.hub.publish(
            room["room_id"], {"type": "ROOM_FINDING_STATE_CHANGED", "finding": finding}
        )
        return finding

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
                SELECT payload_json FROM chat_room_event
                WHERE room_id = ? AND event_type = 'MESSAGE'
                ORDER BY room_sequence ASC
                LIMIT ?
                """,
                (rid, lim),
            ).fetchall()
            messages: list[dict[str, Any]] = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                message = payload.get("message")
                if isinstance(message, dict):
                    messages.append(message)
            return messages

    def list_room_events(
        self,
        room_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rid = _text(room_id, "room_id", limit=80)
        after = max(0, int(after_sequence))
        cap = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM chat_room_event
                WHERE room_id = ? AND room_sequence > ?
                ORDER BY room_sequence ASC
                LIMIT ?
                """,
                (rid, after, cap),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def participant_cursor(self, binding_id: str) -> dict[str, Any]:
        bid = _text(binding_id, "binding_id", limit=80)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, b.room_id
                FROM chat_room_participant_cursor c
                JOIN chat_room_session b ON b.binding_id = c.binding_id
                WHERE c.binding_id = ?
                """,
                (bid,),
            ).fetchone()
        if row is None:
            raise MultiRoomError("BINDING_NOT_FOUND", "session binding not found", 404)
        return {
            "schema": ROOM_CURSOR_SCHEMA,
            "binding_id": row["binding_id"],
            "room_id": row["room_id"],
            "participant_state": row["participant_state"],
            "delivery_sequence": int(row["delivery_sequence"]),
            "provider_observation_cursor": row["provider_observation_cursor"],
            "updated_at": row["updated_at"],
        }

    def set_participant_state(self, binding_id: str, state: str) -> dict[str, Any]:
        bid = _text(binding_id, "binding_id", limit=80)
        normalized = _text(state, "participant_state").upper()
        if normalized not in PARTICIPANT_STATES:
            raise MultiRoomError(
                "PARTICIPANT_STATE_INVALID",
                f"unsupported participant_state: {normalized}",
            )
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE chat_room_participant_cursor
                SET participant_state = ?, updated_at = ?
                WHERE binding_id = ?
                """,
                (normalized, now, bid),
            )
            if cursor.rowcount != 1:
                raise MultiRoomError(
                    "BINDING_NOT_FOUND", "session binding not found", 404
                )
        return self.participant_cursor(bid)

    def participant_delivery_batch(
        self,
        binding_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        cursor = self.participant_cursor(binding_id)
        cap = max(1, min(500, int(limit)))
        events = self.list_room_events(
            cursor["room_id"],
            after_sequence=cursor["delivery_sequence"],
            limit=cap,
        )
        deliverable: list[dict[str, Any]] = []
        blocker: dict[str, Any] | None = None
        with self._connect() as connection:
            for event in events:
                if event.get("origin_binding_id") == cursor["binding_id"]:
                    continue
                message = event.get("message")
                target_binding_ids = (
                    message.get("target_binding_ids")
                    if isinstance(message, Mapping)
                    else None
                )
                if (
                    isinstance(target_binding_ids, list)
                    and target_binding_ids
                    and cursor["binding_id"] not in target_binding_ids
                ):
                    continue
                prior = connection.execute(
                    """
                    SELECT status, provider_result_json, observed_at
                    FROM chat_room_delivery
                    WHERE binding_id = ? AND room_event_id = ?
                    """,
                    (cursor["binding_id"], event["room_event_id"]),
                ).fetchone()
                if prior is None:
                    deliverable.append(event)
                    continue
                status = str(prior["status"])
                if status in ACCEPTED_DELIVERY_STATES:
                    continue
                blocker = {
                    "room_event_id": event["room_event_id"],
                    "room_sequence": event["room_sequence"],
                    "status": status,
                    "provider_result": json.loads(
                        prior["provider_result_json"] or "{}"
                    ),
                    "observed_at": prior["observed_at"],
                }
                break
        return {
            "schema": ROOM_CURSOR_SCHEMA,
            "status": "PARTICIPANT_DELIVERY_READY" if not blocker else "PARTICIPANT_DELIVERY_BLOCKED",
            "cursor": cursor,
            "events": deliverable,
            "blocker": blocker,
        }

    def record_delivery_observation(
        self,
        binding_id: str,
        room_event_id: str,
        *,
        status: str,
        provider_result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        bid = _text(binding_id, "binding_id", limit=80)
        event_id = _text(room_event_id, "room_event_id", limit=80)
        normalized = _text(status, "delivery_status").upper()
        if normalized not in DELIVERY_STATES:
            raise MultiRoomError(
                "DELIVERY_STATE_INVALID",
                f"unsupported delivery status: {normalized}",
            )
        if provider_result is not None and not isinstance(provider_result, Mapping):
            raise MultiRoomError(
                "PROVIDER_RESULT_INVALID",
                "provider_result must be an object",
            )
        now = utc_now()
        with self._connect() as connection:
            binding = connection.execute(
                "SELECT room_id FROM chat_room_session WHERE binding_id = ?",
                (bid,),
            ).fetchone()
            if binding is None:
                raise MultiRoomError(
                    "BINDING_NOT_FOUND", "session binding not found", 404
                )
            event = connection.execute(
                """
                SELECT room_sequence FROM chat_room_event
                WHERE room_id = ? AND room_event_id = ?
                """,
                (binding["room_id"], event_id),
            ).fetchone()
            if event is None:
                raise MultiRoomError(
                    "ROOM_EVENT_NOT_FOUND", "room event does not exist", 404
                )
            room_sequence = int(event["room_sequence"])
            current = connection.execute(
                """
                SELECT delivery_sequence FROM chat_room_participant_cursor
                WHERE binding_id = ?
                """,
                (bid,),
            ).fetchone()
            if current is None:
                raise MultiRoomError(
                    "BINDING_NOT_FOUND", "session binding not found", 404
                )
            prior = connection.execute(
                """
                SELECT status FROM chat_room_delivery
                WHERE binding_id = ? AND room_event_id = ?
                """,
                (bid, event_id),
            ).fetchone()
            if (
                prior is not None
                and str(prior["status"]) in ACCEPTED_DELIVERY_STATES
                and normalized == "QUEUED"
            ):
                normalized = str(prior["status"])
            connection.execute(
                """
                INSERT INTO chat_room_delivery(
                    binding_id, room_event_id, room_sequence, status,
                    provider_result_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(binding_id, room_event_id) DO UPDATE SET
                    status = excluded.status,
                    provider_result_json = excluded.provider_result_json,
                    observed_at = excluded.observed_at
                """,
                (
                    bid,
                    event_id,
                    room_sequence,
                    normalized,
                    json.dumps(
                        dict(provider_result or {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
            if normalized in ACCEPTED_DELIVERY_STATES:
                connection.execute(
                    """
                    UPDATE chat_room_participant_cursor
                    SET delivery_sequence = ?, updated_at = ?
                    WHERE binding_id = ? AND delivery_sequence <= ?
                    """,
                    (room_sequence, now, bid, room_sequence),
                )
        return {
            "schema": ROOM_CURSOR_SCHEMA,
            "status": "DELIVERY_OBSERVATION_RECORDED",
            "delivery_status": normalized,
            "room_event_id": event_id,
            "cursor": self.participant_cursor(bid),
        }

    def record_provider_observation(
        self,
        binding_id: str,
        provider_cursor: str,
        *,
        expected_previous: str | None = None,
    ) -> dict[str, Any]:
        bid = _text(binding_id, "binding_id", limit=80)
        observed = _text(provider_cursor, "provider_observation_cursor", limit=512)
        now = utc_now()
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT provider_observation_cursor
                FROM chat_room_participant_cursor WHERE binding_id = ?
                """,
                (bid,),
            ).fetchone()
            if current is None:
                raise MultiRoomError(
                    "BINDING_NOT_FOUND", "session binding not found", 404
                )
            previous = current["provider_observation_cursor"]
            if expected_previous is not None and previous != expected_previous:
                raise MultiRoomError(
                    "PROVIDER_OBSERVATION_CURSOR_CHANGED",
                    "provider observation cursor changed before update",
                    409,
                )
            connection.execute(
                """
                UPDATE chat_room_participant_cursor
                SET provider_observation_cursor = ?, updated_at = ?
                WHERE binding_id = ?
                """,
                (observed, now, bid),
            )
        return self.participant_cursor(bid)

    def update_viewer_cursor(
        self,
        room_id: str,
        viewer_id: str,
        room_sequence: int,
    ) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        viewer = _text(viewer_id, "viewer_id", limit=128)
        sequence = max(0, int(room_sequence))
        now = utc_now()
        self.get_room(rid)
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT room_sequence FROM chat_room_viewer_cursor
                WHERE room_id = ? AND viewer_id = ?
                """,
                (rid, viewer),
            ).fetchone()
            if current is not None and sequence < int(current["room_sequence"]):
                raise MultiRoomError(
                    "VIEWER_CURSOR_REGRESSION",
                    "viewer cursor cannot move backwards",
                    409,
                )
            connection.execute(
                """
                INSERT INTO chat_room_viewer_cursor(
                    room_id, viewer_id, room_sequence, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(room_id, viewer_id) DO UPDATE SET
                    room_sequence = excluded.room_sequence,
                    updated_at = excluded.updated_at
                """,
                (rid, viewer, sequence, now),
            )
        return {
            "schema": ROOM_CURSOR_SCHEMA,
            "status": "VIEWER_CURSOR_UPDATED",
            "room_id": rid,
            "viewer_id": viewer,
            "room_sequence": sequence,
            "updated_at": now,
        }

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
        idempotency_key = _optional_text(
            value.get("idempotency_key"), "idempotency_key", limit=200
        )
        models = value.get("models") or value.get("model_slots") or []
        if not isinstance(models, list):
            raise MultiRoomError("MEETING_MODELS_INVALID", "models must be a list")
        room_id = None
        if idempotency_key is not None:
            room_id = "room_" + hashlib.sha256(
                f"MEETING\0{project_id or ''}\0{idempotency_key}".encode("utf-8")
            ).hexdigest()[:24]
            try:
                existing = self.get_room(room_id)
            except MultiRoomError as error:
                if error.code != "ROOM_NOT_FOUND":
                    raise
            else:
                if existing.get("state") != "OPEN":
                    raise MultiRoomError(
                        "MEETING_IDEMPOTENCY_CLOSED",
                        "idempotent Meeting Room is closed",
                        409,
                    )
                expected = (
                    "MEETING",
                    project_id,
                    title,
                    topic,
                    idempotency_key,
                )
                actual = (
                    existing.get("room_type"),
                    existing.get("project_id"),
                    existing.get("title"),
                    (existing.get("metadata") or {}).get("topic"),
                    (existing.get("metadata") or {}).get("idempotency_key"),
                )
                if actual != expected:
                    raise MultiRoomError(
                        "MEETING_IDEMPOTENCY_CONFLICT",
                        "idempotency_key already refers to different Meeting material",
                        409,
                    )
                bindings = self.list_bindings(room_id)
                if not any(
                    item.get("slot_role") == "CONDUCTOR"
                    and item.get("state") == "ACTIVE"
                    for item in bindings
                ):
                    self.attach_session(
                        room_id,
                        {
                            "slot_role": "CONDUCTOR",
                            "display_name": value.get("conductor_name") or "Conductor",
                            "provider": value.get("conductor_provider"),
                            "provider_session_ref": value.get("conductor_session_ref"),
                        },
                    )
                    bindings = self.list_bindings(room_id)
                return {
                    "schema": ROOM_SCHEMA,
                    "status": "MEETING_ROOM_REPLAYED",
                    "room": self.get_room(room_id),
                    "bindings": bindings,
                    "authority": "UNASSIGNED",
                    "execution_assignment": "UNASSIGNED",
                }
        room = self.create_room(
            room_type="MEETING",
            title=title,
            host_role="CONDUCTOR",
            project_id=project_id,
            room_id=room_id,
            metadata={
                "topic": topic,
                "source": "meeting-room.create",
                "created_by": "skill_or_api",
                "idempotency_key": idempotency_key,
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

    def record_control_event(
        self,
        room_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a room control observation and publish its live projection."""

        room = self.get_room(room_id)
        normalized_type = _text(event_type, "event_type", limit=96).upper()
        event_id = "evt_" + secrets.token_hex(12)
        now = utc_now()
        event = {
            "schema": ROOM_EVENT_SCHEMA,
            "event_id": event_id,
            "event_type": normalized_type,
            "room_id": room["room_id"],
            "project_id": room.get("project_id"),
            "task_frame_id": room.get("task_frame_id"),
            "payload": dict(payload or {}),
            "created_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_room_control_event(
                    event_id, room_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    room["room_id"],
                    normalized_type,
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            connection.commit()
        self.hub.publish(
            room["room_id"],
            {"type": normalized_type, "event": event},
        )
        return event

    def list_control_events(
        self,
        room_id: str,
        *,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rid = _text(room_id, "room_id", limit=80)
        cap = max(1, min(500, int(limit)))
        clauses = ["room_id = ?"]
        params: list[Any] = [rid]
        if event_type:
            clauses.append("event_type = ?")
            params.append(_text(event_type, "event_type", limit=96).upper())
        params.append(cap)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM chat_room_control_event
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, event_id ASC LIMIT ?",
                params,
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def room_snapshot(self, room_id: str) -> dict[str, Any]:
        room = self.get_room(room_id)
        bindings = self.list_bindings(room_id)
        messages = self.list_messages(room_id, limit=200)
        artifacts = self.list_artifacts(room_id, limit=200)
        findings = self.list_findings(room_id, limit=200)
        events = self.list_room_events(room_id, limit=200)
        participant_cursors = [
            self.participant_cursor(binding["binding_id"]) for binding in bindings
        ]
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
            "artifacts": artifacts,
            "findings": findings,
            "events": events,
            "participant_cursors": participant_cursors,
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

    def _finding_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            evidence_refs = json.loads(row["evidence_refs_json"] or "[]")
        except json.JSONDecodeError:
            evidence_refs = []
        try:
            feature_refs = json.loads(row["feature_refs_json"] or "[]")
        except json.JSONDecodeError:
            feature_refs = []
        finding_type = str(row["finding_type"])
        return {
            "schema": ROOM_FINDING_SCHEMA,
            "finding_id": row["finding_id"],
            "room_id": row["room_id"],
            "finding_type": finding_type,
            "summary": row["summary"],
            "detail_text": row["detail_text"],
            "reporter_role": row["reporter_role"],
            "reporter_binding_id": row["reporter_binding_id"],
            "evidence_refs": evidence_refs,
            "feature_refs": feature_refs,
            "requested_owner_role": row["requested_owner_role"],
            "source_message_id": row["source_message_id"],
            "content_digest": row["content_digest"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "resolution_state": (
                "RESOLVED"
                if row["state"] == "RESOLVED"
                else "ACKNOWLEDGED"
                if row["state"] == "ACKNOWLEDGED"
                else "OWNER_ACTION_REQUIRED"
                if finding_type == "ESCALATION_REQUEST"
                else "REVIEW_REQUIRED"
            ),
            "authority": "UNASSIGNED",
        }

    def _artifact_revision_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            evidence_refs = json.loads(row["evidence_refs_json"] or "[]")
        except json.JSONDecodeError:
            evidence_refs = []
        return {
            "schema": ROOM_ARTIFACT_REVISION_SCHEMA,
            "artifact_id": row["artifact_id"],
            "revision": int(row["revision"]),
            "title": row["title"],
            "state": row["state"],
            "body_text": row["body_text"],
            "author_role": row["author_role"],
            "author_binding_id": row["author_binding_id"],
            "evidence_refs": evidence_refs,
            "source_message_id": row["source_message_id"],
            "content_digest": row["content_digest"],
            "created_at": row["created_at"],
        }

    def _artifact_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            evidence_refs = json.loads(row["evidence_refs_json"] or "[]")
        except json.JSONDecodeError:
            evidence_refs = []
        return {
            "schema": ROOM_ARTIFACT_SCHEMA,
            "artifact_id": row["artifact_id"],
            "room_id": row["room_id"],
            "artifact_type": row["artifact_type"],
            "title": row["title"],
            "state": row["state"],
            "current_revision": int(row["current_revision"]),
            "body_text": row["body_text"],
            "author_role": row["author_role"],
            "author_binding_id": row["revision_author_binding_id"],
            "evidence_refs": evidence_refs,
            "source_message_id": row["source_message_id"],
            "content_digest": row["content_digest"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision_created_at": row["revision_created_at"],
            "promotion_state": "USER_SELECTION_REQUIRED",
            "authority": "UNASSIGNED",
        }

    def _binding_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return {
            "schema": ROOM_ATTACH_SCHEMA,
            "binding_id": row["binding_id"],
            "room_id": row["room_id"],
            "slot_role": row["slot_role"],
            "provider": row["provider"],
            "provider_session_ref": row["provider_session_ref"],
            "supervisor_session_id": row["supervisor_session_id"],
            "display_name": row["display_name"],
            "session_anchor_ref": metadata.get("session_anchor_ref"),
            "metadata": metadata,
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class MultiRoomDeliveryCoordinator:
    """Fan out unseen room events through already-attached native controls."""

    def __init__(
        self,
        store: MultiRoomStore,
        send_input: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        self.store = store
        self.send_input = send_input

    def deliver_binding(self, binding_id: str, *, limit: int = 100) -> dict[str, Any]:
        binding = self.store.get_binding(binding_id)
        cursor = self.store.participant_cursor(binding_id)
        if cursor["participant_state"] not in {"CONTROLLED", "LIVE"}:
            return {
                "status": "PARTICIPANT_CONTROL_UNAVAILABLE",
                "binding_id": binding_id,
                "participant_state": cursor["participant_state"],
                "delivered": [],
            }
        batch = self.store.participant_delivery_batch(binding_id, limit=limit)
        if batch["blocker"] is not None and not batch["events"]:
            return {
                "status": "PARTICIPANT_DELIVERY_BLOCKED",
                "binding_id": binding_id,
                "blocker": batch["blocker"],
                "delivered": [],
            }
        delivered: list[dict[str, Any]] = []
        for event in batch["events"]:
            try:
                result = dict(self.send_input(binding, event))
                status = str(result.get("status") or "UNCERTAIN").upper()
                if status not in DELIVERY_STATES:
                    status = "UNCERTAIN"
            except Exception as error:
                status = "UNCERTAIN"
                result = {
                    "error": f"{type(error).__name__}: {error}",
                    "retry": "EXPLICIT_ONLY",
                }
            recorded = self.store.record_delivery_observation(
                binding_id,
                str(event["room_event_id"]),
                status=status,
                provider_result=result,
            )
            status = recorded["delivery_status"]
            delivered.append(
                {
                    "room_event_id": event["room_event_id"],
                    "room_sequence": event["room_sequence"],
                    "status": status,
                }
            )
            if status not in ACCEPTED_DELIVERY_STATES:
                return {
                    "status": "PARTICIPANT_DELIVERY_BLOCKED",
                    "binding_id": binding_id,
                    "blocker": delivered[-1],
                    "delivered": delivered,
                    "cursor": recorded["cursor"],
                }
        return {
            "status": "PARTICIPANT_DELIVERY_COMPLETED",
            "binding_id": binding_id,
            "delivered": delivered,
            "cursor": self.store.participant_cursor(binding_id),
        }

    def deliver_room(self, room_id: str, *, limit: int = 100) -> dict[str, Any]:
        results = [
            self.deliver_binding(binding["binding_id"], limit=limit)
            for binding in self.store.list_bindings(room_id)
        ]
        return {
            "status": "ROOM_DELIVERY_OBSERVED",
            "room_id": room_id,
            "participants": results,
        }


class MultiRoomNativeControlRegistry:
    """Process-local native provider controls bound to durable room slots."""

    def __init__(self, store: MultiRoomStore) -> None:
        self.store = store
        self._controls: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        binding_id: str,
        *,
        provider: str,
        provider_session_ref: str,
        send_input: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    ) -> dict[str, Any]:
        bid = _text(binding_id, "binding_id", limit=80)
        normalized_provider = _text(provider, "provider", limit=64).upper()
        normalized_session_ref = _text(
            provider_session_ref,
            "provider_session_ref",
            limit=256,
        )
        if not callable(send_input):
            raise MultiRoomError(
                "NATIVE_CONTROL_SENDER_INVALID",
                "native control sender must be callable",
            )
        binding = self.store.get_binding(bid)
        if binding.get("state") != "ACTIVE":
            raise MultiRoomError(
                "NATIVE_CONTROL_BINDING_INACTIVE",
                "native control requires an active room binding",
                409,
            )
        if str(binding.get("provider") or "").upper() != normalized_provider:
            raise MultiRoomError(
                "NATIVE_CONTROL_PROVIDER_MISMATCH",
                "native control provider does not match room binding",
                409,
            )
        if binding.get("provider_session_ref") != normalized_session_ref:
            raise MultiRoomError(
                "NATIVE_CONTROL_SESSION_MISMATCH",
                "native control session does not match room binding",
                409,
            )
        control_ref = "native_control_" + secrets.token_hex(12)
        with self._lock:
            self._controls[bid] = {
                "control_ref": control_ref,
                "provider": normalized_provider,
                "provider_session_ref": normalized_session_ref,
                "send_input": send_input,
            }
        cursor = self.store.set_participant_state(bid, "CONTROLLED")
        return {
            "status": "NATIVE_CONTROL_REGISTERED",
            "binding_id": bid,
            "control_ref": control_ref,
            "provider": normalized_provider,
            "provider_session_ref": normalized_session_ref,
            "participant_state": cursor["participant_state"],
        }

    def unregister(
        self,
        binding_id: str,
        *,
        control_ref: str | None = None,
    ) -> bool:
        bid = _text(binding_id, "binding_id", limit=80)
        with self._lock:
            current = self._controls.get(bid)
            if current is None:
                return False
            if control_ref is not None and current["control_ref"] != control_ref:
                return False
            self._controls.pop(bid, None)
        try:
            self.store.set_participant_state(bid, "DISCONNECTED")
        except MultiRoomError:
            pass
        return True

    def send_input(
        self,
        binding: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        bid = _text(binding.get("binding_id"), "binding.binding_id", limit=80)
        with self._lock:
            control = self._controls.get(bid)
        if control is None:
            raise MultiRoomError(
                "NATIVE_CONTROL_UNAVAILABLE",
                "room binding has no live native provider control",
                409,
            )
        if str(binding.get("provider") or "").upper() != control["provider"]:
            raise MultiRoomError(
                "NATIVE_CONTROL_PROVIDER_MISMATCH",
                "live control provider no longer matches room binding",
                409,
            )
        if binding.get("provider_session_ref") != control["provider_session_ref"]:
            raise MultiRoomError(
                "NATIVE_CONTROL_SESSION_MISMATCH",
                "live control session no longer matches room binding",
                409,
            )
        accepted = control["send_input"](dict(binding), dict(event))
        if accepted is False:
            raise MultiRoomError(
                "NATIVE_CONTROL_QUEUE_REJECTED",
                "native provider control rejected the incremental event",
                409,
            )
        transport = dict(accepted) if isinstance(accepted, Mapping) else {}
        status = str(transport.get("status") or "QUEUED").upper()
        if status not in DELIVERY_STATES:
            status = "QUEUED"
        return {
            "status": status,
            "binding_id": bid,
            "room_event_id": event.get("room_event_id"),
            "control_ref": control["control_ref"],
            "transport": transport,
        }

    def close(self) -> None:
        with self._lock:
            binding_ids = list(self._controls)
            self._controls.clear()
        for binding_id in binding_ids:
            try:
                self.store.set_participant_state(binding_id, "DISCONNECTED")
            except MultiRoomError:
                pass


class MultiRoomMeetingCoordinator:
    """Run a bounded, deterministic meeting over model bindings.

    The legacy protocol forwards only the immediately preceding room delta.
    Role-aware meetings first fan out the same prompt independently, then send
    bounded excerpts of those proposals for one structured review phase. The
    durable room remains the source of the observable transcript; neither
    protocol forwards the unbounded room transcript.
    """

    def __init__(
        self,
        store: MultiRoomStore,
        invoke_provider: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        *,
        max_turns: int = 24,
    ) -> None:
        if not callable(invoke_provider):
            raise MultiRoomError(
                "MEETING_PROVIDER_INVOKER_INVALID",
                "meeting provider invoker must be callable",
            )
        self.store = store
        self.invoke_provider = invoke_provider
        self.max_turns = max(1, min(48, int(max_turns)))
        self._cancel_lock = threading.RLock()
        self._cancel_requests: dict[str, str] = {}
        self._run_lock = threading.Lock()
        self._active_rooms: set[str] = set()

    def cancel(self, run_id: str, *, reason: str = "operator") -> dict[str, Any]:
        rid = _text(run_id, "run_id", limit=120)
        cancel_reason = _text(reason, "reason", limit=240)
        with self._cancel_lock:
            self._cancel_requests[rid] = cancel_reason
        return {
            "schema": MEETING_COORDINATOR_SCHEMA,
            "status": "MEETING_CANCEL_REQUESTED",
            "run_id": rid,
            "reason": cancel_reason,
        }

    def _cancel_reason(self, run_id: str) -> str | None:
        with self._cancel_lock:
            return self._cancel_requests.get(run_id)

    def _clear_cancel(self, run_id: str) -> None:
        with self._cancel_lock:
            self._cancel_requests.pop(run_id, None)

    def run(
        self,
        room_id: str,
        *,
        prompt: str,
        max_turns: int = 6,
        run_id: str | None = None,
        cancel_check: Callable[[Mapping[str, Any]], bool] | None = None,
        binding_ids: list[str] | tuple[str, ...] | None = None,
        protocol: str = "INCREMENTAL_DELTA_ONLY",
        participant_briefs: Mapping[str, Mapping[str, Any]] | None = None,
        max_attempts_per_turn: int = 1,
        required_reviewers: int | None = None,
    ) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        with self._run_lock:
            if rid in self._active_rooms:
                raise MultiRoomError(
                    "MEETING_ROOM_BUSY",
                    "meeting room already has an active run",
                    409,
                )
            self._active_rooms.add(rid)
        try:
            return self._run_single(
                rid,
                prompt=prompt,
                max_turns=max_turns,
                run_id=run_id,
                cancel_check=cancel_check,
                binding_ids=binding_ids,
                protocol=protocol,
                participant_briefs=participant_briefs,
                max_attempts_per_turn=max_attempts_per_turn,
                required_reviewers=required_reviewers,
            )
        finally:
            with self._run_lock:
                self._active_rooms.discard(rid)

    def _run_single(
        self,
        room_id: str,
        *,
        prompt: str,
        max_turns: int,
        run_id: str | None,
        cancel_check: Callable[[Mapping[str, Any]], bool] | None,
        binding_ids: list[str] | tuple[str, ...] | None,
        protocol: str,
        participant_briefs: Mapping[str, Mapping[str, Any]] | None,
        max_attempts_per_turn: int,
        required_reviewers: int | None,
    ) -> dict[str, Any]:
        room = self.store.get_room(room_id)
        if room["room_type"] != "MEETING":
            raise MultiRoomError(
                "MEETING_ROOM_TYPE_INVALID",
                "round-robin coordination requires a MEETING room",
                409,
            )
        if room["state"] != "OPEN":
            raise MultiRoomError("ROOM_CLOSED", "cannot run a closed meeting", 409)
        try:
            if isinstance(max_turns, bool):
                raise ValueError
            bounded_turns = int(max_turns)
        except (TypeError, ValueError) as error:
            raise MultiRoomError(
                "MEETING_TURN_LIMIT_INVALID",
                "max_turns must be an integer",
            ) from error
        if bounded_turns < 1 or bounded_turns > self.max_turns:
            raise MultiRoomError(
                "MEETING_TURN_LIMIT_INVALID",
                f"max_turns must be between 1 and {self.max_turns}",
            )
        if cancel_check is not None and not callable(cancel_check):
            raise MultiRoomError(
                "MEETING_CANCEL_CHECK_INVALID",
                "cancel_check must be callable",
            )
        meeting_protocol = str(protocol or "INCREMENTAL_DELTA_ONLY").upper()
        if meeting_protocol not in MEETING_PROTOCOLS:
            raise MultiRoomError(
                "MEETING_PROTOCOL_INVALID",
                f"unsupported meeting protocol: {meeting_protocol}",
            )
        selected_binding_ids = (
            None
            if binding_ids is None
            else {_text(item, "binding_id", limit=80) for item in binding_ids}
        )
        models = sorted(
            (
                binding
                for binding in self.store.list_bindings(room["room_id"])
                if binding["slot_role"] == "MODEL"
                and (
                    selected_binding_ids is None
                    or binding["binding_id"] in selected_binding_ids
                )
            ),
            key=lambda item: (item.get("created_at") or "", item["binding_id"]),
        )
        if not models:
            raise MultiRoomError(
                "MEETING_MODELS_UNAVAILABLE",
                "meeting requires at least one active MODEL binding",
                409,
            )
        try:
            if isinstance(max_attempts_per_turn, bool):
                raise ValueError
            bounded_attempts = int(max_attempts_per_turn)
        except (TypeError, ValueError) as error:
            raise MultiRoomError(
                "MEETING_RETRY_LIMIT_INVALID",
                "max_attempts_per_turn must be an integer",
            ) from error
        if bounded_attempts < 1 or bounded_attempts > 3:
            raise MultiRoomError(
                "MEETING_RETRY_LIMIT_INVALID",
                "max_attempts_per_turn must be between 1 and 3",
            )
        raw_required_reviewers = (
            len(models)
            if required_reviewers is None
            and meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW"
            else (1 if required_reviewers is None else required_reviewers)
        )
        try:
            if isinstance(raw_required_reviewers, bool):
                raise ValueError
            reviewer_quorum = int(raw_required_reviewers)
        except (TypeError, ValueError) as error:
            raise MultiRoomError(
                "MEETING_REVIEWER_QUORUM_INVALID",
                "required_reviewers must be an integer",
            ) from error
        if reviewer_quorum < 1 or reviewer_quorum > len(models):
            raise MultiRoomError(
                "MEETING_REVIEWER_QUORUM_INVALID",
                "required_reviewers must select between one and all MODEL bindings",
            )
        briefs: dict[str, dict[str, Any]] = {}
        if participant_briefs is not None:
            if not isinstance(participant_briefs, Mapping):
                raise MultiRoomError(
                    "MEETING_PARTICIPANT_BRIEFS_INVALID",
                    "participant_briefs must be an object keyed by binding_id",
                )
            model_ids = {str(item["binding_id"]) for item in models}
            for binding_id, raw_brief in participant_briefs.items():
                bid = _text(binding_id, "participant_briefs.binding_id", limit=80)
                if bid not in model_ids or not isinstance(raw_brief, Mapping):
                    raise MultiRoomError(
                        "MEETING_PARTICIPANT_BRIEFS_INVALID",
                        "participant brief must target a selected MODEL binding",
                    )
                role = _text(raw_brief.get("role"), "participant_brief.role", limit=120)
                mandate = _text(
                    raw_brief.get("mandate"), "participant_brief.mandate", limit=2000
                )
                raw_assistants = raw_brief.get("assistants") or []
                if not isinstance(raw_assistants, list) or len(raw_assistants) > 8:
                    raise MultiRoomError(
                        "MEETING_PARTICIPANT_BRIEFS_INVALID",
                        "participant assistants must be a list of at most 8 names",
                    )
                assistants = [
                    _text(item, "participant_brief.assistant", limit=120)
                    for item in raw_assistants
                ]
                briefs[bid] = {
                    "role": role,
                    "mandate": mandate,
                    "assistants": assistants,
                }
        if meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW":
            missing = [item["binding_id"] for item in models if item["binding_id"] not in briefs]
            if missing:
                raise MultiRoomError(
                    "MEETING_PARTICIPANT_BRIEFS_REQUIRED",
                    "independent proposal review requires one brief per MODEL binding",
                )
            if bounded_turns < len(models) * 2:
                raise MultiRoomError(
                    "MEETING_TURN_LIMIT_INVALID",
                    "independent proposal review requires at least two turns per participant",
                )
        meeting_run_id = _text(
            run_id or "meeting_" + secrets.token_hex(12),
            "run_id",
            limit=120,
        )
        existing = [
            event
            for event in self.store.list_control_events(
                room["room_id"], event_type="MEETING_SUMMARY", limit=500
            )
            if event.get("payload", {}).get("run_id") == meeting_run_id
        ]
        if existing:
            raise MultiRoomError(
                "MEETING_RUN_CONFLICT",
                "run_id already has an observable meeting summary",
                409,
            )

        started_at = utc_now()
        turns: list[dict[str, Any]] = []
        current_delta: dict[str, Any] | None = None
        proposal_outputs: dict[str, dict[str, Any]] = {}
        status = "COMPLETED"
        reason = "BOUNDED_TURNS_REACHED"
        prompt_message = self.store.post_message(
            room["room_id"],
            {
                "author_role": "USER",
                "body_text": prompt,
                "idempotency_key": f"meeting:{meeting_run_id}:prompt",
                "correlation_id": meeting_run_id,
            },
        )
        current_delta = prompt_message
        self.store.record_control_event(
            room["room_id"],
            "MEETING_STARTED",
            {
                "schema": MEETING_COORDINATOR_SCHEMA,
                "run_id": meeting_run_id,
                "max_turns": bounded_turns,
                "max_attempts_per_turn": bounded_attempts,
                "required_reviewers": reviewer_quorum,
                "participant_order": [item["binding_id"] for item in models],
                "delivery_mode": meeting_protocol,
                "transcript_forwarded": False,
                "cancel_policy": "TURN_BOUNDARY_FAIL_CLOSED",
            },
        )
        try:
            for turn_number in range(bounded_turns):
                cancel_reason = self._cancel_reason(meeting_run_id)
                if cancel_reason:
                    status = "INTERRUPTED"
                    reason = cancel_reason
                    break
                checkpoint = {
                    "run_id": meeting_run_id,
                    "turn_number": turn_number,
                    "turn_count": len(turns),
                    "max_turns": bounded_turns,
                }
                if cancel_check is not None:
                    try:
                        should_cancel = bool(cancel_check(checkpoint))
                    except Exception as error:  # fail closed at turn boundary
                        should_cancel = True
                        reason = f"CANCEL_CHECK_ERROR:{type(error).__name__}"
                    if should_cancel:
                        status = "INTERRUPTED"
                        if reason == "BOUNDED_TURNS_REACHED":
                            reason = "CANCEL_CHECK_REQUESTED"
                        break
                if (
                    meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW"
                    and turn_number == len(models)
                    and len(proposal_outputs) < reviewer_quorum
                ):
                    status = "BLOCKED"
                    reason = (
                        f"MEETING_QUORUM_UNMET:PROPOSAL:"
                        f"{len(proposal_outputs)}/{reviewer_quorum}"
                    )
                    break
                binding = models[turn_number % len(models)]
                if current_delta is None:
                    status = "FAILED"
                    reason = "INCREMENTAL_DELTA_MISSING"
                    break
                phase = "DISCUSSION"
                turn_delta = current_delta
                brief = briefs.get(str(binding["binding_id"]))
                if meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW":
                    phase = "PROPOSAL" if turn_number < len(models) else "REVIEW"
                    assistant_text = ", ".join((brief or {}).get("assistants") or []) or "none"
                    role_text = (
                        f"Assigned role: {(brief or {}).get('role')}\n"
                        f"Role mandate: {(brief or {}).get('mandate')}\n"
                        f"Research assistants available by function: {assistant_text}\n"
                    )
                    if phase == "PROPOSAL":
                        delta_body = (
                            f"{prompt}\n\n{role_text}\n"
                            "Independent proposal phase: produce a genuinely distinct candidate from your "
                            "assigned role. You cannot see other participants' proposals."
                        )
                    else:
                        excerpt_budget = max(1000, min(5000, 14000 // len(models)))
                        available_proposals = [
                            item
                            for item in models
                            if str(item["binding_id"]) in proposal_outputs
                        ]
                        proposal_text = "\n\n".join(
                            f"Candidate {index + 1} ({item.get('display_name') or item.get('provider')}):\n"
                            f"{proposal_outputs[str(item['binding_id'])]['body_text'][:excerpt_budget]}"
                            for index, item in enumerate(available_proposals)
                        )
                        delta_body = (
                            f"{prompt[:6000]}\n\n{role_text}\n"
                            "Review phase: compare every independent candidate below. Preserve real "
                            "disagreements, correct weaknesses from your assigned role, and return one "
                            "complete revised candidate in the original requested format. Do not merely "
                            "repeat another candidate.\n\n"
                            f"Independent candidates:\n{proposal_text}"
                        )
                    turn_delta = dict(prompt_message)
                    turn_delta["body_text"] = delta_body
                turn = {
                    "schema": MEETING_COORDINATOR_SCHEMA,
                    "run_id": meeting_run_id,
                    "turn_number": turn_number,
                    "round_number": turn_number // len(models) + 1,
                    "binding_id": binding["binding_id"],
                    "provider": binding.get("provider"),
                    "provider_session_ref": binding.get("provider_session_ref"),
                    "input_mode": meeting_protocol,
                    "phase": phase,
                    "meeting_role": (brief or {}).get("role"),
                    "assistant_roles": list((brief or {}).get("assistants") or []),
                    "transcript_forwarded": False,
                    "delta": dict(turn_delta),
                }
                self.store.record_control_event(
                    room["room_id"], "MEETING_TURN_STARTED", turn
                )
                provider_result: Mapping[str, Any] | None = None
                body = ""
                attempt_count = 0
                failure_reason: str | None = None
                interrupted_reason: str | None = None
                for attempt_number in range(1, bounded_attempts + 1):
                    attempt_count = attempt_number
                    try:
                        candidate_result = self.invoke_provider(dict(binding), turn)
                        if not isinstance(candidate_result, Mapping):
                            raise MultiRoomError(
                                "MEETING_PROVIDER_RESULT_INVALID",
                                "provider result must be an object",
                            )
                        provider_result = candidate_result
                        provider_status = str(
                            provider_result.get("status") or "COMPLETED"
                        ).upper()
                        if provider_status in {"CANCELLED", "INTERRUPTED", "ABORTED"}:
                            interrupted_reason = str(
                                provider_result.get("reason") or provider_status
                            )
                            break
                        if provider_status in {"FAILED", "ERROR", "REJECTED"}:
                            failure_reason = str(
                                provider_result.get("reason") or provider_status
                            )
                        else:
                            candidate_body = provider_result.get("body_text") or provider_result.get(
                                "text"
                            )
                            if isinstance(candidate_body, str) and candidate_body.strip():
                                body = candidate_body
                                failure_reason = None
                                break
                            failure_reason = "PROVIDER_OUTPUT_MISSING"
                    except Exception as error:
                        error_code = str(
                            getattr(error, "code", "") or type(error).__name__
                        )
                        error_detail = " ".join(str(error).split())[:240]
                        failure_reason = f"PROVIDER_ERROR:{error_code}:{error_detail}"
                    if attempt_number < bounded_attempts:
                        self.store.record_control_event(
                            room["room_id"],
                            "MEETING_TURN_RETRY",
                            {
                                "schema": MEETING_COORDINATOR_SCHEMA,
                                "run_id": meeting_run_id,
                                "turn_number": turn_number,
                                "binding_id": binding["binding_id"],
                                "attempt_number": attempt_number + 1,
                                "reason": failure_reason,
                            },
                        )
                if interrupted_reason is not None:
                    status = "INTERRUPTED"
                    reason = interrupted_reason
                    turns.append(
                        {
                            "turn_number": turn_number,
                            "binding_id": binding["binding_id"],
                            "status": "INTERRUPTED",
                            "reason": reason,
                            "attempt_count": attempt_count,
                            "input_event_id": turn["delta"].get("room_event_id"),
                            "output_event_id": None,
                        }
                    )
                    break
                if failure_reason is not None or provider_result is None:
                    turn_failure_reason = failure_reason or "PROVIDER_RESULT_MISSING"
                    turns.append(
                        {
                            "turn_number": turn_number,
                            "binding_id": binding["binding_id"],
                            "phase": phase,
                            "status": "FAILED",
                            "reason": turn_failure_reason,
                            "attempt_count": attempt_count,
                            "input_event_id": turn["delta"].get("room_event_id"),
                            "output_event_id": None,
                        }
                    )
                    if meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW":
                        continue
                    status = "FAILED"
                    reason = turn_failure_reason
                    break
                output_artifact = None
                room_body = body
                if meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW":
                    output_artifact = self.store.create_artifact(
                        room["room_id"],
                        {
                            "artifact_type": (
                                "PROPOSAL" if phase == "PROPOSAL" else "SPECIFICATION"
                            ),
                            "title": (
                                f"{(brief or {}).get('role') or binding.get('display_name') or 'Reviewer'} "
                                f"{phase.title()} turn {turn_number + 1}"
                            ),
                            "body_text": body,
                            "state": "CANDIDATE",
                            "author_role": "MODEL",
                            "author_binding_id": binding["binding_id"],
                            "evidence_refs": [],
                        },
                    )
                    artifact_ref = (
                        f"universe://chat-rooms/{room['room_id']}/artifacts/"
                        f"{output_artifact['artifact_id']}"
                    )
                    room_body = json.dumps(
                        {
                            "phase": phase,
                            "role": (brief or {}).get("role"),
                            "artifact_ref": artifact_ref,
                            "summary": " ".join(body.split())[:600],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                try:
                    output_message = self.store.post_message(
                        room["room_id"],
                        {
                            "author_role": "MODEL",
                            "author_binding_id": binding["binding_id"],
                            "body_text": room_body,
                            "idempotency_key": f"meeting:{meeting_run_id}:turn:{turn_number}",
                            "provider_event_id": provider_result.get("provider_event_id"),
                            "correlation_id": meeting_run_id,
                        },
                    )
                except Exception as error:
                    status = "FAILED"
                    error_code = str(getattr(error, "code", "") or type(error).__name__)
                    error_detail = " ".join(str(error).split())[:240]
                    reason = f"ROOM_OUTPUT_ERROR:{error_code}:{error_detail}"
                    turns.append(
                        {
                            "turn_number": turn_number,
                            "binding_id": binding["binding_id"],
                            "status": "FAILED",
                            "reason": reason,
                            "input_event_id": current_delta.get("room_event_id"),
                            "output_event_id": None,
                        }
                    )
                    break
                current_delta = output_message
                if meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW" and phase == "PROPOSAL":
                    proposal_outputs[str(binding["binding_id"])] = {
                        **output_message,
                        "body_text": body,
                        "artifact_id": (output_artifact or {}).get("artifact_id"),
                    }
                turns.append(
                    {
                        "turn_number": turn_number,
                        "binding_id": binding["binding_id"],
                        "phase": phase,
                        "meeting_role": (brief or {}).get("role"),
                        "artifact_id": (output_artifact or {}).get("artifact_id"),
                        "output_warning": provider_result.get("output_warning"),
                        "status": "COMPLETED",
                        "reason": "NONE",
                        "attempt_count": attempt_count,
                        "input_event_id": turn["delta"].get("room_event_id"),
                        "output_event_id": output_message["room_event_id"],
                    }
                )
            if meeting_protocol == "INDEPENDENT_PROPOSAL_REVIEW" and status == "COMPLETED":
                completed_reviewers = {
                    str(item["binding_id"])
                    for item in turns
                    if item.get("phase") == "REVIEW" and item.get("status") == "COMPLETED"
                }
                if len(completed_reviewers) < reviewer_quorum:
                    status = "BLOCKED"
                    reason = (
                        f"MEETING_QUORUM_UNMET:REVIEW:"
                        f"{len(completed_reviewers)}/{reviewer_quorum}"
                    )
        finally:
            self._clear_cancel(meeting_run_id)
        finished_at = utc_now()
        summary = {
            "schema": MEETING_SUMMARY_SCHEMA,
            "run_id": meeting_run_id,
            "room_id": room["room_id"],
            "status": status,
            "reason": reason,
            "started_at": started_at,
            "completed_at": finished_at,
            "max_turns": bounded_turns,
            "max_attempts_per_turn": bounded_attempts,
            "required_reviewers": reviewer_quorum,
            "protocol": meeting_protocol,
            "participant_briefs": briefs,
            "turn_count": len(turns),
            "round_count": (len(turns) + len(models) - 1) // len(models),
            "participant_order": [item["binding_id"] for item in models],
            "turns": turns,
            "delivery_mode": "INCREMENTAL_DELTA_ONLY",
            "transcript_forwarded": False,
            "cancel_policy": "TURN_BOUNDARY_FAIL_CLOSED",
            "bounded": True,
            "last_event_id": current_delta.get("room_event_id") if current_delta else None,
        }
        recorded = self.store.record_control_event(
            room["room_id"], "MEETING_SUMMARY", {**summary}
        )
        return {
            **summary,
            "summary_event_id": recorded["event_id"],
        }

    def summary(self, room_id: str, run_id: str) -> dict[str, Any]:
        rid = _text(room_id, "room_id", limit=80)
        target = _text(run_id, "run_id", limit=120)
        events = self.store.list_control_events(
            rid, event_type="MEETING_SUMMARY", limit=500
        )
        for event in reversed(events):
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("run_id") == target:
                return {
                    **payload,
                    "summary_event_id": event.get("event_id"),
                }
        raise MultiRoomError(
            "MEETING_SUMMARY_NOT_FOUND",
            "meeting summary does not exist",
            404,
        )
