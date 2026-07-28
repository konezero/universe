from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UniverseRuntimeHostLayoutTests(unittest.TestCase):
    def test_provider_workers_are_owned_by_universe_runtime_host(self) -> None:
        expected = (
            "tools/universe_runtime_host_dispatch.ps1",
            "tools/universe_runtime_host_grok_adapter.json",
            "tools/universe_runtime_host_grok_invoke.ps1",
            "tools/universe_runtime_host_codex_worker.ps1",
        )
        for relative in expected:
            self.assertTrue((ROOT / relative).is_file(), relative)

        dispatcher = (ROOT / expected[0]).read_text(encoding="utf-8")
        self.assertIn("tools\\runtime_host\\grok\\invoke.ps1", dispatcher)
        self.assertIn("tools\\runtime_host\\codex\\worker.ps1", dispatcher)
        self.assertIn("LOCALAPPDATA", dispatcher)

    def test_installed_codex_adapter_does_not_declare_universe_worker(self) -> None:
        adapter = json.loads(
            (ROOT / ".ai/adapters/codex/adapter.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("task_frame_worker", adapter)

        removed = (
            ".ai/adapters/worker-dispatch.ps1",
            ".ai/adapters/worker-dispatch.md",
            ".ai/adapters/grok/adapter.json",
            ".ai/adapters/grok/invoke.ps1",
            ".ai/adapters/codex/worker.ps1",
        )
        for relative in removed:
            self.assertFalse((ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
