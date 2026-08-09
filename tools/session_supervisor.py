from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

from process_identity import process_instance_observation, redact_sensitive_argv


SESSION_SUPERVISOR_SCHEMA = "universe.session-supervisor.v1"
PERSISTENT_SESSION_KIND = "PERSISTENT_MODE_SESSION"
SESSION_STATES = frozenset(
    {"REGISTERED", "STARTING", "LIVE", "DISCONNECTED", "STOPPED", "UNKNOWN"}
)
CURRENTNESS_STATES = frozenset({"UNKNOWN", "CURRENT", "STALE"})
LEASE_STATES = frozenset({"OWNED", "STALE", "UNKNOWN", "STOP_AUTHORIZED", "RELEASED"})
PROCESS_IDENTITY_FIELDS = (
    "pid",
    "process_created_at",
    "executable",
    "command",
    "endpoint",
    "handshake_fingerprint",
)


class SessionSupervisorError(RuntimeError):
    def __init__(self, code: str, detail: str, *, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SessionSupervisorError(
            "SESSION_SUPERVISOR_REQUEST_INVALID", f"{field} must be non-empty text"
        )
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SessionSupervisorError(
            "SESSION_SUPERVISOR_REQUEST_INVALID",
            f"{field} must be a non-negative integer",
        )
    return value


def _json_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SessionSupervisorError(
            "SESSION_SUPERVISOR_REQUEST_INVALID", f"{field} must be an object"
        )
    return dict(value)


def _canonical_process_timestamp(value: Any) -> str:
    text = _required_text(value, "process_identity.process_created_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID",
            "process_identity.process_created_at must be ISO-8601",
        ) from error
    if parsed.tzinfo is None:
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID",
            "process_identity.process_created_at must include a timezone",
        )
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _normalize_loopback_endpoint(value: Any) -> str:
    endpoint = _required_text(value, "process_identity.endpoint")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID", "process_identity.endpoint is invalid"
        ) from error
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID",
            "process_identity.endpoint must be a secret-free loopback HTTP origin",
        )
    return endpoint


def _normalize_session_descriptor(value: Any) -> dict[str, Any]:
    item = _json_object(value, "session")
    session_kind = item.get("session_kind", PERSISTENT_SESSION_KIND)
    session_kind = _required_text(session_kind, "session.session_kind").upper()
    if session_kind != PERSISTENT_SESSION_KIND:
        raise SessionSupervisorError(
            "SESSION_KIND_FORBIDDEN",
            "only persistent Mode sessions may enter the Supervisor registry",
            status=409,
        )
    state = _required_text(item.get("state", "REGISTERED"), "session.state").upper()
    if state not in SESSION_STATES:
        raise SessionSupervisorError(
            "SESSION_STATE_INVALID", f"unsupported session state: {state}"
        )
    currentness = _required_text(
        item.get("currentness", "UNKNOWN"), "session.currentness"
    ).upper()
    if currentness not in CURRENTNESS_STATES:
        raise SessionSupervisorError(
            "SESSION_CURRENTNESS_INVALID", f"unsupported currentness: {currentness}"
        )
    session_id = item.get("session_id")
    if session_id is None:
        session_id = f"session_{secrets.token_hex(12)}"
    return {
        "session_id": _required_text(session_id, "session.session_id"),
        "node": _required_text(item.get("node"), "session.node"),
        "mode": _required_text(item.get("mode"), "session.mode").upper(),
        "provider": _required_text(item.get("provider"), "session.provider").upper(),
        "provider_session_ref": _optional_text(
            item.get("provider_session_ref"), "session.provider_session_ref"
        ),
        "anchor_ref": _optional_text(
            item.get("anchor_ref") or item.get("current_anchor_ref"),
            "session.anchor_ref",
        ),
        "alias": _optional_text(item.get("alias"), "session.alias"),
        "session_kind": session_kind,
        "state": state,
        "currentness": currentness,
        "bounded_summary": _optional_text(
            item.get("bounded_summary"), "session.bounded_summary"
        ),
    }


def _normalize_process_identity(value: Any) -> dict[str, Any]:
    item = _json_object(value, "process_identity")
    pid = item.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID", "process_identity.pid must be positive"
        )
    command = item.get("command")
    if not isinstance(command, list) or not command:
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID",
            "process_identity.command must be a non-empty argument array",
        )
    normalized_command: list[str] = []
    for index, argument in enumerate(command):
        if not isinstance(argument, str):
            raise SessionSupervisorError(
                "PROCESS_IDENTITY_INVALID",
                f"process_identity.command[{index}] must be text",
            )
        normalized_command.append(argument)
    normalized_command = _fingerprint_command_arguments(normalized_command)
    fingerprint = _required_text(
        item.get("handshake_fingerprint"),
        "process_identity.handshake_fingerprint",
    ).lower()
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_INVALID",
            "handshake_fingerprint must be a SHA-256 hex digest",
        )
    if "handshake_token" in item:
        raise SessionSupervisorError(
            "PROCESS_IDENTITY_SECRET_FORBIDDEN",
            "raw handshake tokens must not enter the Supervisor registry",
        )
    return {
        "pid": pid,
        "process_created_at": _canonical_process_timestamp(
            item.get("process_created_at")
        ),
        "executable": _required_text(
            item.get("executable"), "process_identity.executable"
        ),
        "command": normalized_command,
        "endpoint": _normalize_loopback_endpoint(item.get("endpoint")),
        "handshake_fingerprint": fingerprint,
    }


