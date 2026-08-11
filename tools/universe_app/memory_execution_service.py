"""Memory batch execution orchestration outside the monolithic server."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Protocol

from universe_memory import (
    MEMORY_BATCH_RUN_SCHEMA,
    MEMORY_BATCH_STAGES,
    MemoryError,
    consolidate_memory_candidates,
    extract_memory_candidates_from_activity_batch,
    independent_check_memory_candidates,
    normalize_memory_candidate,
    synthesize_memory_candidates,
)

from .connection import UniverseError


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


class MemoryBatchExecutionStore(Protocol):
    def get_project(self, project_id: str) -> dict[str, Any]: ...

    def count_memory_batch_runs(self, project_id: str, stage: str) -> int: ...

    def list_provider_session_sources(self) -> list[dict[str, Any]]: ...

    def prepare_provider_activity_batch(self, source_id: str) -> dict[str, Any]: ...

    def list_memory_candidates(
        self, project_id: str, *, stage: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def _insert_memory_candidates(
        self, project_id: str, candidates: list[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]: ...

    def memory_batch_run_id(
        self,
        project_id: str,
        stage: str,
        config: Mapping[str, Any],
        input_material: list[str],
    ) -> tuple[str, str, str]: ...

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
    ) -> None: ...


class MemoryBatchExecutionService:
    """Run deterministic Memory stages and persist one durable result envelope."""

    def __init__(self, store: MemoryBatchExecutionStore) -> None:
        self.store = store

    def execute(
        self,
        project_id: str,
        config: Mapping[str, Any],
        request: Mapping[str, Any] | None = None,
        *,
        provider_candidates: list[Mapping[str, Any]] | None = None,
        input_material_override: list[str] | None = None,
        run_id_override: str | None = None,
        provider_execution: Mapping[str, Any] | None = None,
        skill_observation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        request = request if isinstance(request, Mapping) else {}
        stage = str(config.get("stage") or "").strip().upper()
        if stage not in MEMORY_BATCH_STAGES:
            raise UniverseError("MEMORY_BATCH_STAGE_INVALID", "stage is invalid")

        dry_run = bool(config.get("dry_run", False))
        budget = config.get("quota_or_budget")
        if isinstance(budget, Mapping) and budget.get("max_runs") is not None:
            count = self.store.count_memory_batch_runs(project["project_id"], stage)
            if int(count) >= int(budget["max_runs"]):
                raise UniverseError(
                    "MEMORY_BATCH_QUOTA_EXCEEDED",
                    "configured memory batch max_runs quota is exhausted",
                    HTTPStatus.CONFLICT,
                )

        candidates: list[dict[str, Any]] = []
        check: dict[str, Any] | None = None
        input_material: list[str] = []
        if stage == "FAST_EXTRACT":
            if provider_candidates is not None:
                try:
                    candidates = [
                        normalize_memory_candidate(
                            item,
                            project_id=project["project_id"],
                            stage="FAST_EXTRACT",
                        )
                        for item in provider_candidates
                    ]
                except MemoryError as error:
                    raise UniverseError(error.code, error.message) from error
                input_material = list(input_material_override or [])
                if not input_material:
                    input_material = [item["candidate_digest"] for item in candidates]
            else:
                batches = request.get("activity_batches")
                if batches is None:
                    batches = []
                    source_ids = request.get("source_ids")
                    if source_ids is None:
                        source_ids = [
                            item["source_id"]
                            for item in self.store.list_provider_session_sources()
                        ]
                    if not isinstance(source_ids, list):
                        raise UniverseError(
                            "MEMORY_BATCH_SOURCE_IDS_INVALID",
                            "source_ids must be an array",
                        )
                    for source_id in source_ids:
                        batches.append(
                            self.store.prepare_provider_activity_batch(str(source_id))
                        )
                if not isinstance(batches, list):
                    raise UniverseError(
                        "MEMORY_BATCH_ACTIVITY_INVALID",
                        "activity_batches must be an array",
                    )
                for batch in batches:
                    extracted = extract_memory_candidates_from_activity_batch(
                        batch, project_id=project["project_id"]
                    )
                    candidates.extend(extracted)
                    input_material.extend(
                        item["candidate_digest"] for item in extracted
                    )
        elif stage == "CONSOLIDATE":
            existing = self.store.list_memory_candidates(
                project["project_id"], stage="FAST_EXTRACT", limit=500
            )
            input_material = [item["candidate_digest"] for item in existing]
            candidates = consolidate_memory_candidates(existing)
        elif stage == "SYNTHESIZE":
            existing = self.store.list_memory_candidates(
                project["project_id"], stage="CONSOLIDATE", limit=500
            )
            if not existing:
                existing = self.store.list_memory_candidates(
                    project["project_id"], stage="FAST_EXTRACT", limit=500
                )
            input_material = [item["candidate_digest"] for item in existing]
            raw_kinds = request.get("kinds", ["IDEA", "HYPOTHESIS", "PRODUCT"])
            if not isinstance(raw_kinds, list):
                raise UniverseError(
                    "MEMORY_BATCH_KINDS_INVALID", "kinds must be an array"
                )
            try:
                candidates = synthesize_memory_candidates(existing, kinds=raw_kinds)
            except MemoryError as error:
                raise UniverseError(error.code, error.message) from error
        else:
            existing = self.store.list_memory_candidates(
                project["project_id"], limit=500
            )
            input_material = [item["candidate_digest"] for item in existing]
            try:
                check = independent_check_memory_candidates(existing)
            except MemoryError as error:
                raise UniverseError(error.code, error.message) from error

        input_digest = _json_sha256(sorted(input_material))
        output_digest = _json_sha256(
            check
            if check is not None
            else [item["candidate_digest"] for item in candidates]
        )
        stored_candidates: list[dict[str, Any]] = []
        created_count = 0
        if candidates and not dry_run:
            stored_candidates, created_count = self.store._insert_memory_candidates(
                project["project_id"], candidates
            )
        elif candidates:
            stored_candidates = candidates

        run_id = run_id_override
        if run_id is None:
            run_id, _config_digest, _input_digest = self.store.memory_batch_run_id(
                project["project_id"], stage, config, input_material
            )
        now = _utc_now()
        result: dict[str, Any] = {
            "schema": MEMORY_BATCH_RUN_SCHEMA,
            "status": "DRY_RUN_COMPLETED" if dry_run else "COMPLETED",
            "run_id": run_id,
            "project_id": project["project_id"],
            "stage": stage,
            "config_digest": _json_sha256(config),
            "input_digest": input_digest,
            "output_digest": output_digest,
            "candidate_count": len(stored_candidates),
            "created_count": created_count,
            "candidate_ids": [item["candidate_id"] for item in stored_candidates],
            "candidates": stored_candidates,
            "independent_check": check,
            "execution": dict(provider_execution)
            if provider_execution is not None
            else {
                "mode": "DETERMINISTIC",
                "provider_invocation": "NOT_RUN",
                "provider_attribution": "NONE",
            },
            "effects": {
                "candidate_write": "NONE"
                if dry_run
                else ("CREATED" if created_count else "IDEMPOTENT"),
                "memory_write": "NONE",
                "current_anchor": "NONE",
                "project_facts": "NONE",
                "seed": "NONE",
                "authority": "NONE",
                "execution_assignment": "NONE",
                "auto_adoption": False,
                "skill_observation": "RECORDED"
                if skill_observation is not None
                else "NONE",
            },
        }
        if skill_observation is not None:
            result["skill_observation"] = {
                key: skill_observation[key]
                for key in ("candidate_id", "candidate_digest", "observation_count")
                if key in skill_observation
            }

        self.store.persist_memory_batch_result(
            run_id=run_id,
            project_id=project["project_id"],
            stage=stage,
            result=result,
            input_digest=input_digest,
            output_digest=output_digest,
            now=now,
        )
        return {**result, "started_at": now, "completed_at": now}
