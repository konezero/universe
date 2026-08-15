"""Durable wall-clock scheduler orchestration for Memory batch stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4


UTC = timezone.utc
SCHEDULER_SCHEMA = "universe.memory-batch-scheduler.v1"


def parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def retry_delay_seconds(attempt_count: int, *, base: int = 30, cap: int = 900) -> int:
    attempt = max(1, int(attempt_count))
    return min(int(cap), int(base) * (2 ** (attempt - 1)))


def next_cadence_due(planned_due_at: str, interval_minutes: int, now: str) -> str:
    """Advance from a persisted slot to the first future cadence slot."""

    interval = max(1, int(interval_minutes))
    planned = parse_utc(planned_due_at)
    current = parse_utc(now)
    step = timedelta(minutes=interval)
    if planned > current:
        return format_utc(planned)
    elapsed = current - planned
    skipped = int(elapsed.total_seconds() // step.total_seconds()) + 1
    return format_utc(planned + step * skipped)


def quota_window(now: str, window_hours: int) -> tuple[str, str]:
    """Return a deterministic UTC epoch-aligned quota window."""

    hours = max(1, int(window_hours))
    current = parse_utc(now)
    seconds = hours * 3600
    epoch = int(current.timestamp())
    start_epoch = epoch - (epoch % seconds)
    start = datetime.fromtimestamp(start_epoch, tz=UTC)
    return format_utc(start), format_utc(start + timedelta(seconds=seconds))


class SchedulerClock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class MemoryScheduleStore(Protocol):
    def recover_expired_memory_batch_schedules(self, now: str) -> list[str]: ...

    def claim_due_memory_batch_schedule(
        self,
        *,
        now: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None: ...

    def finish_memory_batch_schedule(
        self,
        claim: Mapping[str, Any],
        *,
        now: str,
        outcome: str,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]: ...


Execution = Callable[[str, str], Mapping[str, Any]]


class MemoryBatchScheduler:
    """Claim persisted due slots and execute them without chat or adoption."""

    def __init__(
        self,
        store: MemoryScheduleStore,
        execute: Execution,
        *,
        clock: SchedulerClock | None = None,
        lease_owner: str | None = None,
        lease_seconds: int = 600,
        max_claims_per_tick: int = 4,
    ) -> None:
        self.store = store
        self.execute = execute
        self.clock = clock or SystemClock()
        self.lease_owner = lease_owner or f"universe-memory-scheduler:{uuid4().hex}"
        self.lease_seconds = max(60, min(int(lease_seconds), 3600))
        self.max_claims_per_tick = max(1, min(int(max_claims_per_tick), 32))
        self.accepting_claims = True

    def stop_accepting(self) -> None:
        self.accepting_claims = False

    def tick(self) -> dict[str, Any]:
        now = format_utc(self.clock.now())
        recovered = self.store.recover_expired_memory_batch_schedules(now)
        executions: list[dict[str, Any]] = []
        if not self.accepting_claims:
            return {
                "schema": SCHEDULER_SCHEMA,
                "status": "MEMORY_BATCH_SCHEDULER_STOPPED",
                "observed_at": now,
                "recovered_schedule_ids": recovered,
                "executions": executions,
            }
        for _ in range(self.max_claims_per_tick):
            claim = self.store.claim_due_memory_batch_schedule(
                now=now,
                lease_owner=self.lease_owner,
                lease_seconds=self.lease_seconds,
            )
            if claim is None:
                break
            try:
                result = dict(self.execute(claim["project_id"], claim["stage"]))
                completed_at = format_utc(self.clock.now())
                terminal = self.store.finish_memory_batch_schedule(
                    claim,
                    now=completed_at,
                    outcome="SUCCEEDED",
                    run_id=str(result.get("run_id") or "") or None,
                )
                executions.append(
                    {
                        "schedule_id": claim["schedule_id"],
                        "due_slot_key": claim["due_slot_key"],
                        "outcome": "SUCCEEDED",
                        "run_id": result.get("run_id"),
                        "schedule": terminal,
                    }
                )
            except Exception as error:  # noqa: BLE001 - durable failure boundary
                completed_at = format_utc(self.clock.now())
                error_code = str(getattr(error, "code", "") or type(error).__name__)
                terminal = self.store.finish_memory_batch_schedule(
                    claim,
                    now=completed_at,
                    outcome="FAILED",
                    error_code=error_code,
                )
                executions.append(
                    {
                        "schedule_id": claim["schedule_id"],
                        "due_slot_key": claim["due_slot_key"],
                        "outcome": "FAILED",
                        "error_code": error_code,
                        "schedule": terminal,
                    }
                )
        return {
            "schema": SCHEDULER_SCHEMA,
            "status": "MEMORY_BATCH_SCHEDULER_TICK_COMPLETED",
            "observed_at": now,
            "recovered_schedule_ids": recovered,
            "executions": executions,
        }
