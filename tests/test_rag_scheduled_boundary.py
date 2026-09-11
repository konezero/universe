import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from universe_server import UniverseHTTPServer, UniverseError

class ScheduledMemoryBoundaryTests(unittest.TestCase):
    def test_schedule_persists_the_actual_run_identity(self):
        server = SimpleNamespace(run_memory_batch=lambda project, request: {"status": "MEMORY_BATCH_RUN_COMPLETED", "run": {"run_id": "run-1", "status": "COMPLETED"}})
        result = UniverseHTTPServer._run_scheduled_memory_batch(server, "p", "CONSOLIDATE")
        self.assertEqual("run-1", result["run_id"])

    def test_missing_runtime_preparation_is_explicit_and_never_dispatches(self):
        config = {"stage":"FAST_EXTRACT", "fallback":"NONE", "provider":"CODEX", "model_ref":"gpt-5.6-luna", "effort":"MAX", "enabled":True, "resolution":{"status":"AVAILABLE"}}
        server = SimpleNamespace(store=SimpleNamespace(get_memory_batch_config=lambda *args: config), _resolve_memory_batch_config=lambda *args: (config, {}))
        with self.assertRaises(UniverseError) as caught:
            UniverseHTTPServer.run_memory_batch(server, "p", {"stage":"FAST_EXTRACT"})
        self.assertEqual("MEMORY_BATCH_RUNTIME_PREPARATION_REQUIRED", caught.exception.code)
