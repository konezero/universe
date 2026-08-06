from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import (  # noqa: E402
    UniverseStore,
    discover_network_anchor_candidates,
    ensure_network_anchor_projects,
)


class NetworkAnchorProjectTests(unittest.TestCase):
    def test_discover_includes_universe_home(self) -> None:
        candidates = discover_network_anchor_candidates(universe_root=ROOT)
        ids = {item["project_id"] for item in candidates}
        self.assertIn("universe", ids)
        universe = next(item for item in candidates if item["project_id"] == "universe")
        self.assertEqual("UNIVERSE_HOME", universe["metadata"]["network_role"])

    def test_ensure_registers_universe_and_career_when_present(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "u.sqlite3"
            store = UniverseStore(db)
            ensured = ensure_network_anchor_projects(store, universe_root=ROOT)
            ids = {item["project_id"] for item in ensured}
            self.assertIn("universe", ids)
            listed = {item["project_id"] for item in store.list_projects()}
            self.assertIn("universe", listed)
            # Sibling career root exists on this workstation as C:\workspace\ai-career
            career = ROOT.parent / "ai-career"
            if career.is_dir():
                self.assertIn("ai-career", ids)
                project = store.get_project("ai-career")
                self.assertEqual(
                    "CAREER_SOURCE",
                    (project.get("metadata") or {}).get("network_role"),
                )


if __name__ == "__main__":
    unittest.main()
