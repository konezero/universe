"""Durable, bounded automation runs for an assigned Conductor persona.

This module deliberately stops at the server-owned boundary.  It records a
run, its lease, evidence-backed provider decisions, Master queue dispatches,
and result reviews.  It never interprets a persona body as executable code or
chooses a provider.  The HTTP server supplies the existing Task Frame and
queue callbacks and validates the Session Anchor before a run is started.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Callable, Mapping


SCHEMA = "universe.persona-automation.v1"
RUN_SCHEMA = "universe.persona-automation-run.v1"
RUN_STATES = frozenset({"RUNNING", "WAITING", "PAUSED", "STOPPED", "COMPLETED", "FAILED"})
DECISION_KINDS = frozenset({"EXECUTE", "MEETING", "ESCALATE", "WAIT"})
REVIEW_OUTCOMES = frozenset({"PASS", "NEEDS_REVISION", "BLOCKED", "NOT_RUN"})
EXECUTION_MODES = frozenset({"MASTER_DIRECT", "WORKER_REVIEW"})
WORKER_RESULT_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "BLOCKED", "NOT_RUN"})

# surface()'s node_ref default: a caller must be able to ask for
# node_ref IS NULL explicitly (a project-wide Conductor run) without that
# request being indistinguishable from "no node_ref filter at all" -- a
# plain `None` default cannot tell those apart. See surface() below.
_NODE_REF_UNSET = object()


class PersonaAutomationError(ValueError):
    """A typed, safe-to-expose domain error from the automation store."""

    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status = int(status)
        super().__init__(detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PersonaAutomationError("PERSONA_AUTOMATION_FIELD_REQUIRED", f"{field} is required")
    return text


def _positive_int(value: Any, field: str, default: int | None = None) -> int | None:
    if value is None and default is not None:
        return default
    if type(value) is not int or value < 1:
        raise PersonaAutomationError("PERSONA_AUTOMATION_INTEGER_INVALID", f"{field} must be a positive integer")
    return int(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def resolve_default_reviewer_persona_id(
    connection: sqlite3.Connection,
    *,
    master_persona_id: str,
    worker_config: Mapping[str, Any] | None = None,
) -> str:
    """Choose the Reviewer persona for an automatic Master-result review.

    Explicit ``worker_config.persona_id`` (or ``reviewer_persona_id``) wins.
    Otherwise select an ACTIVE project persona whose title identifies an
    independent Reviewer (prefer exact ``독립 Reviewer``), never the Master
    run's own ``persona_id``.
    """

    config = worker_config if isinstance(worker_config, Mapping) else {}
    explicit = str(
        config.get("reviewer_persona_id") or config.get("persona_id") or ""
    ).strip()
    if explicit:
        return explicit

    master_id = str(master_persona_id or "").strip()
    try:
        rows = connection.execute(
            "SELECT persona_id, title FROM persona_definition "
            "WHERE state = 'ACTIVE' ORDER BY updated_at DESC, persona_id"
        ).fetchall()
    except sqlite3.Error as error:
        raise PersonaAutomationError(
            "PERSONA_AUTOMATION_REVIEWER_PERSONA_REQUIRED",
            "automatic Reviewer requires an independent Reviewer persona "
            "(persona_definition is unavailable)",
            409,
        ) from error

    preferred: str | None = None
    fallback: str | None = None
    for row in rows:
        persona_id = str(row["persona_id"] or "").strip()
        title = str(row["title"] or "").strip()
        if not persona_id or persona_id == master_id:
            continue
        if title == "독립 Reviewer":
            preferred = persona_id
            break
        title_key = title.casefold()
        if "reviewer" in title_key or "리뷰어" in title or "review" in title_key:
            # Skip implementer/master-shaped titles even if they mention review.
            if any(
                marker in title_key
                for marker in ("implement", "구현", "master", "마스터", "facilitat", "퍼실리")
            ):
                continue
            if fallback is None:
                fallback = persona_id
    chosen = preferred or fallback
    if not chosen:
        raise PersonaAutomationError(
            "PERSONA_AUTOMATION_REVIEWER_PERSONA_REQUIRED",
            "automatic Reviewer requires an independent Reviewer persona; "
            "worker_config.persona_id was not set and no Reviewer-titled "
            "ACTIVE persona distinct from the Master persona was found",
            409,
        )
    return chosen


class PersonaAutomationStore:
    """SQLite-backed run state with CAS transitions and idempotent events."""

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
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS persona_automation_run (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_anchor_ref TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    persona_revision INTEGER NOT NULL,
                    assignment_revision INTEGER NOT NULL,
                    scope_text TEXT NOT NULL,
                    instruction_text TEXT NOT NULL,
                    goal_ref TEXT,
                    goal_version TEXT,
                    budget_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    cursor_json TEXT NOT NULL,
                    current_assignment_json TEXT,
                    current_meeting_json TEXT,
                    current_decision_json TEXT,
                    current_review_json TEXT,
                    current_worker_json TEXT,
                    current_reviewer_json TEXT,
                    execution_mode TEXT NOT NULL DEFAULT 'MASTER_DIRECT',
                    worker_config_json TEXT NOT NULL DEFAULT '{}',
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    tick_count INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 1,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    stop_reason TEXT,
                    quota_state TEXT NOT NULL DEFAULT 'UNKNOWN',
                    next_condition TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS persona_automation_run_project
                    ON persona_automation_run(project_id, updated_at);
                CREATE INDEX IF NOT EXISTS persona_automation_run_anchor
                    ON persona_automation_run(session_anchor_ref, state);
                CREATE TABLE IF NOT EXISTS persona_automation_event (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES persona_automation_run(run_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS persona_automation_event_run
                    ON persona_automation_event(run_id, created_at);
                CREATE TABLE IF NOT EXISTS persona_automation_decision (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES persona_automation_run(run_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    target_json TEXT,
                    next_condition TEXT,
                    invocation_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, decision_id)
                );
                CREATE TABLE IF NOT EXISTS persona_automation_review (
                    review_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES persona_automation_run(run_id) ON DELETE CASCADE,
                    result_ref TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    acceptance_status TEXT,
                    evidence_refs_json TEXT NOT NULL,
                    note TEXT,
                    next_action TEXT,
                    dispatch_id TEXT,
                    assignment_revision INTEGER,
                    source_message_id TEXT,
                    source_project_id TEXT,
                    source_reply_anchor_ref TEXT,
                    source_reply_terminal_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, result_ref)
                );
                """
            )
            # The automation projection was introduced after some runtime
            # databases had already been created.  Keep those databases
            # readable while adding review provenance without destructive
            # migration or a second source of truth.
            existing_columns = {
                str(item[1])
                for item in connection.execute(
                    "PRAGMA table_info(persona_automation_review)"
                ).fetchall()
            }
            for name, declaration in (
                ("acceptance_status", "TEXT"),
                ("dispatch_id", "TEXT"),
                ("assignment_revision", "INTEGER"),
                ("source_message_id", "TEXT"),
                ("source_project_id", "TEXT"),
                ("source_reply_anchor_ref", "TEXT"),
                ("source_reply_terminal_id", "TEXT"),
            ):
                if name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE persona_automation_review ADD COLUMN {name} {declaration}"
                    )
            decision_columns = {
                str(item[1])
                for item in connection.execute(
                    "PRAGMA table_info(persona_automation_decision)"
                ).fetchall()
            }
            if "invocation_json" not in decision_columns:
                connection.execute(
                    "ALTER TABLE persona_automation_decision ADD COLUMN invocation_json TEXT"
                )
            run_columns = {
                str(item[1])
                for item in connection.execute(
                    "PRAGMA table_info(persona_automation_run)"
                ).fetchall()
            }
            if "node_ref" not in run_columns:
                # NULL = project-wide (CONDUCTOR) scope, unchanged. A
                # non-NULL value is copied from the owning MASTER Anchor's
                # persona assignment at start_run and never re-derived from
                # a later request -- reassigning that Anchor to a different
                # node does not retroactively widen or move an existing run
                # (2026-09-15: node Master automation ownership).
                connection.execute(
                    "ALTER TABLE persona_automation_run ADD COLUMN node_ref TEXT"
                )
            for name, declaration in (
                ("current_worker_json", "TEXT"),
                ("current_reviewer_json", "TEXT"),
                ("execution_mode", "TEXT NOT NULL DEFAULT 'MASTER_DIRECT'"),
                ("worker_config_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if name not in run_columns:
                    connection.execute(
                        f"ALTER TABLE persona_automation_run ADD COLUMN {name} {declaration}"
                    )

    @staticmethod
    def _row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = {
            "run_id": row["run_id"],
            "project_id": row["project_id"],
            "session_anchor_ref": row["session_anchor_ref"],
            "persona_id": row["persona_id"],
            "persona_revision": int(row["persona_revision"]),
            "assignment_revision": int(row["assignment_revision"]),
            "scope": row["scope_text"],
            "instruction": row["instruction_text"],
            "goal_ref": row["goal_ref"],
            "goal_version": row["goal_version"],
            "node_ref": row["node_ref"],
            "budget": _load(row["budget_json"], {}),
            "state": row["state"],
            "cursor": _load(row["cursor_json"], {}),
            "current_assignment": _load(row["current_assignment_json"], None),
            "current_meeting": _load(row["current_meeting_json"], None),
            "current_decision": _load(row["current_decision_json"], None),
            "current_review": _load(row["current_review_json"], None),
            "current_worker": _load(row["current_worker_json"], None),
            "current_reviewer": _load(row["current_reviewer_json"], None),
            "execution_mode": str(row["execution_mode"] or "MASTER_DIRECT"),
            "worker_config": _load(row["worker_config_json"], {}),
            "lease": {"owner": row["lease_owner"], "expires_at": row["lease_expires_at"]},
            "tick_count": int(row["tick_count"]),
            "revision": int(row["revision"]),
            "idempotency_key": row["idempotency_key"],
            "stop_reason": row["stop_reason"],
            "quota_state": row["quota_state"],
            "next_condition": row["next_condition"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        return result

    def _get(self, connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM persona_automation_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise PersonaAutomationError("PERSONA_AUTOMATION_RUN_NOT_FOUND", "automation run does not exist", 404)
        return row

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_id = _text(run_id, "run_id")
        with self._connection() as connection:
            return self._row(self._get(connection, run_id))

    def record_worker_instruction(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Bind one Session Bus instruction to the exact Worker assignment.

        Creating a live Worker Host and delivering its Persona are separate
        operations from sending the bounded task.  This receipt closes that
        gap without widening the assignment: the message id is CAS-bound to
        the current run, dispatch, Worker role, assignment revision and
        Session Anchor.  A retry with the same coordinates is a replay.
        """

        run_id = _text(value.get("run_id"), "run_id")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        worker_role = _text(value.get("worker_role"), "worker_role").upper()
        if worker_role not in {"IMPLEMENTER", "REVIEWER"}:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_WORKER_ROLE_INVALID",
                "worker_role must be IMPLEMENTER or REVIEWER",
            )
        worker_assignment_id = _text(
            value.get("worker_assignment_id"), "worker_assignment_id"
        )
        worker_assignment_revision = _positive_int(
            value.get("worker_assignment_revision"),
            "worker_assignment_revision",
        )
        worker_anchor_ref = _text(
            value.get("worker_anchor_ref"), "worker_anchor_ref"
        )
        message_id = _text(value.get("message_id"), "message_id")
        idempotency_key = _text(
            value.get("idempotency_key"), "idempotency_key"
        )
        payload = {
            "run_id": run_id,
            "dispatch_id": dispatch_id,
            "worker_role": worker_role,
            "worker_assignment_id": worker_assignment_id,
            "worker_assignment_revision": worker_assignment_revision,
            "worker_anchor_ref": worker_anchor_ref,
            "message_id": message_id,
            "idempotency_key": idempotency_key,
        }
        with self._connection() as connection:
            row = self._get(connection, run_id)
            assignment = _load(row["current_assignment_json"], None)
            if (
                not isinstance(assignment, Mapping)
                or str(assignment.get("dispatch_id") or "") != dispatch_id
            ):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_INSTRUCTION_PROVENANCE_MISMATCH",
                    "Worker instruction does not belong to the current dispatch",
                    409,
                )
            current = (
                _load(row["current_worker_json"], None)
                if worker_role == "IMPLEMENTER"
                else _load(row["current_reviewer_json"], None)
            )
            if not isinstance(current, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_WORKER_ASSIGNMENT_REQUIRED",
                    "the exact Worker assignment is required before sending work",
                    409,
                )
            current_assignment = current.get("assignment")
            if not isinstance(current_assignment, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_WORKER_ASSIGNMENT_REQUIRED",
                    "the exact Worker assignment is required before sending work",
                    409,
                )
            expected = {
                "assignment_id": worker_assignment_id,
                "session_anchor_ref": worker_anchor_ref,
                "assignment_revision": worker_assignment_revision,
                "worker_role": worker_role,
                "project_id": row["project_id"],
                "node_ref": row["node_ref"],
                "todo_id": assignment.get("todo_id"),
                "task_frame_id": assignment.get("task_frame_id"),
            }
            for field, expected_value in expected.items():
                if current_assignment.get(field) != expected_value:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_INSTRUCTION_PROVENANCE_MISMATCH",
                        f"Worker instruction does not preserve exact {field} lineage",
                        409,
                    )
            existing_message_id = str(current.get("instruction_message_id") or "")
            if existing_message_id:
                if existing_message_id != message_id:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_INSTRUCTION_CONFLICT",
                        "the assignment already has a different instruction message",
                        409,
                    )
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_WORKER_INSTRUCTION_REPLAYED",
                    "run": self._row(row),
                    "instruction": payload,
                }
            current_payload = {
                **dict(current),
                "instruction_message_id": message_id,
                "instruction_idempotency_key": idempotency_key,
                "instruction_state": "DISPATCHED",
            }
            assignment_payload = {
                **dict(assignment),
                "worker_instruction_message_id": message_id
                if worker_role == "IMPLEMENTER"
                else assignment.get("worker_instruction_message_id"),
                "reviewer_instruction_message_id": message_id
                if worker_role == "REVIEWER"
                else assignment.get("reviewer_instruction_message_id"),
            }
            now = _timestamp()
            if worker_role == "IMPLEMENTER":
                update = (
                    _json(assignment_payload),
                    _json(current_payload),
                    row["current_reviewer_json"],
                )
            else:
                update = (
                    _json(assignment_payload),
                    row["current_worker_json"],
                    _json(current_payload),
                )
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_assignment_json = ?, "
                "current_worker_json = ?, current_reviewer_json = ?, "
                "revision = revision + 1, updated_at = ? "
                "WHERE run_id = ? AND revision = ?",
                (*update[:3], now, run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    "automation run changed while recording Worker instruction",
                    409,
                )
            event, _ = self._event(
                connection,
                run_id,
                "WORKER_INSTRUCTION_POSTED",
                "worker-instruction:" + worker_role + ":" + dispatch_id,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": "PERSONA_AUTOMATION_WORKER_INSTRUCTION_RECORDED",
                "run": self._row(self._get(connection, run_id)),
                "instruction": payload,
                "event": event,
            }

    def mark_worker_instruction_claim_state(
        self,
        *,
        run_id: str,
        worker_role: str,
        message_id: str,
        expected_revision: int,
        claim_state: str = "DISPATCHED",
    ) -> dict[str, Any]:
        """CAS-update the pre-created Bus claim after transport completion.

        A combined native Persona turn creates the typed Bus instruction before
        it queues the Persona text.  The instruction is therefore persisted as
        ``CLAIMED`` until ``complete_instruction_claim`` succeeds.  Keeping
        this marker separate from ``record_worker_instruction`` makes a retry
        safe when the process stops between those two effects: recovery can
        retry the same completion instead of treating an unfinished claim as
        already dispatched.
        """

        run_id = _text(run_id, "run_id")
        worker_role = _text(worker_role, "worker_role").upper()
        if worker_role not in {"IMPLEMENTER", "REVIEWER"}:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_WORKER_ROLE_INVALID",
                "worker_role must be IMPLEMENTER or REVIEWER",
            )
        message_id = _text(message_id, "message_id")
        expected_revision = _positive_int(expected_revision, "expected_revision")
        claim_state = _text(claim_state, "claim_state").upper()
        if claim_state not in {"CLAIMED", "DISPATCHED"}:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_CLAIM_STATE_INVALID",
                "claim_state must be CLAIMED or DISPATCHED",
            )
        column = "current_worker_json" if worker_role == "IMPLEMENTER" else "current_reviewer_json"
        with self._connection() as connection:
            row = self._get(connection, run_id)
            current = _load(row[column], None)
            if not isinstance(current, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_WORKER_ASSIGNMENT_REQUIRED",
                    "the exact Worker assignment is required before updating claim state",
                    409,
                )
            precreated = current.get("precreated_instruction")
            if not isinstance(precreated, Mapping) or str(precreated.get("message_id") or "") != message_id:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_INSTRUCTION_PROVENANCE_MISMATCH",
                    "claim state does not match the pre-created Worker instruction",
                    409,
                )
            if str(precreated.get("claim_state") or "").upper() == claim_state:
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_WORKER_CLAIM_STATE_REPLAYED",
                    "run": self._row(row),
                    "claim_state": claim_state,
                }
            current_payload = {
                **dict(current),
                "precreated_instruction": {
                    **dict(precreated),
                    "claim_state": claim_state,
                },
            }
            now = _timestamp()
            cursor = connection.execute(
                f"UPDATE persona_automation_run SET {column} = ?, revision = revision + 1, updated_at = ? "
                "WHERE run_id = ? AND revision = ?",
                (_json(current_payload), now, run_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    "automation run changed while updating Worker claim state",
                    409,
                )
            return {
                "schema": SCHEMA,
                "status": "PERSONA_AUTOMATION_WORKER_CLAIM_STATE_UPDATED",
                "run": self._row(self._get(connection, run_id)),
                "claim_state": claim_state,
            }

    def record_worker_route_failure(
        self, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Keep a durable diagnostic when a Bus result cannot be correlated."""

        run_id = _text(value.get("run_id"), "run_id")
        result_message_id = _text(
            value.get("result_message_id"), "result_message_id"
        )
        payload = {
            "result_message_id": result_message_id,
            "dispatch_id": _text(value.get("dispatch_id"), "dispatch_id"),
            "worker_role": _text(value.get("worker_role"), "worker_role").upper(),
            "error_code": _text(value.get("error_code"), "error_code"),
            "detail": str(value.get("detail") or "")[:2000],
        }
        with self._connection() as connection:
            self._get(connection, run_id)
            event, created = self._event(
                connection,
                run_id,
                "WORKER_RESULT_ROUTE_FAILED",
                "worker-route-failure:" + result_message_id,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": (
                    "PERSONA_AUTOMATION_WORKER_ROUTE_FAILED"
                    if created
                    else "PERSONA_AUTOMATION_WORKER_ROUTE_FAILURE_REPLAYED"
                ),
                "run": self._row(self._get(connection, run_id)),
                "event": event,
            }

    def _event(self, connection: sqlite3.Connection, run_id: str, event_type: str,
               idempotency_key: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = connection.execute(
            "SELECT * FROM persona_automation_event WHERE run_id = ? AND idempotency_key = ?",
            (run_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            stored = _load(existing["payload_json"], {})
            if _digest(stored) != _digest(payload):
                raise PersonaAutomationError("PERSONA_AUTOMATION_EVENT_IDEMPOTENCY_CONFLICT", "event idempotency key already refers to different content", 409)
            return {"event_id": existing["event_id"], "event_type": existing["event_type"], **stored}, False
        event_id = "persona_event_" + uuid.uuid4().hex[:24]
        now = _timestamp()
        connection.execute(
            "INSERT INTO persona_automation_event(event_id, run_id, event_type, idempotency_key, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, run_id, event_type, idempotency_key, _json(payload), now),
        )
        return {"event_id": event_id, "event_type": event_type, **dict(payload)}, True

    @staticmethod
    def _ensure_assignment(assignment: Mapping[str, Any], project_id: str, anchor: str) -> dict[str, Any]:
        if not isinstance(assignment, Mapping) or assignment.get("state") != "ACTIVE":
            raise PersonaAutomationError("PERSONA_AUTOMATION_ASSIGNMENT_REQUIRED", "an ACTIVE persona assignment is required", 409)
        if str(assignment.get("project_id") or "").strip() != project_id:
            raise PersonaAutomationError("PERSONA_AUTOMATION_ASSIGNMENT_PROJECT_MISMATCH", "persona assignment belongs to another project", 409)
        if str(assignment.get("session_anchor_ref") or "").strip() != anchor:
            raise PersonaAutomationError("PERSONA_AUTOMATION_ASSIGNMENT_ANCHOR_MISMATCH", "persona assignment is bound to another Session Anchor", 409)
        return dict(assignment)

    def start_run(self, value: Mapping[str, Any], assignment: Mapping[str, Any]) -> dict[str, Any]:
        project_id = _text(value.get("project_id"), "project_id")
        anchor = _text(value.get("session_anchor_ref"), "session_anchor_ref")
        assignment = self._ensure_assignment(assignment, project_id, anchor)
        scope = _text(value.get("scope"), "scope")
        instruction = _text(value.get("instruction"), "instruction")
        key = _text(value.get("idempotency_key") or value.get("request_id"), "idempotency_key")
        execution_mode = str(value.get("execution_mode") or "MASTER_DIRECT").strip().upper()
        if execution_mode not in EXECUTION_MODES:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_EXECUTION_MODE_INVALID",
                "execution_mode must be MASTER_DIRECT or WORKER_REVIEW",
            )
        if execution_mode == "WORKER_REVIEW":
            # Worker and Reviewer are persona roles inside a Task Frame the Master
            # launches (persona.automation.launch-frame), not a mode of the run.
            # The server no longer starts or reviews Workers by itself.
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_EXECUTION_MODE_RETIRED",
                "WORKER_REVIEW is retired: the Master launches Worker/Reviewer Task Frames "
                "itself with persona.automation.launch-frame",
                409,
            )
        worker_config = {
            "persona_id": value.get("worker_persona_id"),
            "provider": value.get("worker_provider"),
            "model_ref": value.get("worker_model_ref"),
            "effort": value.get("worker_effort"),
        }
        worker_config = {key: item for key, item in worker_config.items() if item not in (None, "")}
        # NULL = the project-wide CONDUCTOR scope (legacy, unchanged); a real
        # feature_id pins one MASTER's node. Never taken from the request --
        # only the caller's own durable assignment decides this, so a
        # client cannot claim a wider or different node than it owns
        # (2026-09-15: node Master automation ownership).
        node_ref = assignment.get("node_ref")
        payload = {
            "project_id": project_id, "session_anchor_ref": anchor, "scope": scope,
            "instruction": instruction, "goal_ref": value.get("goal_ref"),
            "goal_version": value.get("goal_version"), "budget": value.get("budget") or {},
            "persona_id": assignment.get("persona_id"),
            "persona_revision": assignment.get("persona_revision"),
            "assignment_revision": assignment.get("assignment_revision"),
            "node_ref": node_ref,
            "execution_mode": execution_mode,
            "worker_config": worker_config,
        }
        digest = _digest(payload)
        now = _timestamp()
        run_id = "persona_run_" + uuid.uuid4().hex[:24]
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM persona_automation_run WHERE project_id = ? AND idempotency_key = ?",
                (project_id, key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != digest:
                    raise PersonaAutomationError("PERSONA_AUTOMATION_IDEMPOTENCY_CONFLICT", "idempotency_key already refers to another run", 409)
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_RUN_REPLAYED", "run": self._row(existing), "created": False}
            # One active run per (project, node) bucket, not per project:
            # the whole point of node ownership is that separate nodes run
            # concurrently under their own Master. The project-wide
            # CONDUCTOR bucket (node_ref IS NULL) keeps its original
            # single-active-run behaviour.
            active = connection.execute(
                "SELECT run_id, state, session_anchor_ref FROM persona_automation_run WHERE project_id = ? "
                "AND (node_ref IS ?) AND state IN ('RUNNING','WAITING','PAUSED') "
                "ORDER BY updated_at DESC LIMIT 1",
                (project_id, node_ref),
            ).fetchone()
            if active is not None:
                scope_text = (
                    "this node already has an active persona automation run"
                    if node_ref
                    else "project already has an active persona automation run"
                )
                held_by = (
                    "the requesting Anchor"
                    if active["session_anchor_ref"] == anchor
                    else f"a different Anchor ({active['session_anchor_ref']})"
                )
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_ACTIVE_RUN_EXISTS",
                    f"{scope_text}: run {active['run_id']} is {active['state']} and owned by {held_by}; "
                    "stop it (persona.automation.stop) before starting a new run",
                    409,
                )
            connection.execute(
                "INSERT INTO persona_automation_run(run_id, project_id, session_anchor_ref, persona_id, persona_revision, assignment_revision, scope_text, instruction_text, goal_ref, goal_version, node_ref, budget_json, state, cursor_json, execution_mode, worker_config_json, idempotency_key, request_digest, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?, ?, ?)",
                (run_id, project_id, anchor, assignment["persona_id"], int(assignment["persona_revision"]), int(assignment["assignment_revision"]), scope, instruction, value.get("goal_ref"), value.get("goal_version"), node_ref, _json(value.get("budget") or {}), _json({}), execution_mode, _json(worker_config), key, digest, now, now),
            )
            self._event(connection, run_id, "RUN_STARTED", "start:" + key, {"assignment": assignment, "provider_invocation": "NONE"})
            row = self._get(connection, run_id)
        return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_RUN_STARTED", "run": self._row(row), "created": True}

    def record_driver_message(
        self,
        run_id: str,
        *,
        driver_key: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Record the exact bounded Master-control message for a run.

        Starting a run must mean more than creating a RUNNING row.  The
        durable event is deliberately separate from a work assignment: this
        message asks the owning Master to perform one control cycle (tick,
        plan, and only then any bounded dispatch).  A deterministic queue key
        makes retries reuse the same message rather than wake a Master twice.
        """

        run_id = _text(run_id, "run_id")
        driver_key = _text(driver_key, "driver_key")
        message_id = _text(message.get("message_id"), "message.message_id")
        payload = {
            "driver_key": driver_key,
            "message_id": message_id,
            "target_session_anchor_ref": message.get("target_session_anchor_ref"),
            "node_ref": message.get("node_ref"),
            # The driver key identifies the durable queue item.  It is a
            # creation fact, not the current call's replay result, so keep it
            # stable across retries (including runs written by the first
            # deployed version of this feature).
            "created": True,
        }
        with self._connection() as connection:
            row = self._get(connection, run_id)
            event, created = self._event(
                connection,
                run_id,
                "DRIVER_ENQUEUED",
                "driver:" + driver_key,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": (
                    "PERSONA_AUTOMATION_DRIVER_ENQUEUED"
                    if created
                    else "PERSONA_AUTOMATION_DRIVER_REPLAYED"
                ),
                "run": self._row(row),
                "driver": payload,
                "event": event,
            }

    def _mutate_state(self, run_id: str, state: str, *, request_id: str, expected_revision: int | None = None, reason: str | None = None, allowed_sources: set[str] | None = None) -> dict[str, Any]:
        if state not in RUN_STATES:
            raise PersonaAutomationError("PERSONA_AUTOMATION_STATE_INVALID", "unsupported automation run state")
        with self._connection() as connection:
            row = self._get(connection, _text(run_id, "run_id"))
            current = str(row["state"])
            allowed = allowed_sources if allowed_sources is not None else {
                "PAUSED": {"RUNNING", "WAITING"},
                "RUNNING": {"PAUSED", "WAITING"},
                "STOPPED": {"RUNNING", "WAITING", "PAUSED"},
            }.get(state, set())
            if current == state:
                if expected_revision is not None and (type(expected_revision) is not int or int(row["revision"]) != expected_revision):
                    raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run revision changed", 409)
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_STATE_REPLAYED", "run": self._row(row), "changed": False}
            if current not in allowed:
                raise PersonaAutomationError("PERSONA_AUTOMATION_STATE_CONFLICT", f"cannot transition {current} to {state}", 409)
            if expected_revision is not None and (type(expected_revision) is not int or int(row["revision"]) != expected_revision):
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run revision changed", 409)
            now = _timestamp()
            cursor = connection.execute(
                "UPDATE persona_automation_run SET state = ?, stop_reason = ?, lease_owner = NULL, lease_expires_at = NULL, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (state, reason, now, run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed during transition", 409)
            self._event(connection, run_id, "RUN_STATE_CHANGED", f"state:{request_id}", {"state": state, "reason": reason})
            return {"schema": SCHEMA, "status": f"PERSONA_AUTOMATION_{state}", "run": self._row(self._get(connection, run_id)), "changed": True}

    def pause_run(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return self._mutate_state(value.get("run_id"), "PAUSED", request_id=_text(value.get("request_id"), "request_id"), expected_revision=value.get("expected_revision"), reason=value.get("reason"))

    def resume_run(self, value: Mapping[str, Any], *, recover_stopped: bool = False) -> dict[str, Any]:
        return self._mutate_state(
            value.get("run_id"), "RUNNING",
            request_id=_text(value.get("request_id"), "request_id"),
            expected_revision=value.get("expected_revision"), reason=None,
            allowed_sources={"PAUSED", "WAITING", "STOPPED"} if recover_stopped else None,
        )

    def stop_run(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return self._mutate_state(value.get("run_id"), "STOPPED", request_id=_text(value.get("request_id"), "request_id"), expected_revision=value.get("expected_revision"), reason=value.get("reason") or "operator_stop")

    def claim_tick(self, value: Mapping[str, Any]) -> dict[str, Any]:
        run_id = _text(value.get("run_id"), "run_id")
        owner = _text(value.get("owner_ref"), "owner_ref")
        tick_id = _text(value.get("tick_id"), "tick_id")
        lease_seconds = _positive_int(value.get("lease_seconds"), "lease_seconds", 90) or 90
        with self._connection() as connection:
            row = self._get(connection, run_id)
            if row["state"] not in {"RUNNING", "WAITING"}:
                raise PersonaAutomationError("PERSONA_AUTOMATION_TICK_STATE_INVALID", "ticks are not accepted for this run state", 409)
            existing = connection.execute("SELECT * FROM persona_automation_event WHERE run_id = ? AND idempotency_key = ?", (run_id, "tick:" + tick_id)).fetchone()
            if existing is not None:
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_TICK_REPLAYED", "run": self._row(row), "event": _load(existing["payload_json"], {})}
            expiry = _parse_timestamp(row["lease_expires_at"])
            now = _now()
            current_owner = str(row["lease_owner"] or "")
            if current_owner and current_owner != owner and expiry and expiry > now:
                raise PersonaAutomationError("PERSONA_AUTOMATION_LEASE_HELD", "automation run lease is held by another owner", 409)
            takeover = bool(current_owner and current_owner != owner and expiry and expiry <= now)
            expires_at = _timestamp(now + timedelta(seconds=lease_seconds))
            cursor = connection.execute(
                "UPDATE persona_automation_run SET lease_owner = ?, lease_expires_at = ?, tick_count = tick_count + 1, cursor_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (owner, expires_at, _json(value.get("cursor") or _load(row["cursor_json"], {})), _timestamp(now), run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_LEASE_CONFLICT", "automation run changed during tick claim", 409)
            event, _ = self._event(connection, run_id, "TICK_CLAIMED", "tick:" + tick_id, {"tick_id": tick_id, "owner_ref": owner, "takeover": takeover, "lease_expires_at": expires_at})
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_TICK_CLAIMED", "run": self._row(self._get(connection, run_id)), "event": event}

    def _require_lease(self, row: sqlite3.Row, owner: str) -> None:
        if str(row["lease_owner"] or "") != owner:
            raise PersonaAutomationError("PERSONA_AUTOMATION_LEASE_REQUIRED", "the current lease owner is required", 409)
        expiry = _parse_timestamp(row["lease_expires_at"])
        if expiry is None or expiry <= _now():
            raise PersonaAutomationError("PERSONA_AUTOMATION_LEASE_EXPIRED", "automation run lease has expired", 409)

    def record_invocation_attempt(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the owner/input coordinates before a provider call.

        The attempt event makes a failed provider turn observable without
        pretending that a provider result or a decision was produced.
        """

        run_id = _text(value.get("run_id"), "run_id")
        owner = _text(value.get("owner_ref"), "owner_ref")
        decision_id = _text(value.get("decision_id"), "decision_id")
        invocation = value.get("invocation")
        if not isinstance(invocation, Mapping):
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_INVOCATION_INVALID",
                "invocation must be an object",
            )
        payload = {
            "decision_id": decision_id,
            "owner_ref": owner,
            "invocation": dict(invocation),
        }
        with self._connection() as connection:
            row = self._get(connection, run_id)
            self._require_lease(row, owner)
            event, created = self._event(
                connection,
                run_id,
                "PROVIDER_INVOCATION_ATTEMPTED",
                "invocation:" + decision_id,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": (
                    "PERSONA_AUTOMATION_INVOCATION_RECORDED"
                    if created
                    else "PERSONA_AUTOMATION_INVOCATION_REPLAYED"
                ),
                "run": self._row(self._get(connection, run_id)),
                "invocation": dict(invocation),
                "event": event,
            }

    def get_decision(self, run_id: str, decision_id: str) -> dict[str, Any] | None:
        run_id = _text(run_id, "run_id")
        decision_id = _text(decision_id, "decision_id")
        with self._connection() as connection:
            self._get(connection, run_id)
            row = connection.execute(
                "SELECT * FROM persona_automation_decision WHERE run_id = ? AND decision_id = ?",
                (run_id, decision_id),
            ).fetchone()
            return self._decision_row(row) if row is not None else None

    def record_decision(self, value: Mapping[str, Any]) -> dict[str, Any]:
        run_id = _text(value.get("run_id"), "run_id")
        owner = _text(value.get("owner_ref"), "owner_ref")
        decision_id = _text(value.get("decision_id"), "decision_id")
        kind = _text(value.get("kind"), "kind").upper()
        if kind not in DECISION_KINDS:
            raise PersonaAutomationError("PERSONA_AUTOMATION_DECISION_INVALID", "kind must be EXECUTE, MEETING, ESCALATE or WAIT")
        rationale = _text(value.get("rationale"), "rationale")
        evidence = value.get("evidence_refs") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaAutomationError("PERSONA_AUTOMATION_EVIDENCE_INVALID", "evidence_refs must be a list of non-empty references")
        with self._connection() as connection:
            row = self._get(connection, run_id)
            self._require_lease(row, owner)
            existing = connection.execute("SELECT * FROM persona_automation_decision WHERE run_id = ? AND decision_id = ?", (run_id, decision_id)).fetchone()
            if existing is not None:
                if (existing["kind"] != kind or existing["rationale"] != rationale
                        or _load(existing["evidence_refs_json"], []) != evidence
                        or _load(existing["target_json"], None) != value.get("target")
                        or existing["next_condition"] != value.get("next_condition")
                        or _load(existing["invocation_json"], None) != value.get("invocation")):
                    raise PersonaAutomationError("PERSONA_AUTOMATION_DECISION_IDEMPOTENCY_CONFLICT", "decision_id already refers to different content", 409)
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_DECISION_REPLAYED", "run": self._row(row), "decision": self._decision_row(existing)}
            now = _timestamp()
            invocation = value.get("invocation")
            if invocation is not None and not isinstance(invocation, Mapping):
                raise PersonaAutomationError("PERSONA_AUTOMATION_INVOCATION_INVALID", "invocation must be an object")
            connection.execute("INSERT INTO persona_automation_decision(decision_id, run_id, kind, rationale, evidence_refs_json, target_json, next_condition, invocation_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (decision_id, run_id, kind, rationale, _json(evidence), _json(value.get("target")) if value.get("target") is not None else None, value.get("next_condition"), _json(invocation) if invocation is not None else None, now))
            next_state = "RUNNING" if kind == "EXECUTE" else "WAITING"
            current_decision = {"decision_id": decision_id, "kind": kind, "rationale": rationale, "evidence_refs": evidence, "target": value.get("target"), "next_condition": value.get("next_condition"), "invocation": dict(invocation) if isinstance(invocation, Mapping) else None}
            cursor = connection.execute("UPDATE persona_automation_run SET state = ?, current_decision_json = ?, current_meeting_json = ?, next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?", (next_state, _json(current_decision), _json(value.get("meeting")) if kind == "MEETING" else None, value.get("next_condition"), now, run_id, int(row["revision"])))
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording decision", 409)
            event, _ = self._event(connection, run_id, "DECISION_RECORDED", "decision:" + decision_id, {"decision_id": decision_id, "kind": kind, "rationale": rationale, "evidence_refs": evidence, "target": value.get("target"), "next_condition": value.get("next_condition"), "invocation": dict(invocation) if isinstance(invocation, Mapping) else None, "meeting": value.get("meeting") if kind == "MEETING" else None})
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_DECISION_RECORDED", "run": self._row(self._get(connection, run_id)), "decision": {"decision_id": decision_id, "kind": kind, "rationale": rationale, "evidence_refs": evidence, "target": value.get("target"), "next_condition": value.get("next_condition"), "invocation": dict(invocation) if isinstance(invocation, Mapping) else None}, "meeting": value.get("meeting") if kind == "MEETING" else None, "event": event}

    @staticmethod
    def _decision_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"decision_id": row["decision_id"], "kind": row["kind"], "rationale": row["rationale"], "evidence_refs": _load(row["evidence_refs_json"], []), "target": _load(row["target_json"], None), "next_condition": row["next_condition"], "invocation": _load(row["invocation_json"], None), "created_at": row["created_at"]}

    def dispatch_work(
        self,
        value: Mapping[str, Any],
        enqueue: Callable[[str, Mapping[str, Any]], tuple[Mapping[str, Any], bool]],
        *,
        create_worker: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run_id = _text(value.get("run_id"), "run_id")
        owner = _text(value.get("owner_ref"), "owner_ref")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        title = _text(value.get("title"), "title")
        instruction = _text(value.get("instruction"), "instruction")
        conditions = value.get("completion_conditions") or []
        if not isinstance(conditions, list) or not conditions:
            raise PersonaAutomationError("PERSONA_AUTOMATION_COMPLETION_CONDITIONS_REQUIRED", "completion_conditions must contain at least one condition")
        with self._connection() as connection:
            row = self._get(connection, run_id)
            self._require_lease(row, owner)
            if row["state"] != "RUNNING":
                raise PersonaAutomationError("PERSONA_AUTOMATION_DISPATCH_STATE_INVALID", "dispatch requires a RUNNING automation run", 409)
            decision = _load(row["current_decision_json"], {})
            if decision.get("kind") != "EXECUTE":
                raise PersonaAutomationError("PERSONA_AUTOMATION_EXECUTE_DECISION_REQUIRED", "an EXECUTE decision with evidence is required before dispatch", 409)
            decision_target = (
                decision.get("target")
                if isinstance(decision.get("target"), Mapping)
                else {}
            )
            decision_todo_id = str(
                decision_target.get("todo_id") or ""
            ).strip()
            requested_todo_id = str(value.get("todo_id") or "").strip()

            if (
                requested_todo_id
                and decision_todo_id
                and requested_todo_id != decision_todo_id
            ):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_TODO_SELECTION_CONFLICT",
                    "todo_id does not match the Todo selected by the current plan",
                    409,
                )
            todo_id = requested_todo_id or decision_todo_id or None
            decision_node_ref = str(
                decision_target.get("node_ref") or ""
            ).strip()
            run_node_ref = str(row["node_ref"] or "").strip()
            if run_node_ref and decision_node_ref and decision_node_ref != run_node_ref:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_NODE_SELECTION_CONFLICT",
                    "the selected Todo is outside the automation run node",
                    409,
                )
            if run_node_ref and not todo_id:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_TODO_REQUIRED",
                    "a node-scoped dispatch requires the exact Todo selected by persona.automation.plan",
                    409,
                )
            decision_task_frame_id = str(
                decision_target.get("task_frame_id") or ""
            ).strip()
            requested_task_frame_id = str(value.get("task_frame_id") or "").strip()
            if (
                requested_task_frame_id
                and decision_task_frame_id
                and requested_task_frame_id != decision_task_frame_id
            ):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_TASK_FRAME_SELECTION_CONFLICT",
                    "task_frame_id does not match the Task Frame selected by the current plan",
                    409,
                )
            task_frame_id = requested_task_frame_id or decision_task_frame_id or None
            existing = connection.execute("SELECT * FROM persona_automation_event WHERE run_id = ? AND idempotency_key = ?", (run_id, "dispatch:" + dispatch_id)).fetchone()
            if existing is not None:
                payload = _load(existing["payload_json"], {})
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_DISPATCH_REPLAYED", "run": self._row(row), "dispatch": payload}
            current_assignment = _load(row["current_assignment_json"], None)
            if isinstance(current_assignment, Mapping):
                current_state = str(current_assignment.get("state") or "DISPATCHED").upper()
                current_dispatch = str(current_assignment.get("dispatch_id") or "")
                if current_dispatch and current_dispatch != dispatch_id and current_state not in {"REVIEWED", "RESOLVED"}:
                    raise PersonaAutomationError("PERSONA_AUTOMATION_ASSIGNMENT_ACTIVE", "a different bounded work assignment already owns this run", 409)
                if current_dispatch and current_dispatch != dispatch_id:
                    current_review = _load(row["current_review_json"], {})
                    if str(current_review.get("outcome") or "").upper() == "PASS":
                        raise PersonaAutomationError("PERSONA_AUTOMATION_ASSIGNMENT_PASS_PENDING_COMPLETION", "the current assignment passed review and must be completed before another dispatch", 409)
            assignment_revision = int(row["revision"]) + 1
            assignment_id = f"persona_assignment:{run_id}:{dispatch_id}"
            # node_ref comes only from the run's own immutable, server-set
            # column (copied from the owning Anchor's assignment at
            # start_run) -- dispatch's own request schema has no node_ref
            # field at all, so a caller cannot widen or redirect scope here.
            # create_master_message independently re-resolves and stamps
            # the CURRENT owning assignment for this exact node_ref, so a
            # node reassigned since the run started is re-verified at
            # enqueue time, not merely copied forward
            # (2026-09-15 Conductor review: dispatch_work previously never
            # passed node_ref, so every node-scoped run's work silently
            # fell back to the project-wide queue bucket).
            execution_mode = str(row["execution_mode"] or "MASTER_DIRECT").upper()
            if execution_mode == "WORKER_REVIEW":
                if not todo_id:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_TODO_REQUIRED",
                        "WORKER_REVIEW dispatch requires the exact Todo selected by persona.automation.plan",
                        409,
                    )
                worker_config = _load(row["worker_config_json"], {})
                if not isinstance(worker_config, Mapping):
                    worker_config = {}
                worker_spec = {
                    "run_id": run_id,
                    "dispatch_id": dispatch_id,
                    "assignment_id": assignment_id,
                    "assignment_revision": assignment_revision,
                    "project_id": row["project_id"],
                    "node_ref": row["node_ref"],
                    "todo_id": todo_id,
                    "task_frame_id": task_frame_id,
                    "worker_role": "IMPLEMENTER",
                    "assigned_by_session_anchor_ref": row["session_anchor_ref"],
                    "persona_id": worker_config.get("persona_id"),
                    "provider": worker_config.get("provider"),
                    "model_ref": worker_config.get("model_ref"),
                    "effort": worker_config.get("effort"),
                    "title": title,
                    "instruction": instruction,
                    "completion_conditions": list(conditions),
                }
                if not callable(create_worker):
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_WORKER_ROUTE_UNAVAILABLE",
                        "WORKER_REVIEW dispatch requires the typed Fleet Worker session route",
                        503,
                    )
                try:
                    worker_result = create_worker(worker_spec)
                except PersonaAutomationError:
                    raise
                except Exception as error:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_WORKER_SESSION_FAILED",
                        "the typed Fleet Worker session route failed; retry the same dispatch",
                        503,
                    ) from error
                worker_assignment = worker_result.get("assignment") if isinstance(worker_result, Mapping) else None
                if not isinstance(worker_assignment, Mapping):
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_WORKER_RECEIPT_INVALID",
                        "Fleet Worker session route did not return a durable assignment",
                        502,
                    )
                # A Task Frame Worker has no resident provider Session Anchor.
                # Its durable assignment still carries the exact Frame minted
                # by the Host, so bind that coordinate before checking the
                # remaining dispatch lineage.  The old Fleet route supplied a
                # caller Task Frame (or None); the ephemeral route may mint
                # one after the dispatch request was validated.
                worker_task_frame_id = str(
                    worker_assignment.get("task_frame_id") or ""
                ).strip() or None
                if task_frame_id is None:
                    task_frame_id = worker_task_frame_id
                elif worker_task_frame_id != task_frame_id:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_WORKER_PROVENANCE_MISMATCH",
                        "Fleet Worker assignment does not preserve exact task_frame_id lineage",
                        409,
                    )
                expected_scope = {
                    "project_id": row["project_id"],
                    "node_ref": row["node_ref"],
                    "todo_id": todo_id,
                    "task_frame_id": task_frame_id,
                    "worker_role": "IMPLEMENTER",
                    "assigned_by_session_anchor_ref": row["session_anchor_ref"],
                }
                for field, expected in expected_scope.items():
                    if worker_assignment.get(field) != expected:
                        raise PersonaAutomationError(
                            "PERSONA_AUTOMATION_WORKER_PROVENANCE_MISMATCH",
                            f"Fleet Worker assignment does not preserve exact {field} lineage",
                            409,
                        )
                worker_anchor = _text(worker_assignment.get("session_anchor_ref"), "worker_assignment.session_anchor_ref")
                worker_assignment_revision = _positive_int(worker_assignment.get("assignment_revision"), "worker_assignment.assignment_revision")
                assignment = {
                    "assignment_id": assignment_id,
                    "assignment_revision": assignment_revision,
                    "state": "WORKER_ASSIGNED",
                    "execution_mode": execution_mode,
                    "dispatch_id": dispatch_id,
                    "message_id": None,
                    "created": True,
                    "title": title,
                    "instruction": instruction,
                    "completion_conditions": list(conditions),
                    "project_id": row["project_id"],
                    "node_ref": row["node_ref"],
                    "todo_id": todo_id,
                    "task_frame_id": task_frame_id,
                    "worker_assignment_id": worker_assignment.get("assignment_id"),
                    "worker_assignment_revision": worker_assignment_revision,
                    "worker_anchor_ref": worker_anchor,
                    "result_ref": None,
                    "review_id": None,
                }
                precreated_instruction = worker_result.get("automation_instruction") if isinstance(worker_result, Mapping) else None
                worker_payload = {
                    "state": "ASSIGNED",
                    "assignment": dict(worker_assignment),
                    "instruction": instruction,
                    "completion_conditions": list(conditions),
                    **(
                        {"precreated_instruction": dict(precreated_instruction)}
                        if isinstance(precreated_instruction, Mapping)
                        else {}
                    ),
                }
                now = _timestamp()
                cursor = connection.execute(
                    "UPDATE persona_automation_run SET current_assignment_json = ?, current_worker_json = ?, state = 'WAITING', next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json(assignment), _json(worker_payload), "Worker must submit one result through persona.automation.worker-result.", now, run_id, int(row["revision"])),
                )
                if cursor.rowcount != 1:
                    raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording Worker dispatch", 409)
                event, _ = self._event(
                    connection,
                    run_id,
                    "WORKER_ASSIGNED",
                    "dispatch:" + dispatch_id,
                    {"dispatch": assignment, "worker_assignment": dict(worker_assignment)},
                )
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_WORKER_DISPATCHED",
                    "run": self._row(self._get(connection, run_id)),
                    "dispatch": assignment,
                    "worker": dict(worker_assignment),
                    "event": event,
                }

            message_value = {
                "idempotency_key": f"persona-automation:{run_id}:{dispatch_id}",
                "title": title,
                "instruction": instruction,
                "node_ref": row["node_ref"],
                "todo_id": todo_id,
                "task_frame_id": task_frame_id,
                "metadata": {
                    "persona_automation_run_id": run_id,
                    "persona_automation_assignment_id": assignment_id,
                    "persona_automation_assignment_revision": assignment_revision,
                    "dispatch_id": dispatch_id,
                    "session_anchor_ref": row["session_anchor_ref"],
                    "persona_id": row["persona_id"],
                    "persona_revision": int(row["persona_revision"]),
                    "completion_conditions": conditions,
                    "provider_invocation": "DEFERRED_TO_MASTER_QUEUE",
                    "completion_route": "PERSONA_AUTOMATION_STORE",
                    "todo_id": todo_id,
                    "task_frame_id": task_frame_id,
                },
            }
            # A node-bound MASTER_DIRECT run is completed by the owning Node
            # Master through the typed ``persona.automation.master-result``
            # Action. It must not create a project Master queue item whose
            # recipient is the same Anchor (self-ping). Keep the exact
            # assignment tuple durable so that Action can apply CAS and
            # reviewer provenance checks just like the queue route.
            if execution_mode == "MASTER_DIRECT" and str(row["node_ref"] or "").strip():
                direct_message_id = f"persona-direct:{run_id}:{dispatch_id}"
                direct = {
                    "message_id": direct_message_id,
                    "project_id": row["project_id"],
                    "node_ref": row["node_ref"],
                    "todo_id": todo_id,
                    "task_frame_id": task_frame_id,
                    "target_session_anchor_ref": row["session_anchor_ref"],
                    "delivery_state": "DIRECT",
                    "route": "NODE_MASTER_ACTION",
                    "metadata": {
                        "persona_automation_run_id": run_id,
                        "persona_automation_assignment_id": assignment_id,
                        "persona_automation_assignment_revision": assignment_revision,
                        "dispatch_id": dispatch_id,
                        "session_anchor_ref": row["session_anchor_ref"],
                        "persona_id": row["persona_id"],
                        "persona_revision": int(row["persona_revision"]),
                        "completion_route": "PERSONA_AUTOMATION_STORE",
                        "provider_invocation": "NODE_MASTER_ACTION",
                        "todo_id": todo_id,
                        "task_frame_id": task_frame_id,
                    },
                }
                assignment = {
                    "assignment_id": assignment_id,
                    "assignment_revision": assignment_revision,
                    "state": "DISPATCHED",
                    "execution_mode": execution_mode,
                    "dispatch_id": dispatch_id,
                    "message_id": direct_message_id,
                    "delivery_route": "NODE_MASTER_ACTION",
                    "created": True,
                    "title": title,
                    "instruction": instruction,
                    "completion_conditions": conditions,
                    "project_id": row["project_id"],
                    "node_ref": row["node_ref"],
                    "todo_id": todo_id,
                    "task_frame_id": task_frame_id,
                    "result_ref": None,
                    "review_id": None,
                }
                now = _timestamp()
                cursor = connection.execute(
                    "UPDATE persona_automation_run SET current_assignment_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json(assignment), now, run_id, int(row["revision"])),
                )
                if cursor.rowcount != 1:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_REVISION_CONFLICT",
                        "automation run changed while recording direct Master dispatch",
                        409,
                    )
                event, _ = self._event(
                    connection,
                    run_id,
                    "MASTER_DIRECT_DISPATCHED",
                    "dispatch:" + dispatch_id,
                    {"dispatch": assignment, "direct": direct},
                )
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_MASTER_DIRECT_DISPATCHED",
                    "run": self._row(self._get(connection, run_id)),
                    "dispatch": assignment,
                    "direct": direct,
                    "event": event,
                }
            # The callback is the existing Master queue gateway.  No provider
            # call is made here; delivery and result review remain separate.
            try:
                message, created = enqueue(row["project_id"], message_value)
            except PersonaAutomationError:
                raise
            except Exception as error:
                raise PersonaAutomationError("PERSONA_AUTOMATION_QUEUE_ENQUEUE_FAILED", "Master queue enqueue failed; retry the same dispatch idempotency key", 503) from error
            if not isinstance(message, Mapping) or not str(message.get("message_id") or "").strip():
                raise PersonaAutomationError("PERSONA_AUTOMATION_QUEUE_RECEIPT_INVALID", "Master queue did not return a message receipt", 502)
            assignment = {
                "assignment_id": assignment_id,
                "assignment_revision": assignment_revision,
                "state": "DISPATCHED",
                "execution_mode": execution_mode,
                "dispatch_id": dispatch_id,
                "message_id": message.get("message_id"),
                "created": bool(created),
                "title": title,
                "instruction": instruction,
                "completion_conditions": conditions,
                "project_id": row["project_id"],
                "node_ref": row["node_ref"],
                "todo_id": todo_id,
                "task_frame_id": task_frame_id,
                "result_ref": None,
                "review_id": None,
            }
            now = _timestamp()
            cursor = connection.execute("UPDATE persona_automation_run SET current_assignment_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?", (_json(assignment), now, run_id, int(row["revision"])))
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording dispatch", 409)
            event, _ = self._event(connection, run_id, "WORK_DISPATCHED", "dispatch:" + dispatch_id, {"dispatch": assignment, "message": dict(message)})
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_WORK_DISPATCHED" if created else "PERSONA_AUTOMATION_WORK_REPLAYED", "run": self._row(self._get(connection, run_id)), "dispatch": assignment, "message": dict(message), "event": event}

    def record_worker_result(
        self,
        value: Mapping[str, Any],
        create_reviewer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Record one exact IMPLEMENTER result and create its independent Reviewer.

        The Worker result and Reviewer assignment are separate durable phases.
        A result receipt never implies delivery, provider execution, or review
        acceptance; every coordinate is checked against the current run and
        Fleet assignment before the next phase is created.
        """

        run_id = _text(value.get("run_id"), "run_id")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        worker_assignment_id = _text(value.get("worker_assignment_id"), "worker_assignment_id")
        worker_anchor_ref = _text(value.get("worker_anchor_ref"), "worker_anchor_ref")
        worker_assignment_revision = _positive_int(value.get("worker_assignment_revision"), "worker_assignment_revision")
        result_ref = _text(value.get("result_ref"), "result_ref")
        outcome = _text(value.get("outcome"), "outcome").upper()
        if outcome not in WORKER_RESULT_OUTCOMES:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_WORKER_RESULT_INVALID",
                "outcome must be SUCCEEDED, FAILED, BLOCKED or NOT_RUN",
            )
        evidence = value.get("evidence_refs") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaAutomationError("PERSONA_AUTOMATION_EVIDENCE_INVALID", "evidence_refs must be a list of non-empty references")
        result_digest = str(value.get("result_digest") or "").strip()
        validation_state = str(value.get("validation_state") or "NOT_RUN").strip().upper()
        if not validation_state:
            validation_state = "NOT_RUN"
        if validation_state not in {"PASSED", "FAILED", "PENDING", "NOT_RUN"}:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_VALIDATION_STATE_INVALID",
                "validation_state must be PASSED, FAILED, PENDING or NOT_RUN",
            )
        if not result_digest:
            result_digest = _digest({"result_ref": result_ref, "outcome": outcome, "evidence_refs": evidence, "validation_state": validation_state, "result_text": value.get("result_text")})
        result_payload = {
            "result_ref": result_ref,
            "outcome": outcome,
            "evidence_refs": list(evidence),
            "validation_state": validation_state,
            "result_digest": result_digest,
            "result_text": value.get("result_text"),
            "dispatch_id": dispatch_id,
            "worker_assignment_id": worker_assignment_id,
            "worker_assignment_revision": worker_assignment_revision,
            "worker_anchor_ref": worker_anchor_ref,
        }
        reviewer_spec: dict[str, Any] | None = None
        with self._connection() as connection:
            row = self._get(connection, run_id)
            if str(row["execution_mode"] or "MASTER_DIRECT").upper() != "WORKER_REVIEW":
                raise PersonaAutomationError("PERSONA_AUTOMATION_EXECUTION_MODE_INVALID", "Worker results require a WORKER_REVIEW automation run", 409)
            assignment = _load(row["current_assignment_json"], None)
            worker = _load(row["current_worker_json"], None)
            if not isinstance(assignment, Mapping) or str(assignment.get("dispatch_id") or "") != dispatch_id:
                raise PersonaAutomationError("PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH", "Worker result is not bound to the current dispatch", 409)
            if not isinstance(worker, Mapping):
                raise PersonaAutomationError("PERSONA_AUTOMATION_WORKER_ASSIGNMENT_REQUIRED", "an IMPLEMENTER Worker assignment is required before recording a result", 409)
            worker_assignment = worker.get("assignment") if isinstance(worker.get("assignment"), Mapping) else {}
            expected = {
                "assignment_id": worker_assignment_id,
                "session_anchor_ref": worker_anchor_ref,
                "assignment_revision": worker_assignment_revision,
                "worker_role": "IMPLEMENTER",
                "project_id": row["project_id"],
                "node_ref": row["node_ref"],
                "todo_id": assignment.get("todo_id"),
                "task_frame_id": assignment.get("task_frame_id"),
            }
            for field, expected_value in expected.items():
                if worker_assignment.get(field) != expected_value:
                    raise PersonaAutomationError("PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH", f"Worker result does not preserve exact {field} lineage", 409)
            existing_result = worker.get("result") if isinstance(worker.get("result"), Mapping) else None
            if existing_result is not None:
                if _digest(existing_result) != _digest(result_payload):
                    raise PersonaAutomationError("PERSONA_AUTOMATION_WORKER_RESULT_CONFLICT", "result_ref already refers to different Worker result content", 409)
                reviewer = _load(row["current_reviewer_json"], None)
                if isinstance(reviewer, Mapping):
                    return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_WORKER_RESULT_REPLAYED", "run": self._row(row), "worker_result": dict(existing_result), "reviewer": reviewer}
            else:
                now = _timestamp()
                worker_payload = {**dict(worker), "state": "RESULT_RECORDED", "result": result_payload}
                updated_assignment = {**dict(assignment), "state": "WORKER_RESULT_RECORDED", "result_ref": result_ref}
                cursor = connection.execute(
                    "UPDATE persona_automation_run SET current_assignment_json = ?, current_worker_json = ?, state = 'WAITING', next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json(updated_assignment), _json(worker_payload), "an independent REVIEWER Worker must record a verdict", now, run_id, int(row["revision"])),
                )
                if cursor.rowcount != 1:
                    raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording Worker result", 409)
                self._event(connection, run_id, "WORKER_RESULT_RECORDED", "worker-result:" + result_ref, result_payload)
                row = self._get(connection, run_id)
                assignment = updated_assignment
                worker = worker_payload
            reviewer = _load(row["current_reviewer_json"], None)
            if isinstance(reviewer, Mapping):
                reviewer_state = str(reviewer.get("state") or "").upper()
                if reviewer_state == "CREATING":
                    return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_REVIEWER_CREATION_PENDING", "run": self._row(row), "worker_result": result_payload, "reviewer": reviewer}
                if reviewer_state != "CREATION_FAILED":
                    return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_WORKER_RESULT_RECORDED", "run": self._row(row), "worker_result": result_payload, "reviewer": reviewer}
            reviewer_spec = {
                "run_id": run_id,
                "dispatch_id": dispatch_id,
                "worker_result_ref": result_ref,
                "worker_assignment_id": worker_assignment_id,
                "assignment_revision": int(assignment.get("assignment_revision") or -1),
                "project_id": row["project_id"],
                "node_ref": row["node_ref"],
                "todo_id": assignment.get("todo_id"),
                "task_frame_id": assignment.get("task_frame_id"),
                "worker_role": "REVIEWER",
                "assigned_by_session_anchor_ref": row["session_anchor_ref"],
                "persona_id": (_load(row["worker_config_json"], {}) or {}).get("persona_id") if isinstance(_load(row["worker_config_json"], {}), Mapping) else None,
                "provider": (_load(row["worker_config_json"], {}) or {}).get("provider") if isinstance(_load(row["worker_config_json"], {}), Mapping) else None,
                "model_ref": (_load(row["worker_config_json"], {}) or {}).get("model_ref") if isinstance(_load(row["worker_config_json"], {}), Mapping) else None,
                "effort": (_load(row["worker_config_json"], {}) or {}).get("effort") if isinstance(_load(row["worker_config_json"], {}), Mapping) else None,
                "title": "Review Worker result: " + result_ref,
                "instruction": "Independently review the pinned Worker result and record PASS, NEEDS_REVISION, or BLOCKED.",
                "worker_result": result_payload,
            }
            reservation_id = "reviewer-create:" + uuid.uuid4().hex[:24]
            reservation_payload = {
                "state": "CREATING",
                "reservation_id": reservation_id,
                "worker_result_ref": result_ref,
                "worker_assignment_id": worker_assignment_id,
                "instruction": reviewer_spec["instruction"],
            }
            now = _timestamp()
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (_json(reservation_payload), now, run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while reserving Reviewer creation", 409)
            self._event(connection, run_id, "REVIEWER_CREATION_RESERVED", "reviewer-reservation:" + result_ref, reservation_payload)
            row = self._get(connection, run_id)
        if not callable(create_reviewer):
            with self._connection() as connection:
                now = _timestamp()
                connection.execute(
                    "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json({"state": "CREATION_FAILED", "reservation_id": reservation_id, "worker_result_ref": result_ref, "reason": "ROUTE_UNAVAILABLE"}), now, run_id, int(row["revision"])),
                )
            raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEWER_ROUTE_UNAVAILABLE", "Worker result requires the typed Fleet Reviewer session route", 503)
        try:
            reviewer_result = create_reviewer(reviewer_spec)
        except PersonaAutomationError:
            with self._connection() as connection:
                now = _timestamp()
                connection.execute(
                    "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json({"state": "CREATION_FAILED", "reservation_id": reservation_id, "worker_result_ref": result_ref, "reason": "PERSONA_ERROR"}), now, run_id, int(row["revision"])),
                )
            raise
        except Exception as error:
            with self._connection() as connection:
                now = _timestamp()
                connection.execute(
                    "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json({"state": "CREATION_FAILED", "reservation_id": reservation_id, "worker_result_ref": result_ref, "reason": type(error).__name__}), now, run_id, int(row["revision"])),
                )
            raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEWER_SESSION_FAILED", "the typed Fleet Reviewer session route failed; retry the same Worker result", 503) from error
        reviewer_assignment = reviewer_result.get("assignment") if isinstance(reviewer_result, Mapping) else None
        if not isinstance(reviewer_assignment, Mapping):
            with self._connection() as connection:
                now = _timestamp()
                connection.execute(
                    "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json({"state": "CREATION_FAILED", "reservation_id": reservation_id, "worker_result_ref": result_ref, "reason": "RECEIPT_INVALID"}), now, run_id, int(row["revision"])),
                )
            raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEWER_RECEIPT_INVALID", "Fleet Reviewer session route did not return a durable assignment", 502)
        for field, expected_value in {
            "project_id": reviewer_spec["project_id"],
            "node_ref": reviewer_spec["node_ref"],
            "todo_id": reviewer_spec["todo_id"],
            "task_frame_id": reviewer_spec["task_frame_id"],
            "worker_role": "REVIEWER",
            "assigned_by_session_anchor_ref": reviewer_spec["assigned_by_session_anchor_ref"],
        }.items():
            if reviewer_assignment.get(field) != expected_value:
                with self._connection() as connection:
                    now = _timestamp()
                    connection.execute(
                        "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                        (_json({"state": "CREATION_FAILED", "reservation_id": reservation_id, "worker_result_ref": result_ref, "reason": "PROVENANCE_MISMATCH", "field": field}), now, run_id, int(row["revision"])),
                    )
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEWER_PROVENANCE_MISMATCH", f"Fleet Reviewer assignment does not preserve exact {field} lineage", 409)
        reviewer_anchor = _text(reviewer_assignment.get("session_anchor_ref"), "reviewer_assignment.session_anchor_ref")
        reviewer_is_task_frame = str(
            reviewer_assignment.get("execution_shape") or ""
        ).upper() == "TASK_FRAME"
        worker_is_task_frame = str(
            worker_assignment.get("execution_shape") or ""
        ).upper() == "TASK_FRAME"
        if reviewer_anchor == worker_anchor_ref and not (
            reviewer_is_task_frame and worker_is_task_frame
        ):
            with self._connection() as connection:
                now = _timestamp()
                connection.execute(
                    "UPDATE persona_automation_run SET current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (_json({"state": "CREATION_FAILED", "reservation_id": reservation_id, "worker_result_ref": result_ref, "reason": "SAME_WORKER_ANCHOR"}), now, run_id, int(row["revision"])),
                )
            raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEWER_MUST_BE_INDEPENDENT", "Reviewer must use a distinct Worker Session Anchor", 409)
        reviewer_revision = _positive_int(reviewer_assignment.get("assignment_revision"), "reviewer_assignment.assignment_revision")
        precreated_instruction = reviewer_result.get("automation_instruction") if isinstance(reviewer_result, Mapping) else None
        reviewer_payload = {
            "state": "ASSIGNED",
            "assignment": dict(reviewer_assignment),
            "worker_result_ref": result_ref,
            "worker_assignment_id": worker_assignment_id,
            "instruction": reviewer_spec["instruction"],
            "completion_conditions": [],
            **(
                {"precreated_instruction": dict(precreated_instruction)}
                if isinstance(precreated_instruction, Mapping)
                else {}
            ),
        }
        with self._connection() as connection:
            row = self._get(connection, run_id)
            existing_reviewer = _load(row["current_reviewer_json"], None)
            if isinstance(existing_reviewer, Mapping) and str(existing_reviewer.get("reservation_id") or "") != reservation_id:
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_WORKER_RESULT_REPLAYED", "run": self._row(row), "worker_result": result_payload, "reviewer": existing_reviewer}
            if not isinstance(existing_reviewer, Mapping) or str(existing_reviewer.get("state") or "").upper() != "CREATING":
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_REVIEWER_CREATION_PENDING", "run": self._row(row), "worker_result": result_payload, "reviewer": existing_reviewer}
            assignment = _load(row["current_assignment_json"], {})
            updated_assignment = {
                **dict(assignment),
                "state": "REVIEWER_ASSIGNED",
                "reviewer_assignment_id": reviewer_assignment.get("assignment_id"),
                "reviewer_assignment_revision": reviewer_revision,
                "reviewer_anchor_ref": reviewer_anchor,
            }
            # Ephemeral Task Frame reviewers run under the owning Master
            # Anchor.  Persist the Host-minted Frame on the automation
            # assignment so the later verdict CAS checks the same lineage.
            if not updated_assignment.get("task_frame_id") and reviewer_assignment.get(
                "task_frame_id"
            ):
                updated_assignment["task_frame_id"] = reviewer_assignment.get(
                    "task_frame_id"
                )
            now = _timestamp()
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_assignment_json = ?, current_reviewer_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (_json(updated_assignment), _json(reviewer_payload), now, run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording Reviewer assignment", 409)
            event, _ = self._event(connection, run_id, "REVIEWER_ASSIGNED", "reviewer:" + result_ref, {"reviewer": reviewer_payload, "worker_result_ref": result_ref})
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_WORKER_RESULT_RECORDED", "run": self._row(self._get(connection, run_id)), "worker_result": result_payload, "reviewer": reviewer_payload, "event": event}

    def record_reviewer_verdict(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Record the independent Reviewer verdict for the current Worker result."""

        run_id = _text(value.get("run_id"), "run_id")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        reviewer_assignment_id = _text(value.get("reviewer_assignment_id"), "reviewer_assignment_id")
        reviewer_anchor_ref = _text(value.get("reviewer_anchor_ref"), "reviewer_anchor_ref")
        reviewer_assignment_revision = _positive_int(value.get("reviewer_assignment_revision"), "reviewer_assignment_revision")
        worker_result_ref = _text(value.get("worker_result_ref"), "worker_result_ref")
        outcome = _text(value.get("outcome"), "outcome").upper()
        if outcome not in REVIEW_OUTCOMES - {"NOT_RUN"}:
            raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_INVALID", "outcome must be PASS, NEEDS_REVISION or BLOCKED")
        evidence = value.get("evidence_refs") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaAutomationError("PERSONA_AUTOMATION_EVIDENCE_INVALID", "evidence_refs must be a list of non-empty references")
        acceptance_status = str(value.get("acceptance_status") or ("VERIFIED_EVIDENCE" if outcome == "PASS" else "PENDING")).strip().upper()
        if outcome == "PASS" and acceptance_status != "VERIFIED_EVIDENCE":
            raise PersonaAutomationError("PERSONA_AUTOMATION_ACCEPTANCE_NOT_VERIFIED", "PASS requires explicit verified acceptance evidence", 409)
        evidence_text = " ".join(evidence).upper()
        if outcome == "PASS" and ("NOT_RUN" in evidence_text or "NOT RUN" in evidence_text):
            raise PersonaAutomationError("PERSONA_AUTOMATION_ACCEPTANCE_NOT_VERIFIED", "a NOT_RUN result cannot prove acceptance", 409)
        with self._connection() as connection:
            row = self._get(connection, run_id)
            assignment = _load(row["current_assignment_json"], None)
            reviewer = _load(row["current_reviewer_json"], None)
            worker = _load(row["current_worker_json"], None)
            reviewer_assignment = reviewer.get("assignment") if isinstance(reviewer, Mapping) and isinstance(reviewer.get("assignment"), Mapping) else {}
            worker_result = worker.get("result") if isinstance(worker, Mapping) and isinstance(worker.get("result"), Mapping) else {}
            master_result = reviewer.get("master_result") if isinstance(reviewer, Mapping) and isinstance(reviewer.get("master_result"), Mapping) else {}
            source_result = worker_result or master_result
            if not isinstance(assignment, Mapping) or str(assignment.get("dispatch_id") or "") != dispatch_id:
                raise PersonaAutomationError("PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH", "Reviewer verdict is not bound to the current dispatch", 409)
            expected = {
                "assignment_id": reviewer_assignment_id,
                "session_anchor_ref": reviewer_anchor_ref,
                "assignment_revision": reviewer_assignment_revision,
                "worker_role": "REVIEWER",
                "project_id": row["project_id"],
                "node_ref": row["node_ref"],
                "todo_id": assignment.get("todo_id"),
                "task_frame_id": assignment.get("task_frame_id"),
            }
            for field, expected_value in expected.items():
                if reviewer_assignment.get(field) != expected_value:
                    raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_PROVENANCE_MISMATCH", f"Reviewer verdict does not preserve exact {field} lineage", 409)
            if source_result.get("result_ref") != worker_result_ref or reviewer.get("worker_result_ref") != worker_result_ref:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_PROVENANCE_MISMATCH", "Reviewer verdict must name the exact Worker result", 409)
            existing = _load(row["current_review_json"], None)
            if isinstance(existing, Mapping):
                existing_result_ref = str(existing.get("result_ref") or "")
                if existing_result_ref == worker_result_ref and existing.get("outcome") == outcome:
                    return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_REVIEW_REPLAYED", "run": self._row(row), "review": existing}
                # A fresh Reviewer assignment can arrive after a historical
                # NEEDS_REVISION/BLOCKED verdict.  The old review row remains
                # immutable history, but its current pointer must not reject
                # the exact new result that already passed assignment and
                # Anchor provenance checks above.  Keep conflicts for a
                # second verdict on the same result or any mismatched slot.
                if not (
                    existing_result_ref != worker_result_ref
                    and str(reviewer.get("worker_result_ref") or "") == worker_result_ref
                    and str(reviewer.get("state") or "").upper() == "ASSIGNED"
                ):
                    raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_IDEMPOTENCY_CONFLICT", "Worker result already has a different Reviewer verdict", 409)
            review_id = "persona_review_" + uuid.uuid4().hex[:24]
            review_payload = {
                "review_id": review_id,
                "result_ref": worker_result_ref,
                "outcome": outcome,
                "acceptance_status": acceptance_status,
                "evidence_refs": list(evidence),
                "note": value.get("note"),
                "next_action": value.get("next_action"),
                "dispatch_id": dispatch_id,
                "assignment_revision": int(assignment.get("assignment_revision") or -1),
                "source_message_id": reviewer_assignment_id,
                "source_project_id": row["project_id"],
                "source_reply_anchor_ref": reviewer_anchor_ref,
                "reviewer_assignment_id": reviewer_assignment_id,
                "reviewer_assignment_revision": reviewer_assignment_revision,
                "worker_result_ref": worker_result_ref,
            }
            now = _timestamp()
            connection.execute(
                "INSERT INTO persona_automation_review(review_id, run_id, result_ref, outcome, acceptance_status, evidence_refs_json, note, next_action, dispatch_id, assignment_revision, source_message_id, source_project_id, source_reply_anchor_ref, source_reply_terminal_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (review_id, run_id, worker_result_ref, outcome, acceptance_status, _json(evidence), value.get("note"), value.get("next_action"), dispatch_id, int(assignment.get("assignment_revision") or -1), reviewer_assignment_id, row["project_id"], reviewer_anchor_ref, now),
            )
            updated_assignment = {**dict(assignment), "state": "REVIEWED", "review_id": review_id, "review_outcome": outcome}
            reviewer_payload = {**dict(reviewer), "state": "VERDICT_RECORDED", "verdict": review_payload}
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_assignment_json = ?, current_reviewer_json = ?, current_review_json = ?, state = 'WAITING', next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (_json(updated_assignment), _json(reviewer_payload), _json(review_payload), value.get("next_action"), now, run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording Reviewer verdict", 409)
            event, _ = self._event(connection, run_id, "REVIEWER_VERDICT_RECORDED", "review:" + worker_result_ref, review_payload)
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_REVIEW_RECORDED", "run": self._row(self._get(connection, run_id)), "review": review_payload, "event": event}

    def todo_completion_gate(self, project_id: str, todo_id: str, node_ref: str | None = None) -> dict[str, Any] | None:
        """Return a blocking active WORKER_REVIEW run for a Todo, if any."""

        project_id = _text(project_id, "project_id")
        todo_id = _text(todo_id, "todo_id")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM persona_automation_run WHERE project_id = ? AND execution_mode = 'WORKER_REVIEW' AND state IN ('RUNNING','WAITING','PAUSED') ORDER BY updated_at DESC, run_id DESC",
                (project_id,),
            ).fetchall()
            for row in rows:
                assignment = _load(row["current_assignment_json"], None)
                if not isinstance(assignment, Mapping) or str(assignment.get("todo_id") or "") != todo_id:
                    continue
                if node_ref is not None and str(row["node_ref"] or "") != str(node_ref or ""):
                    continue
                review = _load(row["current_review_json"], None)
                if isinstance(review, Mapping) and str(review.get("outcome") or "").upper() == "PASS":
                    continue
                return {
                    "run_id": row["run_id"],
                    "project_id": project_id,
                    "node_ref": row["node_ref"],
                    "todo_id": todo_id,
                    "execution_mode": "WORKER_REVIEW",
                    "review": review,
                    "next_condition": row["next_condition"],
                }
        return None

    def record_master_completion(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Bind an exact Master completion to its automation dispatch.

        A Master calls ``master.complete`` to report to the server.  That is
        the control-plane acknowledgement for Persona automation; it must not
        be routed back into the same Master session's inbox.  The resulting
        state is deliberately *not* a review or acceptance: the server then
        creates an independent Reviewer Worker, whose typed verdict advances
        the run.
        """

        run_id = _text(value.get("run_id"), "run_id")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        source_message_id = _text(value.get("source_message_id"), "source_message_id")
        supplied_assignment_revision = value.get("assignment_revision")
        legacy_self_reply_migration = value.get("legacy_self_reply_migration") is True
        if supplied_assignment_revision is None and not legacy_self_reply_migration:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_INTEGER_INVALID",
                "assignment_revision must be a positive integer",
            )
        assignment_revision = (
            _positive_int(supplied_assignment_revision, "assignment_revision")
            if supplied_assignment_revision is not None
            else None
        )
        result_ref = str(value.get("result_ref") or "").strip()
        body_digest = _text(value.get("body_text_utf8_sha256"), "body_text_utf8_sha256")
        completed_at = _text(value.get("completed_at"), "completed_at")
        completion_route = (
            "MASTER_DIRECT_ACTION"
            if value.get("direct_master") is True
            else "LEGACY_SELF_REPLY_MIGRATION"
            if legacy_self_reply_migration
            else "AUTOMATION_STORE"
        )
        with self._connection() as connection:
            row = self._get(connection, run_id)
            assignment = _load(row["current_assignment_json"], None)
            if not isinstance(assignment, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_ASSIGNMENT_REQUIRED",
                    "a current Master assignment is required before recording its result", 409,
                )
            current_assignment_revision = int(assignment.get("assignment_revision") or -1)
            if assignment_revision is None:
                # One-time, explicit migration for already committed
                # automation messages that used the removed self-reply route.
                # The current assignment's exact run/dispatch/message tuple is
                # still authoritative; no anchor, cache, or message scan is
                # consulted to fill a missing revision.
                assignment_revision = current_assignment_revision
            # ``master.complete`` permits an omitted result_ref for ordinary
            # queue work. Persona automation still needs a stable source
            # coordinate before it can create an independent Reviewer. Derive
            # that coordinate only from the authenticated completion message
            # and exact assignment revision; never substitute a delivery
            # receipt or infer a result from recency.
            if not result_ref:
                result_ref = (
                    "master-result://"
                    f"{source_message_id}/{int(assignment_revision or 0)}"
                )
            payload = {
                "dispatch_id": dispatch_id,
                "assignment_revision": int(assignment_revision),
                "source_message_id": source_message_id,
                "result_ref": result_ref,
                "body_text_utf8_sha256": body_digest,
                "completed_at": completed_at,
                "route": completion_route,
            }
            existing = connection.execute(
                "SELECT * FROM persona_automation_event WHERE run_id = ? AND idempotency_key = ?",
                (run_id, "master-completion:" + source_message_id),
            ).fetchone()
            if existing is not None:
                stored = _load(existing["payload_json"], {})
                if _digest(stored) != _digest(payload):
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_MASTER_COMPLETION_CONFLICT",
                        "Master completion differs from the recorded automation result", 409,
                    )
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_MASTER_RESULT_REPLAYED",
                    "run": self._row(row),
                    "result": dict(stored),
                }
            if (
                str(assignment.get("dispatch_id") or "") != dispatch_id
                or str(assignment.get("message_id") or "") != source_message_id
                or int(assignment.get("assignment_revision") or -1) != int(assignment_revision)
            ):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH",
                    "Master completion is not bound to the current automation assignment", 409,
                )
            if str(assignment.get("state") or "") != "DISPATCHED":
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_MASTER_COMPLETION_STATE_INVALID",
                    "Master completion requires a DISPATCHED automation assignment", 409,
                )
            updated_assignment = dict(assignment)
            updated_assignment.update({
                "state": "RESULT_READY_FOR_REVIEW",
                "result_ref": result_ref or None,
                "master_result_digest": body_digest,
                "master_result_text": str(value.get("result_text") or ""),
                "master_completed_at": completed_at,
            })
            next_condition = "Independent Reviewer verdict is required before automation can complete."
            now = _timestamp()
            cursor = connection.execute(
                "UPDATE persona_automation_run SET state = 'WAITING', current_assignment_json = ?, next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (_json(updated_assignment), next_condition, now, run_id, int(row["revision"])),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    "automation run changed while recording Master completion", 409,
                )
            event, _ = self._event(
                connection,
                run_id,
                "MASTER_RESULT_RECORDED",
                "master-completion:" + source_message_id,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": "PERSONA_AUTOMATION_MASTER_RESULT_RECORDED",
                "run": self._row(self._get(connection, run_id)),
                "result": payload,
                "event": event,
            }

    def attach_master_reviewer(
        self,
        value: Mapping[str, Any],
        create_reviewer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create the Reviewer Worker for one completed Master assignment.

        ``master.complete`` records the Master result first.  This second
        transition reserves and creates a distinct Fleet Reviewer through the
        server callback, then CAS-binds that assignment to the exact
        project/node/Todo/Task Frame/result tuple.  A queue receipt or a
        Master result is never treated as a review verdict.
        """

        run_id = _text(value.get("run_id"), "run_id")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        source_message_id = _text(value.get("source_message_id"), "source_message_id")
        assignment_revision = _positive_int(
            value.get("assignment_revision"), "assignment_revision"
        )
        result_ref = _text(value.get("result_ref"), "result_ref")
        result_text = value.get("result_text")
        if result_text is None:
            with self._connection() as connection:
                prior = _load(self._get(connection, run_id)["current_assignment_json"], {})
            result_text = prior.get("master_result_text") if isinstance(prior, Mapping) else ""
            result_text = result_text if isinstance(result_text, str) else ""
        if not isinstance(result_text, str):
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_RESULT_TEXT_INVALID",
                "result_text must be text",
            )
        body_digest = _text(
            value.get("body_text_utf8_sha256"), "body_text_utf8_sha256"
        )
        master_result = {
            "result_ref": result_ref,
            "result_text": result_text,
            "body_text_utf8_sha256": body_digest,
            "source_message_id": source_message_id,
            "dispatch_id": dispatch_id,
            "assignment_revision": assignment_revision,
            "source_role": "MASTER",
        }
        reviewer_spec: dict[str, Any]
        reservation_id: str
        replaces_historical_review = False
        replaces_legacy_reviewer = False
        with self._connection() as connection:
            row = self._get(connection, run_id)
            assignment = _load(row["current_assignment_json"], None)
            if not isinstance(assignment, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_ASSIGNMENT_REQUIRED",
                    "a current Master assignment is required before creating its Reviewer",
                    409,
                )
            if (
                str(assignment.get("dispatch_id") or "") != dispatch_id
                or str(assignment.get("message_id") or "") != source_message_id
                or int(assignment.get("assignment_revision") or -1)
                != assignment_revision
                or str(assignment.get("result_ref") or "") != result_ref
            ):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH",
                    "Reviewer creation is not bound to the exact completed Master assignment",
                    409,
                )
            existing = _load(row["current_reviewer_json"], None)
            if isinstance(existing, Mapping):
                existing_ref = str(
                    existing.get("worker_result_ref")
                    or (existing.get("master_result") or {}).get("result_ref")
                    or ""
                )
                if existing_ref and existing_ref != result_ref:
                    # A prior NEEDS_REVISION/BLOCKED verdict is historical
                    # evidence, not an active Reviewer reservation.  Once the
                    # node Master dispatches the bounded follow-up, the exact
                    # old Reviewer slot must be replaceable by a fresh typed
                    # Worker assignment.  Keep the strict conflict for an
                    # assigned/creating Reviewer (or any other outcome) so an
                    # active result can never be silently re-bound.
                    existing_state = str(existing.get("state") or "").upper()
                    prior_review = _load(row["current_review_json"], {})
                    prior_outcome = str(
                        prior_review.get("outcome") if isinstance(prior_review, Mapping) else ""
                    ).upper()
                    if not (
                        existing_state == "VERDICT_RECORDED"
                        and prior_outcome in {"NEEDS_REVISION", "BLOCKED"}
                    ):
                        raise PersonaAutomationError(
                            "PERSONA_AUTOMATION_REVIEWER_PROVENANCE_MISMATCH",
                            "a different Master result already owns this Reviewer slot",
                            409,
                        )
                    # Keep the prior review row/event as immutable history,
                    # but clear the current pointer before reserving the new
                    # Reviewer.  Otherwise a valid verdict for the fresh
                    # result is mistaken for an idempotency conflict with the
                    # historical non-PASS result.
                    replaces_historical_review = True
                existing_state = str(existing.get("state") or "").upper()
                if existing_state == "ASSIGNED":
                    return {
                        "schema": SCHEMA,
                        "status": "PERSONA_AUTOMATION_MASTER_REVIEWER_REPLAYED",
                        "run": self._row(row),
                        "reviewer": existing,
                    }
                if existing_state == "VERDICT_RECORDED" and existing_ref == result_ref:
                    return {
                        "schema": SCHEMA,
                        "status": "PERSONA_AUTOMATION_MASTER_REVIEWER_REPLAYED",
                        "run": self._row(row),
                        "reviewer": existing,
                    }
                if existing_state == "CREATING":
                    return {
                        "schema": SCHEMA,
                        "status": "PERSONA_AUTOMATION_MASTER_REVIEWER_CREATION_PENDING",
                        "run": self._row(row),
                        "reviewer": existing,
                    }
                if existing_state == "REPAIRING":
                    replaced_assignment = existing.get("replaced_assignment")
                    replaces_legacy_reviewer = (
                        isinstance(replaced_assignment, Mapping)
                        and str(replaced_assignment.get("worker_role") or "").upper()
                        == "REVIEWER"
                        and str(replaced_assignment.get("execution_shape") or "").upper()
                        != "TASK_FRAME"
                        and not str(replaced_assignment.get("task_frame_id") or "").strip()
                    )
            worker_config = _load(row["worker_config_json"], {})
            if not isinstance(worker_config, Mapping):
                worker_config = {}
            # MASTER_DIRECT runs do not need a provider to perform the
            # bounded Master step, so their worker_config is normally empty.
            # The automatic Reviewer is still a real Worker session and must
            # therefore carry an explicit supported provider/model through
            # the typed Fleet route.  Keep an explicit configured provider
            # when one exists, while defaulting the bounded automation path
            # to the supported Codex Luna capability.
            # Persona defaults differently: never reuse the Master run's
            # persona_id. Prefer an independent Reviewer-titled persona
            # (or an explicit worker_config persona override).
            reviewer_provider = str(worker_config.get("provider") or "CODEX").strip().upper()
            reviewer_model = str(worker_config.get("model_ref") or "gpt-5.6-luna").strip()
            reviewer_effort = str(worker_config.get("effort") or "LOW").strip().upper()
            reviewer_persona_id = resolve_default_reviewer_persona_id(
                connection,
                master_persona_id=str(row["persona_id"] or ""),
                worker_config=worker_config,
            )
            reviewer_spec = {
                "run_id": run_id,
                "dispatch_id": dispatch_id,
                "worker_result_ref": result_ref,
                "assignment_revision": assignment_revision,
                "project_id": row["project_id"],
                "node_ref": row["node_ref"],
                "todo_id": assignment.get("todo_id"),
                "task_frame_id": assignment.get("task_frame_id"),
                "worker_role": "REVIEWER",
                "assigned_by_session_anchor_ref": row["session_anchor_ref"],
                "persona_id": reviewer_persona_id,
                "provider": reviewer_provider,
                "model_ref": reviewer_model,
                "effort": reviewer_effort,
                "title": "Review Master result: " + result_ref,
                "instruction": (
                    "Independently review the pinned Master result and record "
                    "PASS, NEEDS_REVISION, or BLOCKED."
                ),
                "master_result": master_result,
                "worker_result": master_result,
            }
            reservation_id = "master-reviewer-create:" + _digest(
                {"run_id": run_id, "dispatch_id": dispatch_id, "result_ref": result_ref}
            )[:24]
            reservation_payload = {
                "state": "CREATING",
                "reservation_id": reservation_id,
                "worker_result_ref": result_ref,
                "source_role": "MASTER",
                "master_result": master_result,
                "instruction": reviewer_spec["instruction"],
            }
            now = _timestamp()
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_reviewer_json = ?, "
                "current_review_json = CASE WHEN ? THEN NULL ELSE current_review_json END, "
                "state = 'WAITING', next_condition = ?, revision = revision + 1, "
                "updated_at = ? WHERE run_id = ? AND revision = ?",
                (
                    _json(reservation_payload),
                    1 if replaces_historical_review else 0,
                    "Reviewer Worker must submit an independent verdict",
                    now,
                    run_id,
                    int(row["revision"]),
                ),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    "automation run changed while reserving Master Reviewer creation",
                    409,
                )
            self._event(
                connection,
                run_id,
                "MASTER_REVIEWER_CREATION_RESERVED",
                "master-reviewer-reservation:" + result_ref,
                reservation_payload,
            )
            row = self._get(connection, run_id)

        def mark_failed(reason: str, error_code: str) -> dict[str, Any]:
            with self._connection() as connection:
                current = self._get(connection, run_id)
                current_reviewer = {
                    "state": "CREATION_FAILED",
                    "reservation_id": reservation_id,
                    "worker_result_ref": result_ref,
                    "source_role": "MASTER",
                    "master_result": master_result,
                    "reason": reason,
                    "error_code": error_code,
                }
                now = _timestamp()
                connection.execute(
                    "UPDATE persona_automation_run SET current_reviewer_json = ?, "
                    "next_condition = ?, revision = revision + 1, updated_at = ? "
                    "WHERE run_id = ? AND revision = ?",
                    (
                        _json(current_reviewer),
                        "retry creation of the exact Reviewer Worker assignment",
                        now,
                        run_id,
                        int(current["revision"]),
                    ),
                )
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_MASTER_REVIEWER_CREATION_FAILED",
                    "run": self._row(self._get(connection, run_id)),
                    "reviewer": current_reviewer,
                    "error_code": error_code,
                    "reason": reason,
                }

        if not callable(create_reviewer):
            return mark_failed("ROUTE_UNAVAILABLE", "PERSONA_AUTOMATION_REVIEWER_ROUTE_UNAVAILABLE")
        try:
            reviewer_result = create_reviewer(reviewer_spec)
        except PersonaAutomationError as error:
            return mark_failed(str(error), error.code)
        except Exception as error:
            return mark_failed(str(error), type(error).__name__)
        reviewer_assignment = (
            reviewer_result.get("assignment")
            if isinstance(reviewer_result, Mapping)
            else None
        )
        if not isinstance(reviewer_assignment, Mapping):
            return mark_failed(
                "Fleet Reviewer session route did not return a durable assignment",
                "PERSONA_AUTOMATION_REVIEWER_RECEIPT_INVALID",
            )
        for field, expected_value in {
            "project_id": reviewer_spec["project_id"],
            "node_ref": reviewer_spec["node_ref"],
            "todo_id": reviewer_spec["todo_id"],
            "task_frame_id": reviewer_spec["task_frame_id"],
            "worker_role": "REVIEWER",
            "assigned_by_session_anchor_ref": reviewer_spec[
                "assigned_by_session_anchor_ref"
            ],
        }.items():
            # A legacy MASTER_DIRECT run may have been left with a persistent
            # FLEET_SESSION Reviewer.  The typed repair reservation permits a
            # narrow upgrade to a Host-minted Task Frame; all other reviewer
            # creation remains exact-field CAS validation.
            if (
                field == "task_frame_id"
                and replaces_legacy_reviewer
                and not str(expected_value or "").strip()
                and str(reviewer_assignment.get(field) or "").strip()
                and str(reviewer_assignment.get("execution_shape") or "").upper()
                == "TASK_FRAME"
            ):
                continue
            if reviewer_assignment.get(field) != expected_value:
                return mark_failed(
                    f"Fleet Reviewer assignment does not preserve exact {field} lineage",
                    "PERSONA_AUTOMATION_REVIEWER_PROVENANCE_MISMATCH",
                )
        reviewer_anchor = _text(
            reviewer_assignment.get("session_anchor_ref"),
            "reviewer_assignment.session_anchor_ref",
        )
        reviewer_is_task_frame = str(
            reviewer_assignment.get("execution_shape") or ""
        ).upper() == "TASK_FRAME"
        if reviewer_anchor == str(reviewer_spec["assigned_by_session_anchor_ref"]) and not reviewer_is_task_frame:
            return mark_failed(
                "Reviewer must use a distinct Worker Session Anchor",
                "PERSONA_AUTOMATION_REVIEWER_MUST_BE_INDEPENDENT",
            )
        reviewer_revision = _positive_int(
            reviewer_assignment.get("assignment_revision"),
            "reviewer_assignment.assignment_revision",
        )
        reviewer_payload = {
            "state": "ASSIGNED",
            "reservation_id": reservation_id,
            "assignment": dict(reviewer_assignment),
            "worker_result_ref": result_ref,
            "source_role": "MASTER",
            "master_result": master_result,
            "instruction": reviewer_spec["instruction"],
        }
        # The Fleet route may have already posted and claimed the exact
        # Reviewer instruction while combining it with the Persona native
        # queue turn. Persist that receipt on the automation projection so a
        # subsequent kick replays the same message instead of posting a second
        # body (which would be rejected by Session Bus idempotency).
        precreated_instruction = (
            reviewer_result.get("automation_instruction")
            if isinstance(reviewer_result, Mapping)
            else None
        )
        if isinstance(precreated_instruction, Mapping):
            reviewer_payload["precreated_instruction"] = dict(precreated_instruction)
        with self._connection() as connection:
            current = self._get(connection, run_id)
            existing = _load(current["current_reviewer_json"], None)
            if not isinstance(existing, Mapping) or str(
                existing.get("reservation_id") or ""
            ) != reservation_id:
                return {
                    "schema": SCHEMA,
                    "status": "PERSONA_AUTOMATION_MASTER_REVIEWER_REPLAYED",
                    "run": self._row(current),
                    "reviewer": existing,
                }
            assignment = _load(current["current_assignment_json"], {})
            updated_assignment = {
                **dict(assignment),
                "state": "REVIEWER_ASSIGNED",
                "reviewer_assignment_id": reviewer_assignment.get("assignment_id"),
                "reviewer_assignment_revision": reviewer_revision,
                "reviewer_anchor_ref": reviewer_anchor,
            }
            if not updated_assignment.get("task_frame_id") and reviewer_assignment.get(
                "task_frame_id"
            ):
                updated_assignment["task_frame_id"] = reviewer_assignment.get(
                    "task_frame_id"
                )
            now = _timestamp()
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_assignment_json = ?, "
                "current_reviewer_json = ?, next_condition = ?, revision = revision + 1, "
                "updated_at = ? WHERE run_id = ? AND revision = ?",
                (
                    _json(updated_assignment),
                    _json(reviewer_payload),
                    "Reviewer Worker must submit an independent verdict",
                    now,
                    run_id,
                    int(current["revision"]),
                ),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    "automation run changed while recording Master Reviewer assignment",
                    409,
                )
            reviewer_event_key = "master-reviewer:" + result_ref
            if replaces_legacy_reviewer:
                reviewer_event_key = (
                    "master-reviewer-repair:"
                    + result_ref
                    + ":"
                    + str(reviewer_assignment.get("assignment_id") or "")
                )
            event, _ = self._event(
                connection,
                run_id,
                "MASTER_REVIEWER_ASSIGNED",
                reviewer_event_key,
                {
                    "reviewer": reviewer_payload,
                    "master_result_ref": result_ref,
                },
            )
            return {
                "schema": SCHEMA,
                "status": "PERSONA_AUTOMATION_MASTER_REVIEWER_ASSIGNED",
                "run": self._row(self._get(connection, run_id)),
                "reviewer": reviewer_payload,
                "event": event,
            }

    def list_active_node_runs(self) -> list[dict[str, Any]]:
        """RUNNING node-scoped runs across projects, for the Host-side driver.

        Only ``RUNNING`` qualifies: PAUSED/WAITING/STOPPED/COMPLETED runs must
        never be advanced, and project-wide (CONDUCTOR) runs are not driven by
        the node-Master loop.
        """

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM persona_automation_run WHERE state = 'RUNNING' "
                "AND node_ref IS NOT NULL ORDER BY updated_at ASC, run_id ASC"
            ).fetchall()
            return [self._row(row) for row in rows]

    def list_waiting_node_runs(self) -> list[dict[str, Any]]:
        """WAITING node-scoped runs.  The driver advances these only when the
        server has recorded a control receipt that was never delivered (for
        example after a Reviewer PASS); an ordinary wait is left alone."""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM persona_automation_run WHERE state = 'WAITING' "
                "AND node_ref IS NOT NULL ORDER BY updated_at ASC, run_id ASC"
            ).fetchall()
            return [self._row(row) for row in rows]

    def recent_completed_todo_ids(
        self, project_id: str, node_ref: str, limit: int = 2
    ) -> list[str]:
        """Todo ids of a node's newest COMPLETED runs, newest first ('' if the
        run never dispatched a Todo).  Used to detect a continuation chain that
        keeps re-running the same unfinished Todo."""

        project_id = _text(project_id, "project_id")
        node_ref = _text(node_ref, "node_ref")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT current_assignment_json FROM persona_automation_run "
                "WHERE project_id = ? AND node_ref = ? AND state = 'COMPLETED' "
                "ORDER BY updated_at DESC, run_id DESC LIMIT ?",
                (project_id, node_ref, max(1, int(limit))),
            ).fetchall()
        ids: list[str] = []
        for row in rows:
            assignment = _load(row["current_assignment_json"], None)
            ids.append(
                str(assignment.get("todo_id") or "")
                if isinstance(assignment, Mapping)
                else ""
            )
        return ids

    def record_continuation_stalled(self, run_id: str, todo_id: str) -> None:
        """Make a stopped continuation chain visible on the run's own history."""

        run_id = _text(run_id, "run_id")
        with self._connection() as connection:
            self._get(connection, run_id)
            self._event(
                connection,
                run_id,
                "CONTINUATION_STALLED",
                "continuation-stalled:" + _text(todo_id, "todo_id"),
                {
                    "todo_id": todo_id,
                    "reason": "the same Todo completed consecutive runs without changing state",
                },
            )

    def record_host_event(self, run_id: str, event_type: str, task_frame_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Record a Task Frame Host launch or collection on the run's own history.

        The event is the run's proof that a frame belongs to it; it grants
        nothing and carries no Host credentials.
        """

        if event_type not in {"TASK_FRAME_HOST_LAUNCHED", "TASK_FRAME_COLLECTED"}:
            raise PersonaAutomationError("PERSONA_AUTOMATION_HOST_EVENT_INVALID", "unsupported Host event type")
        run_id = _text(run_id, "run_id")
        frame = _text(task_frame_id, "task_frame_id")
        suffix = ":" + str(payload.get("status")) if event_type == "TASK_FRAME_COLLECTED" else ""
        with self._connection() as connection:
            row = self._get(connection, run_id)
            event, _created = self._event(
                connection, run_id, event_type, f"frame-host:{event_type}:{frame}{suffix}",
                {"task_frame_id": frame, **dict(payload)},
            )
            # An independent Host has no persona dispatch cursor. Collection
            # cannot be treated as a PASS review, but it must not leave an idle
            # RUNNING run free to select and execute the same Todo again.
            if (event_type == "TASK_FRAME_COLLECTED"
                    and row["state"] == "RUNNING"
                    and _load(row["current_assignment_json"], None) is None):
                now = _timestamp()
                cursor = connection.execute(
                    "UPDATE persona_automation_run SET state = 'WAITING', next_condition = ?, "
                    "revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                    (f"resolve collected Task Frame {frame} against its Todo before selecting work",
                     now, run_id, int(row["revision"])),
                )
                if cursor.rowcount != 1:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_REVISION_CONFLICT",
                        "automation run changed while collecting its Task Frame", 409,
                    )
            return event

    def list_host_frames(self, run_id: str) -> list[dict[str, Any]]:
        """Every Task Frame Host this run launched, oldest first."""

        run_id = _text(run_id, "run_id")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM persona_automation_event WHERE run_id = ? AND event_type = 'TASK_FRAME_HOST_LAUNCHED' ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [_load(row["payload_json"], {}) for row in rows]

    def host_frame_launched(self, run_id: str, task_frame_id: str) -> dict[str, Any] | None:
        run_id = _text(run_id, "run_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM persona_automation_event WHERE run_id = ? AND event_type = 'TASK_FRAME_HOST_LAUNCHED' AND idempotency_key = ?",
                (run_id, f"frame-host:TASK_FRAME_HOST_LAUNCHED:{_text(task_frame_id, 'task_frame_id')}"),
            ).fetchone()
        return _load(row["payload_json"], {}) if row is not None else None

    def host_frame_collected(self, run_id: str, task_frame_id: str) -> dict[str, Any] | None:
        """Return the exact frame's latest durable collection, if any."""

        run_id = _text(run_id, "run_id")
        frame_id = _text(task_frame_id, "task_frame_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM persona_automation_event "
                "WHERE run_id = ? AND event_type = 'TASK_FRAME_COLLECTED' "
                "AND json_extract(payload_json, '$.task_frame_id') = ? "
                "ORDER BY created_at DESC, event_id DESC LIMIT 1",
                (run_id, frame_id),
            ).fetchone()
        return _load(row["payload_json"], {}) if row is not None else None

    def driver_control_recorded(self, run_id: str, driver_key: str) -> bool:
        run_id = _text(run_id, "run_id")
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM persona_automation_event WHERE run_id = ? AND idempotency_key = ?",
                (run_id, "driver-control:" + _text(driver_key, "driver_key")),
            ).fetchone() is not None

    def latest_driver_control_key(self, run_id: str) -> str:
        """driver_key of the newest server-recorded control receipt, or ''.

        ``auto-r<revision>`` events are the driver's own nudge ledger, not
        receipts owed a delivery, so they are excluded.
        """

        run_id = _text(run_id, "run_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT idempotency_key FROM persona_automation_event "
                "WHERE run_id = ? AND event_type = 'DRIVER_CONTROL_RECORDED' "
                "AND idempotency_key NOT LIKE 'driver-control:auto-%' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        prefix = "driver-control:"
        key = str(row["idempotency_key"]) if row is not None else ""
        return key[len(prefix):] if key.startswith(prefix) else ""

    def record_driver_control(
        self,
        run_id: str,
        *,
        driver_key: str,
        control: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Record a node-Master control cycle without creating a queue item.

        Node Masters own their node Todo loop. The project Master queue is a
        Conductor/reporting transport, so a node run must never enqueue a
        message addressed back to its own Master Anchor. This event is the
        durable idempotency boundary for the direct Action route; the owning
        Master performs the returned typed Actions in its current session.
        """

        run_id = _text(run_id, "run_id")
        driver_key = _text(driver_key, "driver_key")
        if not isinstance(control, Mapping):
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_DRIVER_CONTROL_INVALID",
                "control must be an object",
            )
        control_id = _text(control.get("control_id"), "control.control_id")
        payload = {
            "driver_key": driver_key,
            "control_id": control_id,
            "route": "NODE_MASTER_DIRECT_ACTIONS",
            "target_session_anchor_ref": control.get("target_session_anchor_ref"),
            "node_ref": control.get("node_ref"),
            "next_actions": list(control.get("next_actions") or []),
            "created": True,
        }
        with self._connection() as connection:
            row = self._get(connection, run_id)
            event, created = self._event(
                connection,
                run_id,
                "DRIVER_CONTROL_RECORDED",
                "driver-control:" + driver_key,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": (
                    "PERSONA_AUTOMATION_DRIVER_READY"
                    if created
                    else "PERSONA_AUTOMATION_DRIVER_REPLAYED"
                ),
                "run": self._row(row),
                "driver": payload,
                "event": event,
            }

    def prepare_legacy_reviewer_repair(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Reserve a CAS-scoped replacement for an old FLEET Reviewer.

        Early MASTER_DIRECT runs used a persistent FLEET_SESSION Reviewer.
        Those assignments are immutable history and must be ended through the
        Fleet Action before a Task Frame Reviewer is attached.  This method
        only moves the automation pointer to ``REPAIRING``; the server action
        then performs the supported assignment end and calls
        :meth:`attach_master_reviewer`.  The reservation makes a retry safe
        if the process stops between those two guarded transitions.
        """

        run_id = _text(value.get("run_id"), "run_id")
        expected_revision = _positive_int(value.get("expected_revision"), "expected_revision")
        assignment_id = _text(value.get("legacy_reviewer_assignment_id"), "legacy_reviewer_assignment_id")
        expected_assignment_revision = _positive_int(
            value.get("expected_reviewer_assignment_revision"),
            "expected_reviewer_assignment_revision",
        )
        request_id = _text(value.get("request_id"), "request_id")
        reason = str(value.get("reason") or "LEGACY_AUTOMATION_TASK_FRAME_REPAIR").strip()[:200]
        event_key = "legacy-reviewer-repair:" + assignment_id + ":" + str(expected_assignment_revision)
        event_payload = {
            "run_id": run_id,
            "legacy_reviewer_assignment_id": assignment_id,
            "expected_reviewer_assignment_revision": expected_assignment_revision,
            "reason": reason,
            "request_id": request_id,
        }
        recovery_from_stale_reservation = False
        prior_event_record: dict[str, Any] | None = None
        with self._connection() as connection:
            prior_event = connection.execute(
                "SELECT * FROM persona_automation_event WHERE run_id = ? AND idempotency_key = ?",
                (run_id, event_key),
            ).fetchone()
            if prior_event is not None:
                stored = _load(prior_event["payload_json"], {})
                if any(stored.get(key) != value for key, value in event_payload.items()):
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_REPAIR_IDEMPOTENCY_CONFLICT",
                        "legacy Reviewer repair request conflicts with its recorded reservation",
                        409,
                    )
                prior_event_record = {
                    "event_id": prior_event["event_id"],
                    "event_type": prior_event["event_type"],
                    "idempotency_key": prior_event["idempotency_key"],
                    **dict(stored),
                }
                existing_row = self._get(connection, run_id)
                existing_reviewer = _load(existing_row["current_reviewer_json"], None)
                existing_assignment = _load(existing_row["current_assignment_json"], None)
                existing_reviewer_state = (
                    str(existing_reviewer.get("state") or "").upper()
                    if isinstance(existing_reviewer, Mapping)
                    else ""
                )
                existing_task_frame = (
                    str(existing_assignment.get("task_frame_id") or "").strip()
                    if isinstance(existing_assignment, Mapping)
                    else ""
                )
                existing_reviewer_id = (
                    str(existing_assignment.get("reviewer_assignment_id") or "").strip()
                    if isinstance(existing_assignment, Mapping)
                    else ""
                )
                if not (
                    existing_reviewer_state == "CREATING"
                    and existing_reviewer_id == assignment_id
                    and not existing_task_frame
                ):
                    return {
                        "schema": SCHEMA,
                        "status": "PERSONA_AUTOMATION_LEGACY_REVIEWER_REPAIR_REPLAYED",
                        "run": self._row(existing_row),
                        "repair": dict(stored),
                    }
                # A process/reconnect can leave the first repair creator in
                # CREATING after the Fleet row was ended. Re-enter the same
                # reservation with its original request coordinates and let
                # the CAS path finish the Task Frame replacement.
                recovery_from_stale_reservation = True
            row = self._get(connection, run_id)
            if int(row["revision"]) != expected_revision and not recovery_from_stale_reservation:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    f"automation run revision changed; current revision is {row['revision']}",
                    409,
                )
            if str(row["execution_mode"] or "MASTER_DIRECT").upper() != "MASTER_DIRECT":
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_MODE_INVALID",
                    "legacy Reviewer repair is only valid for MASTER_DIRECT runs",
                    409,
                )
            if str(row["state"] or "").upper() not in {"RUNNING", "WAITING", "PAUSED"}:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_STATE_INVALID",
                    "a terminal automation run cannot be repaired",
                    409,
                )
            assignment = _load(row["current_assignment_json"], None)
            reviewer = _load(row["current_reviewer_json"], None)
            if not isinstance(assignment, Mapping) or str(assignment.get("state") or "").upper() not in {
                "RESULT_READY_FOR_REVIEW",
                "REVIEWER_ASSIGNED",
            }:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_ASSIGNMENT_INVALID",
                    "the current Master result must be waiting for a Reviewer",
                    409,
                )
            if not isinstance(reviewer, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_REVIEWER_REQUIRED",
                    "the legacy Reviewer assignment is missing",
                    409,
                )
            reviewer_state = str(reviewer.get("state") or "").upper()
            if reviewer_state == "REPAIRING":
                replaced = reviewer.get("replaced_assignment")
                if isinstance(replaced, Mapping) and str(replaced.get("assignment_id") or "") == assignment_id:
                    return {
                        "schema": SCHEMA,
                        "status": "PERSONA_AUTOMATION_LEGACY_REVIEWER_REPAIR_REPLAYED",
                        "run": self._row(row),
                        "repair": dict(reviewer),
                    }
            if reviewer_state == "CREATING" and recovery_from_stale_reservation:
                reviewer_assignment = None
            elif reviewer_state != "ASSIGNED":
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_REVIEWER_STATE_INVALID",
                    "only an assigned legacy Reviewer can be repaired",
                    409,
                )
            reviewer_assignment = reviewer.get("assignment") if isinstance(reviewer, Mapping) else None
            if reviewer_state == "CREATING" and recovery_from_stale_reservation:
                reviewer_assignment = {
                    "assignment_id": assignment_id,
                    "assignment_revision": expected_assignment_revision,
                    "worker_role": "REVIEWER",
                    "project_id": row["project_id"],
                    "node_ref": row["node_ref"],
                    "todo_id": assignment.get("todo_id"),
                    "task_frame_id": assignment.get("task_frame_id"),
                    "session_anchor_ref": assignment.get("reviewer_anchor_ref"),
                    "assigned_by_session_anchor_ref": row["session_anchor_ref"],
                    "execution_shape": "FLEET_SESSION",
                }
            if not isinstance(reviewer_assignment, Mapping):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_REVIEWER_REQUIRED",
                    "the legacy Reviewer assignment receipt is missing",
                    409,
                )
            expected_fields = {
                "assignment_id": assignment_id,
                "assignment_revision": expected_assignment_revision,
                "worker_role": "REVIEWER",
            }
            for field, expected_value in expected_fields.items():
                actual = reviewer_assignment.get(field)
                if (int(actual) if field == "assignment_revision" and isinstance(actual, int) else actual) != expected_value:
                    raise PersonaAutomationError(
                        "PERSONA_AUTOMATION_REPAIR_PROVENANCE_MISMATCH",
                        f"legacy Reviewer does not preserve exact {field} lineage",
                        409,
                    )
            if str(reviewer_assignment.get("execution_shape") or "").upper() == "TASK_FRAME" or str(reviewer_assignment.get("task_frame_id") or "").strip():
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_NOT_LEGACY",
                    "the current Reviewer is already a Task Frame assignment",
                    409,
                )
            if str(reviewer_assignment.get("session_anchor_ref") or "") == str(row["session_anchor_ref"] or ""):
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_ANCHOR_INVALID",
                    "a same-anchor Reviewer without a Task Frame cannot be repaired",
                    409,
                )
            result_ref = str(assignment.get("result_ref") or "").strip()
            if not result_ref or str(reviewer.get("worker_result_ref") or "") != result_ref:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REPAIR_RESULT_MISMATCH",
                    "legacy Reviewer is not pinned to the current Master result",
                    409,
                )
            repair_payload = {
                **event_payload,
                "worker_result_ref": result_ref,
                "dispatch_id": assignment.get("dispatch_id"),
                "source_message_id": assignment.get("message_id"),
                "replaced_assignment": dict(reviewer_assignment),
                "state": "REPAIRING",
            }
            replacement = {
                "state": "REPAIRING",
                "reservation_id": event_key,
                "worker_result_ref": result_ref,
                "source_role": "MASTER",
                "master_result": reviewer.get("master_result") or {},
                "reason": reason,
                "legacy_reviewer_assignment_id": assignment_id,
                "legacy_reviewer_assignment_revision": expected_assignment_revision,
                "replaced_assignment": dict(reviewer_assignment),
            }
            now = _timestamp()
            cas_revision = int(row["revision"]) if recovery_from_stale_reservation else expected_revision
            cursor = connection.execute(
                "UPDATE persona_automation_run SET current_reviewer_json = ?, next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?",
                (
                    _json(replacement),
                    "replace the legacy Reviewer with a Task Frame Reviewer",
                    now,
                    run_id,
                    cas_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVISION_CONFLICT",
                    "automation run changed while reserving legacy Reviewer repair",
                    409,
                )
            if recovery_from_stale_reservation and prior_event_record is not None:
                event = prior_event_record
            else:
                event, _ = self._event(connection, run_id, "LEGACY_REVIEWER_REPAIR_RESERVED", event_key, repair_payload)
            return {
                "schema": SCHEMA,
                "status": "PERSONA_AUTOMATION_LEGACY_REVIEWER_REPAIR_RESERVED",
                "run": self._row(self._get(connection, run_id)),
                "repair": replacement,
                "event": event,
            }

    def record_review(self, value: Mapping[str, Any]) -> dict[str, Any]:
        run_id = _text(value.get("run_id"), "run_id")
        result_ref = _text(value.get("result_ref"), "result_ref")
        dispatch_id = _text(value.get("dispatch_id"), "dispatch_id")
        source_message_id = _text(value.get("source_message_id"), "source_message_id")
        assignment_revision = _positive_int(value.get("assignment_revision"), "assignment_revision")
        outcome = _text(value.get("outcome"), "outcome").upper()
        if outcome not in REVIEW_OUTCOMES:
            raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_INVALID", "outcome must be PASS, NEEDS_REVISION, BLOCKED or NOT_RUN")
        raw_acceptance = value.get("acceptance_status")
        acceptance_status = (
            str(raw_acceptance or "").strip().upper()
            if raw_acceptance is not None
            else ("VERIFIED_EVIDENCE" if outcome == "PASS" else "NOT_RUN" if outcome == "NOT_RUN" else "PENDING")
        )
        if acceptance_status not in {"VERIFIED_EVIDENCE", "NOT_RUN", "PENDING", "BLOCKED"}:
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_ACCEPTANCE_STATUS_INVALID",
                "acceptance_status must be VERIFIED_EVIDENCE, NOT_RUN, PENDING or BLOCKED",
            )
        evidence = value.get("evidence_refs") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise PersonaAutomationError("PERSONA_AUTOMATION_EVIDENCE_INVALID", "evidence_refs must be a list of non-empty references")
        evidence_text = " ".join(evidence).upper()
        if outcome == "PASS" and acceptance_status != "VERIFIED_EVIDENCE":
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_ACCEPTANCE_NOT_VERIFIED",
                "PASS requires explicit verified acceptance evidence",
                409,
            )
        if outcome == "PASS" and ("NOT_RUN" in evidence_text or "NOT RUN" in evidence_text):
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_ACCEPTANCE_NOT_VERIFIED",
                "a NOT_RUN UI or validation result cannot prove acceptance",
                409,
            )
        queue_markers = (
            "NATIVE_QUEUED",
            "QUEUE_RECEIPT",
            "QUEUE_ACCEPTED",
            "PENDING_PROVIDER_PHASE",
            "APPLIED_NATIVE_QUEUE",
        )
        provider_phase_markers = ("PROMPT_SUBMITTED", "STARTED")
        if (
            outcome == "PASS"
            and any(marker in evidence_text for marker in queue_markers)
            and not any(marker in evidence_text for marker in provider_phase_markers)
        ):
            raise PersonaAutomationError(
                "PERSONA_AUTOMATION_ACCEPTANCE_NOT_VERIFIED",
                "a queue receipt without PROMPT_SUBMITTED or STARTED cannot prove provider application",
                409,
            )
        with self._connection() as connection:
            row = self._get(connection, run_id)
            existing = connection.execute("SELECT * FROM persona_automation_review WHERE run_id = ? AND result_ref = ?", (run_id, result_ref)).fetchone()
            if existing is not None:
                if (existing["outcome"] != outcome or _load(existing["evidence_refs_json"], []) != evidence
                        or (existing["acceptance_status"] or "") != acceptance_status
                        or existing["note"] != value.get("note") or existing["next_action"] != value.get("next_action")
                        or existing["dispatch_id"] != dispatch_id or existing["assignment_revision"] != assignment_revision
                        or existing["source_message_id"] != source_message_id
                        or (existing["source_project_id"] or row["project_id"]) != (value.get("source_project_id") or row["project_id"])):
                    raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_IDEMPOTENCY_CONFLICT", "result_ref already refers to different review content", 409)
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_REVIEW_REPLAYED", "run": self._row(row), "review": self._review_row(existing)}
            assignment = _load(row["current_assignment_json"], None)
            if not isinstance(assignment, Mapping) or not str(assignment.get("dispatch_id") or ""):
                raise PersonaAutomationError("PERSONA_AUTOMATION_ASSIGNMENT_REQUIRED", "a current Master assignment is required before reviewing a result", 409)
            if (str(assignment.get("dispatch_id") or "") != dispatch_id
                    or str(assignment.get("message_id") or "") != source_message_id
                    or int(assignment.get("assignment_revision") or -1) != int(assignment_revision)):
                raise PersonaAutomationError("PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH", "result is not bound to the current assignment", 409)
            source_project_id = str(value.get("source_project_id") or row["project_id"]).strip()
            if source_project_id != str(row["project_id"]):
                raise PersonaAutomationError("PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH", "result belongs to another project", 409)
            prior_review = _load(row["current_review_json"], {})
            if str(prior_review.get("outcome") or "").upper() == "PASS":
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_ALREADY_PASSED", "a passed assignment cannot accept another result", 409)
            review_id = "persona_review_" + uuid.uuid4().hex[:24]
            now = _timestamp()
            connection.execute("INSERT INTO persona_automation_review(review_id, run_id, result_ref, outcome, acceptance_status, evidence_refs_json, note, next_action, dispatch_id, assignment_revision, source_message_id, source_project_id, source_reply_anchor_ref, source_reply_terminal_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)", (review_id, run_id, result_ref, outcome, acceptance_status, _json(evidence), value.get("note"), value.get("next_action"), dispatch_id, int(assignment_revision), source_message_id, source_project_id, now))
            review_payload = {"review_id": review_id, "result_ref": result_ref, "outcome": outcome, "acceptance_status": acceptance_status, "evidence_refs": evidence, "note": value.get("note"), "next_action": value.get("next_action"), "dispatch_id": dispatch_id, "assignment_revision": int(assignment_revision), "source_message_id": source_message_id, "source_project_id": source_project_id}
            updated_assignment = dict(assignment)
            updated_assignment.update({"state": "REVIEWED", "result_ref": result_ref, "review_id": review_id, "review_outcome": outcome})
            cursor = connection.execute("UPDATE persona_automation_run SET current_assignment_json = ?, current_review_json = ?, next_condition = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?", (_json(updated_assignment), _json(review_payload), value.get("next_action"), now, run_id, int(row["revision"])))
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording review", 409)
            event, _ = self._event(connection, run_id, "RESULT_REVIEWED", "review:" + result_ref, review_payload)
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_REVIEW_RECORDED", "run": self._row(self._get(connection, run_id)), "review": review_payload, "event": event}

    @staticmethod
    def _review_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"review_id": row["review_id"], "result_ref": row["result_ref"], "outcome": row["outcome"], "acceptance_status": row["acceptance_status"], "evidence_refs": _load(row["evidence_refs_json"], []), "note": row["note"], "next_action": row["next_action"], "dispatch_id": row["dispatch_id"], "assignment_revision": row["assignment_revision"], "source_message_id": row["source_message_id"], "source_project_id": row["source_project_id"], "source_reply_anchor_ref": row["source_reply_anchor_ref"], "source_reply_terminal_id": row["source_reply_terminal_id"], "created_at": row["created_at"]}

    def record_review_followup(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Record the exact Todo generated from one non-passing review.

        The Todo itself is owned by the work store.  This durable automation
        event is its idempotency receipt: a retry after creating the Todo but
        before this receipt is written reuses the caller-supplied deterministic
        Todo id, while a retry after the receipt is a pure replay.
        """

        run_id = _text(value.get("run_id"), "run_id")
        review_id = _text(value.get("review_id"), "review_id")
        todo_id = _text(value.get("todo_id"), "todo_id")
        source_todo_id = _text(value.get("source_todo_id"), "source_todo_id")
        payload = {
            "review_id": review_id,
            "todo_id": todo_id,
            "source_todo_id": source_todo_id,
            "outcome": _text(value.get("outcome"), "outcome").upper(),
        }
        with self._connection() as connection:
            row = self._get(connection, run_id)
            review = _load(row["current_review_json"], {})
            if str(review.get("review_id") or "") != review_id:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVIEW_FOLLOWUP_PROVENANCE_MISMATCH",
                    "follow-up Todo must be bound to the current review",
                    409,
                )
            if str(review.get("outcome") or "").upper() == "PASS":
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_REVIEW_FOLLOWUP_NOT_REQUIRED",
                    "a passing review cannot generate a follow-up Todo",
                    409,
                )
            event, created = self._event(
                connection,
                run_id,
                "REVIEW_FOLLOWUP_TODO_CREATED",
                "review-followup:" + review_id,
                payload,
            )
            return {
                "schema": SCHEMA,
                "status": (
                    "PERSONA_AUTOMATION_REVIEW_FOLLOWUP_RECORDED"
                    if created
                    else "PERSONA_AUTOMATION_REVIEW_FOLLOWUP_REPLAYED"
                ),
                "run": self._row(self._get(connection, run_id)),
                "followup": payload,
                "event": event,
            }

    def complete_run(self, value: Mapping[str, Any]) -> dict[str, Any]:
        run_id = _text(value.get("run_id"), "run_id")
        if value.get("complete") is not True:
            raise PersonaAutomationError("PERSONA_AUTOMATION_COMPLETION_CONFIRMATION_REQUIRED", "complete must be true after an explicit PASS review")
        with self._connection() as connection:
            row = self._get(connection, run_id)
            review = _load(row["current_review_json"], {})
            if review.get("outcome") != "PASS":
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVIEW_REQUIRED", "a PASS review is required before completion", 409)
            assignment = _load(row["current_assignment_json"], None)
            if not isinstance(assignment, Mapping) or assignment.get("state") != "REVIEWED" or assignment.get("dispatch_id") != review.get("dispatch_id") or assignment.get("result_ref") != review.get("result_ref") or assignment.get("review_id") != review.get("review_id"):
                raise PersonaAutomationError("PERSONA_AUTOMATION_RESULT_PROVENANCE_MISMATCH", "the PASS review is not bound to the current assignment", 409)
            if row["state"] == "COMPLETED":
                return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_COMPLETED_REPLAYED", "run": self._row(row), "changed": False}
            if row["state"] in {"STOPPED", "FAILED"}:
                raise PersonaAutomationError("PERSONA_AUTOMATION_STATE_CONFLICT", "a stopped or failed run cannot be completed", 409)
            expected_revision = value.get("expected_revision")
            if expected_revision is not None and (type(expected_revision) is not int or int(row["revision"]) != expected_revision):
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run revision changed", 409)
            now = _timestamp()
            cursor = connection.execute("UPDATE persona_automation_run SET state = 'COMPLETED', lease_owner = NULL, lease_expires_at = NULL, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?", (now, run_id, int(row["revision"])))
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed during completion", 409)
            self._event(connection, run_id, "RUN_COMPLETED", "complete:" + _text(value.get("request_id"), "request_id"), {"review": review})
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_COMPLETED", "run": self._row(self._get(connection, run_id)), "changed": True}

    def events(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            self._get(connection, _text(run_id, "run_id"))
            rows = connection.execute("SELECT event_id, event_type, idempotency_key, payload_json, created_at FROM persona_automation_event WHERE run_id = ? ORDER BY created_at DESC, event_id DESC LIMIT ?", (run_id, limit)).fetchall()
            return [{"event_id": row["event_id"], "event_type": row["event_type"], "idempotency_key": row["idempotency_key"], "payload": _load(row["payload_json"], {}), "created_at": row["created_at"]} for row in rows]

    def surface(self, project_id: str, *, node_ref: str | None = _NODE_REF_UNSET, session_anchor_ref: str | None = None) -> dict[str, Any]:
        project_id = _text(project_id, "project_id")
        with self._connection() as connection:
            clauses = ["project_id = ?"]
            params: list[Any] = [project_id]
            if node_ref is not _NODE_REF_UNSET:
                # node_ref=None here means "project-wide only" (node_ref IS
                # NULL), explicitly requested by the caller -- distinct from
                # omitting the argument, which applies no node_ref filter.
                clauses.append("node_ref IS ?")
                params.append(str(node_ref).strip() or None if node_ref else None)
            if session_anchor_ref is not None:
                clauses.append("session_anchor_ref = ?")
                params.append(str(session_anchor_ref).strip())
            rows = connection.execute("SELECT * FROM persona_automation_run WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC, run_id DESC LIMIT 25", tuple(params)).fetchall()
            runs = [self._row(row) for row in rows]
            active_states = {"RUNNING", "WAITING", "PAUSED"}
            current = next((run for run in runs if run["state"] in active_states), None)
            if current is not None:
                current["events"] = self.events(current["run_id"], 30)
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_SURFACE_COLLECTED", "project_id": project_id, "run": current, "last_run": runs[0] if runs else None, "runs": runs, "provider_invocation": "NONE_UNTIL_MASTER_DELIVERY"}
