"""Ask the Master when a Worker's write falls outside the declared scope, or a
Worker or Reviewer wants to run a command.

A refusal that nobody sees stalls the work, and a silent approval defeats the
scope.  So the Host pauses only the turn that asked: it posts a permission
request to the Boss room, wakes the Master over the Session Bus, and waits for
the Master's decision in that room.  Anything but an explicit MASTER APPROVE
(the Todo or room ending while it waits, an unreachable room, a malformed
reply, or a configured timeout) is a denial.

Who may approve is the server's decision, not the Host's: a non-destructive
write is the Master's to approve, a destructive one (DELETE or MOVE) needs the
Conductor first.  The Host only reports what was asked, marks it destructive or
not, and carries the answer back.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Callable, Mapping

from task_frame_host import BusPort, RoomPort

PERMISSION_REQUEST_SCHEMA = "universe.task-frame-host-permission-request.v1"
PERMISSION_DECISION_SCHEMA = "universe.task-frame-host-permission-decision.v1"


def parse_permission_decision(event: Mapping[str, Any], request_id: str) -> str | None:
    """APPROVE or DENY when a MASTER-authored message decides this request."""

    message = event.get("message")
    if not isinstance(message, Mapping) or str(message.get("author_role") or "").upper() != "MASTER":
        return None
    try:
        body = json.loads(str(message.get("body_text") or ""))
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(body, Mapping)
        or body.get("schema") != PERMISSION_DECISION_SCHEMA
        or body.get("request_id") != request_id
    ):
        return None
    decision = str(body.get("decision") or "").upper()
    return decision if decision in {"APPROVE", "DENY"} else None


class HostPermissionEscalator:
    def __init__(
        self,
        *,
        task_frame_id: str,
        todo_id: str,
        room: RoomPort,
        bus: BusPort,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 2.0,
        alive: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.task_frame_id = task_frame_id
        self.todo_id = todo_id
        self.room = room
        self.bus = bus
        # None waits for as long as the Todo and the room are live: an answer may
        # have to come from the operator through the Master and the Conductor.
        self.timeout_seconds = timeout_seconds
        self._alive = alive
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._count = 0
        self._count_lock = threading.Lock()
        self.current_role = "WORKER"

    def __call__(self, description: Mapping[str, Any]) -> str:
        with self._count_lock:
            self._count += 1
            count = self._count
        # A concurrent waiter must not advance this request past its own decision.
        cursor = 0
        digest = hashlib.sha256(
            json.dumps([self.task_frame_id, self.current_role, count, dict(description)], sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        request_id = f"perm_{digest}"
        payload = {
            "schema": PERMISSION_REQUEST_SCHEMA,
            "request_id": request_id,
            "task_frame_id": self.task_frame_id,
            "todo_id": self.todo_id,
            "role": self.current_role,
            "tool": description.get("tool"),
            "targets": list(description.get("targets") or []),
            "operations": list(description.get("operations") or []),
            "destructive": bool(description.get("destructive")),
        }
        if description.get("kind") == "COMMAND":
            payload.update(
                {
                    "kind": "COMMAND",
                    "command": str(description.get("command") or ""),
                    "cwd": str(description.get("cwd") or ""),
                    "reason": str(description.get("reason") or ""),
                    "escalated_permissions": description.get("escalated_permissions"),
                }
            )
            text = (
                f"{self.current_role} asks to run a command: {payload['command'][:300]!r}"
                + (" [needs more than the sandbox allows]" if payload["escalated_permissions"] else "")
                + (" [DESTRUCTIVE: needs the Conductor]" if payload["destructive"] else "")
            )
        else:
            text = (
                f"{self.current_role} asks to {'/'.join(payload['operations']) or 'write'} "
                f"{', '.join(payload['targets']) or '(unknown target)'} outside its declared scope"
                + (" [DESTRUCTIVE: needs the Conductor]" if payload["destructive"] else "")
            )
        try:
            self.room.post_report(
                body_text=json.dumps(payload, ensure_ascii=False), severity="PERMISSION",
                idempotency_key=f"host:{self.task_frame_id}:PERMISSION:{request_id}",
            )
            self.bus.notify(
                idempotency_key=f"host:{self.task_frame_id}:PERMISSION:{request_id}",
                body_text=text, payload=payload,
            )
            self.room.call_master(reason=text)
        except Exception:  # noqa: BLE001 - a Master that cannot be reached cannot approve
            return "DENY"
        deadline = None if self.timeout_seconds is None else self._clock() + self.timeout_seconds
        while True:
            try:
                events = list(self.room.events_after(cursor))
            except Exception:  # noqa: BLE001
                events = []
            for event in sorted(events, key=lambda item: int(item.get("room_sequence") or 0)):
                cursor = max(cursor, int(event.get("room_sequence") or 0))
                decision = parse_permission_decision(event, request_id)
                if decision is not None:
                    return decision
            if deadline is not None and self._clock() >= deadline:
                return "DENY"
            if self._alive is not None:
                try:
                    if not self._alive():
                        return "DENY"
                except Exception:  # noqa: BLE001 - unknown is not a reason to stop waiting
                    pass
            self._sleep(self.poll_interval_seconds)
