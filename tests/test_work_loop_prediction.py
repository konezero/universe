from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.work_loop_prediction import (  # noqa: E402
    LOW_CONFIDENCE_THRESHOLD,
    build_result_fanout,
    build_work_loop_predictions,
    retrieve_recurrence_prevention,
    tokenize,
)


class WorkLoopPredictionTests(unittest.TestCase):
    def test_empty_evidence_is_unsupported(self) -> None:
        result = build_work_loop_predictions({"project_id": "GCS"})
        self.assertEqual("WORK_LOOP_PREDICTION_READY", result["status"])
        self.assertEqual([], result["suggestions"])
        self.assertEqual("UNSUPPORTED", result["rejected"][0]["reason"])
        self.assertFalse(result["adoption_policy"]["auto_adopt"])

    def test_single_source_is_unsupported(self) -> None:
        result = build_work_loop_predictions(
            {
                "project_id": "GCS",
                "todos": [
                    {
                        "todo_id": "todo_done",
                        "title": "Ship the operator spine",
                        "state": "DONE",
                    }
                ],
            }
        )
        self.assertEqual([], result["suggestions"])
        self.assertEqual("UNSUPPORTED", result["rejected"][0]["reason"])

    def test_low_confidence_risk_is_rejected(self) -> None:
        result = build_work_loop_predictions(
            {
                "project_id": "GCS",
                "todos": [
                    {
                        "todo_id": "todo_blocked",
                        "title": "xyz",
                        "state": "BLOCKED",
                    }
                ],
            }
        )
        self.assertEqual([], result["suggestions"])
        self.assertEqual("LOW_CONFIDENCE", result["rejected"][0]["reason"])
        self.assertLess(
            result["rejected"][0]["confidence"], LOW_CONFIDENCE_THRESHOLD
        )

    def test_supported_prediction_stays_proposal_only(self) -> None:
        result = build_work_loop_predictions(
            {
                "project_id": "GCS",
                "seed": {
                    "seed_id": "seed-gcs",
                    "project": {"goal": "Ship the operator spine"},
                    "nodes": [{"node_id": "n1", "title": "Confirm the product flow"}],
                },
                "todos": [
                    {
                        "todo_id": "todo_done",
                        "title": "Ship the operator spine",
                        "state": "DONE",
                    }
                ],
                "memories": [
                    {
                        "memory_id": "memory_1",
                        "title": "Operator spine notes",
                        "body": "Ship the operator spine after review.",
                    }
                ],
                "experience_cases": [
                    {
                        "case_id": "case_ok",
                        "title": "Ship the operator spine",
                        "observations": [
                            {
                                "outcome": "SUCCEEDED",
                                "validation_state": "PASS",
                                "skill": {"skill_id": "source-review"},
                            }
                        ],
                    }
                ],
                "bench_observations": [
                    {
                        "observation_id": "obs_1",
                        "outcome": "SUCCEEDED",
                        "skill": {"skill_id": "source-review"},
                        "task_kind": "operator",
                    }
                ],
            }
        )
        kinds = {item["kind"] for item in result["suggestions"]}
        self.assertIn("GOAL", kinds)
        goal = next(item for item in result["suggestions"] if item["kind"] == "GOAL")
        self.assertGreaterEqual(goal["confidence"], LOW_CONFIDENCE_THRESHOLD)
        self.assertEqual("PROPOSAL_ONLY", goal["adoption_state"])
        provenance_kinds = {entry["kind"] for entry in goal["provenance"]}
        self.assertGreaterEqual(len(provenance_kinds), 2)
        self.assertFalse(result["adoption_policy"]["creates_goal"])

    def test_prediction_digest_and_id_are_stable_for_identical_evidence(self) -> None:
        evidence = {
            "project_id": "GCS",
            "seed": {
                "seed_id": "seed-gcs",
                "project": {"goal": "Ship the operator spine"},
            },
            "todos": [
                {
                    "todo_id": "todo_done",
                    "title": "Ship the operator spine",
                    "state": "DONE",
                }
            ],
            "memories": [
                {
                    "memory_id": "memory_1",
                    "title": "Operator spine notes",
                    "body": "Ship the operator spine after review.",
                }
            ],
        }
        first = build_work_loop_predictions(evidence)
        second = build_work_loop_predictions(evidence)

        self.assertEqual(first["proposal_digest"], second["proposal_digest"])
        self.assertEqual(first["proposal_id"], second["proposal_id"])
        self.assertEqual(first["suggestions"], second["suggestions"])
        self.assertEqual(first["rejected"], second["rejected"])

    def test_failed_experience_prevents_repeating_the_plan(self) -> None:
        tokens = tokenize("source review operator spine")
        hits = retrieve_recurrence_prevention(
            [
                {
                    "case_id": "case_fail",
                    "title": "source review operator spine",
                    "observations": [
                        {
                            "outcome": "FAILED",
                            "validation_state": "FAIL",
                            "skill": {"skill_id": "source-review"},
                            "task_kind": "operator",
                        }
                    ],
                }
            ],
            tokens,
        )
        self.assertEqual("case_fail", hits[0]["case_id"])
        self.assertEqual("RECURRENCE_PREVENTION", hits[0]["relation"])

        result = build_work_loop_predictions(
            {
                "project_id": "GCS",
                "seed": {
                    "seed_id": "seed-gcs",
                    "project": {"goal": "source review operator spine"},
                },
                "experience_cases": [
                    {
                        "case_id": "case_fail",
                        "title": "source review operator spine",
                        "observations": [
                            {
                                "outcome": "FAILED",
                                "validation_state": "FAIL",
                                "skill": {"skill_id": "source-review"},
                                "task_kind": "operator",
                            }
                        ],
                    }
                ],
            }
        )
        self.assertTrue(result["suggestions"])
        self.assertTrue(all(item["kind"] == "RISK" for item in result["suggestions"]))
        self.assertTrue(result["suggestions"][0]["recurrence_prevention"])
        self.assertEqual("PROPOSAL_ONLY", result["suggestions"][0]["adoption_state"])

    def test_result_fanout_does_not_create_entities(self) -> None:
        fanout = build_result_fanout(
            project_id="GCS",
            source_kind="TODO",
            source_id="todo_1",
            outcome="COMPLETED",
            state="DONE",
        )
        self.assertEqual("universe.work-loop-result-fanout.v1", fanout["schema"])
        self.assertFalse(fanout["auto_adopted"])
        self.assertFalse(fanout["effects"]["creates_goal"])
        self.assertFalse(fanout["effects"]["creates_experience_case"])


if __name__ == "__main__":
    unittest.main()
