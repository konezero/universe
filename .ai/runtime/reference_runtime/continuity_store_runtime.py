"""File-backed persistence for passive Checkpoint and Resume records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATABASE_RELATIVE_PATH = Path(".ai/runtime/continuity/continuity.sqlite")
DATABASE_REF = f"sqlite://{DATABASE_RELATIVE_PATH.as_posix()}"
STORE_SCHEMA = "ai-career.continuity-store.v1"
EVIDENCE_SCHEMA = "ai-career.continuity-save-evidence.v1"
RECORD_TYPES = frozenset({"CHECKPOINT", "RESUME"})


@dataclass(frozen=True)
class ContinuityStoreError(Exception):
    error_code: str
    detail: str


class ContinuityStore:
    """Append-only continuity records bound to one project repository."""

    def __init__(
        self,
        *,
        repository_root: Path,
        connection: sqlite3.Connection,
        database_path: Path,
        writable: bool,
    ) -> None:
        self.repository_root = repository_root
        self.connection = connection
        self.database_path = database_path
        self.writable = writable
        self.connection.row_factory = sqlite3.Row

    @classmethod
    def open_for_write(cls, repository_root: Path) -> "ContinuityStore":
        root, database_path = _resolve_store_path(repository_root, create_parent=True)
        connection = sqlite3.connect(
            str(database_path),
            timeout=30,
            isolation_level=None,
        )
        store = cls(
            repository_root=root,
            connection=connection,
            database_path=database_path,
            writable=True,
        )
        try:
            store._configure_connection()
            store._initialize_schema()
        except Exception:
            connection.close()
            raise
        return store

    @classmethod
    def open_for_read(cls, repository_root: Path) -> "ContinuityStore | None":
        root, database_path = _resolve_store_path(repository_root, create_parent=False)
        if not database_path.is_file():
            return None
        uri = f"file:{database_path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        store = cls(
            repository_root=root,
            connection=connection,
            database_path=database_path,
            writable=False,
        )
        try:
            store.connection.execute("PRAGMA query_only = ON")
            store._verify_schema()
        except Exception:
            connection.close()
            raise
        return store

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ContinuityStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def database_ref(self) -> str:
        return DATABASE_RELATIVE_PATH.as_posix()

    def save(
        self,
        *,
        record_type: str,
        record_id: str,
        node: str,
        mode: str,
        candidate: Mapping[str, Any],
        profile_sha256: str,
        source_commit: str,
    ) -> dict[str, Any]:
        if not self.writable:
            raise ContinuityStoreError(
                "CONTINUITY_STORE_READ_ONLY", "continuity store is read-only"
            )
        normalized_type = _record_type(record_type)
        normalized_id = _required_text(record_id, "record_id")
        normalized_node = _required_text(node, "node")
        normalized_mode = _required_text(mode, "mode").upper()
        normalized_candidate = dict(candidate)
        candidate_json = _canonical_json_text(normalized_candidate)
        candidate_sha256 = _sha256(candidate_json.encode("utf-8"))
        observed_at = _required_text(
            normalized_candidate.get("observed_at"), "candidate.observed_at"
        )
        session_id = _required_text(
            normalized_candidate.get("session_id"), "candidate.session_id"
        )
        frame_id = _required_text(
            normalized_candidate.get("frame_id"), "candidate.frame_id"
        )
        anchor_id = _required_text(
            normalized_candidate.get("anchor_id"), "candidate.anchor_id"
        )
        summary = _optional_text(normalized_candidate.get("summary", ""), "summary")
        source_refs = normalized_candidate.get("source_refs")
        if not isinstance(source_refs, list) or not source_refs:
            raise ContinuityStoreError(
                "CONTINUITY_STORE_RECORD_INVALID",
                "candidate.source_refs must be a non-empty array",
            )
        source_refs_json = _canonical_json_text(source_refs)

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                """
                SELECT record_type, node, mode, candidate_sha256, saved_at,
                       transaction_id
                FROM continuity_records
                WHERE record_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if existing is not None:
                expected = (
                    normalized_type,
                    normalized_node,
                    normalized_mode,
                    candidate_sha256,
                )
                actual = (
                    existing["record_type"],
                    existing["node"],
                    existing["mode"],
                    existing["candidate_sha256"],
                )
                if actual != expected:
                    raise ContinuityStoreError(
                        "CONTINUITY_RECORD_CONFLICT",
                        "record_id already exists with different immutable content",
                    )
                self.connection.execute("COMMIT")
                return self._save_result(
                    record_type=normalized_type,
                    record_id=normalized_id,
                    candidate_sha256=candidate_sha256,
                    saved_at=existing["saved_at"],
                    transaction_id=existing["transaction_id"],
                    idempotent=True,
                )

            saved_at = _physical_time()
            transaction_id = _stable_id(
                "continuity_tx",
                {
                    "record_id": normalized_id,
                    "candidate_sha256": candidate_sha256,
                    "saved_at": saved_at,
                },
            )
            self.connection.execute(
                """
                INSERT INTO continuity_records (
                    record_id, record_type, node, mode, session_id, frame_id,
                    anchor_id, summary, candidate_json, candidate_sha256,
                    source_refs_json, observed_at, saved_at, transaction_id,
                    profile_sha256, source_commit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    normalized_type,
                    normalized_node,
                    normalized_mode,
                    session_id,
                    frame_id,
                    anchor_id,
                    summary,
                    candidate_json,
                    candidate_sha256,
                    source_refs_json,
                    observed_at,
                    saved_at,
                    transaction_id,
                    _required_text(profile_sha256, "profile_sha256"),
                    _required_text(source_commit, "source_commit"),
                ),
            )
            self.connection.execute(
                """
                INSERT INTO continuity_events (
                    event_id, record_id, event_type, payload_sha256, created_at
                ) VALUES (?, ?, 'RECORD_SAVED', ?, ?)
                """,
                (
                    _stable_id(
                        "continuity_event",
                        {
                            "record_id": normalized_id,
                            "transaction_id": transaction_id,
                        },
                    ),
                    normalized_id,
                    candidate_sha256,
                    saved_at,
                ),
            )
            self.connection.execute("COMMIT")
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

        return self._save_result(
            record_type=normalized_type,
            record_id=normalized_id,
            candidate_sha256=candidate_sha256,
            saved_at=saved_at,
            transaction_id=transaction_id,
            idempotent=False,
        )

    def list_records(
        self,
        *,
        record_type: str,
        node: str,
        mode: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_type = _record_type(record_type)
        normalized_node = _required_text(node, "node")
        normalized_mode = _required_text(mode, "mode").upper()
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ContinuityStoreError(
                "CONTINUITY_STORE_REQUEST_INVALID",
                "limit must be an integer from 1 to 100",
            )
        rows = self.connection.execute(
            """
            SELECT record_id, record_type, node, mode, session_id, frame_id,
                   anchor_id, summary, candidate_sha256, observed_at, saved_at,
                   transaction_id
            FROM continuity_records
            WHERE record_type = ? AND node = ? AND mode = ?
            ORDER BY saved_at DESC, record_id DESC
            LIMIT ?
            """,
            (normalized_type, normalized_node, normalized_mode, limit),
        ).fetchall()
        return [self._summary(row) for row in rows]

    def load(self, *, record_type: str, record_id: str) -> dict[str, Any] | None:
        normalized_type = _record_type(record_type)
        normalized_id = _required_text(record_id, "record_id")
        row = self.connection.execute(
            """
            SELECT record_id, record_type, node, mode, session_id, frame_id,
                   anchor_id, summary, candidate_json, candidate_sha256,
                   observed_at, saved_at, transaction_id, profile_sha256,
                   source_commit
            FROM continuity_records
            WHERE record_type = ? AND record_id = ?
            """,
            (normalized_type, normalized_id),
        ).fetchone()
        if row is None:
            return None
        candidate = json.loads(row["candidate_json"])
        return {
            **self._summary(row),
            "candidate": candidate,
            "profile_sha256": row["profile_sha256"],
            "source_commit": row["source_commit"],
            "evidence": self._evidence(
                record_type=row["record_type"],
                record_id=row["record_id"],
                candidate_sha256=row["candidate_sha256"],
                saved_at=row["saved_at"],
                transaction_id=row["transaction_id"],
            ),
        }

    def _configure_connection(self) -> None:
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")

    def _initialize_schema(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS continuity_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS continuity_records (
                    record_id TEXT PRIMARY KEY,
                    record_type TEXT NOT NULL CHECK (
                        record_type IN ('CHECKPOINT', 'RESUME')
                    ),
                    node TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    frame_id TEXT NOT NULL,
                    anchor_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    transaction_id TEXT NOT NULL UNIQUE,
                    profile_sha256 TEXT NOT NULL,
                    source_commit TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS continuity_records_coordinate_idx
                ON continuity_records(record_type, node, mode, saved_at DESC)
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS continuity_events (
                    event_id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(record_id) REFERENCES continuity_records(record_id)
                )
                """
            )
            row = self.connection.execute(
                "SELECT value FROM continuity_metadata WHERE key = 'schema'"
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO continuity_metadata(key, value) VALUES ('schema', ?)",
                    (STORE_SCHEMA,),
                )
            elif row[0] != STORE_SCHEMA:
                raise ContinuityStoreError(
                    "CONTINUITY_STORE_SCHEMA_UNSUPPORTED",
                    f"continuity store schema is not supported: {row[0]}",
                )
            self.connection.execute("COMMIT")
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _verify_schema(self) -> None:
        try:
            row = self.connection.execute(
                "SELECT value FROM continuity_metadata WHERE key = 'schema'"
            ).fetchone()
        except sqlite3.Error as error:
            raise ContinuityStoreError(
                "CONTINUITY_STORE_INVALID", "continuity store schema is unavailable"
            ) from error
        if row is None or row[0] != STORE_SCHEMA:
            raise ContinuityStoreError(
                "CONTINUITY_STORE_SCHEMA_UNSUPPORTED",
                "continuity store schema is not supported",
            )

    def _save_result(
        self,
        *,
        record_type: str,
        record_id: str,
        candidate_sha256: str,
        saved_at: str,
        transaction_id: str,
        idempotent: bool,
    ) -> dict[str, Any]:
        return {
            "status": "SAVED",
            "record_type": record_type,
            "record_id": record_id,
            "durability": "LOCAL_SQLITE_COMMITTED",
            "persistence_state": "PASSIVE",
            "idempotent": idempotent,
            "runtime_state_write": True,
            "evidence": self._evidence(
                record_type=record_type,
                record_id=record_id,
                candidate_sha256=candidate_sha256,
                saved_at=saved_at,
                transaction_id=transaction_id,
            ),
        }

    def _evidence(
        self,
        *,
        record_type: str,
        record_id: str,
        candidate_sha256: str,
        saved_at: str,
        transaction_id: str,
    ) -> dict[str, Any]:
        return {
            "schema": EVIDENCE_SCHEMA,
            "provider": "LOCAL_SQLITE",
            "database_ref": self.database_ref,
            "record_type": record_type,
            "record_id": record_id,
            "candidate_sha256": candidate_sha256,
            "transaction_id": transaction_id,
            "saved_at": saved_at,
            "commit_status": "COMMITTED",
        }

    def _summary(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "record_id": row["record_id"],
            "record_type": row["record_type"],
            "node": row["node"],
            "mode": row["mode"],
            "session_id": row["session_id"],
            "frame_id": row["frame_id"],
            "anchor_id": row["anchor_id"],
            "summary": row["summary"],
            "candidate_sha256": row["candidate_sha256"],
            "observed_at": row["observed_at"],
            "saved_at": row["saved_at"],
            "transaction_id": row["transaction_id"],
            "record_ref": f"{DATABASE_REF}#record={row['record_id']}",
        }


