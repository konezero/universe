"""Project Host-owned turn phases into Bus and persona assignment state.

The Rust Host owns the durable turn ledger. This module only observes that
ledger during recovery; it never submits input. A ``NATIVE_QUEUED`` persona
receipt is promoted to ``applied`` only when the same Host message reaches
``PROMPT_SUBMITTED`` or ``STARTED``.
"""
from __future__ import annotations

from typing import Any


_PROVIDER_PHASES = {"PROMPT_SUBMITTED", "STARTED"}


def reconcile(server: Any) -> dict[str, Any]:
    reader = getattr(server.terminal_host, "turn_delivery_status", None)
    if not callable(reader):
        return {"started": [], "persona_applied": [], "errors": []}

    started: list[str] = []
    persona_applied: list[str] = []
    errors: list[dict[str, Any]] = []
    host = server._session_anchor_terminal_host()
    store = getattr(server, "store", None)
    read_assignment = getattr(store, "read_persona_assignment", None)
    record_applied = getattr(store, "record_persona_applied", None)

    for terminal in host.list_sessions():
        if terminal.get("state") != "LIVE" or terminal.get("provider") not in {"CODEX", "GROK"}:
            continue
        terminal_id = str(terminal.get("terminal_id") or "")
        terminal_provider = str(terminal.get("provider") or "").upper()
        anchor = str(
            terminal.get("session_anchor_ref")
            or terminal.get("active_session_anchor_ref")
            or ""
        )
        if not terminal_id or not anchor:
            continue

        try:
            state = reader(terminal_id)
        except Exception as error:
            errors.append(
                {
                    "operation": "HOST_TURN_PROJECT",
                    "terminal_id": terminal_id,
                    "error_code": getattr(error, "code", "HOST_TURN_PROJECT_FAILED"),
                    "detail": str(error),
                }
            )
            continue

        deliveries = state.get("messages") or []
        # Persona queue messages are offered directly to the Rust Host, so
        # they are not required to appear in Session Bus inbox(). Read the
        # assignment independently and bind only the exact queued message.
        if callable(read_assignment) and callable(record_applied):
            try:
                assignment = read_assignment(anchor)
                if (
                    assignment
                    and assignment.get("state") == "ACTIVE"
                    and assignment.get("queued_message_id")
                    and str(assignment.get("queued_provider") or "").upper()
                    == terminal_provider
                ):
                    queued_message_id = str(assignment["queued_message_id"])
                    for delivery in deliveries:
                        message_id = str(delivery.get("message_id") or "")
                        phase = str(delivery.get("phase") or "")
                        if message_id != queued_message_id or phase not in _PROVIDER_PHASES:
                            continue
                        previous_phase = str(assignment.get("applied_phase") or "")
                        previous_rank = {"PROMPT_SUBMITTED": 1, "STARTED": 2}.get(
                            previous_phase, 0
                        )
                        phase_rank = {"PROMPT_SUBMITTED": 1, "STARTED": 2}[phase]
                        was_applied = bool(assignment.get("applied_at"))
                        stamped = record_applied(
                            anchor,
                            terminal_id,
                            str(assignment.get("persona_id") or ""),
                            int(assignment.get("persona_revision") or 0),
                            int(assignment.get("assignment_revision") or 0),
                            phase=phase,
                            message_id=queued_message_id,
                        )
                        if stamped and (not was_applied or phase_rank > previous_rank):
                            persona_applied.append(queued_message_id)
                        break
            except Exception as error:
                errors.append(
                    {
                        "operation": "PERSONA_NATIVE_QUEUE_RECONCILE",
                        "terminal_id": terminal_id,
                        "session_anchor_ref": anchor,
                        "error_code": getattr(
                            error, "code", "PERSONA_RECONCILE_FAILED"
                        ),
                        "detail": str(error),
                    }
                )

        # Existing Bus turn projection remains independent from persona
        # assignment evidence. A missing Bus message must not suppress the
        # persona reconciliation above.
        try:
            messages = server.session_bus.inbox(
                host, session_anchor_ref=anchor, projection="ACTIVITY"
            )["messages"]
            pending = {
                m["message_id"]: m
                for m in messages
                if m.get("lifecycle_state") == "STARTED"
                and (m.get("lifecycle") or {}).get("delivery_channel")
                == "HOST_TURN_DELIVERY"
                and (m.get("lifecycle") or {}).get("execution_phase") != "RUNNING"
            }
            if not pending:
                continue
            for delivery in deliveries:
                message_id = delivery.get("message_id")
                phase = delivery.get("phase")
                if message_id not in pending or phase not in _PROVIDER_PHASES:
                    continue
                if (
                    phase == "PROMPT_SUBMITTED"
                    and (pending[message_id].get("lifecycle") or {}).get(
                        "provider_received_at"
                    )
                ):
                    continue
                server.session_bus.acknowledge_instruction(
                    message_id,
                    terminal_id=terminal_id,
                    session_anchor_ref=anchor,
                    phase="STARTED" if phase == "STARTED" else "RECEIVED",
                )
                started.append(message_id)
        except Exception as error:
            errors.append(
                {
                    "operation": "HOST_TURN_PROJECT",
                    "terminal_id": terminal_id,
                    "error_code": getattr(error, "code", "HOST_TURN_PROJECT_FAILED"),
                    "detail": str(error),
                }
            )

    return {"started": started, "persona_applied": persona_applied, "errors": errors}
