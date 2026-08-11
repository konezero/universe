from __future__ import annotations

import sqlite3
import unittest

from universe_app.bench_repository import BenchObservationRepository


class BenchObservationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE skill_catalog (
                skill_id TEXT NOT NULL,
                skill_version TEXT NOT NULL,
                operation_class TEXT NOT NULL,
                first_observed_at TEXT NOT NULL,
                last_observed_at TEXT NOT NULL,
                PRIMARY KEY(skill_id, skill_version, operation_class)
            );
            CREATE TABLE skill_run_observation (
                observation_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                candidate_digest TEXT NOT NULL,
                task_frame_ref TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                observation_digest TEXT NOT NULL,
                skill_binding_digest TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                skill_version TEXT NOT NULL,
                operation_class TEXT NOT NULL,
                context_pack_digest TEXT NOT NULL,
                model_ref TEXT NOT NULL,
                provider_ref TEXT NOT NULL DEFAULT 'UNKNOWN',
                worker_role TEXT NOT NULL DEFAULT 'UNKNOWN',
                task_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
                node_ref TEXT NOT NULL DEFAULT 'UNKNOWN',
                outcome TEXT NOT NULL,
                validation_state TEXT NOT NULL,
                failure_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
                quota_state TEXT NOT NULL DEFAULT 'UNKNOWN',
                evidence_refs_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(project_id, candidate_id, observation_digest)
            );
            CREATE TABLE project_skill_observation_queue (
                queue_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                candidate_digest TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                publication_approval_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('QUEUED', 'INGESTED')),
                queued_at TEXT NOT NULL,
                ingested_at TEXT,
                UNIQUE(project_id, candidate_id)
            );
            """
        )
        self.repository = BenchObservationRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_observation_and_catalog_round_trip(self) -> None:
        self.repository.insert_observation(
            {
                "observation_id": "observation_1",
                "project_id": "GCS",
                "candidate_id": "candidate_1",
                "candidate_digest": "candidate_digest_1",
                "task_frame_ref": "frame_1",
                "source_ref": "session_1",
                "observation_digest": "observation_digest_1",
                "skill_binding_digest": "binding_digest_1",
                "skill_id": "source-review",
                "skill_version": "1.0.0",
                "operation_class": "READ_ONLY",
                "context_pack_digest": "context_1",
                "model_ref": "claude/opus",
                "provider_ref": "CLAUDE",
                "worker_role": "REVIEWER",
                "task_kind": "SOURCE_REVIEW",
                "node_ref": "GCS",
                "outcome": "PASS",
                "validation_state": "VALIDATED",
                "failure_kind": "NONE",
                "quota_state": "AVAILABLE",
                "evidence_refs_json": "[]",
                "metrics_json": '{"duration_ms": 10}',
                "observed_at": "2026-08-11T00:00:00Z",
                "recorded_at": "2026-08-11T00:01:00Z",
            }
        )
        self.repository.upsert_skill_catalog(
            skill_id="source-review",
            skill_version="1.0.0",
            operation_class="READ_ONLY",
            observed_at="2026-08-11T00:00:00Z",
        )
        self.repository.upsert_skill_catalog(
            skill_id="source-review",
            skill_version="1.0.0",
            operation_class="READ_ONLY",
            observed_at="2026-08-12T00:00:00Z",
        )

        self.assertEqual(
            self.repository.candidate_digests("GCS", "candidate_1"),
            ["candidate_digest_1"],
        )
        self.assertIsNotNone(
            self.repository.observation("GCS", "candidate_1", "observation_digest_1")
        )
        self.assertEqual(len(self.repository.list_observations("GCS", limit=10)), 1)
        self.assertEqual(len(self.repository.list_all_observations()), 1)
        catalog = self.connection.execute("SELECT * FROM skill_catalog").fetchone()
        self.assertEqual(catalog["first_observed_at"], "2026-08-11T00:00:00Z")
        self.assertEqual(catalog["last_observed_at"], "2026-08-12T00:00:00Z")

    def test_queue_lifecycle(self) -> None:
        self.repository.insert_queue_item(
            {
                "queue_id": "queue_1",
                "project_id": "GCS",
                "candidate_id": "candidate_1",
                "candidate_digest": "candidate_digest_1",
                "candidate_json": '{"candidate_id":"candidate_1"}',
                "publication_approval_json": '{"status":"APPROVED"}',
                "queued_at": "2026-08-11T00:00:00Z",
            }
        )

        self.assertEqual(len(self.repository.list_queue_items("GCS", limit=10)), 1)
        self.assertEqual(len(self.repository.list_queued_items(limit=10)), 1)
        self.assertEqual(self.repository.queue_item("GCS", "candidate_1")["status"], "QUEUED")

        self.repository.mark_queue_ingested("queue_1", "2026-08-11T00:01:00Z")

        self.assertEqual(self.repository.list_queued_items(limit=10), [])
        item = self.repository.queue_item("GCS", "candidate_1")
        self.assertEqual(item["status"], "INGESTED")
        self.assertEqual(item["ingested_at"], "2026-08-11T00:01:00Z")


if __name__ == "__main__":
    unittest.main()
