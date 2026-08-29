from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.memory_scheduler import (  # noqa: E402
    MemoryBatchScheduler,
    next_cadence_due,
    quota_window,
    retry_delay_seconds,
)


class FakeClock:
    def __init__(self, value: str) -> None:
        self.value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    def now(self) -> datetime:
        return self.value.astimezone(timezone.utc)


class FakeStore:
    def __init__(self, claim=None) -> None:
        self.claim = claim
        self.finished = []
        self.recovered = ["schedule_recovered"]

    def recover_expired_memory_batch_schedules(self, now):
        self.recovery_now = now
        return list(self.recovered)

    def claim_due_memory_batch_schedule(self, **kwargs):
        self.claim_args = kwargs
        claim, self.claim = self.claim, None
        return claim

    def finish_memory_batch_schedule(self, claim, **kwargs):
        self.finished.append((dict(claim), dict(kwargs)))
        return {"schedule_id": claim["schedule_id"], "state": kwargs["outcome"]}


class MemorySchedulerTests(unittest.TestCase):
    def test_time_functions_are_deterministic(self) -> None:
        self.assertEqual(30, retry_delay_seconds(1))
        self.assertEqual(120, retry_delay_seconds(3))
        self.assertEqual(900, retry_delay_seconds(99))
        self.assertEqual(
            "2026-08-16T13:00:00Z",
            next_cadence_due(
                "2026-08-16T10:00:00Z", 60, "2026-08-16T12:14:00Z"
            ),
        )
        self.assertEqual(
            ("2026-08-16T12:00:00Z", "2026-08-16T18:00:00Z"),
            quota_window("2026-08-16T12:14:00Z", 6),
        )

    def test_tick_claims_once_and_records_success(self) -> None:
        claim = {
            "schedule_id": "schedule_1",
            "project_id": "GCS",
            "stage": "CONSOLIDATE",
            "due_slot_key": "slot_1",
            "lease_generation": 2,
        }
        store = FakeStore(claim)
        scheduler = MemoryBatchScheduler(
            store,
            lambda project_id, stage: {
                "run_id": f"run:{project_id}:{stage}"
            },
            clock=FakeClock("2026-08-16T12:14:00Z"),
            lease_owner="host-1",
        )
        result = scheduler.tick()

        self.assertEqual("MEMORY_BATCH_SCHEDULER_TICK_COMPLETED", result["status"])
        self.assertEqual(["schedule_recovered"], result["recovered_schedule_ids"])
        self.assertEqual("SUCCEEDED", result["executions"][0]["outcome"])
        self.assertEqual("host-1", store.claim_args["lease_owner"])
        self.assertEqual("SUCCEEDED", store.finished[0][1]["outcome"])

    def test_failure_is_persisted_and_shutdown_refuses_claims(self) -> None:
        claim = {
            "schedule_id": "schedule_1",
            "project_id": "GCS",
            "stage": "SYNTHESIZE",
            "due_slot_key": "slot_1",
            "lease_generation": 1,
        }
        store = FakeStore(claim)

        class ExpectedFailure(RuntimeError):
            code = "MEMORY_BATCH_QUOTA_EXCEEDED"

        scheduler = MemoryBatchScheduler(
            store,
            lambda _project_id, _stage: (_ for _ in ()).throw(ExpectedFailure()),
            clock=FakeClock("2026-08-16T12:14:00Z"),
        )
        failed = scheduler.tick()
        self.assertEqual("FAILED", failed["executions"][0]["outcome"])
        self.assertEqual(
            "MEMORY_BATCH_QUOTA_EXCEEDED",
            store.finished[0][1]["error_code"],
        )

        store.claim = claim
        scheduler.stop_accepting()
        stopped = scheduler.tick()
        self.assertEqual("MEMORY_BATCH_SCHEDULER_STOPPED", stopped["status"])
        self.assertIs(claim, store.claim)


if __name__ == "__main__":
    unittest.main()
