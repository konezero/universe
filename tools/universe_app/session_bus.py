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
from typing import Any, Callable, Mapping

from universe_app.terminal_host import TerminalHostError


BUS_SCHEMA = "universe.session-bus.v1"
MAX_BODY_BYTES = 32 * 1024
KINDS = frozenset({"NOTE", "INSTRUCTION", "RESULT"})
NOTIFY_MODES = frozenset({"NONE", "HEADER"})
INSTRUCTION_DELIVERY_STATES = frozenset({"PENDING", "CLAIMED", "DISPATCHED"})
LIFECYCLE_STATES = frozenset(
    {
        "QUEUED",
        "ACCEPTED",
        "STARTED",
        "COMPLETED",
        "FAILED",
        "REPLIED",
        "CANCELLED",
        "DONE",
    }
)
LIFECYCLE_TRANSITIONS = {
    "QUEUED": frozenset({"ACCEPTED", "CANCELLED"}),
    "ACCEPTED": frozenset({"QUEUED", "STARTED", "FAILED", "CANCELLED"}),
    "STARTED": frozenset({"COMPLETED", "FAILED", "CANCELLED"}),
    "COMPLETED": frozenset({"REPLIED", "DONE"}),
    "FAILED": frozenset({"REPLIED", "DONE"}),
    "REPLIED": frozenset({"DONE"}),
    "CANCELLED": frozenset(),
    "DONE": frozenset(),
}
INBOX_LIFECYCLE_STATES = frozenset({"QUEUED", "ACCEPTED"})
RESULT_LIFECYCLE_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
PROJECTION_STATES = frozenset({"DECISION_NEEDED"})


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
        "session_anchor_ref": _text(
            raw.get("session_anchor_ref"), "session_anchor_ref", limit=256
        ),
        "node_ref": _text(
            raw.get("node_ref") or raw.get("node"), "node_ref", limit=160
        ),
        "task_frame_ref": _text(
            raw.get("task_frame_ref"), "task_frame_ref", limit=256
        ),
    }


def _terminal_anchor(terminal: Mapping[str, Any]) -> str:
    return str(
        terminal.get("active_session_anchor_ref")
        or terminal.get("session_anchor_ref")
        or ""
    ).strip()


def _message_lifecycle(message: Mapping[str, Any]) -> str:
    explicit = str(message.get("lifecycle_state") or "").strip().upper()
    if explicit:
        return explicit
    delivery = str(message.get("delivery_state") or "").strip().upper()
    return {
        "PENDING": "QUEUED",
        "CLAIMED": "ACCEPTED",
        "DISPATCHED": "STARTED",
        "UNREAD": "QUEUED",
        "READ": "DONE",
    }.get(delivery, "QUEUED")


def _is_inbox_message(message: Mapping[str, Any]) -> bool:
    delivery = str(message.get("delivery_state") or "").strip().upper()
    return delivery in {"UNREAD", "PENDING", "CLAIMED"} or _message_lifecycle(
        message
    ) in INBOX_LIFECYCLE_STATES


def _event_projection_state(message: Mapping[str, Any]) -> str:
    lifecycle = message.get("lifecycle")
    lifecycle_map = lifecycle if isinstance(lifecycle, Mapping) else {}
    explicit = str(lifecycle_map.get("projection_state") or "").strip().upper()
    current = _message_lifecycle(message)
    if explicit in PROJECTION_STATES and current in INBOX_LIFECYCLE_STATES:
        return explicit
    return current


def _is_results_message(message: Mapping[str, Any]) -> bool:
    return (
        str(message.get("kind") or "").upper() == "RESULT"
        or _event_projection_state(message) in RESULT_LIFECYCLE_STATES
        or _event_projection_state(message) == "DECISION_NEEDED"
    )


