from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from provider_model_catalog import empty_catalog  # noqa: E402
from universe_app.connection import UniverseError  # noqa: E402
from universe_app.memory_batch_service import (  # noqa: E402
    MemoryBatchConfigurationService,
)


class FakeCatalog:
    def __init__(self, *, available: bool = True) -> None:
        self.value = empty_catalog()
        self.value["providers"]["CODEX"].update(
            {
                "status": "AVAILABLE" if available else "UNAVAILABLE",
                "default": "test-model",
                "models": ["test-model"],
            }
        )

    def snapshot(self) -> dict[str, Any]:
        return self.value


class FakeStore:
    def __init__(self) -> None:
        self.configs: list[dict[str, Any]] = []

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {"project_id": project_id}

    def resolve_worker_binding(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "snapshot": {
                "profile_id": "profile-1",
                "provider": "CODEX",
                "model_ref": "test-model",
                "effort": "MAX",
                "binding_digest": "a" * 64,
            }
        }

    def get_memory_batch_configs(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.configs]

    def upsert_memory_batch_config(
        self, project_id: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        stored = {"project_id": project_id, **dict(request), "persisted": True}
        self.configs = [stored]
        return dict(stored)


class MemoryBatchConfigurationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore()
        self.service = MemoryBatchConfigurationService(
            self.store,
            FakeCatalog(),
            api_schema="universe.test-api.v1",
        )

    def test_catalog_and_save_round_trip_preserve_public_contract(self) -> None:
        catalog = self.service.catalog_settings()
        self.assertEqual("MEMORY_BATCH_CATALOG_COLLECTED", catalog["status"])
        self.assertIn("FAST_EXTRACT", catalog["stages"])

        saved = self.service.save_config(
            "TEST",
            {
                "stage": "FAST_EXTRACT",
                "provider": "CODEX",
                "model_ref": "test-model",
                "effort": "MAX",
                "schedule": {"kind": "MANUAL"},
                "quota_or_budget": {"max_runs": 2},
                "fallback": "NONE",
                "enabled": True,
                "dry_run": False,
            },
        )
        self.assertEqual("MEMORY_BATCH_CONFIG_SAVED", saved["status"])
        self.assertEqual("AVAILABLE", saved["config"]["resolution"]["status"])
        self.assertEqual("profile-1", saved["config"]["worker_binding"]["profile_id"])

        listed = self.service.list_configs("TEST")
        self.assertEqual("MEMORY_BATCH_CONFIGS_COLLECTED", listed["status"])
        self.assertEqual(1, len(listed["configs"]))
        self.assertTrue(listed["configs"][0]["persisted"])

    def test_invalid_stage_and_missing_model_fail_closed(self) -> None:
        with self.assertRaises(UniverseError) as invalid_stage:
            self.service.resolve("TEST", {"stage": "UNKNOWN"})
        self.assertEqual("MEMORY_BATCH_STAGE_INVALID", invalid_stage.exception.code)

        with self.assertRaises(UniverseError) as missing_model:
            self.service.resolve(
                "TEST",
                {
                    "stage": "FAST_EXTRACT",
                    "provider": "CODEX",
                    "model_ref": "missing-model",
                    "fallback": "NONE",
                },
            )
        self.assertEqual("MEMORY_BATCH_MODEL_NOT_FOUND", missing_model.exception.code)

    def test_unavailable_provider_uses_only_explicit_deterministic_fallback(self) -> None:
        service = MemoryBatchConfigurationService(
            self.store,
            FakeCatalog(available=False),
            api_schema="universe.test-api.v1",
        )
        resolved, _catalog = service.resolve(
            "TEST",
            {
                "stage": "CONSOLIDATE",
                "provider": "CODEX",
                "model_ref": "test-model",
                "fallback": "DETERMINISTIC",
            },
        )
        self.assertEqual("FALLBACK_DETERMINISTIC", resolved["resolution"]["status"])


if __name__ == "__main__":
    unittest.main()
