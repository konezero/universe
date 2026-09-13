"""Read-only reminder at Stop; the agent decides which follow-ups to process."""
from __future__ import annotations
from typing import Any, Mapping
from universe_app.session_bus import SessionBusError, message_handling


def handle_hook(bus: Any, host: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        raise SessionBusError("BUS_HOOK_EVENT_INVALID", "only the Stop boundary is supported")
    result: dict[str, Any] = {"status": "NO_PENDING_MESSAGE", "pending_count": 0, "messages": []}
    # A reminder must not create an unbounded Stop -> model -> Stop loop.
    if payload.get("stop_hook_active") is True:
        return {**result, "status": "CONTINUATION_ALREADY_REQUESTED"}
    anchor = str(payload["session_anchor_ref"])
    with bus._lock:
        reports = [{"message_id": m["message_id"], "thread_id": m["thread_id"],
                    "lifecycle_state": m["lifecycle_state"], "handling": message_handling(m)}
                   for m in bus._messages.values()
                   if m.get("recipient_anchor_ref") == anchor
                   and m.get("lifecycle_state") in {"QUEUED", "ACCEPTED", "STARTED"}
                   and message_handling(m)["protocol"] in {"REPLY", "CONVERSATION"}]
    # No body injection, claim, read receipt, reply, or task completion here.
    # Relevance is a contextual decision made by the continued agent after it
    # reads the canonical inbox and original thread.
    if reports:
        result.update(status="PENDING_MESSAGE", pending_count=len(reports), messages=reports[:20])
    return result
