from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LIVE_INITIALIZATION_FAILURE = "LIVE_INITIALIZATION_FAILURE"
RETROSPECTIVE_OBSERVATION = "RETROSPECTIVE_OBSERVATION"
EVIDENCE_KINDS = frozenset(
    {LIVE_INITIALIZATION_FAILURE, RETROSPECTIVE_OBSERVATION}
)


class WorkerFailureEvidenceError(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str, *, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerFailureEvidenceError(f"{field} must be non-empty text")
    normalized = value.strip()
    if normalized.upper() == "UNKNOWN":
        raise WorkerFailureEvidenceError(f"{field} must not be UNKNOWN")
    if len(normalized) > limit:
        raise WorkerFailureEvidenceError(f"{field} exceeds {limit} characters")
    return normalized


def _optional_timestamp(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    normalized = _required_text(value, field, limit=128)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkerFailureEvidenceError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise WorkerFailureEvidenceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkerFailureEvidenceStore:
    """Durable Host evidence for Task Frame Worker claim failures."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS host_worker_failure_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    evidence_kind TEXT NOT NULL,
                    host_evidence_ref TEXT NOT NULL UNIQUE,
                    repository_ref TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    frame_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    worker_run_ref TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    failure_detail TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    failure_observed_at TEXT,
                    recorded_at TEXT NOT NULL,
                    evidence_digest TEXT NOT NULL UNIQUE,
                    evidence_json TEXT NOT NULL,
                    CHECK(evidence_kind IN (
                        'LIVE_INITIALIZATION_FAILURE',
                        'RETROSPECTIVE_OBSERVATION'
                    ))
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS host_worker_failure_coordinate
                ON host_worker_failure_evidence(
                    session_id, frame_id, turn_id, worker_run_ref
                )
                """
            )

    def record_live_failure(
        self,
        *,
        repository_ref: str,
        session_id: str,
        frame_id: str,
        turn_id: str,
        worker_id: str,
        worker_run_ref: str,
        failure_code: str,
        failure_detail: str,
        source_locator: str,
        source_content: bytes,
        failure_observed_at: str,
    ) -> dict[str, Any]:
        return self._record(
            evidence_kind=LIVE_INITIALIZATION_FAILURE,
            repository_ref=repository_ref,
            session_id=session_id,
            frame_id=frame_id,
            turn_id=turn_id,
            worker_id=worker_id,
            worker_run_ref=worker_run_ref,
            failure_code=failure_code,
            failure_detail=failure_detail,
            source_locator=source_locator,
            source_content=source_content,
            failure_observed_at=failure_observed_at,
        )

    def record_retrospective_observation(
        self,
        *,
        repository_ref: str,
        session_id: str,
        frame_id: str,
        turn_id: str,
        worker_id: str,
        worker_run_ref: str,
        failure_code: str,
        failure_detail: str,
        source_locator: str,
        source_content: bytes,
        failure_observed_at: str | None = None,
    ) -> dict[str, Any]:
        return self._record(
            evidence_kind=RETROSPECTIVE_OBSERVATION,
            repository_ref=repository_ref,
            session_id=session_id,
            frame_id=frame_id,
            turn_id=turn_id,
            worker_id=worker_id,
            worker_run_ref=worker_run_ref,
            failure_code=failure_code,
            failure_detail=failure_detail,
            source_locator=source_locator,
            source_content=source_content,
            failure_observed_at=failure_observed_at,
        )

    def get(self, host_evidence_ref: str) -> dict[str, Any] | None:
        normalized_ref = _required_text(
            host_evidence_ref, "host_evidence_ref", limit=2048
        )
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT evidence_json, recorded_at, host_evidence_ref
                FROM host_worker_failure_evidence
                WHERE host_evidence_ref = ?
                """,
                (normalized_ref,),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(str(row["evidence_json"]))
        result["host_evidence_ref"] = str(row["host_evidence_ref"])
        result["recorded_at"] = str(row["recorded_at"])
        return result

    def _record(
        self,
        *,
        evidence_kind: str,
        repository_ref: str,
        session_id: str,
        frame_id: str,
        turn_id: str,
        worker_id: str,
        worker_run_ref: str,
        failure_code: str,
        failure_detail: str,
        source_locator: str,
        source_content: bytes,
        failure_observed_at: str | None,
    ) -> dict[str, Any]:
        if evidence_kind not in EVIDENCE_KINDS:
            raise WorkerFailureEvidenceError("evidence_kind is unsupported")
        if not isinstance(source_content, bytes) or not source_content:
            raise WorkerFailureEvidenceError("source_content must be non-empty bytes")
        body = {
            "schema": "universe.host-worker-failure-evidence.v1",
            "evidence_kind": evidence_kind,
            "repository_ref": _required_text(
                repository_ref, "repository_ref", limit=2048
            ),
            "session_id": _required_text(session_id, "session_id", limit=1024),
            "frame_id": _required_text(frame_id, "frame_id", limit=1024),
            "turn_id": _required_text(turn_id, "turn_id", limit=1024),
            "worker_id": _required_text(worker_id, "worker_id", limit=1024),
            "worker_run_ref": _required_text(
                worker_run_ref, "worker_run_ref", limit=2048
            ),
            "failure_code": _required_text(
                failure_code, "failure_code", limit=256
            ).upper(),
            "failure_detail": _required_text(
                failure_detail, "failure_detail", limit=4096
            ),
            "source_locator": _required_text(
                source_locator, "source_locator", limit=4096
            ),
            "source_digest": hashlib.sha256(source_content).hexdigest(),
            "failure_observed_at": _optional_timestamp(
                failure_observed_at, "failure_observed_at"
            ),
            "durability_claim": (
                "RECORDED_AT_FAILURE_TIME"
                if evidence_kind == LIVE_INITIALIZATION_FAILURE
                else "OBSERVED_AND_RECORDED_RETROSPECTIVELY"
            ),
        }
        evidence_digest = _digest(body)
        evidence_id = f"worker_failure_{evidence_digest[:24]}"
        host_evidence_ref = (
            "universe-host-evidence://worker-initialization/"
            f"{evidence_id}#sha256={evidence_digest}"
        )
        recorded_at = _utc_now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO host_worker_failure_evidence(
                    evidence_id, evidence_kind, host_evidence_ref,
                    repository_ref, session_id, frame_id, turn_id,
                    worker_id, worker_run_ref, failure_code, failure_detail,
                    source_locator, source_digest, failure_observed_at,
                    recorded_at, evidence_digest, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    evidence_kind,
                    host_evidence_ref,
                    body["repository_ref"],
                    body["session_id"],
                    body["frame_id"],
                    body["turn_id"],
                    body["worker_id"],
                    body["worker_run_ref"],
                    body["failure_code"],
                    body["failure_detail"],
                    body["source_locator"],
                    body["source_digest"],
                    body["failure_observed_at"],
                    recorded_at,
                    evidence_digest,
                    _canonical_json(body),
                ),
            )
        result = self.get(host_evidence_ref)
        if result is None:
            raise WorkerFailureEvidenceError("failure evidence was not persisted")
        return result
