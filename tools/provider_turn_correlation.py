"""Reduce explicit provider turn correlation; never infer ownership from time."""
from __future__ import annotations

import re
from typing import Any, Mapping

# The verified PTY submit path may fold newlines into spaces. Accept only
# whitespace between the two anchored, exact metadata fields, not quoted text.
HEADER = re.compile(
    r"\Auniverse_dispatch_ref: (dispatch_[0-9a-f]{32})[ \t\r\n]+"
    r"instruction_ref: session-bus:(msg_[0-9a-f]{16})[ \t\r\n]+"
)


def codex_turn_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Only accept the observed event_msg envelope, not text in tool output."""
    if event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    turn = payload.get("turn_id")
    if not isinstance(turn, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", turn):
        return None
    kind = payload.get("type")
    value: dict[str, Any] = {"provider_turn_id": turn}
    if kind == "task_complete":
        return {**value, "event_kind": "TURN_COMPLETED", "activity_state": "COMPLETED"}
    if kind == "task_started":
        return {**value, "event_kind": "TURN_STARTED", "activity_state": "ACTIVE"}
    item = payload.get("item")
    if kind != "item_completed" or not isinstance(item, Mapping) or item.get("type") != "UserMessage":
        return None
    content = item.get("content")
    # Codex UserMessage content is a list of text input objects. Other input
    # types, assistant echoes, tool output and partial markers are not proof.
    if not isinstance(content, list):
        return None
    text = "\n".join(
        part["text"] for part in content
        if isinstance(part, Mapping) and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )
    match = HEADER.match(text)
    if not match:
        return None
    return {**value, "event_kind": "TURN_STARTED", "activity_state": "ACTIVE",
            "bus_dispatch_ref": match[1], "bus_message_id": match[2]}


def exact_turn_matches(lifecycle: Mapping[str, Any], message_id: str,
                       source_id: str, activity: Mapping[str, Any]) -> bool:
    return bool(
        lifecycle.get("observer_source_id") == source_id
        and activity.get("source_id") == source_id
        and lifecycle.get("bus_dispatch_ref")
        and lifecycle.get("bus_dispatch_ref") == activity.get("bus_dispatch_ref")
        and activity.get("bus_message_id") == message_id
        and activity.get("provider_turn_id")
        and activity.get("event_kind") == "TURN_COMPLETED"
        and activity.get("activity_state") == "COMPLETED"
    )
