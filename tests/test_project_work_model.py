from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import UniverseError, UniverseStore  # noqa: E402


class ProjectWorkModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp.name) / "project"
        self.project_root.mkdir()
        (self.project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# Project\n", encoding="utf-8"
        )
        docs = self.project_root / "docs"
        docs.mkdir()
        (docs / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
        self.database = Path(self.temp.name) / "universe.sqlite3"
        self.store = UniverseStore(self.database)
        self.store.register_project(
            {"project_id": "sample", "project_root": str(self.project_root)}
        )
        self.store.record_project_seed("sample", self.seed())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def digest(self, relative: str) -> str:
        return hashlib.sha256((self.project_root / relative).read_bytes()).hexdigest()

    def seed(self) -> dict:
        return {
            "seed_id": "sample-seed-1",
            "source": {"ref": "test://sample", "commit": "a" * 40},
            "project": {
                "kind": "APPLICATION",
                "technologies": ["python"],
                "goal": "Validate scoped project work",
            },
            "nodes": [
                {
                    "node_id": "feature-a",
                    "kind": "FEATURE",
                    "title": "Feature A",
                    "refs": [
                        {
                            "path": "REPOSITORY_MANIFEST.md",
                            "sha256": self.digest("REPOSITORY_MANIFEST.md"),
                        }
                    ],
                }
            ],
            "edges": [],
            "documents": [
                {
                    "document_id": "architecture",
                    "title": "Architecture",
                    "path": "docs/architecture.md",
                    "sha256": self.digest("docs/architecture.md"),
                    "role": "ARCHITECTURE",
                    "node_ids": ["feature-a"],
                }
            ],
        }

    @staticmethod
    def goal(title: str, **extra: object) -> dict:
        return {
            "title": title,
            "description": "Bounded outcome",
            "owner": "Project Master",
            "state": "DESIGNING",
            "sort_order": 0,
            **extra,
        }

    def test_project_and_node_work_surface_reuses_seed_documents(self) -> None:
        before = {
            path.relative_to(self.project_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        project_goal = self.store.create_goal("sample", self.goal("Project goal"))
        node_goal = self.store.create_goal(
            "sample",
            self.goal("Node goal", scope_kind="NODE", node_ref="feature-a"),
        )
        self.store.create_todo(
            {
                "scope_kind": "PROJECT",
                "project_id": "sample",
                "title": "Project todo",
                "detail": "Project work",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            }
        )
        self.store.create_todo(
            {
                "scope_kind": "NODE",
                "project_id": "sample",
                "node_ref": "feature-a",
                "title": "Node todo",
                "detail": "Node work",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 1,
            }
        )

        surface = self.store.project_work_surface("sample", node_ref="feature-a")

        self.assertEqual("PROJECT", project_goal["scope_kind"])
        self.assertIsNone(project_goal["node_ref"])
        self.assertEqual("NODE", node_goal["scope_kind"])
        self.assertEqual(["Project goal"], [item["title"] for item in surface["project"]["goals"]])
        self.assertEqual(["Node goal"], [item["title"] for item in surface["node"]["goals"]])
        self.assertEqual(["Project todo"], [item["title"] for item in surface["project"]["todos"]])
        self.assertEqual(["Node todo"], [item["title"] for item in surface["node"]["todos"]])
        self.assertEqual([], surface["project"]["documents"])
        self.assertEqual(["architecture"], [item["document_id"] for item in surface["node"]["documents"]])
        self.assertTrue(all(item["effects"]["project_source_write"] == "NONE" for item in surface["template_instances"]))
        self.assertEqual(3, len(surface["template_instances"]))

        graph = self.store.semantic_project_graph("sample")
        node_goal_edges = [
            item for item in graph["edges"]
            if item["edge_type"] == "FUNCTIONAL_NODE_HAS_GOAL"
        ]
        self.assertEqual(1, len(node_goal_edges))
        self.assertEqual(f"goal:{node_goal['goal_id']}", node_goal_edges[0]["to"])

        after = {
            path.relative_to(self.project_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_node_goal_requires_current_seed_node_and_instances_are_idempotent(self) -> None:
        with self.assertRaises(UniverseError) as caught:
            self.store.create_goal(
                "sample",
                self.goal("Unknown node", scope_kind="NODE", node_ref="missing"),
            )
        self.assertEqual("GOAL_NODE_UNKNOWN", caught.exception.code)

        goal = self.store.create_goal(
            "sample", self.goal("Node goal", scope_kind="NODE", node_ref="feature-a")
        )
        updated = self.store.update_goal(
            goal["goal_id"],
            {
                "title": goal["title"],
                "description": goal["description"],
                "owner": goal["owner"],
                "state": "READY",
                "sort_order": goal["sort_order"],
                "revision": goal["revision"],
            },
        )
        self.assertEqual("NODE", updated["scope_kind"])
        self.assertEqual("feature-a", updated["node_ref"])

        self.store._initialize()
        surface = self.store.project_work_surface("sample", node_ref="feature-a")
        self.assertEqual(2, len(surface["template_instances"]))
        connection = sqlite3.connect(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM project_work_template_instance WHERE project_id = 'sample'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(2, count)

    def test_feature_node_is_a_node_work_coordinate_without_project_seed(self) -> None:
        feature_root = Path(self.temp.name) / "feature-only"
        feature_root.mkdir()
        (feature_root / "REPOSITORY_MANIFEST.md").write_text(
            "# Feature-only project\n", encoding="utf-8"
        )
        self.store.register_project(
            {"project_id": "feature-only", "project_root": str(feature_root)}
        )
        feature, created = self.store.create_feature_node(
            "feature-only",
            {
                "idempotency_key": "feature-only-node",
                "title": "Feature-only node",
                "intent_text": "Own node-local Goals and Todos without a Project Seed.",
                "created_by_role": "USER",
            },
        )
        self.assertTrue(created)
        goal = self.store.create_goal(
            "feature-only",
            self.goal(
                "Feature Goal",
                scope_kind="NODE",
                node_ref=feature["feature_id"],
            ),
        )
        todo = self.store.create_todo(
            {
                "scope_kind": "NODE",
                "project_id": "feature-only",
                "node_ref": feature["feature_id"],
                "goal_id": goal["goal_id"],
                "title": "Feature Todo",
                "detail": "Node-local implementation work",
                "priority": "P0",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            }
        )

        surface = self.store.project_work_surface(
            "feature-only", node_ref=feature["feature_id"]
        )

        self.assertEqual("NODE", goal["scope_kind"])
        self.assertEqual(feature["feature_id"], goal["node_ref"])
        self.assertEqual(feature["feature_id"], todo["node_ref"])
        self.assertEqual("FEATURE_NODE", surface["node"]["node_kind"])
        self.assertEqual([goal["goal_id"]], [item["goal_id"] for item in surface["node"]["goals"]])
        self.assertEqual([todo["todo_id"]], [item["todo_id"] for item in surface["node"]["todos"]])
        self.assertEqual([], surface["node"]["documents"])

    def test_ui_exposes_selected_node_goal_scope(self) -> None:
        app = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
        page = (ROOT / "tools" / "universe_ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('value="NODE">Selected node', page)
        self.assertIn("goalsForSelectedContext", app)
        self.assertIn("scope_kind: scopeKind", app)
        self.assertIn("node_ref: selectedNodeRef()", app)


if __name__ == "__main__":
    unittest.main()
