"""SQLite persistence primitives for redacted Skill observations and queues."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any


class BenchObservationRepository:
    """Keep Bench SQL behind a small connection-scoped repository boundary."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def candidate_digests(self, project_id: str, candidate_id: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT candidate_digest
            FROM skill_run_observation
            WHERE project_id = ? AND candidate_id = ?
            """,
            (project_id, candidate_id),
        ).fetchall()
        return [str(row["candidate_digest"]) for row in rows]

    def observation(
        self, project_id: str, candidate_id: str, observation_digest: str
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT *
            FROM skill_run_observation
            WHERE project_id = ?
              AND candidate_id = ?
              AND observation_digest = ?
            """,
            (project_id, candidate_id, observation_digest),
        ).fetchone()

    def insert_observation(self, values: Mapping[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_run_observation(
                observation_id, project_id, candidate_id, candidate_digest,
                task_frame_ref, source_ref, observation_digest,
                skill_binding_digest, skill_id, skill_version,
                operation_class, context_pack_digest, model_ref, provider_ref,
                worker_role, task_kind, node_ref, outcome, validation_state,
                failure_kind, quota_state, evidence_refs_json, metrics_json,
                observed_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["observation_id"],
                values["project_id"],
                values["candidate_id"],
                values["candidate_digest"],
                values["task_frame_ref"],
                values["source_ref"],
                values["observation_digest"],
                values["skill_binding_digest"],
                values["skill_id"],
                values["skill_version"],
                values["operation_class"],
                values["context_pack_digest"],
                values["model_ref"],
                values["provider_ref"],
                values["worker_role"],
                values["task_kind"],
                values["node_ref"],
                values["outcome"],
                values["validation_state"],
                values["failure_kind"],
                values["quota_state"],
                values["evidence_refs_json"],
                values["metrics_json"],
                values["observed_at"],
                values["recorded_at"],
            ),
        )

    def upsert_skill_catalog(
        self,
        *,
        skill_id: str,
        skill_version: str,
        operation_class: str,
        observed_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_catalog(
                skill_id, skill_version, operation_class,
                first_observed_at, last_observed_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(skill_id, skill_version, operation_class)
            DO UPDATE SET
                first_observed_at = MIN(
                    skill_catalog.first_observed_at,
                    excluded.first_observed_at
                ),
                last_observed_at = MAX(
                    skill_catalog.last_observed_at,
                    excluded.last_observed_at
                )
            """,
            (skill_id, skill_version, operation_class, observed_at, observed_at),
        )

    def queue_item(self, project_id: str, candidate_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT queue_id, candidate_id, candidate_digest,
                   publication_approval_json, status, queued_at, ingested_at
            FROM project_skill_observation_queue
            WHERE project_id = ? AND candidate_id = ?
            """,
            (project_id, candidate_id),
        ).fetchone()

    def insert_queue_item(self, values: Mapping[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO project_skill_observation_queue(
                queue_id, project_id, candidate_id, candidate_digest,
                candidate_json, publication_approval_json,
                status, queued_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, NULL)
            """,
            (
                values["queue_id"],
                values["project_id"],
                values["candidate_id"],
                values["candidate_digest"],
                values["candidate_json"],
                values["publication_approval_json"],
                values["queued_at"],
            ),
        )

    def list_queue_items(self, project_id: str, *, limit: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT queue_id, candidate_id, candidate_digest,
                   publication_approval_json, status, queued_at, ingested_at
            FROM project_skill_observation_queue
            WHERE project_id = ?
            ORDER BY queued_at, queue_id
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()

    def list_queued_items(self, *, limit: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT queue_id, project_id, candidate_json
            FROM project_skill_observation_queue
            WHERE status = 'QUEUED'
            ORDER BY queued_at, queue_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def mark_queue_ingested(self, queue_id: str, ingested_at: str) -> None:
        self.connection.execute(
            """
            UPDATE project_skill_observation_queue
            SET status = 'INGESTED', ingested_at = ?
            WHERE queue_id = ? AND status = 'QUEUED'
            """,
            (ingested_at, queue_id),
        )

    def list_observations(self, project_id: str, *, limit: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT *
            FROM skill_run_observation
            WHERE project_id = ?
            ORDER BY observed_at DESC, observation_id DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()

    def list_all_observations(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT *
            FROM skill_run_observation
            ORDER BY skill_id, skill_version, operation_class, model_ref,
                     observed_at, observation_id
            """
        ).fetchall()

    def list_all_observations_for_comparison(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT *
            FROM skill_run_observation
            ORDER BY observed_at DESC, observation_id DESC
            """
        ).fetchall()


__all__ = ["BenchObservationRepository"]