def _process_identity_matches(
    stored: Mapping[str, Any], observed: Mapping[str, Any]
) -> bool:
    return all(stored[field] == observed[field] for field in PROCESS_IDENTITY_FIELDS)


def _fingerprint_command_arguments(command: list[str]) -> list[str]:
    fingerprinted: list[str] = []
    for argument in redact_sensitive_argv(command):
        if (
            argument.startswith("sha256:")
            and len(argument) == 71
            and all(character in "0123456789abcdef" for character in argument[7:])
        ):
            fingerprinted.append(argument)
        else:
            fingerprinted.append(
                "sha256:" + hashlib.sha256(argument.encode("utf-8")).hexdigest()
            )
    return fingerprinted


class SessionSupervisorStore:
    """Durable, provider-neutral registry owned by the local Supervisor service."""

    def __init__(
        self,
        database_path: Path,
        *,
        process_observer: Callable[[int, str], Mapping[str, Any]] = (
            process_instance_observation
        ),
    ):
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.process_observer = process_observer
        self._initialize()

    def _observe_process_instance(
        self, identity: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            observation = self.process_observer(
                int(identity["pid"]), str(identity["process_created_at"])
            )
        except Exception as error:  # noqa: BLE001 - observation fails closed
            return {
                "status": "UNKNOWN",
                "reason": "PROCESS_OBSERVER_FAILED",
                "detail": f"{type(error).__name__}: {error}",
            }
        if not isinstance(observation, Mapping):
            return {
                "status": "UNKNOWN",
                "reason": "PROCESS_OBSERVATION_INVALID",
            }
        return dict(observation)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
            else:
                with connection:
                    yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_record (
                    session_id TEXT PRIMARY KEY,
                    node TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_session_ref TEXT,
                    anchor_ref TEXT,
                    alias TEXT,
                    session_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    currentness TEXT NOT NULL,
                    bounded_summary TEXT,
                    row_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(session_kind = 'PERSISTENT_MODE_SESSION'),
                    CHECK(state IN ('REGISTERED','STARTING','LIVE','DISCONNECTED','STOPPED','UNKNOWN')),
                    CHECK(currentness IN ('UNKNOWN','CURRENT','STALE'))
                );

                CREATE INDEX IF NOT EXISTS session_record_target
                ON session_record(node, mode, updated_at DESC);

                CREATE TABLE IF NOT EXISTS session_binding_history (
                    binding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES session_record(session_id),
                    provider TEXT NOT NULL,
                    provider_session_ref TEXT,
                    mode TEXT NOT NULL,
                    bound_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS process_lease (
                    lease_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE REFERENCES session_record(session_id),
                    pid INTEGER NOT NULL,
                    process_created_at TEXT NOT NULL,
                    executable TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    handshake_fingerprint TEXT NOT NULL,
                    lease_token_sha256 TEXT NOT NULL,
                    lease_version INTEGER NOT NULL,
                    lease_state TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(lease_state IN ('OWNED','STALE','UNKNOWN','STOP_AUTHORIZED','RELEASED'))
                );

                CREATE TABLE IF NOT EXISTS target_default_session (
                    node TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    session_id TEXT NOT NULL REFERENCES session_record(session_id),
                    pointer_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(node, mode)
                );

                CREATE TABLE IF NOT EXISTS supervisor_event (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT REFERENCES session_record(session_id),
                    event_type TEXT NOT NULL,
                    prior_state TEXT,
                    state TEXT,
                    reason TEXT,
                    details_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS supervisor_event_session_time
                ON supervisor_event(session_id, occurred_at, event_id);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(session_record)")
            }
            if "anchor_ref" not in columns:
                connection.execute(
                    "ALTER TABLE session_record ADD COLUMN anchor_ref TEXT"
                )
            connection.execute(
                """
                UPDATE session_record AS record
                SET alias = CASE
                    WHEN record.node = record.mode THEN record.node
                    ELSE record.node || ' ' || record.mode
                END
                WHERE EXISTS (
                    SELECT 1
                    FROM session_binding_history AS history
                    WHERE history.session_id = record.session_id
                      AND record.alias = (
                          CASE
                              WHEN record.node = record.mode THEN record.node
                              ELSE record.node || ' ' || record.mode
                          END
                      ) || ' | ' || history.provider
                )
                """
            )

    @staticmethod
    def _default_alias(session: Mapping[str, Any]) -> str:
        node = str(session["node"])
        mode = str(session["mode"])
        # Provider is replaceable transport, not part of the Anchor Session name.
        return node if node == mode else f"{node} {mode}"

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        session_id: str | None,
        event_type: str,
        prior_state: str | None = None,
        state: str | None = None,
        reason: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO supervisor_event(
                session_id, event_type, prior_state, state, reason,
                details_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                event_type,
                prior_state,
                state,
                reason,
                json.dumps(dict(details or {}), sort_keys=True, separators=(",", ":")),
                utc_now(),
            ),
        )

    def register_session(self, value: Any) -> tuple[dict[str, Any], bool]:
        session = _normalize_session_descriptor(value)
        now = utc_now()
        with self._connection(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM session_record WHERE session_id = ?",
                (session["session_id"],),
            ).fetchone()
            if existing is not None:
                material = self._session_material(connection, existing)
                comparable = {
                    key: material[key]
                    for key in (
                        "node",
                        "mode",
                        "session_kind",
                    )
                }
                requested = {key: session[key] for key in comparable}
                if comparable != requested:
                    raise SessionSupervisorError(
                        "SESSION_IDENTITY_CONFLICT",
                        "session_id is already bound to a different identity",
                        status=409,
                    )
                provider_changed = (
                    material["provider"] != session["provider"]
                    or material["provider_session_ref"]
                    != session["provider_session_ref"]
                )
                summary = session.get("bounded_summary")
                connection.execute(
                    """
                    UPDATE session_record
                    SET provider = ?, provider_session_ref = ?,
                        anchor_ref = COALESCE(?, anchor_ref),
                        state = ?,
                        currentness = CASE
                            WHEN ? = 'UNKNOWN' THEN currentness
                            ELSE ?
                        END,
                        updated_at = ?,
                        bounded_summary = CASE
                            WHEN ? IS NOT NULL AND TRIM(?) != '' THEN ?
                            ELSE bounded_summary
                        END,
                        row_version = row_version + 1
                    WHERE session_id = ?
                    """,
                    (
                        session["provider"],
                        session["provider_session_ref"],
                        session["anchor_ref"],
                        session["state"],
                        session["currentness"],
                        session["currentness"],
                        now,
                        summary,
                        summary,
                        summary,
                        session["session_id"],
                    ),
                )
                if provider_changed:
                    connection.execute(
                        """
                        INSERT INTO session_binding_history(
                            session_id, provider, provider_session_ref, mode, bound_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            session["session_id"],
                            session["provider"],
                            session["provider_session_ref"],
                            session["mode"],
                            now,
                        ),
                    )
                self._event(
                    connection,
                    session_id=session["session_id"],
                    event_type=(
                        "PROVIDER_SESSION_REBOUND"
                        if provider_changed
                        else "SESSION_REOBSERVED"
                    ),
                    prior_state=material.get("state"),
                    state=session["state"],
                    details={
                        "source": "register_session_idempotent",
                        "provider_changed": provider_changed,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM session_record WHERE session_id = ?",
                    (session["session_id"],),
                ).fetchone()
                if row is None:
                    return material, False
                return self._session_material(connection, row), False
            alias = session["alias"] or self._default_alias(session)
            connection.execute(
                """
                INSERT INTO session_record(
                    session_id, node, mode, provider, provider_session_ref,
                    anchor_ref, alias,
                    session_kind, state, currentness, bounded_summary,
                    row_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    session["session_id"],
                    session["node"],
                    session["mode"],
                    session["provider"],
                    session["provider_session_ref"],
                    session["anchor_ref"],
                    alias,
                    session["session_kind"],
                    session["state"],
                    session["currentness"],
                    session["bounded_summary"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO session_binding_history(
                    session_id, provider, provider_session_ref, mode, bound_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session["session_id"],
                    session["provider"],
                    session["provider_session_ref"],
                    session["mode"],
                    now,
                ),
            )
            self._event(
                connection,
                session_id=session["session_id"],
                event_type="SESSION_REGISTERED",
                state=session["state"],
                details={"node": session["node"], "mode": session["mode"]},
            )
            row = connection.execute(
                "SELECT * FROM session_record WHERE session_id = ?",
                (session["session_id"],),
            ).fetchone()
            if row is None:
                raise SessionSupervisorError(
                    "SESSION_SUPERVISOR_INVARIANT_FAILED",
                    "registered session was not observable after insert",
                    status=500,
                )
            return self._session_material(connection, row), True

    def bind_current_anchor(
        self,
        session_id: str,
        *,
        anchor_ref: Any,
        expected_version: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        normalized_anchor = _required_text(anchor_ref, "anchor_ref")
        version = _non_negative_integer(expected_version, "expected_version")
        now = utc_now()
        with self._connection(immediate=True) as connection:
            row = self._require_session(connection, normalized_id)
            if int(row["row_version"]) != version:
                raise SessionSupervisorError(
                    "SESSION_VERSION_CONFLICT", "session row version changed", status=409
                )
            connection.execute(
                """
                UPDATE session_record
                SET anchor_ref = ?, currentness = 'CURRENT',
                    row_version = ?, updated_at = ?
                WHERE session_id = ? AND row_version = ?
                """,
                (normalized_anchor, version + 1, now, normalized_id, version),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="CURRENT_ANCHOR_BOUND",
                prior_state=row["state"],
                state=row["state"],
                details={"anchor_ref": normalized_anchor},
            )
            return self._session_material(
                connection, self._require_session(connection, normalized_id)
            )

    def bind_provider_session(
        self,
        session_id: str,
        *,
        provider: Any,
        provider_session_ref: Any,
        expected_version: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        normalized_provider = _required_text(provider, "provider").upper()
        normalized_ref = _required_text(provider_session_ref, "provider_session_ref")
        version = _non_negative_integer(expected_version, "expected_version")
        now = utc_now()
        with self._connection(immediate=True) as connection:
            row = self._require_session(connection, normalized_id)
            if int(row["row_version"]) != version:
                raise SessionSupervisorError(
                    "SESSION_VERSION_CONFLICT", "session row version changed", status=409
                )
            next_version = version + 1
            connection.execute(
                """
                UPDATE session_record
                SET provider = ?, provider_session_ref = ?, row_version = ?, updated_at = ?
                WHERE session_id = ? AND row_version = ?
                """,
                (
                    normalized_provider,
                    normalized_ref,
                    next_version,
                    now,
                    normalized_id,
                    version,
                ),
            )
            connection.execute(
                """
                INSERT INTO session_binding_history(
                    session_id, provider, provider_session_ref, mode, bound_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (normalized_id, normalized_provider, normalized_ref, row["mode"], now),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="PROVIDER_SESSION_BOUND",
                state=row["state"],
                details={"provider": normalized_provider},
            )
            return self._session_material(
                connection, self._require_session(connection, normalized_id)
            )

    def update_alias(
        self, session_id: str, *, alias: Any, expected_version: Any
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        normalized_alias = _required_text(alias, "alias")
        version = _non_negative_integer(expected_version, "expected_version")
        with self._connection(immediate=True) as connection:
            row = self._require_session(connection, normalized_id)
            if int(row["row_version"]) != version:
                raise SessionSupervisorError(
                    "SESSION_VERSION_CONFLICT", "session row version changed", status=409
                )
            now = utc_now()
            connection.execute(
                """
                UPDATE session_record
                SET alias = ?, row_version = ?, updated_at = ?
                WHERE session_id = ? AND row_version = ?
                """,
                (normalized_alias, version + 1, now, normalized_id, version),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="SESSION_ALIAS_UPDATED",
                state=row["state"],
                details={"alias": normalized_alias},
            )
            return self._session_material(
                connection, self._require_session(connection, normalized_id)
            )

    def set_default(
        self, session_id: str, *, expected_pointer_version: Any
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        expected = _non_negative_integer(
            expected_pointer_version, "expected_pointer_version"
        )
        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            current = connection.execute(
                "SELECT * FROM target_default_session WHERE node = ? AND mode = ?",
                (session["node"], session["mode"]),
            ).fetchone()
            current_version = 0 if current is None else int(current["pointer_version"])
            if current_version != expected:
                raise SessionSupervisorError(
                    "DEFAULT_SESSION_VERSION_CONFLICT",
                    "default session pointer version changed",
                    status=409,
                )
            next_version = current_version + 1
            now = utc_now()
            connection.execute(
                """
                INSERT INTO target_default_session(
                    node, mode, session_id, pointer_version, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node, mode) DO UPDATE SET
                    session_id = excluded.session_id,
                    pointer_version = excluded.pointer_version,
                    updated_at = excluded.updated_at
                """,
                (session["node"], session["mode"], normalized_id, next_version, now),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="DEFAULT_SESSION_SELECTED",
                state=session["state"],
                details={"pointer_version": next_version},
            )
            return {
                "node": session["node"],
                "mode": session["mode"],
                "session_id": normalized_id,
                "pointer_version": next_version,
                "updated_at": now,
            }

    def acquire_lease(
        self,
        session_id: str,
        process_identity: Any,
        *,
        expected_lease_version: Any = 0,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        identity = _normalize_process_identity(process_identity)
        expected = _non_negative_integer(expected_lease_version, "expected_lease_version")
        raw_token = secrets.token_urlsafe(32)
        token_digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = utc_now()
        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            current = connection.execute(
                "SELECT * FROM process_lease WHERE session_id = ?", (normalized_id,)
            ).fetchone()
            current_version = 0 if current is None else int(current["lease_version"])
            if current_version != expected:
                raise SessionSupervisorError(
                    "PROCESS_LEASE_VERSION_CONFLICT",
                    "process lease version changed",
                    status=409,
                )
            if current is not None and current["lease_state"] not in {
                "RELEASED",
                "STALE",
            }:
                raise SessionSupervisorError(
                    "PROCESS_LEASE_ACTIVE",
                    "an existing process lease must be released or proven stale",
                    status=409,
                )
            next_version = current_version + 1
            lease_id = (
                f"lease_{secrets.token_hex(12)}"
                if current is None
                else str(current["lease_id"])
            )
            values = (
                lease_id,
                normalized_id,
                identity["pid"],
                identity["process_created_at"],
                identity["executable"],
                json.dumps(identity["command"], ensure_ascii=False, separators=(",", ":")),
                identity["endpoint"],
                identity["handshake_fingerprint"],
                token_digest,
                next_version,
                "OWNED",
                now if current is None else current["acquired_at"],
                now,
            )
            connection.execute(
                """
                INSERT INTO process_lease(
                    lease_id, session_id, pid, process_created_at, executable,
                    command_json, endpoint, handshake_fingerprint,
                    lease_token_sha256, lease_version, lease_state,
                    acquired_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    pid = excluded.pid,
                    process_created_at = excluded.process_created_at,
                    executable = excluded.executable,
                    command_json = excluded.command_json,
                    endpoint = excluded.endpoint,
                    handshake_fingerprint = excluded.handshake_fingerprint,
                    lease_token_sha256 = excluded.lease_token_sha256,
                    lease_version = excluded.lease_version,
                    lease_state = excluded.lease_state,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            connection.execute(
                """
                UPDATE session_record
                SET state = 'LIVE', row_version = row_version + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (now, normalized_id),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="PROCESS_LEASE_ACQUIRED",
                prior_state=session["state"],
                state="LIVE",
                details={"lease_id": lease_id, "lease_version": next_version},
            )
            return {
                "schema": SESSION_SUPERVISOR_SCHEMA,
                "status": "PROCESS_LEASE_ACQUIRED",
                "lease": self._lease_material(
                    connection.execute(
                        "SELECT * FROM process_lease WHERE session_id = ?",
                        (normalized_id,),
                    ).fetchone()
                ),
                "lease_token": raw_token,
            }

    def reconcile(
        self,
        session_id: str,
        observed_identity: Any,
        *,
        expected_lease_version: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        observed = _normalize_process_identity(observed_identity)
        host_observation = self._observe_process_instance(observed)
        expected = _non_negative_integer(expected_lease_version, "expected_lease_version")
        now = utc_now()
        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            lease = self._require_lease(connection, normalized_id)
            if int(lease["lease_version"]) != expected:
                raise SessionSupervisorError(
                    "PROCESS_LEASE_VERSION_CONFLICT",
                    "process lease version changed",
                    status=409,
                )
            if session["state"] == "STOPPED" or lease["lease_state"] in {
                "RELEASED",
                "STOP_AUTHORIZED",
                "STALE",
            }:
                raise SessionSupervisorError(
                    "PROCESS_LEASE_NOT_RECONCILABLE",
                    "terminal or stop-authorized leases require an explicit lease acquisition",
                    status=409,
                )
            stored = self._lease_identity(lease)
            reported_exact = _process_identity_matches(stored, observed)
            host_exact = host_observation.get("status") == "PROCESS_PRESENT_EXACT"
            exact = reported_exact and host_exact
            next_state = "OWNED" if exact else "UNKNOWN"
            session_state = "LIVE" if exact else "UNKNOWN"
            next_version = expected + 1
            connection.execute(
                """
                UPDATE process_lease
                SET lease_state = ?, lease_version = ?, updated_at = ?
                WHERE session_id = ? AND lease_version = ?
                """,
                (next_state, next_version, now, normalized_id, expected),
            )
            connection.execute(
                """
                UPDATE session_record
                SET state = ?, row_version = row_version + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (session_state, now, normalized_id),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="PROCESS_RECONCILED" if exact else "PROCESS_IDENTITY_MISMATCH",
                prior_state=session["state"],
                state=session_state,
                reason=None if exact else "FULL_PROCESS_IDENTITY_MISMATCH",
                details={
                    "lease_version": next_version,
                    "exact_match": exact,
                    "reported_identity_match": reported_exact,
                    "host_process_observation": host_observation,
                },
            )
            return {
                "schema": SESSION_SUPERVISOR_SCHEMA,
                "status": "PROCESS_RECONCILED" if exact else "PROCESS_IDENTITY_UNKNOWN",
                "exact_match": exact,
                "destructive_action_permitted": exact,
                "session": self._session_material(
                    connection, self._require_session(connection, normalized_id)
                ),
            }

    def authorize_stop(
        self,
        session_id: str,
        observed_identity: Any,
        *,
        lease_token: Any,
        expected_lease_version: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        observed = _normalize_process_identity(observed_identity)
        host_observation = self._observe_process_instance(observed)
        token = _required_text(lease_token, "lease_token")
        expected = _non_negative_integer(expected_lease_version, "expected_lease_version")
        denied = False
        denial_detail = ""
        receipt: dict[str, Any] | None = None
        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            lease = self._require_lease(connection, normalized_id)
            if int(lease["lease_version"]) != expected:
                raise SessionSupervisorError(
                    "PROCESS_LEASE_VERSION_CONFLICT",
                    "process lease version changed",
                    status=409,
                )
            token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            reported_exact = _process_identity_matches(
                self._lease_identity(lease), observed
            )
            host_exact = host_observation.get("status") == "PROCESS_PRESENT_EXACT"
            exact = reported_exact and host_exact
            token_matches = secrets.compare_digest(
                token_digest, str(lease["lease_token_sha256"])
            )
            if not token_matches:
                denied = True
                denial_detail = (
                    "stop requires an exact process identity and current lease capability"
                )
            elif lease["lease_state"] == "UNKNOWN" or not exact:
                connection.execute(
                    """
                    UPDATE process_lease
                    SET lease_state = 'UNKNOWN', lease_version = ?, updated_at = ?
                    WHERE session_id = ? AND lease_version = ?
                    """,
                    (expected + 1, utc_now(), normalized_id, expected),
                )
                connection.execute(
                    """
                    UPDATE session_record
                    SET state = 'UNKNOWN', row_version = row_version + 1, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (utc_now(), normalized_id),
                )
                self._event(
                    connection,
                    session_id=normalized_id,
                    event_type="STOP_AUTHORIZATION_DENIED",
                    prior_state=session["state"],
                    state="UNKNOWN",
                    reason="LEASE_OR_PROCESS_IDENTITY_MISMATCH",
                    details={"host_process_observation": host_observation},
                )
                denied = True
                denial_detail = (
                    "stop requires an exact process identity and current lease capability"
                )
            else:
                next_version = expected + 1
                connection.execute(
                    """
                    UPDATE process_lease
                    SET lease_state = 'STOP_AUTHORIZED', lease_version = ?, updated_at = ?
                    WHERE session_id = ? AND lease_version = ?
                    """,
                    (next_version, utc_now(), normalized_id, expected),
                )
                self._event(
                    connection,
                    session_id=normalized_id,
                    event_type="STOP_AUTHORIZED",
                    prior_state=session["state"],
                    state=session["state"],
                    details={"lease_version": next_version},
                )
                receipt = {
                    "schema": SESSION_SUPERVISOR_SCHEMA,
                    "status": "STOP_AUTHORIZED",
                    "session_id": normalized_id,
                    "lease_id": lease["lease_id"],
                    "lease_version": next_version,
                    "process_identity_digest": hashlib.sha256(
                        json.dumps(
                            observed,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                }
        if denied:
            raise SessionSupervisorError(
                "STOP_AUTHORIZATION_DENIED", denial_detail, status=409
            )
        if receipt is None:
            raise SessionSupervisorError(
                "SESSION_SUPERVISOR_INVARIANT_FAILED",
                "stop authorization completed without a receipt",
                status=500,
            )
        return receipt

    def complete_stop(
        self,
        session_id: str,
        *,
        lease_token: Any,
        expected_lease_version: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        token = _required_text(lease_token, "lease_token")
        expected = _non_negative_integer(expected_lease_version, "expected_lease_version")
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            lease = self._require_lease(connection, normalized_id)
            if (
                int(lease["lease_version"]) != expected
                or lease["lease_state"] != "STOP_AUTHORIZED"
                or not secrets.compare_digest(
                    token_digest, str(lease["lease_token_sha256"])
                )
            ):
                raise SessionSupervisorError(
                    "STOP_COMPLETION_DENIED",
                    "stop completion requires the current authorized lease",
                    status=409,
                )
            now = utc_now()
            connection.execute(
                """
                UPDATE process_lease
                SET lease_state = 'RELEASED', lease_version = ?, updated_at = ?
                WHERE session_id = ? AND lease_version = ?
                """,
                (expected + 1, now, normalized_id, expected),
            )
            connection.execute(
                """
                UPDATE session_record
                SET state = 'STOPPED', row_version = row_version + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (now, normalized_id),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="PROCESS_STOPPED",
                prior_state=session["state"],
                state="STOPPED",
                details={"lease_version": expected + 1},
            )
            return self._session_material(
                connection, self._require_session(connection, normalized_id)
            )

    def mark_lease_stale(
        self,
        session_id: str,
        process_identity: Any,
        *,
        lease_token: Any,
        expected_lease_version: Any,
        reason: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        observed = _normalize_process_identity(process_identity)
        token = _required_text(lease_token, "lease_token")
        expected = _non_negative_integer(expected_lease_version, "expected_lease_version")
        normalized_reason = _required_text(reason, "reason")
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            lease = self._require_lease(connection, normalized_id)
            if (
                int(lease["lease_version"]) != expected
                or not secrets.compare_digest(
                    token_digest, str(lease["lease_token_sha256"])
                )
                or not _process_identity_matches(self._lease_identity(lease), observed)
            ):
                raise SessionSupervisorError(
                    "PROCESS_STALE_MARK_DENIED",
                    "stale marking requires the exact owned process lease",
                    status=409,
                )
            now = utc_now()
            connection.execute(
                """
                UPDATE process_lease
                SET lease_state = 'STALE', lease_version = ?, updated_at = ?
                WHERE session_id = ? AND lease_version = ?
                """,
                (expected + 1, now, normalized_id, expected),
            )
            connection.execute(
                """
                UPDATE session_record
                SET state = 'DISCONNECTED', row_version = row_version + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (now, normalized_id),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="PROCESS_LEASE_STALE",
                prior_state=session["state"],
                state="DISCONNECTED",
                reason=normalized_reason,
                details={"lease_version": expected + 1},
            )
            return self._session_material(
                connection, self._require_session(connection, normalized_id)
            )

    def recover_unknown_lease_if_process_absent(
        self,
        session_id: str,
        *,
        expected_lease_version: Any,
        operator_evidence_ref: Any,
    ) -> dict[str, Any]:
        normalized_id = _required_text(session_id, "session_id")
        expected = _non_negative_integer(
            expected_lease_version, "expected_lease_version"
        )
        evidence_ref = _required_text(operator_evidence_ref, "operator_evidence_ref")
        with self._connection() as connection:
            lease = self._require_lease(connection, normalized_id)
            if (
                int(lease["lease_version"]) != expected
                or lease["lease_state"] != "UNKNOWN"
            ):
                raise SessionSupervisorError(
                    "UNKNOWN_LEASE_RECOVERY_DENIED",
                    "recovery requires the current UNKNOWN process lease",
                    status=409,
                )
            identity = self._lease_identity(lease)

        observation = self._observe_process_instance(identity)
        if observation.get("status") != "ORIGINAL_PROCESS_ABSENT":
            raise SessionSupervisorError(
                "PROCESS_ABSENCE_NOT_PROVEN",
                "UNKNOWN lease recovery requires Supervisor-observed process absence",
                status=409,
            )

        with self._connection(immediate=True) as connection:
            session = self._require_session(connection, normalized_id)
            lease = self._require_lease(connection, normalized_id)
            if (
                int(lease["lease_version"]) != expected
                or lease["lease_state"] != "UNKNOWN"
            ):
                raise SessionSupervisorError(
                    "UNKNOWN_LEASE_RECOVERY_DENIED",
                    "process lease changed after absence observation",
                    status=409,
                )
            now = utc_now()
            next_version = expected + 1
            connection.execute(
                """
                UPDATE process_lease
                SET lease_state = 'RELEASED', lease_version = ?, updated_at = ?
                WHERE session_id = ? AND lease_version = ?
                """,
                (next_version, now, normalized_id, expected),
            )
            connection.execute(
                """
                UPDATE session_record
                SET state = 'DISCONNECTED', row_version = row_version + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (now, normalized_id),
            )
            self._event(
                connection,
                session_id=normalized_id,
                event_type="UNKNOWN_LEASE_RELEASED_PROCESS_ABSENT",
                prior_state=session["state"],
                state="DISCONNECTED",
                reason="SUPERVISOR_OBSERVED_PROCESS_ABSENCE",
                details={
                    "lease_version": next_version,
                    "operator_evidence_ref": evidence_ref,
                    "absence_observation": observation,
                    "process_termination": "NOT_PERFORMED",
                },
            )
            result = self._session_material(
                connection, self._require_session(connection, normalized_id)
            )
        result["recovery"] = {
            "status": "UNKNOWN_LEASE_RECOVERED",
            "absence_observation": observation,
            "process_termination": "NOT_PERFORMED",
        }
        return result

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            return self._session_material(
                connection, self._require_session(connection, session_id)
            )

    def list_sessions(
        self, *, node: str | None = None, mode: str | None = None
    ) -> list[dict[str, Any]]:
        normalized_node = None if node is None else _required_text(node, "node")
        normalized_mode = (
            None if mode is None else _required_text(mode, "mode").upper()
        )
        with self._connection() as connection:
            if normalized_node is not None and normalized_mode is not None:
                rows = connection.execute(
                    """
                    SELECT * FROM session_record
                    WHERE node = ? AND mode = ?
                    ORDER BY updated_at DESC, session_id
                    """,
                    (normalized_node, normalized_mode),
                ).fetchall()
            elif normalized_node is not None:
                rows = connection.execute(
                    """
                    SELECT * FROM session_record WHERE node = ?
                    ORDER BY updated_at DESC, session_id
                    """,
                    (normalized_node,),
                ).fetchall()
            elif normalized_mode is not None:
                rows = connection.execute(
                    """
                    SELECT * FROM session_record WHERE mode = ?
                    ORDER BY updated_at DESC, session_id
                    """,
                    (normalized_mode,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM session_record
                    ORDER BY updated_at DESC, session_id
                    """
                ).fetchall()
            return [self._session_material(connection, row) for row in rows]

    def purge_inactive_sessions(
        self,
        *,
        keep_states: frozenset[str] | None = None,
        include_unknown: bool = True,
    ) -> dict[str, Any]:
        """Delete Supervisor rows that are not actively LIVE/STARTING.

        Keeps only sessions whose state is in keep_states (default LIVE+STARTING).
        Clears default pointers and leases for removed sessions. Does not kill
        processes. Mode-change / inject / boot may re-register coordinates.
        """
        keep = keep_states or frozenset({"LIVE", "STARTING"})
        invalid = keep - SESSION_STATES
        if invalid:
            raise SessionSupervisorError(
                "SESSION_STATE_INVALID",
                f"unsupported keep_states: {', '.join(sorted(invalid))}",
            )
        removed: list[dict[str, Any]] = []
        kept = 0
        with self._connection(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT session_id, node, mode, provider, provider_session_ref,
                       state, alias, updated_at
                FROM session_record
                ORDER BY updated_at DESC, session_id
                """
            ).fetchall()
            for row in rows:
                state = str(row["state"] or "")
                if state in keep:
                    kept += 1
                    continue
                if not include_unknown and state == "UNKNOWN":
                    kept += 1
                    continue
                session_id = str(row["session_id"])
                connection.execute(
                    "DELETE FROM target_default_session WHERE session_id = ?",
                    (session_id,),
                )
                connection.execute(
                    "DELETE FROM process_lease WHERE session_id = ?",
                    (session_id,),
                )
                connection.execute(
                    "DELETE FROM session_binding_history WHERE session_id = ?",
                    (session_id,),
                )
                connection.execute(
                    "DELETE FROM supervisor_event WHERE session_id = ?",
                    (session_id,),
                )
                connection.execute(
                    "DELETE FROM session_record WHERE session_id = ?",
                    (session_id,),
                )
                removed.append(
                    {
                        "session_id": session_id,
                        "node": row["node"],
                        "mode": row["mode"],
                        "provider": row["provider"],
                        "provider_session_ref": row["provider_session_ref"],
                        "state": state,
                        "alias": row["alias"],
                        "updated_at": row["updated_at"],
                    }
                )
            self._event(
                connection,
                session_id=None,
                event_type="SESSIONS_PURGED_INACTIVE",
                details={
                    "removed_count": len(removed),
                    "kept_count": kept,
                    "keep_states": sorted(keep),
                    "include_unknown": include_unknown,
                },
            )
        return {
            "status": "SESSIONS_PURGED",
            "removed_count": len(removed),
            "kept_count": kept,
            "keep_states": sorted(keep),
            "removed": removed[:200],
            "authority": "UNASSIGNED",
            "execution_assignment": "UNASSIGNED",
        }

    def sweep_stale_live_sessions(self) -> dict[str, Any]:
        """Demote LIVE/STARTING sessions that have no living process.

        - OWNED lease + PID gone / PID reused / process exited → DISCONNECTED
        - LIVE/STARTING with no lease or non-OWNED lease → DISCONNECTED
          (cannot prove a live host process; avoids "zombie LIVE" in Observatory)

        Does not kill processes. Uses the same PID+creation-time observer as
        lease recovery.
        """
        now = utc_now()
        demoted: list[dict[str, Any]] = []
        kept_live = 0
        unknown_probe = 0
        with self._connection(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM session_record
                WHERE state IN ('LIVE', 'STARTING')
                ORDER BY updated_at DESC, session_id
                """
            ).fetchall()
            for row in rows:
                session_id = str(row["session_id"])
                prior = str(row["state"])
                lease = connection.execute(
                    "SELECT * FROM process_lease WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                reason = None
                observation: dict[str, Any] | None = None
                if lease is None or str(lease["lease_state"]) != "OWNED":
                    reason = (
                        "NO_PROCESS_LEASE"
                        if lease is None
                        else f"LEASE_NOT_OWNED:{lease['lease_state']}"
                    )
                else:
                    identity = self._lease_identity(lease)
                    observation = self._observe_process_instance(identity)
                    status = str(observation.get("status") or "UNKNOWN")
                    if status == "PROCESS_PRESENT_EXACT":
                        kept_live += 1
                        continue
                    if status == "ORIGINAL_PROCESS_ABSENT":
                        reason = str(
                            observation.get("reason") or "ORIGINAL_PROCESS_ABSENT"
                        )
                        connection.execute(
                            """
                            UPDATE process_lease
                            SET lease_state = 'STALE',
                                lease_version = lease_version + 1,
                                updated_at = ?
                            WHERE session_id = ?
                            """,
                            (now, session_id),
                        )
                    else:
                        # Probe failed — do not invent death, keep state
                        unknown_probe += 1
                        continue
                connection.execute(
                    """
                    UPDATE session_record
                    SET state = 'DISCONNECTED',
                        row_version = row_version + 1,
                        updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now, session_id),
                )
                self._event(
                    connection,
                    session_id=session_id,
                    event_type="LIVE_SESSION_SWEPT_STALE",
                    prior_state=prior,
                    state="DISCONNECTED",
                    reason=reason,
                    details={
                        "host_process_observation": observation,
                        "process_termination": "NOT_PERFORMED",
                    },
                )
                demoted.append(
                    {
                        "session_id": session_id,
                        "prior_state": prior,
                        "state": "DISCONNECTED",
                        "reason": reason,
                        "node": row["node"],
                        "mode": row["mode"],
                        "provider": row["provider"],
                    }
                )
        return {
            "schema": SESSION_SUPERVISOR_SCHEMA,
            "status": "LIVE_SESSION_SWEEP_COMPLETED",
            "demoted_count": len(demoted),
            "kept_live_count": kept_live,
            "unknown_probe_count": unknown_probe,
            "demoted": demoted,
        }

    def list_events(
        self, *, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        normalized_limit = _non_negative_integer(limit, "limit")
        if normalized_limit == 0 or normalized_limit > 500:
            raise SessionSupervisorError(
                "SESSION_SUPERVISOR_REQUEST_INVALID", "limit must be between 1 and 500"
            )
        with self._connection() as connection:
            if session_id is None:
                rows = connection.execute(
                    "SELECT * FROM supervisor_event ORDER BY event_id DESC LIMIT ?",
                    (normalized_limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM supervisor_event
                    WHERE session_id = ? ORDER BY event_id DESC LIMIT ?
                    """,
                    (_required_text(session_id, "session_id"), normalized_limit),
                ).fetchall()
            return [self._event_material(row) for row in rows]

    @staticmethod
    def _require_session(
        connection: sqlite3.Connection, session_id: str
    ) -> sqlite3.Row:
        normalized = _required_text(session_id, "session_id")
        row = connection.execute(
            "SELECT * FROM session_record WHERE session_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise SessionSupervisorError(
                "SESSION_NOT_FOUND", f"unknown session: {normalized}", status=404
            )
        return row

    @staticmethod
    def _require_lease(
        connection: sqlite3.Connection, session_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM process_lease WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise SessionSupervisorError(
                "PROCESS_LEASE_NOT_FOUND", "session has no process lease", status=404
            )
        return row

    @staticmethod
    def _lease_identity(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "pid": int(row["pid"]),
            "process_created_at": row["process_created_at"],
            "executable": row["executable"],
            "command": json.loads(row["command_json"]),
            "endpoint": row["endpoint"],
            "handshake_fingerprint": row["handshake_fingerprint"],
        }

    @classmethod
    def _lease_material(cls, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "lease_id": row["lease_id"],
            "session_id": row["session_id"],
            "process_identity": cls._lease_identity(row),
            "lease_version": int(row["lease_version"]),
            "lease_state": row["lease_state"],
            "acquired_at": row["acquired_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _session_material(
        cls, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        default = connection.execute(
            """
            SELECT * FROM target_default_session
            WHERE node = ? AND mode = ?
            """,
            (row["node"], row["mode"]),
        ).fetchone()
        lease = connection.execute(
            "SELECT * FROM process_lease WHERE session_id = ?", (row["session_id"],)
        ).fetchone()
        bindings = connection.execute(
            """
            SELECT binding_id, provider, provider_session_ref, mode, bound_at
            FROM session_binding_history
            WHERE session_id = ? ORDER BY binding_id
            """,
            (row["session_id"],),
        ).fetchall()
        return {
            "session_id": row["session_id"],
            "node": row["node"],
            "mode": row["mode"],
            "provider": row["provider"],
            "provider_session_ref": row["provider_session_ref"],
            "anchor_ref": row["anchor_ref"],
            "alias": row["alias"],
            "session_kind": row["session_kind"],
            "state": row["state"],
            "currentness": row["currentness"],
            "bounded_summary": row["bounded_summary"],
            "row_version": int(row["row_version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "is_default": default is not None and default["session_id"] == row["session_id"],
            "default_pointer_version": (
                0 if default is None else int(default["pointer_version"])
            ),
            "process_lease": cls._lease_material(lease),
            "binding_history": [dict(item) for item in bindings],
        }

    @staticmethod
    def _event_material(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": int(row["event_id"]),
            "session_id": row["session_id"],
            "event_type": row["event_type"],
            "prior_state": row["prior_state"],
            "state": row["state"],
            "reason": row["reason"],
            "details": json.loads(row["details_json"]),
            "occurred_at": row["occurred_at"],
        }
