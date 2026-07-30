"""Opaque, mode-scoped Current and Beyond Anchor store.

This module owns no source reader and derives no currentness, authority, or
execution state. A Host supplies a source reference and a snapshot it already
observed; the runtime retains that data as raw coordinate evidence only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .session_surface_runtime import observe_commander_input
else:
    from session_surface_runtime import observe_commander_input


SCHEMA = """
CREATE TABLE IF NOT EXISTS anchor_snapshot (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revision INTEGER NOT NULL DEFAULT 1,
    frame_id TEXT NOT NULL,
    anchor_id TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_events (
    event_ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    frame_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS beyond_anchor_footprints (
    anchor_id TEXT PRIMARY KEY,
    frame_id TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    retired_at TEXT NOT NULL,
    retirement_reason TEXT NOT NULL
);
"""

INTERNAL_EVENT_PREFIX = "__anchor_session_memory_snapshot__:"


class AnchorSessionMemoryRuntime:
    """One caller-owned Current Anchor store with immutable Beyond footprints.

    The parent Host selects one explicit Mode store. Session IDs are retained
    in snapshots as active-observer coordinates, not as store identity. This
    class intentionally has no Mode selection, source access, or routing logic
    of its own.
    """

    def __init__(self, *, database_path: str | Path | None = None) -> None:
        self.database_path, self.storage_kind = self._database_location(database_path)
        self.conn = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.storage_kind == "FILE":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(anchor_snapshot)").fetchall()
        }
        if "revision" not in columns:
            self.conn.execute(
                "ALTER TABLE anchor_snapshot ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )

    def record_snapshot(self, *, snapshot: Mapping[str, Any], source_ref: str) -> dict[str, Any]:
        """Store a Host-declared snapshot as opaque evidence.

        Structural validation protects the anchor store only. ``source_ref``
        is retained as a pointer and is never opened or validated here.
        """

        normalized_source_ref = self._required_text(source_ref)
        if not normalized_source_ref:
            return {"status": "SOURCE_REF_REQUIRED"}
        if not isinstance(snapshot, Mapping):
            return {"status": "SNAPSHOT_REQUIRED"}

        frame_id = self._required_text(snapshot.get("frame_id", ""))
        if not frame_id:
            return {"status": "FRAME_ID_REQUIRED"}
        anchor_id = self._required_text(snapshot.get("anchor_id", ""))
        if not anchor_id:
            return {"status": "ANCHOR_ID_REQUIRED"}
        state = self._required_text(snapshot.get("state", ""))
        if not state:
            return {"status": "STATE_REQUIRED"}
        observed_at = self._normalize_timestamp(snapshot.get("observed_at", ""))
        if not observed_at:
            return {"status": "OBSERVED_AT_INVALID"}

        raw_snapshot = dict(snapshot)
        raw_snapshot["frame_id"] = frame_id
        raw_snapshot["anchor_id"] = anchor_id
        raw_snapshot["state"] = state
        raw_snapshot["observed_at"] = observed_at
        snapshot_json = self._json_object(raw_snapshot)
        if snapshot_json is None:
            return {"status": "SNAPSHOT_NOT_JSON_SERIALIZABLE"}

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self.stored_snapshot()
            exists = previous is not None
            beyond_footprint = None
            if previous is not None:
                if previous["anchor_id"] == anchor_id:
                    if self._elapsed_seconds(previous["observed_at"], observed_at) is None:
                        self.conn.execute("ROLLBACK")
                        return {
                            "status": "OBSERVATION_TIME_REGRESSION",
                            "snapshot": previous,
                        }
                    if previous["source_ref"] != normalized_source_ref:
                        self.conn.execute("ROLLBACK")
                        return {
                            "status": "ANCHOR_SOURCE_IDENTITY_MISMATCH",
                            "snapshot": previous,
                        }
                else:
                    if self._beyond_footprint_exists(anchor_id):
                        self.conn.execute("ROLLBACK")
                        return {
                            "status": "BEYOND_ANCHOR_REACTIVATION_BLOCKED",
                            "snapshot": previous,
                        }
                    if self._elapsed_seconds(previous["observed_at"], observed_at) is None:
                        self.conn.execute("ROLLBACK")
                        return {
                            "status": "OBSERVATION_TIME_REGRESSION",
                            "snapshot": previous,
                        }
                    beyond_footprint = self._preserve_beyond_footprint(
                        previous,
                        retired_at=observed_at,
                        retirement_reason="CURRENT_ANCHOR_REPLACED",
                    )
            if previous is None:
                self.conn.execute(
                    """
                    INSERT INTO anchor_snapshot(
                        singleton, revision, frame_id, anchor_id, state, observed_at,
                        source_ref, snapshot_json
                    ) VALUES (1, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        frame_id,
                        anchor_id,
                        state,
                        observed_at,
                        normalized_source_ref,
                        snapshot_json,
                    ),
                )
            else:
                cursor = self.conn.execute(
                    """
                    UPDATE anchor_snapshot
                    SET revision = revision + 1, frame_id = ?, anchor_id = ?, state = ?,
                        observed_at = ?, source_ref = ?, snapshot_json = ?
                    WHERE singleton = 1 AND revision = ?
                    """,
                    (
                        frame_id,
                        anchor_id,
                        state,
                        observed_at,
                        normalized_source_ref,
                        snapshot_json,
                        self._current_revision(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.OperationalError("anchor revision conflict")
            event_type = "SNAPSHOT_UPDATED" if exists else "SNAPSHOT_RECORDED"
            event = self._append_event(
                event_id=self._generated_snapshot_event_id(),
                event_type=event_type,
                frame_id=frame_id,
                action="",
                details={"snapshot_fields": sorted(raw_snapshot.keys())},
                source_ref=normalized_source_ref,
                observed_at=observed_at,
            )
            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise
        result = {
            "status": event_type,
            "snapshot": self.stored_snapshot(),
            "event": event,
        }
        if beyond_footprint is not None:
            result["beyond_footprint"] = beyond_footprint
        return result

    def record_observation(
        self,
        *,
        frame_id: str,
        event_id: str,
        action: str,
        details: Mapping[str, Any] | None,
        source_ref: str,
        observed_at: str,
    ) -> dict[str, Any]:
        """Append one Host-declared observation without interpreting it."""

        normalized_frame_id = self._required_text(frame_id)
        if not normalized_frame_id:
            return {"status": "FRAME_ID_REQUIRED"}
        normalized_event_id = self._required_text(event_id)
        if not normalized_event_id:
            return {"status": "EVENT_ID_REQUIRED"}
        if normalized_event_id.startswith(INTERNAL_EVENT_PREFIX):
            return {"status": "EVENT_ID_RESERVED"}
        normalized_action = self._required_text(action)
        if not normalized_action:
            return {"status": "ACTION_REQUIRED"}
        normalized_source_ref = self._required_text(source_ref)
        if not normalized_source_ref:
            return {"status": "SOURCE_REF_REQUIRED"}
        normalized_observed_at = self._normalize_timestamp(observed_at)
        if not normalized_observed_at:
            return {"status": "OBSERVED_AT_INVALID"}
        if details is not None and not isinstance(details, Mapping):
            return {"status": "OBSERVATION_DETAILS_INVALID"}

        snapshot = self.stored_snapshot()
        if snapshot is None:
            return {"status": "SNAPSHOT_NOT_FOUND"}
        if snapshot["frame_id"] != normalized_frame_id:
            return {"status": "FRAME_ID_MISMATCH"}
        if self.conn.execute("SELECT 1 FROM memory_events WHERE event_id = ?", (normalized_event_id,)).fetchone():
            return {"status": "EVENT_ID_ALREADY_EXISTS"}

        normalized_details = {} if details is None else dict(details)
        if self._json_object(normalized_details) is None:
            return {"status": "OBSERVATION_DETAILS_NOT_JSON_SERIALIZABLE"}
        event = self._append_event(
            event_id=normalized_event_id,
            event_type="OBSERVATION_RECORDED",
            frame_id=normalized_frame_id,
            action=normalized_action,
            details=normalized_details,
            source_ref=normalized_source_ref,
            observed_at=normalized_observed_at,
        )
        return {"status": "OBSERVATION_RECORDED", "snapshot": snapshot, "event": event}

    def observe_current_anchor(
        self,
        *,
        frame_id: str,
        anchor_id: str,
        observed_at: str,
    ) -> dict[str, Any]:
        """Advance physical observation time for the same Current Anchor only."""

        normalized_frame_id = self._required_text(frame_id)
        if not normalized_frame_id:
            return {"status": "FRAME_ID_REQUIRED"}
        normalized_anchor_id = self._required_text(anchor_id)
        if not normalized_anchor_id:
            return {"status": "ANCHOR_ID_REQUIRED"}
        normalized_observed_at = self._normalize_timestamp(observed_at)
        if not normalized_observed_at:
            return {"status": "OBSERVED_AT_INVALID"}

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            stored = self.stored_snapshot()
            if stored is None:
                self.conn.execute("ROLLBACK")
                return {"status": "SNAPSHOT_NOT_FOUND"}
            if stored["frame_id"] != normalized_frame_id:
                self.conn.execute("ROLLBACK")
                return {"status": "FRAME_ID_MISMATCH"}
            if stored["anchor_id"] != normalized_anchor_id:
                self.conn.execute("ROLLBACK")
                return {"status": "CURRENT_ANCHOR_MISMATCH"}

            elapsed_seconds = self._elapsed_seconds(
                stored["observed_at"], normalized_observed_at
            )
            if elapsed_seconds is None:
                self.conn.execute("ROLLBACK")
                return {
                    "status": "OBSERVATION_TIME_REGRESSION",
                    "snapshot": stored,
                }

            raw_snapshot = dict(stored["snapshot"])
            raw_snapshot["observed_at"] = normalized_observed_at
            snapshot_json = self._json_object(raw_snapshot)
            if snapshot_json is None:
                self.conn.execute("ROLLBACK")
                return {"status": "SNAPSHOT_NOT_JSON_SERIALIZABLE"}
            cursor = self.conn.execute(
                """
                UPDATE anchor_snapshot
                SET revision = revision + 1, observed_at = ?, snapshot_json = ?
                WHERE singleton = 1 AND revision = ?
                """,
                (normalized_observed_at, snapshot_json, self._current_revision()),
            )
            if cursor.rowcount != 1:
                raise sqlite3.OperationalError("anchor revision conflict")
            event = self._append_event(
                event_id=self._generated_snapshot_event_id(),
                event_type="CURRENT_ANCHOR_OBSERVED",
                frame_id=normalized_frame_id,
                action="USER_INPUT",
                details={
                    "anchor_id": normalized_anchor_id,
                    "previous_observed_at": stored["observed_at"],
                    "elapsed_seconds": elapsed_seconds,
                    "changed_fields": ["observed_at"] if elapsed_seconds > 0 else [],
                },
                source_ref=stored["source_ref"],
                observed_at=normalized_observed_at,
            )
            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise
        return {
            "status": "CURRENT_ANCHOR_OBSERVED",
            "input_at": normalized_observed_at,
            "previous_observed_at": stored["observed_at"],
            "elapsed_seconds": elapsed_seconds,
            "changed_fields": ["observed_at"] if elapsed_seconds > 0 else [],
            "snapshot": self.stored_snapshot(),
            "event": event,
            "authority_created": False,
            "assignment_created": False,
            "repository_write": False,
        }

    def observe_commander_surface(
        self,
        *,
        commander_surface: str,
        input_at: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        """Record one Commander input without changing Anchor identity."""

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            stored = self.stored_snapshot()
            if stored is None:
                self.conn.execute("ROLLBACK")
                return {"status": "SNAPSHOT_NOT_FOUND"}
            transition = observe_commander_input(
                snapshot=stored["snapshot"],
                observation={
                    "commander_surface": commander_surface,
                    "input_at": input_at,
                    "evidence_ref": evidence_ref,
                },
            )
            if transition["status"] != "COMMANDER_INPUT_OBSERVED":
                self.conn.execute("ROLLBACK")
                return transition
            raw_snapshot = dict(transition["snapshot"])
            snapshot_json = self._json_object(raw_snapshot)
            if snapshot_json is None:
                self.conn.execute("ROLLBACK")
                return {"status": "SNAPSHOT_NOT_JSON_SERIALIZABLE"}
            cursor = self.conn.execute(
                """
                UPDATE anchor_snapshot
                SET revision = revision + 1, observed_at = ?, snapshot_json = ?
                WHERE singleton = 1 AND revision = ?
                """,
                (
                    raw_snapshot["observed_at"],
                    snapshot_json,
                    self._current_revision(),
                ),
            )
            if cursor.rowcount != 1:
                raise sqlite3.OperationalError("anchor revision conflict")
            transition_event = transition["event"]
            event = self._append_event(
                event_id=self._generated_snapshot_event_id(),
                event_type="COMMANDER_INPUT_OBSERVED",
                frame_id=stored["frame_id"],
                action="USER_INPUT",
                details={
                    "anchor_id": stored["anchor_id"],
                    "commander_surface": transition_event["commander_surface"],
                    "previous_commander_surface": transition_event[
                        "previous_commander_surface"
                    ],
                    "evidence_ref": transition_event["evidence_ref"],
                    "changed_fields": transition["changed_snapshot_fields"],
                },
                source_ref=stored["source_ref"],
                observed_at=raw_snapshot["observed_at"],
            )
            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise
        return {
            **transition,
            "snapshot": self.stored_snapshot(),
            "event": event,
            "repository_write": False,
        }

    def stored_snapshot(self) -> dict[str, Any] | None:
        """Return the raw stored snapshot; this is not a currentness verdict."""

        row = self.conn.execute(
            """
            SELECT revision, frame_id, anchor_id, state, observed_at, source_ref, snapshot_json
            FROM anchor_snapshot
            WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "frame_id": str(row["frame_id"]),
            "anchor_id": str(row["anchor_id"]),
            "state": str(row["state"]),
            "observed_at": str(row["observed_at"]),
            "source_ref": str(row["source_ref"]),
            "snapshot": json.loads(str(row["snapshot_json"])),
        }

    def event_history(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT event_id, event_type, frame_id, action, details_json, source_ref, observed_at
            FROM memory_events
            ORDER BY event_ordinal ASC
            """
        ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "event_type": str(row["event_type"]),
                "frame_id": str(row["frame_id"]),
                "action": str(row["action"]),
                "details": json.loads(str(row["details_json"])),
                "source_ref": str(row["source_ref"]),
                "observed_at": str(row["observed_at"]),
            }
            for row in rows
        ]

    def beyond_footprints(self) -> list[dict[str, Any]]:
        """Return immutable historical anchors; callers still treat them as candidates."""

        rows = self.conn.execute(
            """
            SELECT anchor_id, frame_id, state, observed_at, source_ref, snapshot_json,
                   retired_at, retirement_reason
            FROM beyond_anchor_footprints
            ORDER BY retired_at DESC, anchor_id ASC
            """
        ).fetchall()
        return [
            {
                "anchor_id": str(row["anchor_id"]),
                "frame_id": str(row["frame_id"]),
                "state": str(row["state"]),
                "observed_at": str(row["observed_at"]),
                "source_ref": str(row["source_ref"]),
                "snapshot": json.loads(str(row["snapshot_json"])),
                "retired_at": str(row["retired_at"]),
                "retirement_reason": str(row["retirement_reason"]),
                "status": "CANDIDATE",
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _database_location(database_path: str | Path | None) -> tuple[str, str]:
        if database_path is None or str(database_path).strip() in {"", ":memory:"}:
            return ":memory:", "MEMORY"
        path = Path(database_path).resolve()
        if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("anchor store database must use .db, .sqlite, or .sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path), "FILE"

    def _preserve_beyond_footprint(
        self,
        snapshot: Mapping[str, Any],
        *,
        retired_at: str,
        retirement_reason: str,
    ) -> dict[str, Any]:
        raw_snapshot = dict(snapshot["snapshot"])
        snapshot_json = self._json_object(raw_snapshot)
        if snapshot_json is None:
            raise ValueError("current anchor snapshot is not JSON serializable")
        self.conn.execute(
            """
            INSERT OR IGNORE INTO beyond_anchor_footprints(
                anchor_id, frame_id, state, observed_at, source_ref, snapshot_json,
                retired_at, retirement_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(snapshot["anchor_id"]),
                str(snapshot["frame_id"]),
                str(snapshot["state"]),
                str(snapshot["observed_at"]),
                str(snapshot["source_ref"]),
                snapshot_json,
                retired_at,
                retirement_reason,
            ),
        )
        return {
            "anchor_id": str(snapshot["anchor_id"]),
            "frame_id": str(snapshot["frame_id"]),
            "retired_at": retired_at,
            "retirement_reason": retirement_reason,
            "status": "CANDIDATE",
        }

    def _beyond_footprint_exists(self, anchor_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM beyond_anchor_footprints WHERE anchor_id = ?",
                (anchor_id,),
            ).fetchone()
            is not None
        )

    def _generated_snapshot_event_id(self) -> str:
        row = self.conn.execute("SELECT COALESCE(MAX(event_ordinal), 0) + 1 AS next_ordinal FROM memory_events").fetchone()
        return f"{INTERNAL_EVENT_PREFIX}{int(row['next_ordinal'])}"

    def _current_revision(self) -> int:
        row = self.conn.execute(
            "SELECT revision FROM anchor_snapshot WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise sqlite3.OperationalError("anchor snapshot revision is unavailable")
        return int(row["revision"])

    def _append_event(
        self,
        *,
        event_id: str,
        event_type: str,
        frame_id: str,
        action: str,
        details: Mapping[str, Any],
        source_ref: str,
        observed_at: str,
    ) -> dict[str, Any]:
        details_json = self._json_object(details)
        if details_json is None:
            raise ValueError("runtime event details must be JSON serializable")
        self.conn.execute(
            """
            INSERT INTO memory_events(
                event_id, event_type, frame_id, action, details_json, source_ref, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, event_type, frame_id, action, details_json, source_ref, observed_at),
        )
        return {
            "event_id": event_id,
            "event_type": event_type,
            "frame_id": frame_id,
            "action": action,
            "details": json.loads(details_json),
            "source_ref": source_ref,
            "observed_at": observed_at,
        }

    @staticmethod
    def _required_text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _normalize_timestamp(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return ""
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _elapsed_seconds(previous: str, current: str) -> int | None:
        previous_time = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        current_time = datetime.fromisoformat(current.replace("Z", "+00:00"))
        elapsed = int((current_time - previous_time).total_seconds())
        return elapsed if elapsed >= 0 else None

    @staticmethod
    def _json_object(value: Mapping[str, Any]) -> str | None:
        try:
            return json.dumps(dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            return None
