from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".ai" / "skills" / "common"))

from resolve_universe_endpoint import resolve  # noqa: E402


class ResolveUniverseEndpointTests(unittest.TestCase):
    def test_missing_state_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = resolve(Path(directory) / "server.json")
            self.assertEqual("UNIVERSE_LOCAL_ENDPOINT_MISSING", result["status"])

    def test_reads_loopback_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "universe.local-service-state.v1",
                        "endpoint": "http://127.0.0.1:8765",
                        "token": "secret-token",
                        "database": str(Path(directory) / "universe.sqlite3"),
                        "pid": 12,
                    }
                ),
                encoding="utf-8",
            )
            result = resolve(path)
            self.assertEqual("UNIVERSE_LOCAL_ENDPOINT_READY", result["status"])
            self.assertEqual("http://127.0.0.1:8765", result["endpoint"])
            self.assertEqual("secret-token", result["token"])


if __name__ == "__main__":
    unittest.main()
