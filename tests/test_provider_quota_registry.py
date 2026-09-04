import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from universe_app.provider_quota_registry import ProviderQuotaRegistry  # noqa: E402


class ProviderQuotaRegistryTests(unittest.TestCase):
    def test_view_is_always_three_rows_in_a_stable_order(self) -> None:
        view = ProviderQuotaRegistry().view()
        self.assertEqual(
            [row["provider"] for row in view["providers"]],
            ["CLAUDE", "GROK", "CODEX"],
        )
        self.assertTrue(all(row["state"] == "UNKNOWN" for row in view["providers"]))
        self.assertTrue(all(row["observed_at"] is None for row in view["providers"]))

    def test_an_empty_unknown_reading_never_registers(self) -> None:
        registry = ProviderQuotaRegistry()
        registry.record({"provider": "CLAUDE", "state": "UNKNOWN", "windows": []})
        self.assertEqual(registry.view()["providers"][0]["state"], "UNKNOWN")
        self.assertEqual(registry.view()["providers"][0]["source"], "none")

    def test_a_real_reading_is_stored_and_normalised(self) -> None:
        registry = ProviderQuotaRegistry()
        registry.record(
            {
                "schema": "universe.provider-quota-snapshot.v1",
                "provider": "claude",
                "source": "rate_limit_event",
                "state": "warning",
                "windows": [
                    {
                        "name": "five_hour",
                        "used_percent": 82.5,
                        "window_minutes": 300,
                        "resets_at": "2026-09-04T12:00:00Z",
                    }
                ],
            },
            session_ref="session_anchor_x",
        )
        claude = registry.view()["providers"][0]
        self.assertEqual(claude["state"], "WARNING")
        self.assertEqual(claude["session_ref"], "session_anchor_x")
        self.assertEqual(claude["windows"][0]["name"], "FIVE_HOUR")
        self.assertEqual(claude["windows"][0]["used_percent"], 82.5)
        self.assertEqual(claude["windows"][0]["resets_at"], "2026-09-04T12:00:00Z")
        self.assertIsNotNone(claude["observed_at"])

    def test_an_unknown_reading_does_not_clobber_a_real_one(self) -> None:
        registry = ProviderQuotaRegistry()
        registry.record(
            {
                "provider": "CLAUDE",
                "state": "WARNING",
                "windows": [{"name": "W", "used_percent": 80}],
            }
        )
        registry.record({"provider": "CLAUDE", "state": "UNKNOWN", "windows": []})
        self.assertEqual(registry.view()["providers"][0]["state"], "WARNING")

    def test_exhausted_reading_for_codex(self) -> None:
        registry = ProviderQuotaRegistry()
        registry.record(
            {
                "provider": "CODEX",
                "state": "EXHAUSTED",
                "windows": [{"name": "PRIMARY", "used_percent": 100}],
                "rate_limit_reached_type": "primary",
            }
        )
        codex = registry.view()["providers"][2]
        self.assertEqual(codex["state"], "EXHAUSTED")
        self.assertEqual(codex["rate_limit_reached_type"], "primary")

    def test_unknown_provider_is_ignored(self) -> None:
        registry = ProviderQuotaRegistry()
        registry.record(
            {"provider": "GEMINI", "state": "AVAILABLE", "windows": [{"used_percent": 1}]}
        )
        self.assertTrue(
            all(row["state"] == "UNKNOWN" for row in registry.view()["providers"])
        )

    def test_non_mapping_input_is_safe(self) -> None:
        registry = ProviderQuotaRegistry()
        registry.record(None)
        registry.record("nope")
        registry.record([])
        self.assertEqual(len(registry.view()["providers"]), 3)


if __name__ == "__main__":
    unittest.main()
