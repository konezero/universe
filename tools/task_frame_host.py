"""Independent Task Frame Host lifecycle for one Todo.

A Master (the Parent) launches a Task Frame whose Worker and Reviewer are
persona roles inside that frame.  This Host runs those roles and keeps the
Master informed; it does not decide what happens next.

Lifetime is the Todo's, not one role's:

    run the first role -> report the result to the Boss room and wake the
    Master over the Session Bus -> wait for the Master's directive -> run the
    next role or rework -> ... -> exit when the Todo is finished.

A failed role is reported to the Master (Boss room report plus call_master plus
a Session Bus notice) and the Host then waits; it never retries or exits on its
own.  It exits only when the Master says DONE, the Todo reaches a terminal
state, the room is closed, or the Master has been silent for too long.

Everything the Host touches is behind a small port, so the lifecycle is tested
without a server and the HTTP transport (rooms, Session Bus, Todo reads) is a
separate, replaceable layer.  An unreachable port never kills the Host: a
notice that cannot be delivered stays in an outbox and is resent.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

DIRECTIVE_SCHEMA = "universe.task-frame-host-directive.v1"
NOTICE_SCHEMA = "universe.task-frame-host-notice.v1"
ROLES = frozenset({"WORKER", "REVIEWER"})
DIRECTIVES = frozenset({"RUN_ROLE", "REWORK", "DONE"})
TERMINAL_TODO_STATES = frozenset({"DONE", "BLOCKED"})


@dataclass(frozen=True)
class RoleResult:
    status: str  # COMPLETED | FAILED
    summary: str = ""
    result_ref: str | None = None
    result_digest: str | None = None
    error_code: str | None = None


class RoleRunner(Protocol):
    def run(self, role: str, *, attempt: int, feedback: str | None) -> RoleResult: ...


class RoomPort(Protocol):
    def post_report(self, *, body_text: str, severity: str, idempotency_key: str) -> None: ...

    def call_master(self, *, reason: str) -> None: ...

    def events_after(self, sequence: int) -> Sequence[Mapping[str, Any]]: ...

    def is_open(self) -> bool: ...


class BusPort(Protocol):
    def notify(self, *, idempotency_key: str, body_text: str, payload: Mapping[str, Any]) -> None: ...


class TodoPort(Protocol):
    def state(self) -> tuple[str, bool]:
        """Return ``(todo_state, archived)``."""


@dataclass(frozen=True)
class HostConfig:
    task_frame_id: str
    todo_id: str
    first_role: str = "WORKER"
    idle_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0


@dataclass
class HostOutcome:
    exit_reason: str
    roles_run: list[tuple[str, int, str]] = field(default_factory=list)  # (role, attempt, status)
    port_errors: int = 0


def parse_directive(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """A directive is a MASTER-authored room message with a JSON body.

    Anything else (other authors, prose, an unknown directive or role) is not a
    directive: the Host never acts on text it cannot attribute to the Master.
    """

    message = event.get("message")
    if not isinstance(message, Mapping) or str(message.get("author_role") or "").upper() != "MASTER":
        return None
    try:
        body = json.loads(str(message.get("body_text") or ""))
    except (TypeError, ValueError):
        return None
    if not isinstance(body, Mapping) or body.get("schema") != DIRECTIVE_SCHEMA:
        return None
    directive = str(body.get("directive") or "").upper()
    if directive not in DIRECTIVES:
        return None
    role = str(body.get("role") or "").upper() or None
    if directive != "DONE" and role not in ROLES:
        return None
    feedback = body.get("feedback")
    return {
        "directive": directive,
        "role": role,
        "feedback": feedback if isinstance(feedback, str) and feedback.strip() else None,
    }


class TaskFrameHost:
    def __init__(
        self,
        config: HostConfig,
        *,
        runner: RoleRunner,
        room: RoomPort,
        bus: BusPort,
        todo: TodoPort,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        on_tick: Callable[[str], None] | None = None,
    ) -> None:
        if config.first_role not in ROLES:
            raise ValueError("first_role must be WORKER or REVIEWER")
        self.config = config
        self.runner = runner
        self.room = room
        self.bus = bus
        self.todo = todo
        self._clock = clock
        self._sleep = sleep
        self._on_tick = on_tick
        self._cursor = 0
        self._attempts: dict[str, int] = {}
        self._outbox: list[dict[str, Any]] = []
        self._last_activity = clock()
        self.outcome = HostOutcome(exit_reason="")

    # ------------------------------------------------------------------ run
    def run(self) -> HostOutcome:
        self._run_role(self.config.first_role, feedback=None)
        reason = ""
        while not reason:
            self._tick("WAITING")
            reason = self._terminal_reason()
            if reason:
                break
            self._flush_outbox()
            reason = self._handle_directives()
            if reason:
                break
            if self._clock() - self._last_activity >= self.config.idle_timeout_seconds:
                reason = "IDLE_TIMEOUT"
                self._announce_exit(reason, failed=True)
                break
            self._sleep(self.config.poll_interval_seconds)
        if reason != "IDLE_TIMEOUT":
            self._announce_exit(reason, failed=False)
        self._flush_outbox()
        self.outcome.exit_reason = reason
        self._tick("EXITED:" + reason)
        return self.outcome

    def _tick(self, phase: str) -> None:
        if self._on_tick is None:
            return
        try:
            self._on_tick(phase)
        except Exception:  # noqa: BLE001 - a heartbeat failure must not stop the Host
            self.outcome.port_errors += 1

    # ---------------------------------------------------------------- roles
    def _run_role(self, role: str, *, feedback: str | None) -> None:
        self._tick(f"RUNNING_{role}")
        attempt = self._attempts.get(role, 0) + 1
        self._attempts[role] = attempt
        try:
            result = self.runner.run(role, attempt=attempt, feedback=feedback)
        except Exception as error:  # noqa: BLE001 - a runner bug must not kill the Host
            result = RoleResult(status="FAILED", summary=str(error), error_code="RUNNER_EXCEPTION")
        if result.status not in {"COMPLETED", "FAILED"}:
            result = RoleResult(status="FAILED", summary=f"runner returned {result.status!r}", error_code="RUNNER_STATUS_INVALID")
        self.outcome.roles_run.append((role, attempt, result.status))
        self._last_activity = self._clock()
        key = f"host:{self.config.task_frame_id}:{role}:{attempt}:{result.status}"
        payload = {
            "schema": NOTICE_SCHEMA,
            "task_frame_id": self.config.task_frame_id,
            "todo_id": self.config.todo_id,
            "role": role,
            "attempt": attempt,
            "status": result.status,
            "summary": result.summary,
            "result_ref": result.result_ref,
            "result_digest": result.result_digest,
            "error_code": result.error_code,
        }
        failed = result.status == "FAILED"
        text = f"{role} attempt {attempt} {result.status}" + (f": {result.summary}" if result.summary else "")
        self._queue(
            key,
            text,
            payload,
            severity="ERROR" if failed else "INFO",
            call_master=failed,
            reason=f"{role} failed ({result.error_code or 'no code'}); waiting for the Master" if failed else "",
        )
        self._flush_outbox()

    def _handle_directives(self) -> str:
        try:
            events = list(self.room.events_after(self._cursor))
        except Exception:  # noqa: BLE001 - the room being unreachable is survivable
            self.outcome.port_errors += 1
            return ""
        for event in sorted(events, key=lambda item: int(item.get("room_sequence") or 0)):
            sequence = int(event.get("room_sequence") or 0)
            if sequence <= self._cursor:
                continue
            self._cursor = sequence
            directive = parse_directive(event)
            if directive is None:
                continue
            self._last_activity = self._clock()
            if directive["directive"] == "DONE":
                return "MASTER_DONE"
            self._run_role(directive["role"], feedback=directive["feedback"])
        return ""

    # ----------------------------------------------------------------- exit
    def _terminal_reason(self) -> str:
        try:
            state, archived = self.todo.state()
            if archived:
                return "TODO_ARCHIVED"
            if str(state).upper() in TERMINAL_TODO_STATES:
                return f"TODO_{str(state).upper()}"
        except Exception:  # noqa: BLE001 - unknown is not terminal
            self.outcome.port_errors += 1
        try:
            if not self.room.is_open():
                return "ROOM_CLOSED"
        except Exception:  # noqa: BLE001
            self.outcome.port_errors += 1
        return ""

    def _announce_exit(self, reason: str, *, failed: bool) -> None:
        key = f"host:{self.config.task_frame_id}:EXIT:{reason}"
        payload = {
            "schema": NOTICE_SCHEMA,
            "task_frame_id": self.config.task_frame_id,
            "todo_id": self.config.todo_id,
            "role": "HOST",
            "status": "EXITED_FAILED" if failed else "EXITED",
            "exit_reason": reason,
        }
        self._queue(
            key,
            f"Task Frame Host exited: {reason}",
            payload,
            severity="ERROR" if failed else "INFO",
            call_master=failed,
            reason=f"Host exited without a Master decision: {reason}" if failed else "",
        )

    # --------------------------------------------------------------- outbox
    def _queue(
        self,
        key: str,
        text: str,
        payload: Mapping[str, Any],
        *,
        severity: str,
        call_master: bool,
        reason: str,
    ) -> None:
        self._outbox.append(
            {"key": key, "text": text, "payload": dict(payload), "severity": severity,
             "call_master": call_master, "reason": reason, "room_done": False,
             "call_done": False, "bus_done": False}
        )

    def _flush_outbox(self) -> None:
        """Deliver every pending notice; keep what could not be delivered."""
        for item in self._outbox:
            if not item["room_done"]:
                item["room_done"] = self._attempt(
                    lambda item=item: self.room.post_report(
                        body_text=item["text"], severity=item["severity"], idempotency_key=item["key"]
                    )
                )
            if item["call_master"] and not item["call_done"]:
                item["call_done"] = self._attempt(lambda item=item: self.room.call_master(reason=item["reason"]))
            if not item["bus_done"]:
                item["bus_done"] = self._attempt(
                    lambda item=item: self.bus.notify(
                        idempotency_key=item["key"], body_text=item["text"], payload=item["payload"]
                    )
                )
        self._outbox = [
            item for item in self._outbox
            if not (item["room_done"] and item["bus_done"] and (item["call_done"] or not item["call_master"]))
        ]

    def _attempt(self, action: Callable[[], None]) -> bool:
        try:
            action()
            return True
        except Exception:  # noqa: BLE001 - keep the notice and resend on the next cycle
            self.outcome.port_errors += 1
            return False

    @property
    def pending_notices(self) -> int:
        return len(self._outbox)
