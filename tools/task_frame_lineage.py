"""Durable, transport-neutral Session Anchor -> Task Frame -> Result lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


TASK_FRAME_LINEAGE_SCHEMA = "universe.task-frame-lineage.v1"
TASK_FRAME_RESULT_SCHEMA = "universe.task-frame-result-lineage.v1"


class TaskFrameLineageError(RuntimeError):
    def __init__(self, code: str, detail: str, *, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskFrameLineageError(
            "TASK_FRAME_LINEAGE_REQUEST_INVALID", f"{field} must be non-empty text"
        )
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise TaskFrameLineageError(
            "TASK_FRAME_LINEAGE_REQUEST_INVALID", "result must be JSON-serializable"
        ) from error


def _result_material(value: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise TaskFrameLineageError(
            "TASK_FRAME_LINEAGE_REQUEST_INVALID", "result must be an object"
        )
    material = dict(value)
    rendered = _canonical_json(material)
    return material, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


class TaskFrameLineageStore:
    """Append-only lineage store; deliberately independent of transport and UI.

    The caller supplies a Session Anchor reference explicitly. This store never
    reads a mode default, current anchor, lease, provider, or UI selection.
    """

    def __init__(self, database_path: Path):
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
                CREATE TABLE IF NOT EXISTS task_frame_lineage (
                    frame_ref TEXT PRIMARY KEY,
                    origin_session_anchor_ref TEXT NOT NULL,
                    target_session_anchor_ref TEXT,
                    parent_task_frame_ref TEXT
                        REFERENCES task_frame_lineage(frame_ref),
                    frame_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS task_frame_lineage_origin
                ON task_frame_lineage(origin_session_anchor_ref, created_at, frame_ref);

                CREATE INDEX IF NOT EXISTS task_frame_lineage_parent
                ON task_frame_lineage(parent_task_frame_ref, created_at, frame_ref);

                CREATE TABLE IF NOT EXISTS task_frame_lineage_revision (
                    frame_ref TEXT NOT NULL REFERENCES task_frame_lineage(frame_ref),
                    revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    result_ref TEXT,
                    payload_digest TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY(frame_ref, revision),
                    UNIQUE(result_ref)
                );

                CREATE TABLE IF NOT EXISTS task_frame_lineage_result (
                    result_ref TEXT PRIMARY KEY,
                    frame_ref TEXT NOT NULL REFERENCES task_frame_lineage(frame_ref),
                    origin_session_anchor_ref TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    attached_at TEXT NOT NULL,
                    UNIQUE(frame_ref, result_ref)
                );

                CREATE INDEX IF NOT EXISTS task_frame_lineage_result_frame
                ON task_frame_lineage_result(frame_ref, attached_at, result_ref);
                """
            )

    @staticmethod
    def _frame_digest(
        *,
        frame_ref: str,
        origin_session_anchor_ref: str,
        target_session_anchor_ref: str | None,
        parent_task_frame_ref: str | None,
    ) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "frame_ref": frame_ref,
                    "origin_session_anchor_ref": origin_session_anchor_ref,
                    "target_session_anchor_ref": target_session_anchor_ref,
                    "parent_task_frame_ref": parent_task_frame_ref,
                }
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _next_revision(connection: sqlite3.Connection, frame_ref: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision "
            "FROM task_frame_lineage_revision WHERE frame_ref = ?",
            (frame_ref,),
        ).fetchone()
        return int(row["revision"]) + 1

    def create_task_frame(
        self,
        *,
        frame_ref: Any,
        origin_session_anchor_ref: Any,
        target_session_anchor_ref: Any = None,
        parent_task_frame_ref: Any = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create an immutable frame lineage node, idempotently by ``frame_ref``."""
        normalized_frame = _required_text(frame_ref, "frame_ref")
        origin = _required_text(origin_session_anchor_ref, "origin_session_anchor_ref")
        target = _optional_text(target_session_anchor_ref, "target_session_anchor_ref")
        parent = _optional_text(parent_task_frame_ref, "parent_task_frame_ref")
        digest = self._frame_digest(
            frame_ref=normalized_frame,
            origin_session_anchor_ref=origin,
            target_session_anchor_ref=target,
            parent_task_frame_ref=parent,
        )
        with self._connection(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM task_frame_lineage WHERE frame_ref = ?",
                (normalized_frame,),
            ).fetchone()
            if existing is not None:
                if str(existing["frame_digest"]) != digest:
                    raise TaskFrameLineageError(
                        "TASK_FRAME_LINEAGE_CONFLICT",
                        "frame_ref is already bound to different immutable lineage",
                        status=409,
                    )
                return self._frame_material(connection, existing), False
            if parent is not None:
                parent_row = connection.execute(
                    "SELECT origin_session_anchor_ref FROM task_frame_lineage "
                    "WHERE frame_ref = ?",
                    (parent,),
                ).fetchone()
                if parent_row is None:
                    raise TaskFrameLineageError(
                        "TASK_FRAME_PARENT_NOT_FOUND",
                        "parent_task_frame_ref does not exist",
                        status=404,
                    )
                if str(parent_row["origin_session_anchor_ref"]) != origin:
                    raise TaskFrameLineageError(
                        "TASK_FRAME_PARENT_ORIGIN_MISMATCH",
                        "child Task Frame must preserve its parent's origin Session Anchor",
                        status=409,
                    )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO task_frame_lineage(
                    frame_ref, origin_session_anchor_ref, target_session_anchor_ref,
                    parent_task_frame_ref, frame_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_frame, origin, target, parent, digest, now),
            )
            connection.execute(
                """
                INSERT INTO task_frame_lineage_revision(
                    frame_ref, revision, event_type, result_ref, payload_digest, occurred_at
                ) VALUES (?, 1, 'TASK_FRAME_CREATED', NULL, ?, ?)
                """,
                (normalized_frame, digest, now),
            )
            row = connection.execute(
                "SELECT * FROM task_frame_lineage WHERE frame_ref = ?",
                (normalized_frame,),
            ).fetchone()
            if row is None:
                raise TaskFrameLineageError(
                    "TASK_FRAME_LINEAGE_INVARIANT_FAILED",
                    "created Task Frame is unavailable",
                    status=500,
                )
            return self._frame_material(connection, row), True

    def attach_result(
        self,
        *,
        result_ref: Any,
        frame_ref: Any,
        origin_session_anchor_ref: Any,
        result: Any,
    ) -> tuple[dict[str, Any], bool]:
        """Idempotently attach one result to its exact frame and origin anchor."""
        normalized_result = _required_text(result_ref, "result_ref")
        normalized_frame = _required_text(frame_ref, "frame_ref")
        origin = _required_text(origin_session_anchor_ref, "origin_session_anchor_ref")
        result_value, digest = _result_material(result)
        rendered_result = _canonical_json(result_value)
        with self._connection(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM task_frame_lineage_result WHERE result_ref = ?",
                (normalized_result,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["frame_ref"]) != normalized_frame
                    or str(existing["origin_session_anchor_ref"]) != origin
                    or str(existing["result_digest"]) != digest
                ):
                    raise TaskFrameLineageError(
                        "TASK_FRAME_RESULT_CONFLICT",
                        "result_ref is already attached to different lineage or content",
                        status=409,
                    )
                return self._result_material_from_row(existing), False
            frame = connection.execute(
                "SELECT * FROM task_frame_lineage WHERE frame_ref = ?",
                (normalized_frame,),
            ).fetchone()
            if frame is None:
                raise TaskFrameLineageError(
                    "TASK_FRAME_NOT_FOUND", "frame_ref does not exist", status=404
                )
            if str(frame["origin_session_anchor_ref"]) != origin:
                raise TaskFrameLineageError(
                    "TASK_FRAME_RESULT_ORIGIN_MISMATCH",
                    "result origin Session Anchor does not match the Task Frame",
                    status=409,
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO task_frame_lineage_result(
                    result_ref, frame_ref, origin_session_anchor_ref,
                    result_digest, result_json, attached_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_result, normalized_frame, origin, digest, rendered_result, now),
            )
            connection.execute(
                """
                INSERT INTO task_frame_lineage_revision(
                    frame_ref, revision, event_type, result_ref, payload_digest, occurred_at
                ) VALUES (?, ?, 'RESULT_ATTACHED', ?, ?, ?)
                """,
                (
                    normalized_frame,
                    self._next_revision(connection, normalized_frame),
                    normalized_result,
                    digest,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_frame_lineage_result WHERE result_ref = ?",
                (normalized_result,),
            ).fetchone()
            if row is None:
                raise TaskFrameLineageError(
                    "TASK_FRAME_LINEAGE_INVARIANT_FAILED",
                    "attached result is unavailable",
                    status=500,
                )
            return self._result_material_from_row(row), True

    @staticmethod
    def _result_material_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": TASK_FRAME_RESULT_SCHEMA,
            "result_ref": row["result_ref"],
            "frame_ref": row["frame_ref"],
            "origin_session_anchor_ref": row["origin_session_anchor_ref"],
            "result_digest": row["result_digest"],
            "result": json.loads(str(row["result_json"])),
            "attached_at": row["attached_at"],
        }

    def _frame_material(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        revisions = connection.execute(
            """
            SELECT revision, event_type, result_ref, payload_digest, occurred_at
            FROM task_frame_lineage_revision WHERE frame_ref = ? ORDER BY revision
            """,
            (row["frame_ref"],),
        ).fetchall()
        results = connection.execute(
            """
            SELECT * FROM task_frame_lineage_result
            WHERE frame_ref = ? ORDER BY attached_at, result_ref
            """,
            (row["frame_ref"],),
        ).fetchall()
        return {
            "schema": TASK_FRAME_LINEAGE_SCHEMA,
            "frame_ref": row["frame_ref"],
            "origin_session_anchor_ref": row["origin_session_anchor_ref"],
            "target_session_anchor_ref": row["target_session_anchor_ref"],
            "parent_task_frame_ref": row["parent_task_frame_ref"],
            "frame_digest": row["frame_digest"],
            "created_at": row["created_at"],
            "revision": len(revisions),
            "revisions": [dict(item) for item in revisions],
            "results": [self._result_material_from_row(item) for item in results],
        }

    def get_task_frame(self, frame_ref: Any) -> dict[str, Any]:
        normalized_frame = _required_text(frame_ref, "frame_ref")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_frame_lineage WHERE frame_ref = ?",
                (normalized_frame,),
            ).fetchone()
            if row is None:
                raise TaskFrameLineageError(
                    "TASK_FRAME_NOT_FOUND", "frame_ref does not exist", status=404
                )
            return self._frame_material(connection, row)

    def list_task_frames(
        self,
        *,
        origin_session_anchor_ref: Any = None,
        parent_task_frame_ref: Any = None,
    ) -> list[dict[str, Any]]:
        origin = _optional_text(origin_session_anchor_ref, "origin_session_anchor_ref")
        parent = _optional_text(parent_task_frame_ref, "parent_task_frame_ref")
        clauses: list[str] = []
        values: list[str] = []
        if origin is not None:
            clauses.append("origin_session_anchor_ref = ?")
            values.append(origin)
        if parent is not None:
            clauses.append("parent_task_frame_ref = ?")
            values.append(parent)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM task_frame_lineage" + where + " ORDER BY created_at, frame_ref",
                values,
            ).fetchall()
            return [self._frame_material(connection, row) for row in rows]

    def list_results(self, *, frame_ref: Any) -> list[dict[str, Any]]:
        return list(self.get_task_frame(frame_ref)["results"])

    def recover(self, *, origin_session_anchor_ref: Any = None) -> dict[str, Any]:
        """Rehydrate durable frame/result lineage after a process or app restart."""
        frames = self.list_task_frames(origin_session_anchor_ref=origin_session_anchor_ref)
        return {
            "schema": TASK_FRAME_LINEAGE_SCHEMA,
            "recovery": "DURABLE_REHYDRATED",
            "origin_session_anchor_ref": _optional_text(
                origin_session_anchor_ref, "origin_session_anchor_ref"
            ),
            "task_frames": frames,
            "task_frame_count": len(frames),
            "result_count": sum(len(item["results"]) for item in frames),
        }
