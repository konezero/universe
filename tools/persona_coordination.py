"""Durable, typed Master/Worker coordination for Persona conflicts.

The coordination record is deliberately separate from execution authority.  It
pins the exact node/Todo/Task Frame/file scope and participant Session Anchors,
while the existing assignment and Task Frame gateways remain the only routes
that can change ownership or execute work.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping


SCHEMA = "universe.persona-coordination.v1"
STATES = frozenset({"OPEN", "PROPOSED", "NEEDS_REVISION", "ACCEPTED", "ESCALATED", "EXPIRED", "CANCELLED"})
OUTCOMES = frozenset({"AGREE", "NEEDS_REVISION", "UNRESOLVED", "REJECTED"})


class PersonaCoordinationError(ValueError):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = int(status)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _text(value: Any, field: str, *, required: bool = True, limit: int = 256) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PersonaCoordinationError("PERSONA_COORDINATION_FIELD_REQUIRED", f"{field} is required")
    if len(text) > limit:
        raise PersonaCoordinationError("PERSONA_COORDINATION_FIELD_INVALID", f"{field} is too long")
    return text


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class PersonaCoordinationStore:
    """SQLite-backed coordination state with idempotent CAS transitions."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS persona_coordination (
                    coordination_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    node_ref TEXT NOT NULL,
                    todo_id TEXT,
                    task_frame_id TEXT,
                    file_scope_json TEXT NOT NULL,
                    participant_anchors_json TEXT NOT NULL,
                    initiator_anchor_ref TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    proposal_version INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL,
                    responses_json TEXT NOT NULL DEFAULT '{}',
                    deliveries_json TEXT NOT NULL DEFAULT '[]',
                    next_action TEXT,
                    escalation_json TEXT,
                    request_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, request_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS persona_coordination_project_time "
                "ON persona_coordination(project_id, updated_at, coordination_id)"
            )

    @staticmethod
    def _row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "coordination_id": row["coordination_id"],
            "project_id": row["project_id"],
            "node_ref": row["node_ref"],
            "todo_id": row["todo_id"],
            "task_frame_id": row["task_frame_id"],
            "file_scope": _load(row["file_scope_json"], []),
            "participant_anchors": _load(row["participant_anchors_json"], []),
            "initiator_anchor_ref": row["initiator_anchor_ref"],
            "evidence": _load(row["evidence_json"], []),
            "proposal": _load(row["proposal_json"], {}),
            "proposal_version": int(row["proposal_version"]),
            "state": row["state"],
            "responses": _load(row["responses_json"], {}),
            "deliveries": _load(row["deliveries_json"], []),
            "next_action": row["next_action"],
            "escalation": _load(row["escalation_json"], None),
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _get(self, connection: sqlite3.Connection, coordination_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM persona_coordination WHERE coordination_id = ?", (coordination_id,)
        ).fetchone()
        if row is None:
            raise PersonaCoordinationError("PERSONA_COORDINATION_NOT_FOUND", "coordination record does not exist", 404)
        return row

    def create(self, value: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        project_id = _text(value.get("project_id"), "project_id", limit=128)
        node_ref = _text(value.get("node_ref"), "node_ref", limit=128)
        initiator = _text(value.get("initiator_anchor_ref"), "initiator_anchor_ref", limit=256)
        request_id = _text(value.get("request_id"), "request_id", limit=256)
        participants = value.get("participant_anchors")
        if not isinstance(participants, list) or not participants:
            raise PersonaCoordinationError("PERSONA_COORDINATION_PARTICIPANTS_REQUIRED", "participant_anchors must be a non-empty list")
        participants = list(dict.fromkeys(_text(item, "participant_anchor", limit=256) for item in participants))
        if initiator not in participants:
            participants.insert(0, initiator)
        file_scope = value.get("file_scope")
        if not isinstance(file_scope, list) or not file_scope or any(not isinstance(item, str) or not item.strip() for item in file_scope):
            raise PersonaCoordinationError("PERSONA_COORDINATION_SCOPE_REQUIRED", "file_scope must contain at least one path")
        file_scope = [item.strip() for item in file_scope]
        evidence = value.get("evidence") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaCoordinationError("PERSONA_COORDINATION_EVIDENCE_INVALID", "evidence must be a list of non-empty references")
        proposal = value.get("proposal")
        if not isinstance(proposal, Mapping) or not proposal:
            raise PersonaCoordinationError("PERSONA_COORDINATION_PROPOSAL_REQUIRED", "proposal must be a non-empty object")
        material = {
            "project_id": project_id,
            "node_ref": node_ref,
            "todo_id": _text(value.get("todo_id"), "todo_id", required=False, limit=128) or None,
            "task_frame_id": _text(value.get("task_frame_id"), "task_frame_id", required=False, limit=128) or None,
            "file_scope": file_scope,
            "participant_anchors": participants,
            "initiator_anchor_ref": initiator,
            "evidence": evidence,
            "proposal": dict(proposal),
            "request_id": request_id,
        }
        digest = _digest(material)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM persona_coordination WHERE project_id = ? AND request_id = ?",
                (project_id, request_id),
            ).fetchone()
            if existing is not None:
                if _digest(material) != _digest({
                    "project_id": existing["project_id"], "node_ref": existing["node_ref"],
                    "todo_id": existing["todo_id"], "task_frame_id": existing["task_frame_id"],
                    "file_scope": _load(existing["file_scope_json"], []),
                    "participant_anchors": _load(existing["participant_anchors_json"], []),
                    "initiator_anchor_ref": existing["initiator_anchor_ref"],
                    "evidence": _load(existing["evidence_json"], []),
                    "proposal": _load(existing["proposal_json"], {}),
                    "request_id": existing["request_id"],
                }):
                    raise PersonaCoordinationError("PERSONA_COORDINATION_IDEMPOTENCY_CONFLICT", "request_id already refers to different coordination content", 409)
                return self._row(existing), False
            coordination_id = "coord_" + uuid.uuid4().hex[:24]
            now = _now()
            connection.execute(
                """INSERT INTO persona_coordination(
                    coordination_id, project_id, node_ref, todo_id, task_frame_id,
                    file_scope_json, participant_anchors_json, initiator_anchor_ref,
                    evidence_json, proposal_json, proposal_version, state,
                    responses_json, deliveries_json, next_action, escalation_json,
                    request_id, request_digest, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'OPEN', '{}', '[]', ?, NULL, ?, ?, ?, ?)""",
                (coordination_id, project_id, node_ref, material["todo_id"], material["task_frame_id"],
                 _json(file_scope), _json(participants), initiator, _json(evidence), _json(dict(proposal)),
                 "await participant proposal and typed responses", request_id, digest, now, now),
            )
            return self._row(self._get(connection, coordination_id)), True

    def propose(self, value: Mapping[str, Any]) -> dict[str, Any]:
        coordination_id = _text(value.get("coordination_id"), "coordination_id", limit=128)
        proposer = _text(value.get("proposer_anchor_ref"), "proposer_anchor_ref", limit=256)
        request_id = _text(value.get("request_id"), "request_id", limit=256)
        expected = value.get("expected_proposal_version")
        if type(expected) is not int or expected < 1:
            raise PersonaCoordinationError("PERSONA_COORDINATION_VERSION_REQUIRED", "expected_proposal_version must be a positive integer")
        proposal = value.get("proposal")
        if not isinstance(proposal, Mapping) or not proposal:
            raise PersonaCoordinationError("PERSONA_COORDINATION_PROPOSAL_REQUIRED", "proposal must be a non-empty object")
        evidence = value.get("evidence") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaCoordinationError("PERSONA_COORDINATION_EVIDENCE_INVALID", "evidence must be a list of non-empty references")
        with self._connection() as connection:
            row = self._get(connection, coordination_id)
            if proposer not in _load(row["participant_anchors_json"], []):
                raise PersonaCoordinationError("PERSONA_COORDINATION_PARTICIPANT_REQUIRED", "proposer is not a participant", 409)
            history = _load(row["responses_json"], {})
            request_key = "proposal:" + request_id
            if request_key in history:
                return self._row(row)
            if int(row["proposal_version"]) != expected:
                raise PersonaCoordinationError("PERSONA_COORDINATION_VERSION_CONFLICT", "proposal version changed", 409)
            next_version = expected + 1
            history[request_key] = {"by": proposer, "version": next_version, "evidence": list(evidence), "at": _now()}
            now = _now()
            cursor = connection.execute(
                """UPDATE persona_coordination SET proposal_json = ?, proposal_version = ?,
                   state = 'PROPOSED', responses_json = ?, next_action = ?, updated_at = ?
                   WHERE coordination_id = ? AND proposal_version = ? AND state IN ('OPEN','PROPOSED','NEEDS_REVISION')""",
                (_json(dict(proposal)), next_version, _json(history), "await all participants to agree or report NEEDS_REVISION", now, coordination_id, expected),
            )
            if cursor.rowcount != 1:
                raise PersonaCoordinationError("PERSONA_COORDINATION_VERSION_CONFLICT", "coordination changed before proposal was recorded", 409)
            return self._row(self._get(connection, coordination_id))

    def respond(self, value: Mapping[str, Any]) -> dict[str, Any]:
        coordination_id = _text(value.get("coordination_id"), "coordination_id", limit=128)
        responder = _text(value.get("responder_anchor_ref"), "responder_anchor_ref", limit=256)
        request_id = _text(value.get("request_id"), "request_id", limit=256)
        expected = value.get("expected_proposal_version")
        if type(expected) is not int or expected < 1:
            raise PersonaCoordinationError("PERSONA_COORDINATION_VERSION_REQUIRED", "expected_proposal_version must be a positive integer")
        outcome = _text(value.get("outcome"), "outcome", limit=32).upper()
        if outcome not in OUTCOMES:
            raise PersonaCoordinationError("PERSONA_COORDINATION_OUTCOME_INVALID", "outcome must be AGREE, NEEDS_REVISION, UNRESOLVED or REJECTED")
        evidence = value.get("evidence") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaCoordinationError("PERSONA_COORDINATION_EVIDENCE_INVALID", "evidence must be a list of non-empty references")
        with self._connection() as connection:
            row = self._get(connection, coordination_id)
            participants = _load(row["participant_anchors_json"], [])
            if responder not in participants:
                raise PersonaCoordinationError("PERSONA_COORDINATION_PARTICIPANT_REQUIRED", "responder is not a participant", 409)
            if int(row["proposal_version"]) != expected:
                raise PersonaCoordinationError("PERSONA_COORDINATION_VERSION_CONFLICT", "proposal version changed", 409)
            responses = _load(row["responses_json"], {})
            key = "response:" + request_id
            if key in responses:
                return self._row(row)
            responses[key] = {"anchor_ref": responder, "outcome": outcome, "evidence": list(evidence), "proposal_version": expected, "at": _now()}
            participant_outcomes = {
                item["anchor_ref"]: item["outcome"]
                for item in responses.values()
                if isinstance(item, Mapping) and item.get("anchor_ref") in participants and int(item.get("proposal_version") or 0) == expected
            }
            state = "PROPOSED"
            next_action = "await all participant responses"
            escalation = None
            if outcome == "NEEDS_REVISION":
                state = "NEEDS_REVISION"
                next_action = "propose a revised exact scope; do not mutate assignments"
            elif outcome in {"UNRESOLVED", "REJECTED"}:
                state = "ESCALATED"
                next_action = "Conductor/user decision required for goal, scope, or authority change"
                escalation = {"reason": outcome, "by": responder, "proposal_version": expected, "evidence": list(evidence)}
            elif all(participant_outcomes.get(anchor) == "AGREE" for anchor in participants):
                state = "ACCEPTED"
                next_action = "continue only within the recorded node/Todo/Task Frame/file scope"
            now = _now()
            cursor = connection.execute(
                """UPDATE persona_coordination SET responses_json = ?, state = ?,
                   next_action = ?, escalation_json = ?, updated_at = ?
                   WHERE coordination_id = ? AND proposal_version = ?""",
                (_json(responses), state, next_action, _json(escalation) if escalation else None, now, coordination_id, expected),
            )
            if cursor.rowcount != 1:
                raise PersonaCoordinationError("PERSONA_COORDINATION_VERSION_CONFLICT", "coordination changed before response was recorded", 409)
            return self._row(self._get(connection, coordination_id))

    def record_deliveries(self, coordination_id: str, deliveries: list[Mapping[str, Any]]) -> dict[str, Any]:
        with self._connection() as connection:
            row = self._get(connection, coordination_id)
            existing = _load(row["deliveries_json"], [])
            by_key = {str(item.get("message_id") or item.get("anchor_ref") or uuid.uuid4().hex): item for item in existing if isinstance(item, Mapping)}
            for item in deliveries:
                if isinstance(item, Mapping):
                    key = str(item.get("message_id") or item.get("anchor_ref") or uuid.uuid4().hex)
                    by_key[key] = dict(item)
            connection.execute(
                "UPDATE persona_coordination SET deliveries_json = ?, updated_at = ? WHERE coordination_id = ?",
                (_json(list(by_key.values())), _now(), coordination_id),
            )
            return self._row(self._get(connection, coordination_id))

    def get(self, coordination_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            return self._row(self._get(connection, _text(coordination_id, "coordination_id", limit=128)))

    def list(self, project_id: str, *, node_ref: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if node_ref:
                rows = connection.execute("SELECT * FROM persona_coordination WHERE project_id = ? AND node_ref = ? ORDER BY updated_at DESC, coordination_id DESC", (project_id, node_ref)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM persona_coordination WHERE project_id = ? ORDER BY updated_at DESC, coordination_id DESC", (project_id,)).fetchall()
            return [self._row(row) for row in rows]
