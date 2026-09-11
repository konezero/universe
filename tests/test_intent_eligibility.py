"""Unit tests for the shared product-intent eligibility boundary.

docs/memory-candidate-decision-contract-20260911.md §3.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from universe_app.intent_eligibility import classify_intent_eligibility


class ClassifyIntentEligibilityTests(unittest.TestCase):
    def test_empty_text_is_generic(self) -> None:
        self.assertEqual("GENERIC", classify_intent_eligibility(text=""))
        self.assertEqual("GENERIC", classify_intent_eligibility(text="   "))

    def test_single_token_is_generic(self) -> None:
        self.assertEqual("GENERIC", classify_intent_eligibility(text="fix"))

    def test_test_only_marker_wins_over_token_count(self) -> None:
        self.assertEqual(
            "TEST_ONLY",
            classify_intent_eligibility(
                text="Please respond with the exact string OK"
            ),
        )

    def test_generic_marker_wins_over_token_count(self) -> None:
        self.assertEqual(
            "GENERIC",
            classify_intent_eligibility(text="this is a generic summary of nothing"),
        )

    def test_ordinary_multi_token_text_is_product_intent(self) -> None:
        self.assertEqual(
            "PRODUCT_INTENT",
            classify_intent_eligibility(text="Add dark mode to the settings screen"),
        )

    def test_origin_ref_cli_probe_prefix_forces_test_only_regardless_of_text(
        self,
    ) -> None:
        # This is the concrete audit example: a CLI verification phrase reads
        # as ordinary multi-token text and would otherwise classify as
        # PRODUCT_INTENT. An origin tag settles it without another keyword.
        self.assertEqual(
            "TEST_ONLY",
            classify_intent_eligibility(
                origin_ref="universe://cli-probe/codex-verify-1",
                text=(
                    "When explicitly requested, return exactly CODEX_CLI_OK "
                    "with no additional text."
                ),
            ),
        )

    def test_unknown_origin_ref_prefix_falls_through_to_text_heuristic(self) -> None:
        self.assertEqual(
            "PRODUCT_INTENT",
            classify_intent_eligibility(
                origin_ref="universe://memory-candidates/candidate-1",
                text="Add dark mode to the settings screen",
            ),
        )

    def test_no_origin_ref_falls_through_to_text_heuristic(self) -> None:
        # Confirms the still-unresolved gap from the contract doc: without an
        # origin tag, a CLI probe phrase with no known marker is not caught.
        self.assertEqual(
            "PRODUCT_INTENT",
            classify_intent_eligibility(
                text=(
                    "When explicitly requested, return exactly CODEX_CLI_OK "
                    "with no additional text."
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()
