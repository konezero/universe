from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote


LINEAGE_SOURCE_KINDS = frozenset(
    {
        "SESSION_RESULT",
        "ROOM_RESULT",
        "INBOX_RESULT",
        "TASK_FRAME_RESULT",
        "CONDUCTOR_RESULT",
        "DISPATCH_RESULT",
    }
)
TERMINAL_EVENT_TYPES = frozenset(
    {"COMPLETED", "FAILED", "DECISION_REQUIRED", "RESULT_ATTACHED"}
)
RETRIEVAL_STATES = frozenset({"ACTIVE", "CONFLICTED", "SUPERSEDED"})
STALE_AFTER_SECONDS = 30 * 24 * 60 * 60
SOURCE_REF_TEMPLATES = {
    "SESSION_RESULT": "universe://session-results/{event_id}",
    "ROOM_RESULT": "universe://project-room-messages/{event_id}",
    "INBOX_RESULT": "universe://session-inbox-results/{event_id}",
    "TASK_FRAME_RESULT": "universe://task-frame-results/{event_id}",
    "CONDUCTOR_RESULT": "universe://conductor-delegations/{event_id}/result",
    "DISPATCH_RESULT": "universe://dispatches/{event_id}/result",
}


class ProjectRagError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _text(value: Any, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectRagError("LINEAGE_MEMORY_FIELD_INVALID", f"{field} is required")
    text = value.strip()
    if len(text) > max_length:
        raise ProjectRagError("LINEAGE_MEMORY_FIELD_INVALID", f"{field} is too long")
    return text


def parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ProjectRagError(
            "LINEAGE_MEMORY_TIMESTAMP_INVALID", "observed_at must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise ProjectRagError(
            "LINEAGE_MEMORY_TIMESTAMP_INVALID", "observed_at must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def canonical_source_ref(source_kind: str, event_id: str) -> str:
    template = SOURCE_REF_TEMPLATES.get(source_kind.strip().upper())
    if template is None:
        raise ProjectRagError("LINEAGE_MEMORY_SOURCE_INVALID", "source kind is unsupported")
    return template.format(event_id=quote(event_id.strip(), safe=""))


def normalize_lineage_memory_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectRagError("LINEAGE_MEMORY_REQUEST_INVALID", "event must be an object")
    required = {
        "project_id",
        "producer_kind",
        "source_kind",
        "event_id",
        "event_type",
        "title",
        "summary",
        "source_ref",
        "observed_at",
    }
    optional = {"node_ref", "retrieval_state"}
    if set(value) - required - optional or not required.issubset(value):
        raise ProjectRagError(
            "LINEAGE_MEMORY_REQUEST_INVALID",
            "event fields do not match the lineage-memory contract",
        )
    source_kind = _text(value["source_kind"], "source_kind", max_length=64).upper()
    if source_kind not in LINEAGE_SOURCE_KINDS:
        raise ProjectRagError(
            "LINEAGE_MEMORY_SOURCE_INVALID",
            "only authoritative terminal result source kinds may be ingested",
        )
    project_id = _text(value["project_id"], "project_id", max_length=160)
    producer_kind = _text(value["producer_kind"], "producer_kind", max_length=64).upper()
    if producer_kind != "UNIVERSE_STORE":
        raise ProjectRagError(
            "LINEAGE_MEMORY_PRODUCER_INVALID",
            "lineage memory accepts only in-process Universe Store producers",
        )
    event_id = _text(value["event_id"], "event_id", max_length=256)
    expected_source_ref = canonical_source_ref(source_kind, event_id)
    source_ref = _text(value["source_ref"], "source_ref", max_length=320)
    if source_ref != expected_source_ref:
        raise ProjectRagError(
            "LINEAGE_MEMORY_SOURCE_REF_INVALID",
            "source_ref is not the canonical immutable ref for this source and event",
        )
    event_type = _text(value["event_type"], "event_type", max_length=64).upper()
    if event_type not in TERMINAL_EVENT_TYPES:
        raise ProjectRagError(
            "LINEAGE_MEMORY_EVENT_INVALID",
            "only terminal result events may be ingested",
        )
    retrieval_state = str(value.get("retrieval_state") or "ACTIVE").strip().upper()
    if retrieval_state not in RETRIEVAL_STATES:
        raise ProjectRagError(
            "LINEAGE_MEMORY_RETRIEVAL_STATE_INVALID",
            "retrieval_state must be ACTIVE, CONFLICTED, or SUPERSEDED",
        )
    observed_at = parse_timestamp(value["observed_at"]).isoformat().replace(
        "+00:00", "Z"
    )
    node_ref = value.get("node_ref")
    if node_ref is not None:
        node_ref = _text(node_ref, "node_ref", max_length=160)
    return {
        "project_id": project_id,
        "producer_kind": producer_kind,
        "source_kind": source_kind,
        "event_id": event_id,
        "event_type": event_type,
        "title": _text(value["title"], "title", max_length=160),
        "summary": _text(value["summary"], "summary", max_length=2000),
        "source_ref": source_ref,
        "observed_at": observed_at,
        "node_ref": node_ref,
        "retrieval_state": retrieval_state,
    }


def retrieval_state(memory: Mapping[str, Any]) -> str:
    state = str(memory.get("retrieval_state") or "ACTIVE").strip().upper()
    return state if state in RETRIEVAL_STATES else "CONFLICTED"


def freshness_metadata(
    updated_at: Any,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    observed = parse_timestamp(updated_at)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = max(0, int((current - observed).total_seconds()))
    return {
        "state": "STALE" if age_seconds > stale_after_seconds else "FRESH",
        "age_seconds": age_seconds,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "stale_after_seconds": stale_after_seconds,
    }
