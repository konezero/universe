from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from run_test_tier import build_suite, load_manifest, selected_test_names  # noqa: E402


class TestTierManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(ROOT / "tests" / "test_tiers.json")

    def test_changed_path_selects_connection_contract(self) -> None:
        names = selected_test_names(
            self.manifest,
            "changed",
            ["tools/universe_app/connection.py"],
        )
        self.assertEqual(["tests.test_universe_connection"], names)

    def test_server_entrypoint_selects_extracted_contracts(self) -> None:
        names = selected_test_names(
            self.manifest,
            "changed",
            ["tools/universe_server.py"],
        )
        self.assertEqual(
            [
                "tests.test_universe_connection",
                "tests.test_universe_streaming",
                "tests.test_universe_server.UniverseWorkPreflightTests",
                "tests.test_memory_batch_service",
            ],
            names,
        )

    def test_unknown_changed_path_uses_bounded_default(self) -> None:
        names = selected_test_names(
            self.manifest,
            "changed",
            ["docs/unmapped.md"],
        )
        self.assertEqual(
            ["tests.test_universe_connection", "tests.test_universe_streaming"],
            names,
        )

    def test_full_tier_discovers_repository_tests(self) -> None:
        suite = build_suite(self.manifest, "full", [])
        self.assertGreater(suite.countTestCases(), 0)

    def test_manifest_declares_all_four_tiers(self) -> None:
        self.assertEqual(
            {"changed", "smoke", "contract", "full"},
            set(self.manifest["tiers"]),
        )
        json.dumps(self.manifest)


if __name__ == "__main__":
    unittest.main()
