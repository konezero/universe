"""Pure Bench aggregation extracted from the legacy Universe server."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any


SKILL_BENCH_SCHEMA = "universe.skill-bench.v1"
SKILL_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "UNKNOWN"})
SKILL_VALIDATION_STATES = frozenset({"PASS", "FAIL", "NOT_RUN", "UNKNOWN"})
SKILL_FAILURE_KINDS = frozenset(
    {
        "NONE",
        "PROVIDER_QUOTA",
        "PROVIDER_ERROR",
        "VALIDATION",
        "TIMEOUT",
        "CANCELLED",
        "UNKNOWN",
    }
)
SKILL_QUOTA_STATES = frozenset({"AVAILABLE", "WARNING", "EXHAUSTED", "UNKNOWN"})


def aggregate_skill_bench(
    observations: Iterable[Mapping[str, Any]],
    *,
    provider_ref_from_model_ref: Callable[[Any], str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Group redacted observations without depending on storage or HTTP code."""

    bounded_limit = max(1, min(int(limit), 500))
    grouped: dict[tuple[str, str, str, str, str, str, str], dict[str, Any]] = {}
    for raw_item in observations:
        item = dict(raw_item)
        skill = item["skill"]
        execution_context = item["execution_context"]
        provider_ref = execution_context["provider_ref"]
        if provider_ref == "UNKNOWN":
            provider_ref = provider_ref_from_model_ref(item["model_ref"])
        key = (
            skill["skill_id"],
            skill["skill_version"],
            skill["operation_class"],
            item["model_ref"],
            provider_ref,
            execution_context["worker_role"],
            execution_context["task_kind"],
        )
        group = grouped.setdefault(
            key,
            {
                "schema": SKILL_BENCH_SCHEMA,
                "skill": skill,
                "model_ref": item["model_ref"],
                "provider_ref": provider_ref,
                "worker_role": execution_context["worker_role"],
                "task_kind": execution_context["task_kind"],
                "observation_count": 0,
                "outcomes": {state: 0 for state in sorted(SKILL_OUTCOMES)},
                "validation_states": {
                    state: 0 for state in sorted(SKILL_VALIDATION_STATES)
                },
                "failure_kinds": {
                    state: 0 for state in sorted(SKILL_FAILURE_KINDS)
                },
                "quota_states": {
                    state: 0 for state in sorted(SKILL_QUOTA_STATES)
                },
                "metric_totals": {},
                "first_observed_at": item["observed_at"],
                "last_observed_at": item["observed_at"],
            },
        )
        group["observation_count"] += 1
        group["outcomes"][item["outcome"]] += 1
        group["validation_states"][item["validation_state"]] += 1
        group["failure_kinds"][execution_context["failure_kind"]] += 1
        group["quota_states"][execution_context["quota_state"]] += 1
        group["first_observed_at"] = min(
            group["first_observed_at"], item["observed_at"]
        )
        group["last_observed_at"] = max(
            group["last_observed_at"], item["observed_at"]
        )
        for metric_key, metric_value in item["metrics"].items():
            group["metric_totals"][metric_key] = (
                group["metric_totals"].get(metric_key, 0) + metric_value
            )
    return list(grouped.values())[:bounded_limit]


