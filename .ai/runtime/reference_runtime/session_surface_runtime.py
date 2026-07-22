"""Deterministic session-surface observation transitions.

Transport observations are kept outside the Current Anchor snapshot. A
Commander input may update only the interaction surface and the Anchor's
physical observation time. The module never creates authority, assignment, or
execution permission.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


TRANSPORT_EVENTS = frozenset(
    {
        "TRANSPORT_ATTACHED",
        "TRANSPORT_DETACHED",
        "TRANSPORT_RECONNECTED",
        "TRANSPORT_ROUTE_CHANGED",
        "UI_HANDOFF",
    }
)

COMMANDER_INPUT_FIELDS = frozenset(
    {"commander_surface", "input_at", "evidence_ref"}
)

PROTECTED_PATHS = (
    "session_id",
    "frame_id",
    "anchor_id",
    "state",
    "state_updated_at",
    "authority",
    "authority_ref",
    "execution_assignment",
    "assignment_ref",
    "coordinates.session_location",
    "coordinates.execution_surface",
    "coordinates.repository_location",
)


def record_transport_observation(
    *, snapshot: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Record transport evidence without changing the Current Anchor."""

    current = _snapshot(snapshot)
    event_type = _required_text(observation.get("event_type"), "event_type").upper()
    if event_type not in TRANSPORT_EVENTS:
        return _rejected(current, "TRANSPORT_EVENT_UNSUPPORTED")
    observed_at = _timestamp(observation.get("observed_at"), "observed_at")
    evidence_ref = _required_text(observation.get("evidence_ref"), "evidence_ref")
    transport_state = _required_text(
        observation.get("transport_state"), "transport_state"
    ).upper()

    return {
        "status": "TRANSPORT_EVIDENCE_RECORDED",
        "event": {
            "event_type": event_type,
            "transport_state": transport_state,
            "observed_at": observed_at,
            "evidence_ref": evidence_ref,
        },
        "snapshot": current,
        "changed_snapshot_fields": [],
        "current_anchor_changed": False,
        "current_anchor_observed_at_advanced": False,
        "authority_created": False,
        "assignment_created": False,
        "cross_host_equivalence": "UNKNOWN",
    }


def observe_commander_input(
    *, snapshot: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply one Host-observed Commander input to the Current Anchor."""

    current = _snapshot(snapshot)
    unexpected = sorted(set(observation).difference(COMMANDER_INPUT_FIELDS))
    if unexpected:
        return _rejected(
            current,
            "COMMANDER_INPUT_FIELD_UNSUPPORTED",
            detail=unexpected[0],
        )

    commander_surface = _required_text(
        observation.get("commander_surface"), "commander_surface"
    )
    input_at = _timestamp(observation.get("input_at"), "input_at")
    evidence_ref = _required_text(observation.get("evidence_ref"), "evidence_ref")
    previous_observed_at = _timestamp(current.get("observed_at"), "snapshot.observed_at")
    if _parse_timestamp(input_at) < _parse_timestamp(previous_observed_at):
        return _rejected(current, "OBSERVATION_TIME_REGRESSION")

    coordinates = current.get("coordinates")
    if not isinstance(coordinates, Mapping):
        return _rejected(current, "SESSION_COORDINATES_REQUIRED")

    updated = copy.deepcopy(current)
    updated_coordinates = dict(updated["coordinates"])
    previous_surface = _required_text(
        updated_coordinates.get("commander_surface"),
        "snapshot.coordinates.commander_surface",
    )
    updated_coordinates["commander_surface"] = commander_surface
    updated["coordinates"] = updated_coordinates
    updated["observed_at"] = input_at

    evaluation = evaluate_surface_transition(
        before=current,
        after=updated,
        allowed_paths=("coordinates.commander_surface", "observed_at"),
    )
    if evaluation["status"] != "PASS":
        return _rejected(current, "SESSION_SURFACE_TRANSITION_INVALID")

    changed = evaluation["changed_fields"]
    return {
        "status": "COMMANDER_INPUT_OBSERVED",
        "event": {
            "event_type": "USER_INPUT",
            "commander_surface": commander_surface,
            "previous_commander_surface": previous_surface,
            "observed_at": input_at,
            "evidence_ref": evidence_ref,
        },
        "snapshot": updated,
        "changed_snapshot_fields": changed,
        "binding_recheck_required": previous_surface != commander_surface,
        "current_anchor_changed": False,
        "authority_created": False,
        "assignment_created": False,
        "cross_host_equivalence": "UNKNOWN",
    }


def evaluate_surface_transition(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    allowed_paths: tuple[str, ...],
) -> dict[str, Any]:
    """Return a deterministic allow-list comparison for one transition."""

    left = _snapshot(before)
    right = _snapshot(after)
    changed = sorted(_changed_paths(left, right))
    forbidden = [path for path in changed if path not in set(allowed_paths)]
    protected = [
        path
        for path in PROTECTED_PATHS
        if _path_value(left, path) != _path_value(right, path)
    ]
    reasons = []
    if forbidden:
        reasons.append("FIELD_MUTATION_NOT_ALLOWED")
    if protected:
        reasons.append("PROTECTED_COORDINATE_MUTATED")
    return {
        "status": "PASS" if not reasons else "FAIL",
        "changed_fields": changed,
        "forbidden_fields": forbidden,
        "protected_fields": protected,
        "reasons": reasons,
    }


def _snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be an object")
    return copy.deepcopy(dict(value))


def _rejected(
    snapshot: Mapping[str, Any], error_code: str, *, detail: str | None = None
) -> dict[str, Any]:
    result = {
        "status": "SESSION_SURFACE_OBSERVATION_REJECTED",
        "error_code": error_code,
        "snapshot": copy.deepcopy(dict(snapshot)),
        "changed_snapshot_fields": [],
        "authority_created": False,
        "assignment_created": False,
        "cross_host_equivalence": "UNKNOWN",
    }
    if detail is not None:
        result["detail"] = detail
    return result


def _changed_paths(left: Mapping[str, Any], right: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    keys = set(left).union(right)
    for key in keys:
        left_value = left.get(key)
        right_value = right.get(key)
        if isinstance(left_value, Mapping) and isinstance(right_value, Mapping):
            for child in _changed_paths(left_value, right_value):
                paths.add(f"{key}.{child}")
        elif left_value != right_value:
            paths.add(str(key))
    return paths


def _path_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, context: str) -> str:
    normalized = _required_text(value, context).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{context} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{context} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