def _event_context(message: Mapping[str, Any]) -> dict[str, Any]:
    source = message.get("from") if isinstance(message.get("from"), Mapping) else {}
    target = message.get("to") if isinstance(message.get("to"), Mapping) else {}
    provenance = (
        message.get("provenance")
        if isinstance(message.get("provenance"), Mapping)
        else {}
    )
    lifecycle = (
        message.get("lifecycle")
        if isinstance(message.get("lifecycle"), Mapping)
        else {}
    )
    raw_artifacts = provenance.get("artifact_refs")
    artifacts = (
        [str(item).strip() for item in raw_artifacts if str(item).strip()]
        if isinstance(raw_artifacts, (list, tuple))
        else []
    )
    result_ref = str(lifecycle.get("result_ref") or "").strip()
    if result_ref and result_ref not in artifacts:
        artifacts.append(result_ref)
    return {
        "source_event_id": str(
            message.get("in_reply_to") or message.get("message_id") or ""
        ),
        "session_anchor_ref": str(
            message.get("recipient_anchor_ref")
            or message.get("session_anchor_ref")
            or ""
        ),
        "thread_id": str(message.get("thread_id") or ""),
        "room_id": str(message.get("room_id") or ""),
        "task_frame_ref": str(
            provenance.get("task_frame_ref")
            or source.get("task_frame_ref")
            or target.get("task_frame_ref")
            or ""
        ),
        "node_ref": str(
            target.get("node_ref") or target.get("project_id") or ""
        ),
        "artifact_refs": artifacts,
        "projection_state": _event_projection_state(message),
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
    session_anchor_ref: str = "",
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
    wanted_anchor = _text(
        session_anchor_ref, "session_anchor_ref", limit=256
    )
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
        if wanted_anchor and _terminal_anchor(item) != wanted_anchor:
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
        session_anchor_ref=to.get("session_anchor_ref") or "",
    )
    if not rows and to.get("session_anchor_ref"):
        return [
            {
                "terminal_id": "",
                "project_id": to.get("project_id") or "",
                "mode": to.get("mode") or "",
                "provider": to.get("provider") or "",
                "active_session_anchor_ref": to.get("session_anchor_ref") or "",
                "state": "OFFLINE",
            }
        ]
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
    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        result_observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.result_observer = result_observer
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
                    dispatched_at TEXT,
                    recipient_anchor_ref TEXT NOT NULL DEFAULT '',
                    source_anchor_ref TEXT NOT NULL DEFAULT '',
                    in_reply_to TEXT NOT NULL DEFAULT '',
                    lifecycle_state TEXT NOT NULL DEFAULT 'QUEUED',
                    lifecycle_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(session_bus_message)")
            }
            migrations = {
                "recipient_anchor_ref": "TEXT NOT NULL DEFAULT ''",
                "source_anchor_ref": "TEXT NOT NULL DEFAULT ''",
                "in_reply_to": "TEXT NOT NULL DEFAULT ''",
                "lifecycle_state": "TEXT NOT NULL DEFAULT 'QUEUED'",
                "lifecycle_json": "TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE session_bus_message ADD COLUMN {column} {definition}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sbm_terminal "
                "ON session_bus_message (terminal_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sbm_recipient_anchor "
                "ON session_bus_message (recipient_anchor_ref, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sbm_thread "
                "ON session_bus_message (thread_id, created_at)"
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
            inbox_key = tid or (
                "anchor:" + str(message.get("recipient_anchor_ref") or "")
            )
            self._inbox.setdefault(inbox_key, []).append(mid)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        lifecycle = _json_mod.loads(d.get("lifecycle_json") or "{}")
        lifecycle_state = str(d.get("lifecycle_state") or "").strip().upper()
        # Rows created before the lifecycle columns were installed receive the
        # SQLite migration default (QUEUED). Preserve their actual legacy
        # delivery progress instead of silently rewinding them on hydration.
        if not lifecycle and lifecycle_state == "QUEUED":
            lifecycle_state = {
                "CLAIMED": "ACCEPTED",
                "DISPATCHED": "STARTED",
                "READ": "DONE",
            }.get(str(d.get("delivery_state") or "").upper(), lifecycle_state)
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
            "recipient_anchor_ref": d.get("recipient_anchor_ref") or "",
            "source_anchor_ref": d.get("source_anchor_ref") or "",
            "in_reply_to": d.get("in_reply_to") or "",
            "lifecycle_state": lifecycle_state,
            "lifecycle": lifecycle,
            "updated_at": d.get("updated_at") or d["created_at"],
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
                    claimed_at, dispatched_at, recipient_anchor_ref,
                    source_anchor_ref, in_reply_to, lifecycle_state,
                    lifecycle_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    message.get("recipient_anchor_ref") or "",
                    message.get("source_anchor_ref") or "",
                    message.get("in_reply_to") or "",
                    _message_lifecycle(message),
                    _json_mod.dumps(message.get("lifecycle") or {}),
                    message.get("updated_at") or message["created_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def drop_terminal(self, terminal_id: str) -> None:
        """Detach a live delivery handle without deleting durable events."""

        tid = str(terminal_id or "").strip()
        if not tid:
            return

    def unread_map(self) -> dict[str, int]:
        with self._lock:
            return {
                terminal_id: sum(
                    1
                    for message_id in message_ids
                    if _is_inbox_message(self._messages.get(message_id) or {})
                )
                for terminal_id, message_ids in self._inbox.items()
                if any(
                    _is_inbox_message(self._messages.get(message_id) or {})
                    for message_id in message_ids
                )
            }

    def unread_count(self, terminal_id: str) -> int:
        tid = str(terminal_id or "").strip()
        with self._lock:
            return sum(
                1
                for message_id in self._inbox.get(tid, [])
                if _is_inbox_message(self._messages.get(message_id) or {})
            )

    def unread_count_for_anchor(self, session_anchor_ref: str) -> int:
        anchor = str(session_anchor_ref or "").strip()
        if not anchor:
            return 0
        with self._lock:
            return sum(
                1
                for message in self._messages.values()
                if str(message.get("recipient_anchor_ref") or "") == anchor
                and _is_inbox_message(message)
            )

    def working_map(self) -> dict[str, int]:
        """Count accepted/started instructions by their current terminal."""

        with self._lock:
            result: dict[str, int] = {}
            for message in self._messages.values():
                terminal_id = str(message.get("_terminal_id") or "")
                if (
                    terminal_id
                    and str(message.get("kind") or "").upper() == "INSTRUCTION"
                    and _message_lifecycle(message) in {"ACCEPTED", "STARTED"}
                ):
                    result[terminal_id] = result.get(terminal_id, 0) + 1
            return result

    def directory(self, host: Any) -> dict[str, Any]:
        counts = self.unread_map()
        terminals = []
        for item in live_sessions(host):
            terminal_id = str(item.get("terminal_id") or "")
            anchor_ref = _terminal_anchor(item)
            row = {
                "project_id": item.get("project_id"),
                "mode": item.get("mode"),
                "provider": item.get("provider"),
                "terminal_id": terminal_id,
                "session_anchor_ref": anchor_ref,
                "pid": item.get("pid"),
                "unread": max(
                    int(counts.get(terminal_id, 0)),
                    self.unread_count_for_anchor(anchor_ref),
                ),
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
            "recipient_anchor_ref": str(message.get("recipient_anchor_ref") or ""),
            "source_anchor_ref": str(message.get("source_anchor_ref") or ""),
            "in_reply_to": str(message.get("in_reply_to") or ""),
            "lifecycle_state": _message_lifecycle(message),
            "lifecycle": dict(message.get("lifecycle") or {}),
            "event_context": _event_context(message),
            "updated_at": str(message.get("updated_at") or message["created_at"]),
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
        projection_state = _text(
            payload.get("projection_state"), "projection_state", limit=32
        ).upper()
        if projection_state and projection_state not in PROJECTION_STATES:
            raise SessionBusError(
                "BUS_PROJECTION_STATE_INVALID",
                f"unsupported projection state: {projection_state}",
            )
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
                projection_state=projection_state,
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
        projection_state: str = "",
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
            projection_state=projection_state,
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
        projection_state: str,
    ) -> dict[str, Any]:
        terminal_id = str(terminal.get("terminal_id") or "").strip()
        recipient_anchor = _terminal_anchor(terminal) or str(
            to.get("session_anchor_ref") or ""
        ).strip()
        if not terminal_id and not recipient_anchor:
            raise SessionBusError(
                "BUS_TARGET_NOT_FOUND", "terminal id or Session Anchor missing", 404
            )
        source_anchor = str(source.get("session_anchor_ref") or "").strip()
        source_terminal_id = str(source.get("terminal_id") or "").strip()
        if not source_anchor and source_terminal_id:
            try:
                source_anchor = _terminal_anchor(_public_session(host, source_terminal_id))
            except (SessionBusError, TerminalHostError):
                source_anchor = ""
        message_id = "msg_" + secrets.token_hex(8)
        resolved_thread = thread_id or message_id
        message = {
            "message_id": message_id,
            "_terminal_id": terminal_id,
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
                "session_anchor_ref": recipient_anchor,
            },
            "body_text": body,
            "bytes": len(body.encode("utf-8")),
            "created_at": utc_now(),
            "delivery_state": "PENDING" if kind == "INSTRUCTION" else "UNREAD",
            "session_anchor_ref": recipient_anchor,
            "recipient_anchor_ref": recipient_anchor,
            "source_anchor_ref": source_anchor,
            "in_reply_to": "",
            "lifecycle_state": "QUEUED",
            "lifecycle": {
                "queued_at": utc_now(),
                **(
                    {"projection_state": projection_state}
                    if projection_state
                    else {}
                ),
            },
            "updated_at": utc_now(),
            "provenance": _instruction_provenance(source, kind),
        }
        inbox_key = terminal_id or ("anchor:" + recipient_anchor)
        with self._lock:
            inbox = self._inbox.setdefault(inbox_key, [])
            inbox.append(message_id)
            self._messages[message_id] = message
        self._persist_message(terminal_id, message)
        if notify == "HEADER" and terminal_id:
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
        message_id: str = "",
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
        requested_message_id = _text(message_id, "message_id", limit=80)
        with self._lock:
            candidate_ids = (
                [requested_message_id]
                if requested_message_id
                else list(self._inbox.get(tid, []))
            )
            if not requested_message_id:
                candidate_ids.extend(
                    candidate_id
                    for candidate_id, item in self._messages.items()
                    if str(item.get("recipient_anchor_ref") or "") == anchor
                    and candidate_id not in candidate_ids
                )
            for candidate_id in candidate_ids:
                message = self._messages.get(candidate_id)
                if not isinstance(message, dict):
                    continue
                if message.get("kind") != "INSTRUCTION":
                    continue
                if message.get("delivery_state") != "PENDING":
                    continue
                previous_tid = str(message.get("_terminal_id") or "")
                message["delivery_state"] = "CLAIMED"
                message["_terminal_id"] = tid
                message["session_anchor_ref"] = anchor
                message["recipient_anchor_ref"] = anchor
                message["lifecycle_state"] = "ACCEPTED"
                message["claimed_at"] = utc_now()
                message["updated_at"] = message["claimed_at"]
                message.setdefault("lifecycle", {})["accepted_at"] = message[
                    "claimed_at"
                ]
                if candidate_id not in self._inbox.setdefault(tid, []):
                    self._inbox[tid].append(candidate_id)
                if previous_tid and previous_tid != tid:
                    self._inbox[previous_tid] = [
                        item
                        for item in self._inbox.get(previous_tid, [])
                        if item != candidate_id
                    ]
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
            if not isinstance(message, dict):
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
            message["lifecycle_state"] = "STARTED"
            message["dispatched_at"] = utc_now()
            message["updated_at"] = message["dispatched_at"]
            message.setdefault("lifecycle", {})["started_at"] = message[
                "dispatched_at"
            ]
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
            if not isinstance(message, dict):
                return
            if (
                message.get("delivery_state") == "CLAIMED"
                and message.get("session_anchor_ref") == anchor
            ):
                message["delivery_state"] = "PENDING"
                message["lifecycle_state"] = "QUEUED"
                message.pop("claimed_at", None)
                message["updated_at"] = utc_now()
                message.setdefault("lifecycle", {})["requeued_at"] = message[
                    "updated_at"
                ]
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
        session_anchor_ref: str = "",
        thread_id: str = "",
        projection: str = "INBOX",
        event_kind: str = "",
        lifecycle_state: str = "",
        task_frame_ref: str = "",
        node_ref: str = "",
        headers_only: bool = False,
    ) -> dict[str, Any]:
        wanted_terminal = _text(terminal_id, "terminal_id", limit=80)
        wanted_project = _text(project_id, "project_id", limit=120)
        wanted_mode = _text(mode, "mode", limit=64).upper()
        wanted_provider = _text(provider, "provider", limit=64).upper()
        wanted_anchor = _text(
            session_anchor_ref, "session_anchor_ref", limit=256
        )
        coordinate_terminal_ids: set[str] | None = None
        if (
            not wanted_terminal
            and not wanted_anchor
            and any((wanted_project, wanted_mode, wanted_provider))
        ):
            matched_terminal_ids = {
                str(item.get("terminal_id") or "")
                for item in match_live_terminals(
                    host,
                    project_id=wanted_project,
                    mode=wanted_mode,
                    provider=wanted_provider,
                )
                if str(item.get("terminal_id") or "")
            }
            if matched_terminal_ids:
                coordinate_terminal_ids = matched_terminal_ids
        wanted_room = _text(room_id, "room_id", limit=80)
        wanted_thread = _text(thread_id, "thread_id", limit=80)
        wanted_kind = _text(event_kind, "event_kind", limit=32).upper()
        wanted_state = _text(
            lifecycle_state, "lifecycle_state", limit=32
        ).upper()
        wanted_task_frame = _text(
            task_frame_ref, "task_frame_ref", limit=256
        )
        wanted_node = _text(node_ref, "node_ref", limit=160)
        view = _text(projection or "INBOX", "projection", limit=24).upper()
        if view not in {"INBOX", "ACTIVITY", "RESULTS"}:
            raise SessionBusError(
                "BUS_PROJECTION_INVALID", f"unsupported projection: {view}"
            )
        if wanted_kind and wanted_kind not in KINDS:
            raise SessionBusError(
                "BUS_EVENT_KIND_INVALID", f"unsupported event kind: {wanted_kind}"
            )
        if wanted_state and wanted_state not in LIFECYCLE_STATES | PROJECTION_STATES:
            raise SessionBusError(
                "BUS_LIFECYCLE_STATE_INVALID",
                f"unsupported lifecycle state: {wanted_state}",
            )
        if not any(
            (wanted_terminal, wanted_project, wanted_room, wanted_anchor, wanted_thread)
        ):
            raise SessionBusError(
                "BUS_INBOX_TARGET_REQUIRED",
                "inbox requires terminal, Session Anchor, coordinate, room_id, or thread_id",
            )
        live_terminals = [dict(item) for item in live_sessions(host)]
        live_by_anchor = {
            _terminal_anchor(item): item
            for item in live_terminals
            if _terminal_anchor(item)
        }
        transfer_targets = [
            {
                "terminal_id": str(item.get("terminal_id") or ""),
                "session_anchor_ref": _terminal_anchor(item),
                "project_id": str(item.get("project_id") or ""),
                "mode": str(item.get("mode") or "").upper(),
                "provider": str(item.get("provider") or "").upper(),
            }
            for item in live_terminals
            if str(item.get("backend_owner") or "").upper()
            == "RUST_RECONNECTION_HOST"
            and str(item.get("launch_profile") or "INTERACTIVE").upper()
            == "INTERACTIVE"
            and _terminal_anchor(item)
        ]
        messages: list[dict[str, Any]] = []
        with self._lock:
            scan = list(self._messages.values())
            for message in scan:
                to = message.get("to") if isinstance(message.get("to"), Mapping) else {}
                stored_terminal = str(message.get("_terminal_id") or "")
                lifecycle = _message_lifecycle(message)
                if wanted_terminal and stored_terminal != wanted_terminal:
                    continue
                if wanted_anchor and str(message.get("recipient_anchor_ref") or "") != wanted_anchor:
                    continue
                if coordinate_terminal_ids is not None:
                    if stored_terminal not in coordinate_terminal_ids:
                        continue
                else:
                    if wanted_project and str(to.get("project_id") or "") != wanted_project:
                        continue
                    if wanted_mode and str(to.get("mode") or "").upper() != wanted_mode:
                        continue
                    if wanted_provider and wanted_provider != "AUTO" and str(to.get("provider") or "").upper() != wanted_provider:
                        continue
                if wanted_room and str(message.get("room_id") or "") != wanted_room:
                    continue
                if wanted_thread and str(message.get("thread_id") or "") != wanted_thread:
                    continue
                context = _event_context(message)
                if wanted_kind and str(message.get("kind") or "").upper() != wanted_kind:
                    continue
                if wanted_state and context["projection_state"] != wanted_state:
                    continue
                if wanted_task_frame and context["task_frame_ref"] != wanted_task_frame:
                    continue
                if wanted_node and context["node_ref"] != wanted_node:
                    continue
                if view == "INBOX" and not (
                    _is_inbox_message(message) or lifecycle == "STARTED"
                ):
                    continue
                if view == "RESULTS" and not _is_results_message(message):
                    continue
                row = self._public_message(message, headers_only=headers_only)
                row["terminal_id"] = stored_terminal
                recipient_anchor = str(message.get("recipient_anchor_ref") or "")
                live_target = live_by_anchor.get(recipient_anchor)
                if lifecycle in {"ACCEPTED", "STARTED"}:
                    work_state = "WORKING"
                elif lifecycle in {"COMPLETED", "FAILED", "REPLIED", "CANCELLED", "DONE"}:
                    work_state = "COMPLETE"
                elif live_target is not None:
                    work_state = "PENDING_DELIVERY"
                else:
                    work_state = "NEEDS_TRANSFER"
                row["work_state"] = work_state
                row["target_live"] = live_target is not None
                messages.append(row)
        messages.sort(
            key=lambda item: (
                str(item.get("updated_at") or item.get("created_at") or ""),
                str(item.get("created_at") or ""),
                str(item.get("message_id") or ""),
            )
        )
        return {
            "schema": BUS_SCHEMA,
            "status": "OK",
            "projection": view,
            "messages": messages,
            "transfer_targets": transfer_targets,
        }

    def transfer_instruction(
        self,
        host: Any,
        message_id: str,
        *,
        from_anchor_ref: str,
        to_terminal_id: str,
        to_anchor_ref: str,
    ) -> dict[str, Any]:
        """Retarget one queued instruction to an explicit live Rust Host."""

        mid = _text(message_id, "message_id", required=True, limit=80)
        source_anchor = _text(from_anchor_ref, "from_anchor_ref", required=True, limit=256)
        target_tid = _text(to_terminal_id, "to_terminal_id", required=True, limit=80)
        target_anchor = _text(to_anchor_ref, "to_anchor_ref", required=True, limit=256)
        terminal = _public_session(host, target_tid)
        if str(terminal.get("state") or "").upper() != "LIVE":
            raise SessionBusError("BUS_TRANSFER_TARGET_NOT_LIVE", "target terminal is not live", 409)
        if str(terminal.get("backend_owner") or "").upper() != "RUST_RECONNECTION_HOST":
            raise SessionBusError("BUS_TRANSFER_TARGET_HOST_REQUIRED", "target must be owned by a live Rust reconnection Host", 409)
        if str(terminal.get("launch_profile") or "INTERACTIVE").upper() != "INTERACTIVE":
            raise SessionBusError("BUS_TRANSFER_TARGET_INTERACTIVE_REQUIRED", "target must be an interactive session Host", 409)
        if _terminal_anchor(terminal) != target_anchor:
            raise SessionBusError("BUS_TRANSFER_TARGET_ANCHOR_MISMATCH", "target terminal does not own the selected Session Anchor", 409)
        with self._lock:
            message = self._messages.get(mid)
            if not isinstance(message, dict):
                raise SessionBusError("BUS_MESSAGE_NOT_FOUND", "message does not exist", 404)
            if str(message.get("kind") or "").upper() != "INSTRUCTION":
                raise SessionBusError("BUS_TRANSFER_INSTRUCTION_REQUIRED", "only instructions can be transferred", 409)
            if _message_lifecycle(message) != "QUEUED":
                raise SessionBusError("BUS_TRANSFER_NOT_QUEUED", "only a queued instruction can be transferred", 409)
            current_anchor = str(message.get("recipient_anchor_ref") or "")
            if current_anchor != source_anchor:
                raise SessionBusError("BUS_TRANSFER_SOURCE_MISMATCH", "instruction is no longer owned by the source Session Anchor", 409)
            previous_tid = str(message.get("_terminal_id") or "")
            now = utc_now()
            lifecycle = message.setdefault("lifecycle", {})
            history = lifecycle.setdefault("transfer_history", [])
            if not isinstance(history, list):
                history = []
                lifecycle["transfer_history"] = history
            history.append({
                "from_anchor_ref": source_anchor,
                "from_terminal_id": previous_tid,
                "to_anchor_ref": target_anchor,
                "to_terminal_id": target_tid,
                "transferred_at": now,
            })
            target = dict(message.get("to") or {})
            target.update({
                "project_id": str(terminal.get("project_id") or ""),
                "mode": str(terminal.get("mode") or "").upper(),
                "provider": str(terminal.get("provider") or "").upper(),
                "terminal_id": target_tid,
                "session_anchor_ref": target_anchor,
            })
            message["to"] = target
            message["_terminal_id"] = target_tid
            message["session_anchor_ref"] = target_anchor
            message["recipient_anchor_ref"] = target_anchor
            message["updated_at"] = now
            if previous_tid and previous_tid != target_tid:
                self._inbox[previous_tid] = [item for item in self._inbox.get(previous_tid, []) if item != mid]
            if mid not in self._inbox.setdefault(target_tid, []):
                self._inbox[target_tid].append(mid)
            self._persist_message(target_tid, message)
            return self._public_message(message, headers_only=False)

    def ack(self, message_id: str, terminal_id: str) -> dict[str, Any]:
        mid = _text(message_id, "message_id", required=True, limit=80)
        tid = _text(terminal_id, "terminal_id", required=True, limit=80)
        with self._lock:
            message = self._messages.get(mid)
            if not isinstance(message, dict) or str(message.get("_terminal_id") or "") != tid:
                raise SessionBusError("BUS_MESSAGE_NOT_FOUND", "message is not in that inbox", 404)
            message["delivery_state"] = "READ"
            message["lifecycle_state"] = "DONE"
            message["updated_at"] = utc_now()
            message.setdefault("lifecycle", {})["read_at"] = message["updated_at"]
            message["lifecycle"]["done_at"] = message["updated_at"]
            self._persist_message(tid, message)
        return {
            "schema": BUS_SCHEMA,
            "status": "OK",
            "message_id": mid,
            "terminal_id": tid,
            "lifecycle_state": "DONE",
        }

    def transition(
        self,
        message_id: str,
        *,
        state: str,
        terminal_id: str = "",
        session_anchor_ref: str = "",
        provider_message_id: str = "",
        result_ref: str = "",
        error_code: str = "",
    ) -> dict[str, Any]:
        mid = _text(message_id, "message_id", required=True, limit=80)
        next_state = _text(state, "state", required=True, limit=32).upper()
        if next_state not in LIFECYCLE_STATES:
            raise SessionBusError(
                "BUS_LIFECYCLE_STATE_INVALID", f"unsupported state: {next_state}"
            )
        tid = _text(terminal_id, "terminal_id", limit=80)
        anchor = _text(
            session_anchor_ref, "session_anchor_ref", limit=256
        )
        with self._lock:
            message = self._messages.get(mid)
            if not isinstance(message, dict):
                raise SessionBusError(
                    "BUS_MESSAGE_NOT_FOUND", "message does not exist", 404
                )
            stored_tid = str(message.get("_terminal_id") or "")
            stored_anchor = str(message.get("recipient_anchor_ref") or "")
            anchor_matches = bool(anchor and stored_anchor and anchor == stored_anchor)
            if tid and stored_tid and tid != stored_tid and not anchor_matches:
                raise SessionBusError(
                    "BUS_RECIPIENT_MISMATCH", "terminal does not own the message", 409
                )
            if tid and anchor_matches:
                message["_terminal_id"] = tid
                stored_tid = tid
            if anchor and stored_anchor and anchor != stored_anchor:
                raise SessionBusError(
                    "BUS_RECIPIENT_MISMATCH",
                    "Session Anchor does not own the message",
                    409,
                )
            if not tid and not anchor:
                raise SessionBusError(
                    "BUS_RECIPIENT_REQUIRED",
                    "terminal_id or session_anchor_ref is required",
                )
            current = _message_lifecycle(message)
            if next_state != current and next_state not in LIFECYCLE_TRANSITIONS.get(
                current, frozenset()
            ):
                raise SessionBusError(
                    "BUS_LIFECYCLE_TRANSITION_INVALID",
                    f"cannot transition {current} to {next_state}",
                    409,
                )
            now = utc_now()
            message["lifecycle_state"] = next_state
            message["updated_at"] = now
            message.setdefault("lifecycle", {})[
                next_state.lower() + "_at"
            ] = now
            if provider_message_id:
                message["lifecycle"]["provider_message_id"] = _text(
                    provider_message_id, "provider_message_id", limit=160
                )
            if result_ref:
                message["lifecycle"]["result_ref"] = _text(
                    result_ref, "result_ref", limit=512
                )
            if error_code:
                message["lifecycle"]["error_code"] = _text(
                    error_code, "error_code", limit=160
                )
            message["delivery_state"] = {
                "QUEUED": "PENDING",
                "ACCEPTED": "CLAIMED",
                "STARTED": "DISPATCHED",
                "DONE": "READ",
            }.get(next_state, next_state)
            persist_tid = tid or stored_tid
            self._persist_message(persist_tid, message)
            return self._public_message(message, headers_only=False)

    def _originator_mailbox(
        self,
        original: Mapping[str, Any],
        host: Any | None,
        stored_tid: str,
        stored_anchor: str,
    ) -> tuple[str, str]:
        source = original.get("from") if isinstance(original.get("from"), Mapping) else {}
        recipient_tid = str(source.get("terminal_id") or stored_tid).strip()
        recipient_anchor = str(
            original.get("source_anchor_ref")
            or source.get("session_anchor_ref")
            or ""
        ).strip()
        from_provider = str(source.get("provider") or "").upper()
        # UI-originated work stays on the addressed Session Anchor. A Conductor
        # or other provider originator must receive the RESULT on their live
        # Session Anchor even when the inbound `from` omitted it.
        if (
            host is not None
            and from_provider not in {"", "UI"}
            and (not recipient_anchor or recipient_anchor == stored_anchor)
        ):
            matches = match_live_terminals(
                host,
                project_id=str(source.get("project_id") or ""),
                mode=str(source.get("mode") or ""),
                provider=from_provider,
            )
            if matches:
                live = matches[0]
                live_anchor = _terminal_anchor(live)
                live_tid = str(live.get("terminal_id") or "").strip()
                if live_anchor:
                    recipient_anchor = live_anchor
                if live_tid:
                    recipient_tid = live_tid
        if not recipient_anchor:
            recipient_anchor = stored_anchor
        if not recipient_tid:
            recipient_tid = stored_tid
        return recipient_tid, recipient_anchor

    def reply(
        self,
        message_id: str,
        *,
        terminal_id: str = "",
        session_anchor_ref: str = "",
        body_text: str,
        result_ref: str = "",
        outcome: str = "COMPLETED",
        host: Any | None = None,
    ) -> dict[str, Any]:
        mid = _text(message_id, "message_id", required=True, limit=80)
        body = str(body_text or "").strip()
        if not body:
            raise SessionBusError("BUS_BODY_REQUIRED", "body_text is required")
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise SessionBusError(
                "BUS_BODY_TOO_LARGE", "body_text exceeds 32 KiB", 413
            )
        terminal_state = _text(outcome, "outcome", required=True, limit=32).upper()
        if terminal_state not in {"COMPLETED", "FAILED"}:
            raise SessionBusError(
                "BUS_RESULT_OUTCOME_INVALID", "outcome must be COMPLETED or FAILED"
            )
        with self._lock:
            original = self._messages.get(mid)
            if not isinstance(original, dict):
                raise SessionBusError(
                    "BUS_MESSAGE_NOT_FOUND", "message does not exist", 404
                )
            stored_tid = str(original.get("_terminal_id") or "")
            stored_anchor = str(original.get("recipient_anchor_ref") or "")
            anchor_matches = bool(
                session_anchor_ref
                and stored_anchor
                and session_anchor_ref == stored_anchor
            )
            if (
                terminal_id
                and stored_tid
                and terminal_id != stored_tid
                and not anchor_matches
            ):
                raise SessionBusError(
                    "BUS_RECIPIENT_MISMATCH", "terminal does not own the message", 409
                )
            if terminal_id and anchor_matches:
                original["_terminal_id"] = terminal_id
                stored_tid = terminal_id
            if session_anchor_ref and stored_anchor and session_anchor_ref != stored_anchor:
                raise SessionBusError(
                    "BUS_RECIPIENT_MISMATCH",
                    "Session Anchor does not own the message",
                    409,
                )
            current = _message_lifecycle(original)
            if current not in {"STARTED", "COMPLETED", "FAILED", "REPLIED"}:
                raise SessionBusError(
                    "BUS_LIFECYCLE_TRANSITION_INVALID",
                    f"cannot reply while message is {current}",
                    409,
                )
            now = utc_now()
            original["lifecycle_state"] = "REPLIED"
            original["delivery_state"] = "REPLIED"
            original["updated_at"] = now
            lifecycle = original.setdefault("lifecycle", {})
            lifecycle[terminal_state.lower() + "_at"] = lifecycle.get(
                terminal_state.lower() + "_at", now
            )
            lifecycle["replied_at"] = now
            if result_ref:
                lifecycle["result_ref"] = _text(
                    result_ref, "result_ref", limit=512
                )
            self._persist_message(stored_tid, original)

            result_id = "msg_" + secrets.token_hex(8)
            recipient_tid, recipient_anchor = self._originator_mailbox(
                original, host, stored_tid, stored_anchor
            )
            thread_id = str(original.get("thread_id") or mid)
            result = {
                "message_id": result_id,
                "_terminal_id": recipient_tid,
                "thread_id": thread_id,
                "room_id": str(original.get("room_id") or ""),
                "kind": "RESULT",
                "from": dict(original.get("to") or {}),
                "to": {
                    **dict(
                        original.get("from")
                        if isinstance(original.get("from"), Mapping)
                        else {}
                    ),
                    "terminal_id": recipient_tid,
                    "session_anchor_ref": recipient_anchor,
                },
                "body_text": body,
                "bytes": len(body.encode("utf-8")),
                "created_at": now,
                "delivery_state": "UNREAD",
                "session_anchor_ref": recipient_anchor,
                "recipient_anchor_ref": recipient_anchor,
                "source_anchor_ref": stored_anchor,
                "in_reply_to": mid,
                "lifecycle_state": terminal_state,
                "lifecycle": {
                    terminal_state.lower() + "_at": now,
                    "result_ref": result_ref,
                },
                "updated_at": now,
                "provenance": {"kind": "SESSION_BUS_RESULT"},
            }
            inbox_key = recipient_tid or (
                "anchor:" + recipient_anchor if recipient_anchor else "thread:" + thread_id
            )
            self._messages[result_id] = result
            self._inbox.setdefault(inbox_key, []).append(result_id)
            self._persist_message(recipient_tid, result)
            packet = {
                "schema": BUS_SCHEMA,
                "status": "REPLIED",
                "thread_id": thread_id,
                "message": self._public_message(original, headers_only=False),
                "result": self._public_message(result, headers_only=False),
            }
        observer = self.result_observer
        if callable(observer):
            observer(packet)
        return packet


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
                    "projection_state": payload.get("projection_state"),
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
