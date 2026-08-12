from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
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

    def test_configured_private_career_root_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            private_root = Path(tmp) / "universe-private"
            (private_root / "README.md").parent.mkdir(parents=True)
            (private_root / "README.md").write_text("# Private\n", encoding="utf-8")
            for node in ("career", "rendezvous"):
                node_root = private_root / "projects" / node
                node_root.mkdir(parents=True)
                (node_root / "README.md").write_text("# Node\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "UNIVERSE_PRIVATE_ROOT": str(private_root),
                    "UNIVERSE_CAREER_SOURCE_ROOT": "",
                },
                clear=False,
            ):
                candidates = discover_network_anchor_candidates(universe_root=ROOT)
                career = next(
                    item for item in candidates if item["project_id"] == "career"
                )
                self.assertEqual(
                    (private_root / "projects" / "career").resolve(),
                    career["project_root"],
                )
                self.assertEqual(
                    "universe-private", career["metadata"]["parent_project_id"]
                )
                self.assertIn(
                    "rendezvous", {item["project_id"] for item in candidates}
                )

    def test_private_node_registration_migrates_legacy_todos(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            private_root = root / "universe-private"
            private_root.mkdir()
            (private_root / "README.md").write_text("# Private\n", encoding="utf-8")
            for node in ("career", "rendezvous"):
                node_root = private_root / "projects" / node
                node_root.mkdir(parents=True)
                (node_root / "README.md").write_text("# Node\n", encoding="utf-8")
            legacy_root = root / "legacy-career"
            legacy_root.mkdir()
            (legacy_root / "README.md").write_text("# Legacy\n", encoding="utf-8")
            store = UniverseStore(root / "u.sqlite3")
            store.register_project(
                {
                    "project_id": "ai-career",
                    "project_root": str(legacy_root),
                    "metadata": {"network_role": "CAREER_SOURCE"},
                }
            )
            todo = store.create_todo(
                {
                    "scope_kind": "PROJECT",
                    "project_id": "ai-career",
                    "title": "Keep this Todo",
                    "detail": "migration coverage",
                    "priority": "P1",
                    "state": "READY",
                    "source_kind": "USER",
                    "sort_order": 1,
                }
            )
            with mock.patch.dict(
                os.environ,
                {"UNIVERSE_PRIVATE_ROOT": str(private_root)},
                clear=False,
            ):
                ensure_network_anchor_projects(store, universe_root=ROOT)
            self.assertEqual("career", store.get_todo(todo["todo_id"])["project_id"])
            legacy = store.get_project("ai-career")
            self.assertEqual("MIGRATED_LEGACY", legacy["metadata"]["visibility"])


if __name__ == "__main__":
    unittest.main()
