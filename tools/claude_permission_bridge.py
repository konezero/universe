"""Transport adapter between Claude's permission prompt tool and Universe.

Claude Code routes a permission prompt to the MCP tool named by
``--permission-prompt-tool``. This module owns only the transport: it turns
Claude's tool request into the existing ``universe.agent-permission-request.v1``
contract, hands it to the caller-supplied ``permission_requester`` (the
Conductor bridge or the Project Master bridge), and maps the returned option
back into Claude's allow/deny shape.

It does not own a permission policy, does not decide anything on its own, and
never writes global Claude settings or ``allowedTools``.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4


PERMISSION_REQUEST_SCHEMA = "universe.agent-permission-request.v1"

OPTION_ALLOW_ONCE = "allow_once"
OPTION_ALLOW_ALWAYS = "allow_always"
OPTION_REJECT_ONCE = "reject_once"

# The exact option set Universe offers for Claude. Every entry maps to a
# behavior Claude actually supports.
CLAUDE_PERMISSION_OPTIONS: tuple[dict[str, str], ...] = (
    {"optionId": "allow-once", "name": "Allow once", "kind": OPTION_ALLOW_ONCE},
    {
        "optionId": "allow-always",
        "name": "Allow for this session",
        "kind": OPTION_ALLOW_ALWAYS,
    },
    {"optionId": "reject-once", "name": "Reject", "kind": OPTION_REJECT_ONCE},
)
_OPTION_KIND_BY_ID = {item["optionId"]: item["kind"] for item in CLAUDE_PERMISSION_OPTIONS}


class ClaudePermissionError(RuntimeError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaudePermissionError(f"CLAUDE_PERMISSION_FIELD_INVALID:{field}")
    return value.strip()


def deny(message: str) -> dict[str, Any]:
    """Build the fail-closed answer Claude understands."""

    return {"behavior": "deny", "message": message}


def allow(
    updated_input: Mapping[str, Any],
    *,
    updated_permissions: list[Any] | None = None,
) -> dict[str, Any]:
    """Build an allow answer.

    ``updatedInput`` is required by Claude Code; omitting it is rejected as a
    validation error on releases before 2.1.207.
    """

    result: dict[str, Any] = {
        "behavior": "allow",
        "updatedInput": json.loads(json.dumps(dict(updated_input))),
    }
    if updated_permissions:
        result["updatedPermissions"] = json.loads(json.dumps(list(updated_permissions)))
    return result


class ClaudePermissionBridge:
    """Bind one resident Claude session to one Universe permission requester."""

    def __init__(
        self,
        *,
        session_ref: str,
        permission_requester: Callable[[Mapping[str, Any]], str | None],
        timeout_seconds: float = 300.0,
    ) -> None:
        self.session_ref = str(session_ref)
        self.permission_requester = permission_requester
        self.timeout_seconds = float(timeout_seconds)
        self._closed = threading.Event()
        self._turn_id: str | None = None
        # Bumped on every turn bind. A decision is adopted only if the
        # revision is unchanged since the prompt was raised (CAS).
        self._turn_revision = 0
        self._lock = threading.Lock()

    def bind_turn(self, turn_id: str | None) -> None:
        """Bind the turn a permission request must belong to."""

        with self._lock:
            self._turn_id = str(turn_id) if turn_id else None
            self._turn_revision += 1

    def close(self) -> None:
        """Stop serving. Any later request, and any in-flight decision that has
        not yet been adopted, fails closed.

        ``close`` takes the same lock the adoption step uses, so a decision can
        never be adopted after this returns.
        """

        with self._lock:
            self._closed.set()
            self._turn_revision += 1

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Answer one Claude permission prompt.

        Every failure path denies. Nothing here can approve on its own.
        """

        if self._closed.is_set():
            return deny("Universe permission service stopped")
        try:
            tool_name = _text(request.get("tool_name"), "tool_name")
        except ClaudePermissionError as error:
            return deny(str(error))
        tool_input = request.get("input")
        if not isinstance(tool_input, Mapping):
            return deny("CLAUDE_PERMISSION_INPUT_INVALID")

        # Identity binding: a request that names a different session or a turn
        # that is not the active one must not be answered.
        claimed_session = request.get("session_ref")
        if claimed_session is not None and str(claimed_session) != self.session_ref:
            return deny("CLAUDE_PERMISSION_SESSION_MISMATCH")
        claimed_turn = request.get("turn_id")
        with self._lock:
            active_turn = self._turn_id
        if claimed_turn is not None and active_turn is not None:
            if str(claimed_turn) != active_turn:
                return deny("CLAUDE_PERMISSION_TURN_MISMATCH")

        with self._lock:
            revision_at_prompt = self._turn_revision

        suggestions = request.get("suggestions")
        suggestions = suggestions if isinstance(suggestions, list) else []
        universe_request = {
            "schema": PERMISSION_REQUEST_SCHEMA,
            "request_id": f"permission_{uuid4().hex}",
            "provider": "CLAUDE",
            "session_id": self.session_ref,
            "tool_call": {
                "toolCallId": request.get("tool_use_id") or f"claude_{uuid4().hex}",
                "title": tool_name,
                "toolName": tool_name,
                "input": json.loads(json.dumps(dict(tool_input))),
                "command": tool_input.get("command"),
                "path": tool_input.get("file_path"),
                "reason": request.get("permission_suggestions_reason"),
                "turnId": active_turn,
            },
            "options": [dict(option) for option in CLAUDE_PERMISSION_OPTIONS],
        }

        try:
            option_id = self.permission_requester(universe_request)
        except Exception as error:  # noqa: BLE001 - any failure denies
            return deny(f"CLAUDE_PERMISSION_REQUESTER_FAILED:{error}"[:500])

        # Adopt the decision atomically: the shutdown check, the turn-revision
        # CAS, and building the allow result all happen under one lock, so a
        # close() that lands right after the check cannot leak an approval.
        with self._lock:
            if self._closed.is_set():
                return deny("CLAUDE_PERMISSION_CANCELLED_BY_SHUTDOWN")
            if self._turn_revision != revision_at_prompt:
                # The turn moved on while the operator was deciding.
                return deny("CLAUDE_PERMISSION_TURN_SUPERSEDED")
            if claimed_turn is not None and self._turn_id is not None:
                if str(claimed_turn) != self._turn_id:
                    return deny("CLAUDE_PERMISSION_TURN_MISMATCH")
            if option_id is None:
                # No answer from the UI: timeout, cancel, or disconnect.
                return deny("CLAUDE_PERMISSION_NO_DECISION")
            kind = _OPTION_KIND_BY_ID.get(str(option_id))
            if kind is None:
                return deny("CLAUDE_PERMISSION_OPTION_UNKNOWN")
            if kind == OPTION_REJECT_ONCE:
                return deny("User rejected this action")
            if kind == OPTION_ALLOW_ONCE:
                return allow(tool_input)
            # allow_always: persist only what Claude itself proposed, and only
            # because the user explicitly picked the persistent option in the UI.
            persist = [
                item
                for item in suggestions
                if isinstance(item, Mapping)
                and item.get("destination") == "localSettings"
            ]
            return allow(tool_input, updated_permissions=persist)
