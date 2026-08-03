from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from worker_failure_evidence import (  # noqa: E402
    LIVE_INITIALIZATION_FAILURE,
    RETROSPECTIVE_OBSERVATION,
    WorkerFailureEvidenceStore,
)


class WorkerFailureEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "universe.sqlite3"
        self.store = WorkerFailureEvidenceStore(self.database)
        self.base = {
            "repository_ref": "file:///C:/workspace/universe",
            "session_id": "session-1",
            "frame_id": "frame-1",
            "turn_id": "turn-1",
            "worker_id": "worker-1",
            "worker_run_ref": "worker-run-1",
            "failure_code": "TASK_FRAME_INITIALIZATION_FAILED",
            "failure_detail": "WORKER_ADAPTER: approval request failed",
            "source_locator": "universe://runtime-worker-failure/frame-1/turn-1/run",
            "source_content": b"approval request failed\n",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_live_failure_is_durable_and_idempotent(self) -> None:
        first = self.store.record_live_failure(
            **self.base,
            failure_observed_at="2026-08-03T08:00:00+00:00",
        )
        second = self.store.record_live_failure(
            **self.base,
            failure_observed_at="2026-08-03T08:00:00+00:00",
        )

        self.assertEqual(first["host_evidence_ref"], second["host_evidence_ref"])
        loaded = self.store.get(first["host_evidence_ref"])
        assert loaded is not None
        self.assertEqual(LIVE_INITIALIZATION_FAILURE, loaded["evidence_kind"])
        self.assertEqual("RECORDED_AT_FAILURE_TIME", loaded["durability_claim"])
        self.assertEqual("worker-run-1", loaded["worker_run_ref"])
        self.assertEqual(
            hashlib.sha256(self.base["source_content"]).hexdigest(),
            loaded["source_digest"],
        )
        self.assertNotIn("approval request failed\n", json.dumps(loaded))

    def test_retrospective_observation_does_not_claim_prior_durability(self) -> None:
        observed = self.store.record_retrospective_observation(
            **self.base,
            failure_observed_at=None,
        )

        self.assertEqual(RETROSPECTIVE_OBSERVATION, observed["evidence_kind"])
        self.assertEqual(
            "OBSERVED_AND_RECORDED_RETROSPECTIVELY",
            observed["durability_claim"],
        )
        self.assertIsNone(observed["failure_observed_at"])
        self.assertIn("recorded_at", observed)

    def test_retrospective_observation_preserves_known_timestamp(self) -> None:
        observed = self.store.record_retrospective_observation(
            **self.base,
            failure_observed_at="2026-08-03T17:00:00+09:00",
        )

        self.assertEqual(
            "2026-08-03T08:00:00+00:00", observed["failure_observed_at"]
        )


if __name__ == "__main__":
    unittest.main()
