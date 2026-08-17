from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from runtime_state_trust_gate import (  # noqa: E402
    DECLARED_ACTIVE_ING_STATES,
    is_active_ing_state,
    normalize_runtime_state,
)


class RuntimeStateTrustGateTests(unittest.TestCase):
    def test_declared_gate_states_are_active_ing(self) -> None:
        for state in DECLARED_ACTIVE_ING_STATES:
            with self.subTest(state=state):
                self.assertTrue(state.endswith("ING"))
                self.assertTrue(is_active_ing_state(state))
                self.assertTrue(is_active_ing_state(state.lower()))

    def test_non_ing_and_foreign_labels_are_not_active(self) -> None:
        for state in (
            "CURRENT",
            "READY",
            "STOPPED",
            "FAILED",
            "RETIRED",
            "CONNECTING",
            "STREAMING",
            "WORKING",
            "LISTENING",
            "MISSING",
            "",
            None,
        ):
            with self.subTest(state=state):
                self.assertFalse(is_active_ing_state(state))

    def test_normalize_runtime_state_trims_and_upcases(self) -> None:
        self.assertEqual("VALIDATING", normalize_runtime_state(" validating "))
