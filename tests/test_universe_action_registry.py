from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(r"C:\workspace\universe")
sys.path.insert(0, str(ROOT / "tools"))

from universe_action_registry import (  # noqa: E402
    ACTION_CONTRACT_VERSION,
    COVERED,
    LEGACY_DIRECT,
    UNCOVERED,
    ActionContract,
    ActionContractError,
    ActionRegistry,
    DuplicateActionError,
    UnknownActionError,
    build_default_action_registry,
    derive_idempotency_key,
)


class UniverseActionRegistryTests(unittest.TestCase):
    @staticmethod
    def contract(action_id: str = "test.action") -> ActionContract:
        return ActionContract(
            action_id=action_id,
            request_schema_ref="test://request.v1",
            result_schema_ref="test://result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
        )

    def test_contract_validation_declares_server_owned_actor_and_context(self) -> None:
        contract = self.contract()
        serialized = contract.to_dict()
        self.assertEqual(ACTION_CONTRACT_VERSION, contract.contract_version)
        self.assertEqual("SERVER_SIDE", serialized["actor_context"]["resolution"])
        self.assertFalse(serialized["actor_context"]["caller_supplied"])
        self.assertIn("never accepted", serialized["actor_context"]["statement"])
        with self.assertRaises(ActionContractError):
            ActionContract(
                action_id="Bad Action",
                request_schema_ref="test://request.v1",
                result_schema_ref="test://result.v1",
                side_effect_class="LOCAL_DATABASE_MUTATION",
            )
        with self.assertRaises(ActionContractError):
            ActionContract(
                action_id="test.invalid-context",
                request_schema_ref="test://request.v1",
                result_schema_ref="test://result.v1",
                side_effect_class="LOCAL_DATABASE_MUTATION",
                actor_context_resolution="CALLER",
            )

    def test_duplicate_and_unknown_action_ids_are_explicit(self) -> None:
        registry = ActionRegistry()
        registry.register(self.contract(), lambda _request, _context: {"status": "OK"})
        with self.assertRaises(DuplicateActionError):
            registry.register(self.contract(), lambda _request, _context: {"status": "OK"})
        with self.assertRaises(UnknownActionError):
            registry.lookup("missing.action")

    def test_idempotency_key_is_deterministic_and_action_scoped(self) -> None:
        request = {"feature_id": "feature-1", "expected_revision": 2}
        reordered = {"expected_revision": 2, "feature_id": "feature-1"}
        first = derive_idempotency_key("test.action", request)
        second = derive_idempotency_key("test.action", reordered)
        other_action = derive_idempotency_key("other.action", request)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_action)
        self.assertEqual(first, self.contract().idempotency_key(request))

    def test_coverage_is_derived_from_registration_state(self) -> None:
        registry = build_default_action_registry()
        registry.register_uncovered_surface("future.direct.surface")
        self.assertEqual(COVERED, registry.classify_surface("feature.goal.start"))
        self.assertEqual(LEGACY_DIRECT, registry.classify_surface("UniverseStore.start_feature_goal"))
        self.assertEqual(UNCOVERED, registry.classify_surface("future.direct.surface"))
        coverage = registry.coverage()
        self.assertEqual(COVERED, coverage["feature.goal.start"])
        self.assertEqual(LEGACY_DIRECT, coverage["UniverseStore.start_feature_goal"])
        self.assertEqual(UNCOVERED, coverage["future.direct.surface"])
        report = registry.coverage_report()
        self.assertIn("feature.goal.start", report["registered_action_ids"])
        self.assertEqual(coverage, registry.coverage_classification())
        self.assertEqual(
            "universe.feature-goal-start-receipt.v1",
            build_default_action_registry().lookup("feature.goal.start").result_schema_ref,
        )
        self.assertEqual(
            {"COVERED", "LEGACY_DIRECT", "UNCOVERED"},
            {item["classification"] for item in report["coverage"]},
        )

    def test_registry_rejects_caller_owned_context_fields(self) -> None:
        registry = ActionRegistry()
        registry.register(self.contract(), lambda request, _context: request)
        with self.assertRaises(ActionContractError) as raised:
            registry.dispatch("test.action", {"mode": "MASTER"}, {})
        self.assertEqual("ACTION_CALLER_CONTEXT_FORBIDDEN", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
