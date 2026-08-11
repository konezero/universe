from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.bench_service import aggregate_skill_bench  # noqa: E402


def observation(
    *,
    skill_id: str = "source-review",
    model_ref: str = "codex:gpt-test",
    provider_ref: str = "UNKNOWN",
    observed_at: str = "2026-08-11T00:00:00Z",
    outcome: str = "SUCCEEDED",
    validation_state: str = "PASS",
    duration_ms: int = 10,
) -> dict[str, Any]:
    return {
        "skill": {
            "skill_id": skill_id,
            "skill_version": "1.0.0",
            "operation_class": "READ",
        },
        "model_ref": model_ref,
        "execution_context": {
            "provider_ref": provider_ref,
            "worker_role": "REVIEWER",
            "task_kind": "SOURCE_REVIEW",
            "failure_kind": "NONE",
            "quota_state": "AVAILABLE",
        },
        "observed_at": observed_at,
        "outcome": outcome,
        "validation_state": validation_state,
        "metrics": {"duration_ms": duration_ms},
    }


class BenchServiceTests(unittest.TestCase):
    def test_aggregates_by_skill_model_provider_and_role(self) -> None:
        result = aggregate_skill_bench(
            [
                observation(),
                observation(
                    provider_ref="CODEX",
                    observed_at="2026-08-11T00:01:00Z",
                    outcome="FAILED",
                    validation_state="FAIL",
                    duration_ms=20,
                ),
                observation(
                    skill_id="browser-check",
                    model_ref="claude:sonnet-test",
                    provider_ref="CLAUDE",
                ),
            ],
            provider_ref_from_model_ref=lambda value: str(value).split(":", 1)[0].upper(),
        )

        self.assertEqual(2, len(result))
        first = result[0]
        self.assertEqual("CODEX", first["provider_ref"])
        self.assertEqual(2, first["observation_count"])
        self.assertEqual(1, first["outcomes"]["SUCCEEDED"])
        self.assertEqual(1, first["outcomes"]["FAILED"])
        self.assertEqual(30, first["metric_totals"]["duration_ms"])
        self.assertEqual("2026-08-11T00:00:00Z", first["first_observed_at"])
        self.assertEqual("2026-08-11T00:01:00Z", first["last_observed_at"])

    def test_limit_is_bounded_without_changing_group_contents(self) -> None:
        result = aggregate_skill_bench(
            [
                observation(skill_id="one"),
                observation(skill_id="two"),
            ],
            provider_ref_from_model_ref=lambda _value: "CODEX",
            limit=1,
        )

        self.assertEqual(1, len(result))
        self.assertEqual("one", result[0]["skill"]["skill_id"])


if __name__ == "__main__":
    unittest.main()
