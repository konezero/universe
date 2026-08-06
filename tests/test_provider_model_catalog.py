from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from provider_model_catalog import (  # noqa: E402
    DEFAULT_PRESETS,
    ProviderModelCatalogStore,
)
from windows_native_cli import NativeCliResult  # noqa: E402


def completed(stdout: str = "", return_code: int = 0) -> NativeCliResult:
    return NativeCliResult(
        contract="test",
        status="COMPLETED",
        return_code=return_code,
        duration_ms=1,
        stdout=stdout,
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )


class FakeHostProfile:
    def __init__(self, tools: dict) -> None:
        self._tools = tools

    def snapshot(self) -> dict:
        return {"tools": self._tools}


class ProviderModelCatalogTests(unittest.TestCase):
    def test_discover_merges_grok_cli_presets_and_user_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "provider-models.json"
            grok = root / "grok.exe"
            grok.write_bytes(b"x")
            calls: list[tuple] = []

            def runner(request):
                calls.append(tuple(request.arguments))
                if request.arguments == ("models",):
                    return completed(
                        "Default model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (default)\n"
                    )
                return completed("grok 1.0")

            # seed user models
            catalog_path.write_text(
                json.dumps(
                    {
                        "schema": "universe.provider-model-catalog.v1",
                        "providers": {
                            "GROK": {"user_models": ["my-custom-grok"]},
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = ProviderModelCatalogStore(
                catalog_path,
                host_profile=FakeHostProfile(
                    {
                        "grok": {
                            "status": "AVAILABLE",
                            "executable": str(grok),
                            "model": "grok-build",
                        },
                        "codex": {"status": "UNAVAILABLE", "reason": "missing"},
                        "claude": {"status": "UNAVAILABLE", "reason": "missing"},
                    }
                ),
                native_runner=runner,
                home=root,
            )
            result = store.discover(persist=True)
            grok_entry = result["providers"]["GROK"]
            self.assertEqual(grok_entry["status"], "AVAILABLE")
            self.assertEqual(grok_entry["default"], "grok-4.5")
            self.assertIn("grok-4.5", grok_entry["models"])
            self.assertIn("grok-build", grok_entry["models"])
            self.assertIn("my-custom-grok", grok_entry["models"])
            self.assertIn("my-custom-grok", grok_entry["user_models"])
            self.assertTrue(catalog_path.is_file())
            self.assertIn(("models",), calls)

    def test_save_user_edits_keeps_custom_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProviderModelCatalogStore(
                root / "provider-models.json",
                host_profile=FakeHostProfile({}),
                native_runner=lambda request: completed(),
                home=root,
            )
            store.discover(persist=True)
            saved = store.save_user_edits(
                {
                    "providers": {
                        "CLAUDE": {"user_models": ["claude-special"]},
                    }
                }
            )
            self.assertIn(
                "claude-special",
                saved["providers"]["CLAUDE"]["user_models"],
            )
            self.assertIn(
                "claude-special",
                saved["providers"]["CLAUDE"]["models"],
            )
            # rediscover keeps user models
            again = store.discover(persist=True)
            self.assertIn(
                "claude-special",
                again["providers"]["CLAUDE"]["user_models"],
            )

    def test_presets_exist_for_all_providers(self) -> None:
        for name in ("GROK", "CODEX", "CLAUDE"):
            self.assertIn(name, DEFAULT_PRESETS)
            self.assertTrue(DEFAULT_PRESETS[name]["models"])


if __name__ == "__main__":
    unittest.main()
