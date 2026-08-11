from __future__ import annotations

import unittest
from typing import Any, Mapping
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.connection import UniverseError  # noqa: E402
from universe_app.memory_execution_service import (  # noqa: E402
    MemoryBatchExecutionService,
)


class FakeMemoryStore:
    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.run_count = 0
        self.persisted: list[dict[str, Any]] = []

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {"project_id": project_id}

    def count_memory_batch_runs(self, project_id: str, stage: str) -> int:
        return self.run_count

    def list_provider_session_sources(self) -> list[dict[str, Any]]:
        return []

    def prepare_provider_activity_batch(self, source_id: str) -> dict[str, Any]:
        raise AssertionError("the test supplies activity_batches directly")

    def list_memory_candidates(
        self, project_id: str, *, stage: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.candidates
            if stage is None or item["stage"] == stage
        ][:limit]

    def _insert_memory_candidates(
        self, project_id: str, candidates: list[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        stored: list[dict[str, Any]] = []
        created = 0
        for item in candidates:
            candidate = dict(item)
            existing = next(
                (
                    value
                    for value in self.candidates
                    if value["candidate_digest"] == candidate["candidate_digest"]
                ),
                None,
            )
            if existing is None:
                candidate.update({"created_at": "now", "updated_at": "now"})
                self.candidates.append(candidate)
                stored.append(candidate)
                created += 1
            else:
                stored.append(existing)
        return stored, created

    def memory_batch_run_id(
        self,
        project_id: str,
        stage: str,
        config: Mapping[str, Any],
        input_material: list[str],
    ) -> tuple[str, str, str]:
        return "run-1", "config-digest", "input-digest"

    def persist_memory_batch_result(
        self,
        *,
        run_id: str,
        project_id: str,
        stage: str,
        result: Mapping[str, Any],
        input_digest: str,
        output_digest: str,
        now: str,
    ) -> None:
        self.persisted.append(
            {
                "run_id": run_id,
                "project_id": project_id,
                "stage": stage,
                "result": dict(result),
                "input_digest": input_digest,
                "output_digest": output_digest,
            }
        )


class MemoryBatchExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeMemoryStore()
        self.service = MemoryBatchExecutionService(self.store)
        self.activity_batch = {
            "source": {
                "provider": "CODEX",
                "provider_session_id": "session-1",
                "source_id": "source-1",
            },
            "activity_refs": [
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 1,
                    "activity_digest": "d" * 64,
                },
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 2,
                    "activity_digest": "e" * 64,
                },
            ],
        }

    def test_fast_extract_dry_run_persists_result_without_candidates(self) -> None:
        result = self.service.execute(
            "TEST",
            {
                "stage": "FAST_EXTRACT",
                "dry_run": True,
                "quota_or_budget": {"max_runs": 2},
            },
            {"activity_batches": [self.activity_batch]},
        )
        self.assertEqual("DRY_RUN_COMPLETED", result["status"])
        self.assertGreaterEqual(result["candidate_count"], 2)
        self.assertEqual([], self.store.candidates)
        self.assertEqual(1, len(self.store.persisted))
        self.assertEqual("FAST_EXTRACT", self.store.persisted[0]["stage"])

    def test_quota_is_checked_before_candidate_generation(self) -> None:
        self.store.run_count = 1
        with self.assertRaises(UniverseError) as error:
            self.service.execute(
                "TEST",
                {
                    "stage": "FAST_EXTRACT",
                    "dry_run": False,
                    "quota_or_budget": {"max_runs": 1},
                },
                {"activity_batches": [self.activity_batch]},
            )
        self.assertEqual("MEMORY_BATCH_QUOTA_EXCEEDED", error.exception.code)
        self.assertEqual([], self.store.persisted)
        self.assertEqual([], self.store.candidates)


if __name__ == "__main__":
    unittest.main()
