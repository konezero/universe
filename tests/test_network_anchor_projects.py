from __future__ import annotations

import os
import sys
import tempfile
import unittest
import json
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
    def _write_node_root(
        self,
        root: Path,
        *,
        node_id: str,
        display_name: str,
        kind: str = "instance",
        network_role: str = "NETWORK_ANCHOR",
    ) -> None:
        root.mkdir(parents=True)
        (root / "README.md").write_text("# Node\n", encoding="utf-8")
        (root / "universe-node.json").write_text(
            json.dumps(
                {
                    "schema": "universe.project-node.v1",
                    "node_id": node_id,
                    "display_name": display_name,
                    "kind": kind,
                    "network_role": network_role,
                }
            ),
            encoding="utf-8",
        )

    def _write_private_root(
        self,
        private_root: Path,
        nodes: list[dict[str, object]],
    ) -> None:
        private_root.mkdir(parents=True)
        (private_root / "README.md").write_text("# Private\n", encoding="utf-8")
        (private_root / "universe-node.json").write_text(
            json.dumps(
                {
                    "schema": "universe.project-node.v1",
                    "node_id": "private-node-root",
                    "display_name": "Private Node Root",
                    "kind": "container",
                    "network_role": "NETWORK_ANCHOR",
                }
            ),
            encoding="utf-8",
        )
        (private_root / "universe-node-catalog.json").write_text(
            json.dumps(
                {
                    "schema": "universe.node-catalog.v1",
                    "children_root": "projects",
                }
            ),
            encoding="utf-8",
        )
        for node in nodes:
            node_id = str(node["node_id"])
            node_root = private_root / "projects" / node_id
            node_root.mkdir(parents=True)
            (node_root / "README.md").write_text("# Node\n", encoding="utf-8")
            (node_root / "universe-node.json").write_text(
                json.dumps(
                    {
                        "schema": "universe.project-node.v1",
                        "node_id": node_id,
                        "display_name": node["display_name"],
                        "visibility": "private",
                        "kind": "product",
                        "network_role": node.get("network_role", "PRODUCT_NODE"),
                        "legacy_project_ids": node.get("legacy_project_ids", []),
                    }
                ),
                encoding="utf-8",
            )

    def test_discover_includes_universe_home(self) -> None:
        candidates = discover_network_anchor_candidates(universe_root=ROOT)
        ids = {item["project_id"] for item in candidates}
        self.assertIn("universe", ids)
        universe = next(item for item in candidates if item["project_id"] == "universe")
        self.assertEqual("UNIVERSE_HOME", universe["metadata"]["network_role"])
        self.assertEqual(ROOT.name, universe["metadata"]["node_tag"])

    def test_registration_defaults_node_tag_from_ai_parent(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp) / "fresh-imported-product"
            (root / ".ai").mkdir(parents=True)
            (root / "REPOSITORY_MANIFEST.md").write_text("# Manifest\n", encoding="utf-8")
            store = UniverseStore(Path(tmp) / "u.sqlite3")
            project, _created = store.register_project(
                {
                    "project_id": "stable-product-id",
                    "project_root": str(root),
                    "metadata": {},
                }
            )
            self.assertEqual(
                "fresh-imported-product", project["metadata"]["node_tag"]
            )

    def test_node_roots_support_multiple_instance_trees(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            alpha = root / "alpha-host"
            beta = root / "beta-host"
            self._write_node_root(alpha, node_id="alpha-id", display_name="Alpha")
            self._write_node_root(beta, node_id="beta-id", display_name="Beta")
            with mock.patch.dict(
                os.environ,
                {"UNIVERSE_NODE_ROOTS": os.pathsep.join((str(alpha), str(beta)))},
                clear=False,
            ):
                candidates = discover_network_anchor_candidates(universe_root=root)
            by_id = {item["project_id"]: item for item in candidates}
            self.assertEqual({"alpha-id", "beta-id"}, set(by_id))
            self.assertEqual("alpha-host", by_id["alpha-id"]["metadata"]["node_tag"])
            self.assertEqual("beta-host", by_id["beta-id"]["metadata"]["node_tag"])

    def test_ensure_registers_manifest_defined_home(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "u.sqlite3"
            store = UniverseStore(db)
            with mock.patch.dict(os.environ, {"UNIVERSE_NODE_ROOTS": ""}):
                ensured = ensure_network_anchor_projects(store, universe_root=ROOT)
            ids = {item["project_id"] for item in ensured}
            self.assertIn("universe", ids)
            listed = {item["project_id"] for item in store.list_projects()}
            self.assertIn("universe", listed)

    def test_configured_node_roots_discover_generic_child_nodes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            private_root = Path(tmp) / "universe-private"
            self._write_private_root(
                private_root,
                [
                    {
                        "node_id": "career",
                        "display_name": "Career",
                        "network_role": "CAREER_SOURCE",
                    },
                    {"node_id": "rendezvous", "display_name": "Rendezvous"},
                    {"node_id": "new-product", "display_name": "New Product"},
                ],
            )
            with mock.patch.dict(
                os.environ,
                {
                    "UNIVERSE_NODE_ROOTS": str(private_root),
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
                    "private-node-root", career["metadata"]["parent_project_id"]
                )
                self.assertEqual("career", career["metadata"]["node_tag"])
                self.assertIn(
                    "rendezvous", {item["project_id"] for item in candidates}
                )
                self.assertIn(
                    "new-product", {item["project_id"] for item in candidates}
                )

    def test_private_node_registration_migrates_legacy_todos(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            private_root = root / "universe-private"
            self._write_private_root(
                private_root,
                [
                    {
                        "node_id": "career",
                        "display_name": "Career",
                        "network_role": "CAREER_SOURCE",
                        "legacy_project_ids": ["ai-career"],
                    },
                    {"node_id": "rendezvous", "display_name": "Rendezvous"},
                ],
            )
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
                {"UNIVERSE_NODE_ROOTS": str(private_root)},
                clear=False,
            ):
                ensure_network_anchor_projects(store, universe_root=ROOT)
            self.assertEqual("career", store.get_todo(todo["todo_id"])["project_id"])
            legacy = store.get_project("ai-career")
            self.assertEqual("MIGRATED_LEGACY", legacy["metadata"]["visibility"])


if __name__ == "__main__":
    unittest.main()
