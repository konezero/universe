"""Durable internal transport for one exact cross-session delegation.

This module owns only Session Anchor -> provider-session delivery.  It does
not create Room messages and it never accepts a browser/user-chat request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping

from task_frame_lineage import TaskFrameLineageError, TaskFrameLineageStore


class SessionAnchorTransportError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SessionAnchorTransport:
    """Resolve persistent Session Anchors and use only provider-session I/O."""

    def __init__(
        self,
        *,
        database_path: Path,
        session_supervisor: Any,
        provider_sessions: Any,
        provider_chat_catalog: Callable[[], Mapping[str, Any]],
        task_frame_lineage: TaskFrameLineageStore | None = None,
        terminal_claim_callback: Callable[[str], bool] | None = None,
        terminal_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.database_path = database_path.resolve()
        self.session_supervisor = session_supervisor
        self.provider_sessions = provider_sessions
        self.provider_chat_catalog = provider_chat_catalog
        self.task_frame_lineage = task_frame_lineage or TaskFrameLineageStore(
            self.database_path
        )
        self.terminal_claim_callback = terminal_claim_callback
        self.terminal_callback = terminal_callback
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_anchor_transport_delivery (
                    delivery_id TEXT PRIMARY KEY,
                    delegation_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    origin_session_anchor_ref TEXT NOT NULL,
                    target_session_anchor_ref TEXT NOT NULL,
                    task_frame_ref TEXT NOT NULL DEFAULT '',
                    origin_chat_key TEXT NOT NULL,
                    target_chat_key TEXT NOT NULL,
                    target_reply_message_id TEXT,
                    origin_result_message_id TEXT,
                    state TEXT NOT NULL,
                    terminal_result_json TEXT NOT NULL DEFAULT '{}',
                    failure_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def deliver(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Persist and submit one bounded delegation to its exact target chat."""

        delegation_id = self._text(record.get("delegation_id"), "delegation_id")
        project_id = self._text(record.get("project_id"), "project_id")
        request = record.get("request")
        if not isinstance(request, Mapping):
            raise SessionAnchorTransportError(
                "CONDUCTOR_DELEGATION_INVALID", "delegation request is unavailable"
            )
        origin_ref = self._text(
            request.get("origin_session_anchor_ref"), "origin_session_anchor_ref"
        )
        target_ref = self._text(
            request.get("target_session_anchor_ref"), "target_session_anchor_ref"
        )
        summary = self._text(request.get("summary"), "summary")
        idempotency_key = self._text(request.get("idempotency_key"), "idempotency_key")
        delivery_id = "session_delivery_" + hashlib.sha256(
            _json(
                {
                    "delegation_id": delegation_id,
                    "origin": origin_ref,
                    "target": target_ref,
                    "idempotency_key": idempotency_key,
                }
            ).encode("utf-8")
        ).hexdigest()[:24]
        task_frame_ref = "session-anchor-delegation:" + delivery_id

        with self._lock:
            existing = self._get_delivery(delegation_id)
            if existing is not None:
                if (
                    existing["origin_session_anchor_ref"] != origin_ref
                    or existing["target_session_anchor_ref"] != target_ref
                    or existing["idempotency_key"] != idempotency_key
                ):
                    raise SessionAnchorTransportError(
                        "SESSION_ANCHOR_DELIVERY_IDEMPOTENCY_CONFLICT",
                        "delegation id already has another Session Anchor delivery",
                    )
                return self._progress(existing)

            origin = self._resolve_session(origin_ref, project_id, "ORIGIN")
            target = self._resolve_session(target_ref, project_id, "TARGET")
            try:
                self.task_frame_lineage.create_task_frame(
                    frame_ref=task_frame_ref,
                    origin_session_anchor_ref=origin_ref,
                    target_session_anchor_ref=target_ref,
                )
            except TaskFrameLineageError as error:
                raise SessionAnchorTransportError(error.code, error.detail) from error
            requested_provider = str(request.get("provider") or "AUTO").strip().upper()
            if requested_provider != "AUTO" and requested_provider != target["provider"]:
                raise SessionAnchorTransportError(
                    "TARGET_SESSION_PROVIDER_MISMATCH",
                    "delegation provider does not match the target Session Anchor",
                )
            origin_chat_key = self._resolve_chat(origin, origin_ref, "ORIGIN")
            claimed_origin_chat_key = self._text(
                request.get("origin_session_chat_key"), "origin_session_chat_key"
            )
            if claimed_origin_chat_key != origin_chat_key:
                raise SessionAnchorTransportError(
                    "ORIGIN_SESSION_CHAT_MISMATCH",
                    "delegation origin does not match the selected provider chat",
                )
            target_chat_key = self._resolve_chat(target, target_ref, "TARGET")
            now = _now()
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO session_anchor_transport_delivery(
                        delivery_id, delegation_id, idempotency_key, project_id,
                        origin_session_anchor_ref, target_session_anchor_ref,
                        task_frame_ref, origin_chat_key, target_chat_key, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PREPARING', ?, ?)
                    """,
                    (
                        delivery_id,
                        delegation_id,
                        idempotency_key,
                        project_id,
                        origin_ref,
                        target_ref,
                        task_frame_ref,
                        origin_chat_key,
                        target_chat_key,
                        now,
                        now,
                    ),
                )
            body = (
                f"[Universe internal cross-session delegation {delegation_id}]\n"
                f"Origin Session Anchor: {origin_ref}\n\n{summary}"
            )
            try:
                accepted = self.provider_sessions.submit(
                    target_chat_key,
                    {
                        "body": body[:12000],
                        "idempotency_key": "session-anchor-delegation:" + delivery_id,
                    },
                    on_accepted=lambda reply: self._record_target_accepted(
                        delivery_id, reply
                    ),
                    on_terminal=lambda reply: self.record_terminal(
                        delivery_id,
                        {
                            "reply_message_id": reply.get("message_id"),
                            "state": reply.get("state"),
                            "body": reply.get("body"),
                        },
                    ),
                )
                reply = accepted.get("reply") if isinstance(accepted, Mapping) else None
                reply_id = self._text(
                    reply.get("message_id") if isinstance(reply, Mapping) else None,
                    "target reply message_id",
                )
            except Exception as error:  # provider boundary is normalized below
                self._fail(delivery_id, getattr(error, "code", "TARGET_SESSION_TRANSPORT_UNAVAILABLE"))
                raise SessionAnchorTransportError(
                    str(getattr(error, "code", "TARGET_SESSION_TRANSPORT_UNAVAILABLE")),
                    "target Provider Session transport is unavailable",
                ) from error
            persisted = self._require_delivery(delivery_id)
            if persisted["target_reply_message_id"] != reply_id:
                raise SessionAnchorTransportError(
                    "TARGET_SESSION_RESULT_MISMATCH",
                    "accepted target reply does not match the durable delivery",
                )
            return self._progress(self._require_delivery(delivery_id))

    def _record_target_accepted(
        self, delivery_id: str, reply: Mapping[str, Any]
    ) -> None:
        """Durably bind the exact reply before its provider thread can start."""

        reply_id = self._text(reply.get("message_id"), "target reply message_id")
        delivery = self._require_delivery(delivery_id)
        if delivery["state"] == "TARGET_ACCEPTED":
            if delivery["target_reply_message_id"] != reply_id:
                raise SessionAnchorTransportError(
                    "TARGET_SESSION_RESULT_MISMATCH",
                    "target delivery already names another reply",
                )
            return
        if delivery["state"] != "PREPARING":
            raise SessionAnchorTransportError(
                "SESSION_ANCHOR_DELIVERY_STATE_INVALID",
                "target reply cannot be attached in the current delivery state",
            )
        self._update(
            delivery_id,
            state="TARGET_ACCEPTED",
            target_reply_message_id=reply_id,
        )

    def _resolve_session(
        self, session_anchor_ref: str, project_id: str, side: str
    ) -> dict[str, Any]:
        sessions = self.session_supervisor.list_sessions(include_hidden=True)
        matches = [
            dict(session)
            for session in sessions
            if str(session.get("session_anchor_ref") or "") == session_anchor_ref
        ]
        if not matches:
            raise SessionAnchorTransportError(
                f"{side}_SESSION_ANCHOR_NOT_FOUND",
                f"{side.lower()} Session Anchor does not exist",
            )
        if len(matches) != 1:
            raise SessionAnchorTransportError(
                f"{side}_SESSION_ANCHOR_AMBIGUOUS",
                f"{side.lower()} Session Anchor is ambiguous",
            )
        session = matches[0]
        if str(session.get("current_project_id") or session.get("project_id") or "") != project_id:
            raise SessionAnchorTransportError(
                f"{side}_SESSION_PROJECT_MISMATCH",
                f"{side.lower()} Session Anchor belongs to another Project",
            )
        if (
            str(session.get("currentness") or "UNKNOWN").upper() != "CURRENT"
            or str(session.get("state") or "UNKNOWN").upper()
            not in {"LIVE", "STARTING", "REGISTERED"}
        ):
            raise SessionAnchorTransportError(
                f"{side}_SESSION_ANCHOR_STALE",
                f"{side.lower()} Session Anchor is not current and available",
            )
        provider = str(session.get("provider") or "").upper()
        if provider not in {"CODEX", "CLAUDE", "GROK"} or not str(
            session.get("provider_session_ref") or ""
        ).strip():
            raise SessionAnchorTransportError(
                f"{side}_SESSION_TRANSPORT_UNAVAILABLE",
                f"{side.lower()} Session Anchor has no provider session transport",
            )
        session["provider"] = provider
        return session

    def _resolve_chat(
        self, session: Mapping[str, Any], session_anchor_ref: str, side: str
    ) -> str:
        catalog = self.provider_chat_catalog()
        rooms = catalog.get("rooms") if isinstance(catalog, Mapping) else None
        if not isinstance(rooms, list):
            raise SessionAnchorTransportError(
                f"{side}_SESSION_TRANSPORT_UNAVAILABLE",
                "provider chat catalog is unavailable",
            )
        matches = []
        for room in rooms:
            if not isinstance(room, Mapping):
                continue
            binding = room.get("binding")
            if not isinstance(binding, Mapping):
                continue
            chat_key = str(binding.get("vendor_session_chat_key") or room.get("chat_key") or "")
            if (
                binding.get("session_anchor_ref") == session_anchor_ref
                and str(room.get("provider") or "").upper() == session["provider"]
                and str(room.get("session_kind") or "CHAT").upper() != "WORKER"
                and str(room.get("identity_state") or "UNKNOWN").upper() == "VERIFIED"
                and chat_key == str(room.get("chat_key") or "")
            ):
                matches.append(chat_key)
        if not matches:
            raise SessionAnchorTransportError(
                f"{side}_SESSION_TRANSPORT_UNAVAILABLE",
                f"{side.lower()} Session Anchor has no exact verified vendor chat key",
            )
        if len(set(matches)) != 1 or len(matches) != 1:
            raise SessionAnchorTransportError(
                f"{side}_SESSION_TRANSPORT_AMBIGUOUS",
                f"{side.lower()} Session Anchor resolves to multiple vendor chats",
            )
        return matches[0]

    def record_terminal(self, delivery_id: Any, terminal: Mapping[str, Any]) -> dict[str, Any]:
        """Accept one provider-originated terminal callback for a delivery.

        Provider adapters call this only after receiving their native terminal
        event.  The transport does not poll transcript/session snapshots.
        """

        normalized_delivery_id = self._text(delivery_id, "delivery_id")
        if not isinstance(terminal, Mapping):
            raise SessionAnchorTransportError(
                "SESSION_ANCHOR_TERMINAL_INVALID", "terminal callback must be an object"
            )
        delivery = self._require_delivery(normalized_delivery_id)
        if delivery["state"] in {
            "PROCESSING_TERMINAL",
            "ORIGIN_RESULT_ACCEPTED",
            "FAILED",
            "CANCELLED",
        }:
            return self._progress(delivery)
        if delivery["state"] != "TARGET_ACCEPTED":
            raise SessionAnchorTransportError(
                "SESSION_ANCHOR_TERMINAL_STATE_INVALID",
                "delivery is not awaiting a target terminal callback",
            )
        reply_id = self._text(terminal.get("reply_message_id"), "reply_message_id")
        if reply_id != delivery["target_reply_message_id"]:
            raise SessionAnchorTransportError(
                "TARGET_SESSION_RESULT_MISMATCH",
                "terminal callback does not belong to the exact target delivery",
            )
        if not self._claim_terminal(normalized_delivery_id, reply_id):
            return self._progress(self._require_delivery(normalized_delivery_id))
        delivery = self._require_delivery(normalized_delivery_id)
        state = self._text(terminal.get("state"), "state").upper()
        if state != "COMPLETED":
            self._terminal_failure(normalized_delivery_id, "TARGET_SESSION_RESULT_" + state)
            return self._progress(self._require_delivery(normalized_delivery_id))
        result_text = self._text(terminal.get("body"), "body")
        if self.terminal_claim_callback is not None:
            try:
                permitted = bool(
                    self.terminal_claim_callback(str(delivery["delegation_id"]))
                )
            except Exception as error:
                self._terminal_failure(
                    normalized_delivery_id,
                    getattr(error, "code", "TERMINAL_RESULT_CLAIM_FAILED"),
                )
                return self._progress(self._require_delivery(normalized_delivery_id))
            if not permitted:
                self._update_if_state(
                    normalized_delivery_id,
                    "PROCESSING_TERMINAL",
                    state="CANCELLED",
                )
                return self._progress(self._require_delivery(normalized_delivery_id))
        result_ref = "session-anchor-result:" + normalized_delivery_id
        result_event = {
            "delegation_id": delivery["delegation_id"],
            "result_summary": result_text[:12000],
            "result_ref": result_ref,
            "task_frame_ref": delivery["task_frame_ref"],
            "origin_session_anchor_ref": delivery["origin_session_anchor_ref"],
            "target_session_anchor_ref": delivery["target_session_anchor_ref"],
        }
        try:
            self.task_frame_lineage.attach_result(
                result_ref=result_ref,
                frame_ref=delivery["task_frame_ref"],
                origin_session_anchor_ref=delivery["origin_session_anchor_ref"],
                result={
                    "summary": result_text[:12000],
                    "target_session_anchor_ref": delivery["target_session_anchor_ref"],
                    "delivery_id": normalized_delivery_id,
                },
            )
            origin = self.provider_sessions.submit(
                delivery["origin_chat_key"],
                {
                    "body": (
                        "[Universe internal cross-session delegation result "
                        + delivery["delegation_id"]
                        + "]\nTarget Session Anchor: "
                        + delivery["target_session_anchor_ref"]
                        + "\n\n"
                        + result_text
                    )[:12000],
                    "idempotency_key": "session-anchor-result:" + normalized_delivery_id,
                },
            )
            origin_reply = origin.get("reply") if isinstance(origin, Mapping) else None
            origin_reply_id = self._text(
                origin_reply.get("message_id") if isinstance(origin_reply, Mapping) else None,
                "origin result message_id",
            )
        except Exception as error:  # source route must not silently degrade
            self._terminal_failure(
                normalized_delivery_id,
                getattr(error, "code", "ORIGIN_SESSION_TRANSPORT_UNAVAILABLE"),
            )
            return self._progress(self._require_delivery(normalized_delivery_id))
        result_event["origin_result_message_id"] = origin_reply_id
        updated = self._update_if_state(
            normalized_delivery_id,
            "PROCESSING_TERMINAL",
            state="ORIGIN_RESULT_ACCEPTED",
            origin_result_message_id=origin_reply_id,
            terminal_result=result_event,
        )
        if not updated:
            return self._progress(self._require_delivery(normalized_delivery_id))
        if self.terminal_callback is not None:
            try:
                self.terminal_callback({"delivery_id": normalized_delivery_id, **result_event})
            except Exception as error:  # durable delivery remains visible
                with self._connection() as connection:
                    connection.execute(
                        """
                        UPDATE session_anchor_transport_delivery
                        SET failure_code = ?, updated_at = ?
                        WHERE delivery_id = ? AND state = 'ORIGIN_RESULT_ACCEPTED'
                        """,
                        (
                            str(getattr(error, "code", "ORIGIN_RESULT_CALLBACK_FAILED"))[:160],
                            _now(),
                            normalized_delivery_id,
                        ),
                    )
        return self._progress(self._require_delivery(normalized_delivery_id))

    def _terminal_failure(self, delivery_id: str, code: Any) -> None:
        delivery = self._require_delivery(delivery_id)
        self._fail(delivery_id, str(code or "TARGET_SESSION_TRANSPORT_UNAVAILABLE"))
        if self.terminal_callback is not None:
            try:
                self.terminal_callback(
                    {
                        "delivery_id": delivery_id,
                        "delegation_id": delivery["delegation_id"],
                        "failure_code": str(code or "TARGET_SESSION_TRANSPORT_UNAVAILABLE"),
                    }
                )
            except Exception:
                return

    def _claim_terminal(self, delivery_id: str, reply_id: str) -> bool:
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE session_anchor_transport_delivery
                SET state = 'PROCESSING_TERMINAL', updated_at = ?
                WHERE delivery_id = ? AND state = 'TARGET_ACCEPTED'
                  AND target_reply_message_id = ?
                """,
                (_now(), delivery_id, reply_id),
            )
        return updated.rowcount == 1

    def _get_delivery(self, delegation_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM session_anchor_transport_delivery WHERE delegation_id = ?",
                (delegation_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def _require_delivery(self, delivery_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM session_anchor_transport_delivery WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            raise SessionAnchorTransportError("SESSION_ANCHOR_DELIVERY_NOT_FOUND", "delivery is unavailable")
        return self._row(row)

    def _update(self, delivery_id: str, *, state: str, **updates: Any) -> None:
        values = {"state": state, "updated_at": _now()}
        if "target_reply_message_id" in updates:
            values["target_reply_message_id"] = updates["target_reply_message_id"]
        if "origin_result_message_id" in updates:
            values["origin_result_message_id"] = updates["origin_result_message_id"]
        if "terminal_result" in updates:
            values["terminal_result_json"] = _json(updates["terminal_result"])
        assignments = ", ".join(f"{field} = ?" for field in values)
        with self._connection() as connection:
            connection.execute(
                f"UPDATE session_anchor_transport_delivery SET {assignments} WHERE delivery_id = ?",
                (*values.values(), delivery_id),
            )

    def _update_if_state(
        self, delivery_id: str, expected_state: str, *, state: str, **updates: Any
    ) -> bool:
        values = {"state": state, "updated_at": _now()}
        if "origin_result_message_id" in updates:
            values["origin_result_message_id"] = updates["origin_result_message_id"]
        if "terminal_result" in updates:
            values["terminal_result_json"] = _json(updates["terminal_result"])
        assignments = ", ".join(f"{field} = ?" for field in values)
        with self._connection() as connection:
            updated = connection.execute(
                f"UPDATE session_anchor_transport_delivery SET {assignments} "
                "WHERE delivery_id = ? AND state = ?",
                (*values.values(), delivery_id, expected_state),
            )
        return updated.rowcount == 1

    def _fail(self, delivery_id: str, code: Any) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE session_anchor_transport_delivery
                SET state = 'FAILED', failure_code = ?, updated_at = ?
                WHERE delivery_id = ?
                  AND state IN ('PREPARING', 'TARGET_ACCEPTED', 'PROCESSING_TERMINAL')
                """,
                (str(code)[:160], _now(), delivery_id),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["terminal_result"] = json.loads(result.pop("terminal_result_json"))
        return result

    @staticmethod
    def _progress(delivery: Mapping[str, Any]) -> dict[str, Any]:
        state = str(delivery["state"])
        return {
            "progress": {
                "summary": "Cross-session delegation is routed to the exact target Session Anchor.",
                "step": (
                    "WAITING_FOR_TARGET_SESSION_RESULT"
                    if state in {"PREPARING", "TARGET_ACCEPTED", "PROCESSING_TERMINAL"}
                    else state
                ),
                "session_anchor_delivery_id": delivery["delivery_id"],
                "origin_session_anchor_ref": delivery["origin_session_anchor_ref"],
                "target_session_anchor_ref": delivery["target_session_anchor_ref"],
                "room_queue_used": False,
            }
        }

    @staticmethod
    def _text(value: Any, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise SessionAnchorTransportError(
                "SESSION_ANCHOR_TRANSPORT_REQUEST_INVALID", f"{field} is required"
            )
        return text
