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
                "SELECT run_id FROM persona_automation_run WHERE project_id = ? "
                "AND (node_ref IS ?) AND state IN ('RUNNING','WAITING','PAUSED') "
                "ORDER BY updated_at DESC LIMIT 1",
                (project_id, node_ref),
            ).fetchone()
            if active is not None:
                raise PersonaAutomationError(
                    "PERSONA_AUTOMATION_ACTIVE_RUN_EXISTS",
                    "this node already has an active persona automation run"
                    if node_ref
                    else "project already has an active persona automation run",
                    409,
                )
            connection.execute(
                "INSERT INTO persona_automation_run(run_id, project_id, session_anchor_ref, persona_id, persona_revision, assignment_revision, scope_text, instruction_text, goal_ref, goal_version, node_ref, budget_json, state, cursor_json, idempotency_key, request_digest, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?)",
                (run_id, project_id, anchor, assignment["persona_id"], int(assignment["persona_revision"]), int(assignment["assignment_revision"]), scope, instruction, value.get("goal_ref"), value.get("goal_version"), node_ref, _json(value.get("budget") or {}), _json({}), key, digest, now, now),
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

    def dispatch_work(self, value: Mapping[str, Any], enqueue: Callable[[str, Mapping[str, Any]], tuple[Mapping[str, Any], bool]]) -> dict[str, Any]:
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
            message_value = {"idempotency_key": f"persona-automation:{run_id}:{dispatch_id}", "title": title, "instruction": instruction, "node_ref": row["node_ref"], "metadata": {"persona_automation_run_id": run_id, "persona_automation_assignment_id": assignment_id, "persona_automation_assignment_revision": assignment_revision, "dispatch_id": dispatch_id, "session_anchor_ref": row["session_anchor_ref"], "persona_id": row["persona_id"], "persona_revision": int(row["persona_revision"]), "completion_conditions": conditions, "provider_invocation": "DEFERRED_TO_MASTER_QUEUE", "completion_route": "PERSONA_AUTOMATION_STORE"}}
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
            assignment = {"assignment_id": assignment_id, "assignment_revision": assignment_revision, "state": "DISPATCHED", "dispatch_id": dispatch_id, "message_id": message.get("message_id"), "created": bool(created), "title": title, "completion_conditions": conditions, "result_ref": None, "review_id": None}
            now = _timestamp()
            cursor = connection.execute("UPDATE persona_automation_run SET current_assignment_json = ?, revision = revision + 1, updated_at = ? WHERE run_id = ? AND revision = ?", (_json(assignment), now, run_id, int(row["revision"])))
            if cursor.rowcount != 1:
                raise PersonaAutomationError("PERSONA_AUTOMATION_REVISION_CONFLICT", "automation run changed while recording dispatch", 409)
            event, _ = self._event(connection, run_id, "WORK_DISPATCHED", "dispatch:" + dispatch_id, {"dispatch": assignment, "message": dict(message)})
            return {"schema": SCHEMA, "status": "PERSONA_AUTOMATION_WORK_DISPATCHED" if created else "PERSONA_AUTOMATION_WORK_REPLAYED", "run": self._row(self._get(connection, run_id)), "dispatch": assignment, "message": dict(message), "event": event}

    def record_master_completion(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Bind an exact Master completion to its automation dispatch.

        A Master calls ``master.complete`` to report to the server.  That is
        the control-plane acknowledgement for Persona automation; it must not
        be routed back into the same Master session's inbox.  The resulting
        state is deliberately *not* a review or acceptance: an independent
        Reviewer still has to record the verdict through ``record_review``.
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
            payload = {
                "dispatch_id": dispatch_id,
                "assignment_revision": int(assignment_revision),
                "source_message_id": source_message_id,
                "result_ref": result_ref,
                "body_text_utf8_sha256": body_digest,
                "completed_at": completed_at,
                "route": "LEGACY_SELF_REPLY_MIGRATION" if legacy_self_reply_migration else "AUTOMATION_STORE",
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

    def surface(self, project_id: str, *, node_ref: str | None = None, session_anchor_ref: str | None = None) -> dict[str, Any]:
        project_id = _text(project_id, "project_id")
        with self._connection() as connection:
            clauses = ["project_id = ?"]
            params: list[Any] = [project_id]
            if node_ref is not None:
                clauses.append("node_ref IS ?")
                params.append(str(node_ref).strip() or None)
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
