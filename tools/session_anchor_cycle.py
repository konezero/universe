"""Append one finished execution cycle to a Session Anchor's own history.

A Task Frame is an ephemeral child of its origin Session Anchor
(docs/ANCHOR_GRAPH_RUNTIME.md).  When its result is collected, the Session
Anchor appends that fact to its own store, so a later run, resume or reader can
tell how far the session got from its own history alone.

Appending is the revision: the store's append-only ``memory_events`` ordinal
only ever grows, and nothing already written is rewritten.  Universe owns
identity; this module never derives a session from a provider conversation id.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CYCLE_ACTION = "TASK_FRAME_RESULT_COLLECTED"
CYCLE_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def session_store_path(repo_root: Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return Path(repo_root) / ".ai" / "runtime" / "session_store" / f"session-{digest}.sqlite3"


def _runtime_class():
    package = Path(__file__).absolute().parents[1] / ".ai" / "runtime"
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
    from reference_runtime.anchor_session_memory_runtime import AnchorSessionMemoryRuntime

    return AnchorSessionMemoryRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def append_cycle_result(
    repo_root: Path,
    *,
    session_id: str,
    session_anchor_ref: str,
    task_frame_id: str,
    status: str,
    result_ref: str | None = None,
    result_digest: str | None = None,
    detail: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Append the collected result of one Task Frame to its Session Anchor.

    Idempotent by ``(task_frame_id, status)``: repeating it appends nothing and
    reports the existing position.  Returns the Session Anchor's revision (the
    ordinal of its newest appended record) and the append time.
    """

    session_id = str(session_id or "").strip()
    anchor = str(session_anchor_ref or "").strip()
    frame = str(task_frame_id or "").strip()
    normalized_status = str(status or "").strip().upper()
    if not (session_id and anchor and frame):
        return {"status": "SESSION_ANCHOR_CYCLE_IDENTITY_REQUIRED"}
    if normalized_status not in CYCLE_STATUSES:
        return {"status": "SESSION_ANCHOR_CYCLE_STATUS_INVALID", "allowed": sorted(CYCLE_STATUSES)}
    path = session_store_path(repo_root, session_id)
    if not path.is_file():
        return {"status": "SESSION_ANCHOR_STORE_MISSING", "session_id": session_id}
    runtime = _runtime_class()(database_path=path)
    try:
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "SESSION_ANCHOR_SNAPSHOT_MISSING", "session_id": session_id}
        if stored["anchor_id"] != anchor:
            # The store belongs to another Session Anchor: never append across identities.
            return {
                "status": "SESSION_ANCHOR_IDENTITY_MISMATCH",
                "session_id": session_id,
                "stored_anchor_id": stored["anchor_id"],
            }
        # Observation time only moves forward (the store rejects a regression
        # for the same anchor), so never stamp an earlier time than the anchor.
        at = observed_at or _now()
        if str(stored["observed_at"]) > at:
            at = str(stored["observed_at"])
        details: dict[str, Any] = {
            "task_frame_id": frame,
            "status": normalized_status,
            "session_anchor_ref": anchor,
        }
        if result_ref:
            details["result_ref"] = result_ref
        if result_digest:
            details["result_digest"] = result_digest
        if detail:
            details["detail"] = dict(detail)
        outcome = runtime.record_observation(
            frame_id=str(stored["frame_id"]),
            event_id=f"taskframe-cycle:{frame}:{normalized_status}",
            action=CYCLE_ACTION,
            details=details,
            source_ref=str(stored["source_ref"]),
            observed_at=at,
        )
        replayed = outcome.get("status") == "EVENT_ID_ALREADY_EXISTS"
        if outcome.get("status") != "OBSERVATION_RECORDED" and not replayed:
            return {"status": "SESSION_ANCHOR_CYCLE_APPEND_FAILED", "cause": outcome.get("status")}
        revision = runtime.conn.execute(
            "SELECT COALESCE(MAX(event_ordinal), 0) FROM memory_events"
        ).fetchone()[0]
        return {
            "status": "SESSION_ANCHOR_CYCLE_REPLAYED" if replayed else "SESSION_ANCHOR_CYCLE_APPENDED",
            "session_id": session_id,
            "session_anchor_ref": anchor,
            "task_frame_id": frame,
            "cycle_status": normalized_status,
            "revision": int(revision),
            "observed_at": at,
        }
    finally:
        runtime.close()
