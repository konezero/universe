"""Unit tests for the memory-candidate decision_contract pure builders.

docs/memory-candidate-decision-contract-20260911.md §1.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from universe_memory import (
    memory_candidate_allowed_decisions,
    memory_candidate_decision_contract,
    memory_candidate_next_action_kind,
)


def _candidate(**overrides: object) -> dict[str, object]:
    base = {
        "candidate_id": "memory_candidate_abc",
        "kind": "MEMORY",
        "state": "REVIEW_REQUIRED",
        "candidate_digest": "a" * 64,
        "revision": 1,
    }
    base.update(overrides)
    return base


class MemoryCandidateAllowedDecisionsTests(unittest.TestCase):
    def test_memory_excludes_explore(self) -> None:
        allowed = memory_candidate_allowed_decisions("MEMORY")
        self.assertIn("KEEP", allowed)
        self.assertIn("IGNORE", allowed)
        self.assertIn("START_PRODUCT_DESIGN", allowed)
        self.assertNotIn("EXPLORE", allowed)

    def test_idea_hypothesis_product_allow_all_four(self) -> None:
        for kind in ("IDEA", "HYPOTHESIS", "PRODUCT"):
            allowed = memory_candidate_allowed_decisions(kind)
            self.assertEqual(
                {"IGNORE", "KEEP", "EXPLORE", "START_PRODUCT_DESIGN"}, set(allowed)
            )

    def test_unknown_kind_defaults_to_all_four(self) -> None:
        allowed = memory_candidate_allowed_decisions("WHATEVER")
        self.assertEqual(
            {"IGNORE", "KEEP", "EXPLORE", "START_PRODUCT_DESIGN"}, set(allowed)
        )


class MemoryCandidateNextActionKindTests(unittest.TestCase):
    def test_review_required_is_none(self) -> None:
        self.assertEqual("NONE", memory_candidate_next_action_kind("MEMORY", "REVIEW_REQUIRED"))

    def test_ignore_is_none_for_every_kind(self) -> None:
        for kind in ("MEMORY", "IDEA", "HYPOTHESIS", "PRODUCT"):
            self.assertEqual("NONE", memory_candidate_next_action_kind(kind, "IGNORE"))

    def test_memory_keep_is_rag_adopt_available(self) -> None:
        self.assertEqual(
            "RAG_ADOPT_AVAILABLE", memory_candidate_next_action_kind("MEMORY", "KEEP")
        )

    def test_non_memory_keep_is_acknowledged_no_automation(self) -> None:
        for kind in ("IDEA", "HYPOTHESIS", "PRODUCT"):
            self.assertEqual(
                "ACKNOWLEDGED_NO_AUTOMATION",
                memory_candidate_next_action_kind(kind, "KEEP"),
            )

    def test_start_product_design_is_product_proposal_attempted(self) -> None:
        for kind in ("MEMORY", "IDEA", "HYPOTHESIS", "PRODUCT"):
            self.assertEqual(
                "PRODUCT_PROPOSAL_ATTEMPTED",
                memory_candidate_next_action_kind(kind, "START_PRODUCT_DESIGN"),
            )

    def test_legacy_memory_explore_row_still_formats_without_raising(self) -> None:
        # A MEMORY+EXPLORE candidate reviewed before this contract shipped
        # must not crash a pure formatter reading historical data.
        self.assertEqual(
            "PRODUCT_PROPOSAL_ATTEMPTED",
            memory_candidate_next_action_kind("MEMORY", "EXPLORE"),
        )


class MemoryCandidateDecisionContractTests(unittest.TestCase):
    def test_review_required_memory_excludes_explore_with_reason(self) -> None:
        contract = memory_candidate_decision_contract(_candidate())
        self.assertEqual(
            ["IGNORE", "KEEP", "START_PRODUCT_DESIGN"], contract["allowed_actions"]
        )
        self.assertEqual(
            {"EXPLORE": "MEMORY_EXPLORE_NO_AUTOMATION"}, contract["disabled_actions"]
        )
        self.assertIsNone(contract["current_decision"])
        self.assertEqual("NONE", contract["next_action"]["kind"])
        self.assertIsNone(contract["next_action"]["target_ref"])

    def test_review_required_idea_allows_all_four_with_no_disabled(self) -> None:
        contract = memory_candidate_decision_contract(
            _candidate(kind="IDEA", candidate_digest="b" * 64)
        )
        self.assertEqual(
            ["IGNORE", "KEEP", "EXPLORE", "START_PRODUCT_DESIGN"],
            contract["allowed_actions"],
        )
        self.assertEqual({}, contract["disabled_actions"])

    def test_decided_candidate_has_no_allowed_actions(self) -> None:
        contract = memory_candidate_decision_contract(
            _candidate(state="KEEP", revision=2)
        )
        self.assertEqual([], contract["allowed_actions"])
        self.assertEqual("KEEP", contract["current_decision"])

    def test_memory_keep_not_adopted_reports_rag_adopt_available(self) -> None:
        contract = memory_candidate_decision_contract(
            _candidate(state="KEEP", revision=2), adopted=False
        )
        self.assertEqual("RAG_ADOPT_AVAILABLE", contract["next_action"]["kind"])

    def test_reopen_defaults_to_not_evaluated_when_unspecified(self) -> None:
        contract = memory_candidate_decision_contract(_candidate(state="KEEP"))
        self.assertFalse(contract["reopen"]["allowed"])
        self.assertEqual("NOT_EVALUATED", contract["reopen"]["reason"])

    def test_reopen_reports_review_required_reason(self) -> None:
        contract = memory_candidate_decision_contract(
            _candidate(), reopen_allowed=False, reopen_reason="ALREADY_REVIEW_REQUIRED"
        )
        self.assertFalse(contract["reopen"]["allowed"])
        self.assertEqual("ALREADY_REVIEW_REQUIRED", contract["reopen"]["reason"])

    def test_reopen_carries_digest_and_revision(self) -> None:
        contract = memory_candidate_decision_contract(
            _candidate(state="IGNORE", candidate_digest="c" * 64, revision=3),
            reopen_allowed=True,
            reopen_reason=None,
        )
        self.assertTrue(contract["reopen"]["allowed"])
        self.assertEqual("c" * 64, contract["reopen"]["candidate_digest"])
        self.assertEqual(3, contract["reopen"]["candidate_revision"])

    def test_schema_and_state_are_present(self) -> None:
        contract = memory_candidate_decision_contract(_candidate())
        self.assertEqual(
            "universe.memory-candidate-decision-contract.v1", contract["schema"]
        )
        self.assertEqual("REVIEW_REQUIRED", contract["state"])


if __name__ == "__main__":
    unittest.main()