def _resolve_store_path(
    repository_root: Path, *, create_parent: bool
) -> tuple[Path, Path]:
    try:
        root = repository_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise ContinuityStoreError(
            "CONTINUITY_STORE_ROOT_UNAVAILABLE",
            "repository root cannot be resolved",
        ) from error
    if not root.is_dir():
        raise ContinuityStoreError(
            "CONTINUITY_STORE_ROOT_UNAVAILABLE", "repository root is not a directory"
        )
    database_path = root / DATABASE_RELATIVE_PATH
    parent = database_path.parent
    existing = parent
    while not existing.exists() and existing != root:
        existing = existing.parent
    try:
        resolved_existing = existing.resolve(strict=True)
        resolved_existing.relative_to(root)
    except (OSError, ValueError) as error:
        raise ContinuityStoreError(
            "CONTINUITY_STORE_PATH_FORBIDDEN",
            "continuity store path escapes the repository root",
        ) from error
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True)
    if parent.exists():
        try:
            parent.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise ContinuityStoreError(
                "CONTINUITY_STORE_PATH_FORBIDDEN",
                "continuity store parent escapes the repository root",
            ) from error
    return root, database_path


def _record_type(value: str) -> str:
    normalized = _required_text(value, "record_type").upper()
    if normalized not in RECORD_TYPES:
        raise ContinuityStoreError(
            "CONTINUITY_STORE_RECORD_INVALID",
            f"unsupported continuity record type: {normalized}",
        )
    return normalized


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuityStoreError(
            "CONTINUITY_STORE_RECORD_INVALID", f"{context} must be a non-empty string"
        )
    return value.strip()


def _optional_text(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ContinuityStoreError(
            "CONTINUITY_STORE_RECORD_INVALID", f"{context} must be a string"
        )
    return value.strip()


def _canonical_json_text(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ContinuityStoreError(
            "CONTINUITY_STORE_RECORD_INVALID", "candidate must be JSON serializable"
        ) from error


def _physical_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    raw = _canonical_json_text(dict(value)).encode("utf-8")
    return f"{prefix}_{_sha256(raw)[:24]}"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
