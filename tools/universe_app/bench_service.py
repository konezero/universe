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


__all__ = [
    "SKILL_BENCH_SCHEMA",
    "SKILL_FAILURE_KINDS",
    "SKILL_OUTCOMES",
    "SKILL_QUOTA_STATES",
    "SKILL_VALIDATION_STATES",
    "aggregate_skill_bench",
]
