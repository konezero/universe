"""Privacy-bounded provider session activity observer.

Provider transcript files remain the canonical source.  This module persists
only file/cursor identity and reduced operational activity; it never copies a
prompt, response, tool command, or provider message body into Universe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


OBSERVER_SCHEMA = "universe.provider-session-observer.v1"
SOURCE_SCHEMA = "universe.provider-session-source.v1"
ACTIVITY_SCHEMA = "universe.provider-session-activity.v1"
PROVIDERS = frozenset({"CODEX", "CLAUDE", "GROK"})
SOURCE_KINDS = {
    "CODEX": "CODEX_ROLLOUT_JSONL",
    "CLAUDE": "CLAUDE_SESSION_JSONL",
    "GROK": "GROK_UPDATES_JSONL",
}


class ProviderSessionObserverError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderSessionObserverError("SOURCE_REQUEST_INVALID", f"{field} is required")
    return value.strip()


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) > 256 or any(char.isspace() for char in text):
        raise ProviderSessionObserverError(
            "SOURCE_REQUEST_INVALID", f"{field} must be a compact identifier"
        )
    return text


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _file_identity(path: Path) -> str:
    stat = path.stat()
    # Do not include ctime: Unix updates it when a transcript is appended.
    # st_ino is available on NTFS but may be zero on some filesystem drivers.
    return f"{stat.st_dev}:{stat.st_ino}"


def _safe_event_kind(event_type: str) -> tuple[str, str]:
    normalized = event_type.strip().upper().replace("-", "_").replace(" ", "_")
    if "QUOTA" in normalized or "RATE_LIMIT" in normalized:
        return "QUOTA_STOP", "WAITING"
    if "PERMISSION" in normalized or "APPROVAL" in normalized:
        return "APPROVAL_WAIT", "WAITING"
    if "ERROR" in normalized or "FAIL" in normalized:
        return "ERROR", "FAILED"
    if "TOOL" in normalized or "COMMAND" in normalized:
        return "TOOL_PHASE", "ACTIVE"
    if "COMPLETE" in normalized or "RESULT" in normalized or "FINISH" in normalized:
        return "TURN_COMPLETED", "COMPLETED"
    if "START" in normalized or "PROMPT" in normalized or "MESSAGE" in normalized:
        return "TURN_STARTED", "ACTIVE"
    return "ACTIVITY", "OBSERVED"


def _event_type(event: Mapping[str, Any]) -> str:
    for key in ("type", "event_type", "event", "kind"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ProviderSessionObserverError(
        "SOURCE_SCHEMA_UNSUPPORTED", "event has no supported type field"
    )


def _event_time(event: Mapping[str, Any]) -> str:
    for key in ("timestamp", "created_at", "updated_at", "time"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return _now()


def _event_id(event: Mapping[str, Any], fallback: str) -> str:
    for key in ("uuid", "id", "event_id", "message_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    return fallback


class ProviderSessionObserverStore:
    """Durable cursor store with provider-specific fail-closed reducers."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_session_source (
                    source_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_session_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    file_identity TEXT,
                    cursor_offset INTEGER NOT NULL DEFAULT 0,
                    cursor_ordinal INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reason TEXT,
                    last_seen_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_session_id, source_path)
                );

                CREATE TABLE IF NOT EXISTS provider_session_activity (
                    activity_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL
                        REFERENCES provider_session_source(source_id)
                        ON DELETE CASCADE,
                    provider_event_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    activity_state TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    activity_digest TEXT NOT NULL,
                    branch_parent_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(source_id, provider_event_id, activity_digest)
                );

                CREATE INDEX IF NOT EXISTS provider_session_activity_source_time
                ON provider_session_activity(source_id, active, ordinal, activity_id);
                """
            )

    def register_source(self, value: Mapping[str, Any]) -> dict[str, Any]:
        provider = _text(value.get("provider"), "provider").upper()
        if provider not in PROVIDERS:
            raise ProviderSessionObserverError("SOURCE_PROVIDER_UNSUPPORTED", provider)
        source_kind = _text(value.get("source_kind"), "source_kind").upper()
        if source_kind != SOURCE_KINDS[provider]:
            raise ProviderSessionObserverError(
                "SOURCE_KIND_INVALID", f"{provider} requires {SOURCE_KINDS[provider]}"
            )
        source_path = Path(_text(value.get("source_path"), "source_path")).expanduser()
        if not source_path.is_absolute():
            raise ProviderSessionObserverError(
                "SOURCE_PATH_INVALID", "source_path must be absolute"
            )
        if provider == "GROK" and source_path.name != "updates.jsonl":
            raise ProviderSessionObserverError(
                "SOURCE_PATH_FORBIDDEN", "Grok observer accepts updates.jsonl only"
            )
        if (
            provider == "CODEX"
            and (
                not source_path.name.startswith("rollout-")
                or source_path.suffix.lower() != ".jsonl"
            )
        ):
            raise ProviderSessionObserverError(
                "SOURCE_PATH_INVALID", "Codex observer requires rollout-*.jsonl"
            )
        if provider == "CLAUDE" and source_path.suffix.lower() != ".jsonl":
            raise ProviderSessionObserverError(
                "SOURCE_PATH_INVALID", "Claude observer requires a .jsonl session source"
            )
        source_id = str(value.get("source_id") or "source_" + uuid.uuid4().hex)
        provider_session_id = _identifier(value.get("provider_session_id"), "provider_session_id")
        source_version = _text(value.get("source_version", "v1"), "source_version")
        now = _now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT source_id FROM provider_session_source
                WHERE provider = ? AND provider_session_id = ? AND source_path = ?
                """,
                (provider, provider_session_id, str(source_path)),
            ).fetchone()
            if existing is not None:
                return self._source_row(
                    connection.execute(
                        "SELECT * FROM provider_session_source WHERE source_id = ?",
                        (existing["source_id"],),
                    ).fetchone()
                )
            connection.execute(
                """
                INSERT INTO provider_session_source(
                    source_id, provider, provider_session_id, source_path, source_kind,
                    source_version, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'REGISTERED', ?, ?)
                """,
                (
                    source_id,
                    provider,
                    provider_session_id,
                    str(source_path),
                    source_kind,
                    source_version,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
        return self._source_row(row)

    def list_sources(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_session_source ORDER BY updated_at DESC, source_id"
            ).fetchall()
        return [self._source_row(row) for row in rows]

    def list_activities(self, source_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
            if exists is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
            query = "SELECT * FROM provider_session_activity WHERE source_id = ?"
            if active_only:
                query += " AND active = 1"
            query += " ORDER BY ordinal DESC, activity_id DESC"
            rows = connection.execute(query, (source_id,)).fetchall()
        return [self._activity_row(row) for row in rows]

    def build_batch_candidate(self, source_id: str) -> dict[str, Any]:
        """Prepare, but never publish, one bounded activity-to-memory candidate."""
        with self._connection() as connection:
            source = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
        activities = [
            activity
            for activity in self.list_activities(source_id)
            if activity["event_kind"]
            in {"TURN_COMPLETED", "ERROR", "QUOTA_STOP", "APPROVAL_WAIT"}
        ]
        source_view = self._source_row(source)
        material = {
            "source_id": source_id,
            "cursor": source_view["cursor"],
            "activity_digests": [item["activity_digest"] for item in activities],
        }
        return {
            "schema": "universe.provider-activity-batch-candidate.v1",
            "candidate_id": "activitybatch_" + _sha256(_canonical_json(material))[:24],
            "status": "REVIEW_REQUIRED" if activities else "ACTIVITY_BATCH_EMPTY",
            "source": {
                "provider": source_view["provider"],
                "provider_session_id": source_view["provider_session_id"],
                "source_id": source_id,
                "cursor": source_view["cursor"],
            },
            "activity_refs": [
                {
                    "activity_id": item["activity_id"],
                    "activity_digest": item["activity_digest"],
                    "ordinal": item["ordinal"],
                    "event_kind": item["event_kind"],
                    "activity_state": item["activity_state"],
                }
                for item in activities
            ],
            "memory": {"state": "REVIEW_REQUIRED", "publication": "NOT_REQUESTED"},
            "bench": {"state": "NOT_RECORDED", "reason": "SKILL_RUN_EVIDENCE_REQUIRED"},
            "future": {"state": "NOT_PROJECTED", "reason": "CASE_OR_PATTERN_REQUIRED"},
            "raw_transcript": "EXCLUDED",
        }

    def scan(self, source_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            source = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
            path = Path(source["source_path"])
            if not path.is_file():
                return self._mark_unknown(connection, source, "SOURCE_MISSING")
            identity = _file_identity(path)
            offset = int(source["cursor_offset"])
            ordinal = int(source["cursor_ordinal"])
            if source["file_identity"] and source["file_identity"] != identity:
                return self._mark_unknown(connection, source, "SOURCE_ROTATED")
            size = path.stat().st_size
            if size < offset:
                return self._mark_unknown(connection, source, "SOURCE_TRUNCATED")
            if not source["file_identity"]:
                connection.execute(
                    "UPDATE provider_session_source SET file_identity = ? WHERE source_id = ?",
                    (identity, source_id),
                )
            added = 0
            next_offset = offset
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    while True:
                        start = handle.tell()
                        raw_line = handle.readline()
                        if not raw_line or not raw_line.endswith(b"\n"):
                            break
                        next_offset = handle.tell()
                        try:
                            event = json.loads(raw_line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as error:
                            raise ProviderSessionObserverError(
                                "SOURCE_SCHEMA_UNSUPPORTED", type(error).__name__
                            ) from error
                        if not isinstance(event, Mapping):
                            raise ProviderSessionObserverError(
                                "SOURCE_SCHEMA_UNSUPPORTED", "JSONL event must be an object"
                            )
                        ordinal += 1
                        if self._reduce_event(connection, source, event, ordinal, start):
                            added += 1
            except ProviderSessionObserverError as error:
                return self._mark_unknown(connection, source, error.code)
            now = _now()
            connection.execute(
                """
                UPDATE provider_session_source
                SET cursor_offset = ?, cursor_ordinal = ?, status = 'ACTIVE', reason = NULL,
                    last_seen_at = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (next_offset, ordinal, now, now, source_id),
            )
            current = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
        return {"schema": OBSERVER_SCHEMA, "source": self._source_row(current), "added": added}

    def _reduce_event(
        self,
        connection: sqlite3.Connection,
        source: sqlite3.Row,
        event: Mapping[str, Any],
        ordinal: int,
        byte_offset: int,
    ) -> bool:
        provider = str(source["provider"])
        event_type = _event_type(event)
        event_kind, activity_state = _safe_event_kind(event_type)
        event_id = _event_id(event, f"offset-{byte_offset}")
        parent_id: str | None = None
        if provider == "CLAUDE":
            if not isinstance(event.get("uuid"), str) or not str(event["uuid"]).strip():
                raise ProviderSessionObserverError(
                    "SOURCE_SCHEMA_UNSUPPORTED", "Claude event requires uuid"
                )
            parent = event.get("parentUuid")
            if parent is not None and not isinstance(parent, str):
                raise ProviderSessionObserverError(
                    "SOURCE_SCHEMA_UNSUPPORTED", "Claude parentUuid must be a string"
                )
            parent_id = parent.strip() if isinstance(parent, str) and parent.strip() else None
            if parent_id is not None:
                connection.execute(
                    """
                    UPDATE provider_session_activity SET active = 0
                    WHERE source_id = ? AND provider_event_id = ?
                    """,
                    (source["source_id"], parent_id),
                )
        # Deliberately hash only source identity and public reducer metadata.
        # The raw JSON event, transcript text, command text, and prompt are never
        # persisted or fed into this digest.
        safe = {
            "source_id": source["source_id"],
            "provider_event_id": event_id,
            "ordinal": ordinal,
            "event_type": event_type[:96],
            "parent_id": parent_id,
        }
        digest = _sha256(_canonical_json(safe))
        activity_id = "activity_" + digest[:24]
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO provider_session_activity(
                activity_id, source_id, provider_event_id, ordinal, event_kind,
                activity_state, observed_at, activity_digest, branch_parent_id,
                active, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                activity_id,
                source["source_id"],
                event_id,
                ordinal,
                event_kind,
                activity_state,
                _event_time(event),
                digest,
                parent_id,
                _now(),
            ),
        )
        return inserted.rowcount == 1

    def _mark_unknown(
        self, connection: sqlite3.Connection, source: sqlite3.Row, reason: str
    ) -> dict[str, Any]:
        now = _now()
        connection.execute(
            """
            UPDATE provider_session_source
            SET status = 'UNKNOWN', reason = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (reason, now, source["source_id"]),
        )
        row = connection.execute(
            "SELECT * FROM provider_session_source WHERE source_id = ?",
            (source["source_id"],),
        ).fetchone()
        return {"schema": OBSERVER_SCHEMA, "source": self._source_row(row), "added": 0}

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": SOURCE_SCHEMA,
            "source_id": row["source_id"],
            "provider": row["provider"],
            "provider_session_id": row["provider_session_id"],
            "source_path": row["source_path"],
            "source_kind": row["source_kind"],
            "source_version": row["source_version"],
            "enabled": bool(row["enabled"]),
            "file_identity": row["file_identity"],
            "cursor": {"offset": row["cursor_offset"], "ordinal": row["cursor_ordinal"]},
            "status": row["status"],
            "reason": row["reason"],
            "last_seen_at": row["last_seen_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _activity_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": ACTIVITY_SCHEMA,
            "activity_id": row["activity_id"],
            "source_id": row["source_id"],
            "provider_event_id": row["provider_event_id"],
            "ordinal": row["ordinal"],
            "event_kind": row["event_kind"],
            "activity_state": row["activity_state"],
            "observed_at": row["observed_at"],
            "activity_digest": row["activity_digest"],
            "branch_parent_id": row["branch_parent_id"],
            "active": bool(row["active"]),
            "recorded_at": row["recorded_at"],
        }