def compare_skill_bench(
    observations: Iterable[Mapping[str, Any]],
    *,
    group_by: str = "skill",
    provider_ref_from_model_ref: Callable[[Any], str],
    limit: int = 50,
) -> dict[str, Any]:
    """Compare Skill observations by Skill and bounded execution context."""

    normalized = str(group_by or "skill").strip().lower()
    if normalized not in {
        "skill",
        "model",
        "provider",
        "project",
        "worker",
        "task",
        "node",
    }:
        raise ValueError(
            "BENCH_COMPARE_GROUP_INVALID",
            "group_by must be skill, model, provider, project, worker, task, or node",
        )
    bounded = max(1, min(int(limit), 200))
    groups: dict[str, dict[str, Any]] = {}
    for raw_item in observations:
        item = dict(raw_item)
        skill = item["skill"]
        execution_context = item["execution_context"]
        provider_ref = execution_context["provider_ref"]
        if provider_ref == "UNKNOWN":
            provider_ref = provider_ref_from_model_ref(item["model_ref"])
        if normalized == "skill":
            key = f"{skill['skill_id']}@{skill['skill_version']}"
            label = {
                "skill_id": skill["skill_id"],
                "skill_version": skill["skill_version"],
                "operation_class": skill["operation_class"],
            }
        elif normalized == "model":
            key = str(item["model_ref"] or "UNKNOWN")
            label = {"model_ref": item["model_ref"], "provider_ref": provider_ref}
        elif normalized == "provider":
            key = provider_ref
            label = {"provider_ref": provider_ref}
        elif normalized == "project":
            key = str(item.get("project_id") or "UNKNOWN")
            label = {"project_id": item.get("project_id")}
        elif normalized == "worker":
            key = "|".join(
                (
                    provider_ref,
                    str(item["model_ref"] or "UNKNOWN"),
                    execution_context["worker_role"],
                )
            )
            label = {
                "provider_ref": provider_ref,
                "model_ref": item["model_ref"],
                "worker_role": execution_context["worker_role"],
            }
        elif normalized == "task":
            key = execution_context["task_kind"]
            label = {"task_kind": execution_context["task_kind"]}
        else:
            key = execution_context["node_ref"]
            label = {"node_ref": execution_context["node_ref"]}
        group = groups.setdefault(
            key,
            {
                "group_key": key,
                "group_by": normalized,
                "label": label,
                "observation_count": 0,
                "outcomes": {state: 0 for state in sorted(SKILL_OUTCOMES)},
                "validation_states": {
                    state: 0 for state in sorted(SKILL_VALIDATION_STATES)
                },
                "failure_kinds": {
                    state: 0 for state in sorted(SKILL_FAILURE_KINDS)
                },
                "quota_states": {state: 0 for state in sorted(SKILL_QUOTA_STATES)},
                "skills": set(),
                "models": set(),
                "providers": set(),
                "projects": set(),
                "worker_roles": set(),
                "task_kinds": set(),
                "node_refs": set(),
                "metric_totals": {},
                "duration_samples": [],
            },
        )
        group["observation_count"] += 1
        group["outcomes"][item["outcome"]] += 1
        group["validation_states"][item["validation_state"]] += 1
        group["failure_kinds"][execution_context["failure_kind"]] += 1
        group["quota_states"][execution_context["quota_state"]] += 1
        group["skills"].add(f"{skill['skill_id']}@{skill['skill_version']}")
        group["models"].add(str(item["model_ref"] or "UNKNOWN"))
        group["providers"].add(provider_ref)
        group["projects"].add(str(item.get("project_id") or "UNKNOWN"))
        group["worker_roles"].add(execution_context["worker_role"])
        group["task_kinds"].add(execution_context["task_kind"])
        group["node_refs"].add(execution_context["node_ref"])
        for metric_key, metric_value in item["metrics"].items():
            if isinstance(metric_value, (int, float)) and not isinstance(
                metric_value, bool
            ):
                group["metric_totals"][metric_key] = (
                    group["metric_totals"].get(metric_key, 0) + metric_value
                )
                if metric_key == "duration_ms":
                    group["duration_samples"].append(float(metric_value))
    comparisons: list[dict[str, Any]] = []
    for group in groups.values():
        succeeded = int(group["outcomes"].get("SUCCEEDED") or 0)
        failed = int(group["outcomes"].get("FAILED") or 0)
        decided = succeeded + failed
        success_rate = round(succeeded / decided, 4) if decided else None
        samples = group.pop("duration_samples")
        avg_duration = round(sum(samples) / len(samples), 2) if samples else None
        comparisons.append(
            {
                "group_key": group["group_key"],
                "group_by": group["group_by"],
                "label": group["label"],
                "observation_count": group["observation_count"],
                "outcomes": group["outcomes"],
                "validation_states": group["validation_states"],
                "failure_kinds": group["failure_kinds"],
                "quota_states": group["quota_states"],
                "success_rate": success_rate,
                "avg_duration_ms": avg_duration,
                "metric_totals": group["metric_totals"],
                "distinct_skills": len(group["skills"]),
                "distinct_models": len(group["models"]),
                "distinct_providers": len(group["providers"]),
                "distinct_projects": len(group["projects"]),
                "distinct_worker_roles": len(group["worker_roles"]),
                "distinct_task_kinds": len(group["task_kinds"]),
                "distinct_node_refs": len(group["node_refs"]),
            }
        )
    comparisons.sort(
        key=lambda item: (
            -(item["success_rate"] if item["success_rate"] is not None else -1),
            -item["observation_count"],
            item["group_key"],
        )
    )
    return {
        "schema": "universe.skill-bench-compare.v1",
        "status": "SKILL_BENCH_COMPARE_COLLECTED",
        "group_by": normalized,
        "comparisons": comparisons[:bounded],
        "effects": {
            "authority": "NONE",
            "execution_assignment": "NONE",
            "career_promotion": "NONE",
        },
        "next_operation": "USER_REVIEW_ONLY",
    }


__all__ = [
    "SKILL_BENCH_SCHEMA",
    "SKILL_FAILURE_KINDS",
    "SKILL_OUTCOMES",
    "SKILL_QUOTA_STATES",
    "SKILL_VALIDATION_STATES",
    "aggregate_skill_bench",
    "compare_skill_bench",
]
