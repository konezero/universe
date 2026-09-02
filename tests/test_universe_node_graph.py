from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_node_graph import (  # noqa: E402
    NodeGraphError,
    VIEW_NAMES,
    compute_views,
    document_graph,
    unify_seed_graph,
)


def _seed(**overrides):
    seed = {
        "nodes": [
            {"node_id": "structure", "kind": "capability", "title": "Structure",
             "refs": [], "summary": "s"},
            {"node_id": "sessions", "kind": "flow", "title": "Sessions", "refs": []},
            {"node_id": "github", "kind": "external-boundary", "title": "GitHub",
             "refs": []},
        ],
        "edges": [
            {"edge_id": "e1", "from_node": "structure", "to_node": "sessions",
             "kind": "enables"},
            {"edge_id": "e2", "from_node": "sessions", "to_node": "github",
             "kind": "requests"},
        ],
        "implementation_nodes": [
            {"implementation_id": "server", "kind": "service", "title": "Server",
             "refs": []},
            {"implementation_id": "pkg", "kind": "package", "title": "Pkg",
             "refs": []},
        ],
        "implementation_bindings": [
            {"binding_id": "b1", "functional_node_id": "structure",
             "implementation_node_id": "server", "relation": "supports"},
        ],
        "documents": [],
    }
    seed.update(overrides)
    return seed


class UnifySeedGraphTests(unittest.TestCase):
    def test_functional_kinds_mapped_to_unified(self):
        graph = unify_seed_graph(_seed())
        kinds = {n["node_id"]: n["kind"] for n in graph["nodes"]}
        self.assertEqual(kinds["structure"], "CAPABILITY")
        self.assertEqual(kinds["sessions"], "FLOW")
        self.assertEqual(kinds["github"], "EXTERNAL_BOUNDARY")

    def test_implementation_nodes_become_structure_and_component(self):
        graph = unify_seed_graph(_seed())
        by_id = {n["node_id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["server"]["kind"], "COMPONENT")
        self.assertEqual(by_id["server"]["component_role"], "service")
        self.assertEqual(by_id["pkg"]["kind"], "STRUCTURE")
        self.assertEqual(by_id["pkg"]["structure_role"], "package")

    def test_schema_and_default_state(self):
        graph = unify_seed_graph(_seed())
        self.assertEqual(graph["schema"], "universe.node-graph.v1")
        self.assertTrue(all(n["state"] == "ADOPTED" for n in graph["nodes"]))

    def test_edges_carry_relation_from_kind_or_relation_key(self):
        graph = unify_seed_graph(_seed())
        rel = {e["edge_id"]: e["relation"] for e in graph["edges"]}
        self.assertEqual(rel["e1"], "ENABLES")
        self.assertEqual(rel["e2"], "REQUESTS")

    def test_bindings_become_edges_impl_to_functional(self):
        graph = unify_seed_graph(_seed())
        binding = next(e for e in graph["edges"] if e["edge_id"] == "b1")
        self.assertEqual(binding["from_node"], "server")
        self.assertEqual(binding["to_node"], "structure")
        self.assertEqual(binding["relation"], "SUPPORTS")

    def test_relation_key_takes_precedence_over_kind(self):
        seed = _seed(edges=[
            {"edge_id": "e1", "from_node": "structure", "to_node": "sessions",
             "kind": "enables", "relation": "informs"},
        ])
        graph = unify_seed_graph(seed)
        self.assertEqual(graph["edges"][0]["relation"], "INFORMS")

    def test_unknown_binding_relation_falls_back_to_implements(self):
        seed = _seed(implementation_bindings=[
            {"binding_id": "b1", "functional_node_id": "structure",
             "implementation_node_id": "server", "relation": "bogus"},
        ])
        graph = unify_seed_graph(seed)
        self.assertEqual(graph["edges"][-1]["relation"], "IMPLEMENTS")

    def test_edge_to_unknown_node_rejected(self):
        seed = _seed(edges=[
            {"edge_id": "e1", "from_node": "structure", "to_node": "ghost",
             "kind": "enables"},
        ])
        with self.assertRaises(NodeGraphError):
            unify_seed_graph(seed)

    def test_duplicate_node_id_across_groups_rejected(self):
        seed = _seed(implementation_nodes=[
            {"implementation_id": "structure", "kind": "service",
             "title": "clash", "refs": []},
        ])
        with self.assertRaises(NodeGraphError):
            unify_seed_graph(seed)

    def test_missing_edge_id_is_synthesised(self):
        seed = _seed(edges=[
            {"from_node": "structure", "to_node": "sessions", "kind": "enables"},
        ])
        graph = unify_seed_graph(seed)
        self.assertEqual(graph["edges"][0]["edge_id"], "structure-enables-sessions")

    def test_implementation_nodes_under_nested_implementation_key(self):
        # The stored / normalized seed carries impl nodes as
        # implementation={"nodes": [...]}, not implementation_nodes.
        seed = _seed()
        seed["implementation"] = {"nodes": seed.pop("implementation_nodes")}
        graph = unify_seed_graph(seed)
        by_id = {n["node_id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["server"]["kind"], "COMPONENT")
        binding = next(e for e in graph["edges"] if e["edge_id"] == "b1")
        self.assertEqual(binding["from_node"], "server")

    def test_normalized_seed_shape_upper_case_kinds(self):
        seed = _seed(
            nodes=[{"node_id": "cap", "kind": "CAPABILITY", "title": "Cap", "refs": []}],
            edges=[],
            implementation_nodes=[
                {"implementation_id": "svc", "kind": "SERVICE", "title": "Svc", "refs": []}
            ],
            implementation_bindings=[],
        )
        graph = unify_seed_graph(seed)
        by_id = {n["node_id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["cap"]["kind"], "CAPABILITY")
        self.assertEqual(by_id["svc"]["kind"], "COMPONENT")

    def test_proposed_state_preserved(self):
        seed = _seed(nodes=[
            {"node_id": "future", "kind": "feature", "title": "Future", "refs": [],
             "state": "PROPOSED", "expected_path_ref": "ep_1"},
        ], edges=[], implementation_bindings=[])
        graph = unify_seed_graph(seed)
        node = graph["nodes"][0]
        self.assertEqual(node["state"], "PROPOSED")
        self.assertEqual(node["expected_path_ref"], "ep_1")

    def test_flat_seed_with_implementation_kind_as_node_kind(self):
        # GCS records SERVICE directly as a node kind, no implementation graph.
        seed = {
            "nodes": [
                {"node_id": "market-data", "kind": "SERVICE", "title": "Market",
                 "refs": []},
                {"node_id": "domain", "kind": "DOMAIN", "title": "Domain", "refs": []},
            ],
            "edges": [
                {"edge_id": "x", "from_node": "market-data", "to_node": "domain",
                 "kind": "FEEDS"},
            ],
        }
        graph = unify_seed_graph(seed)
        by_id = {n["node_id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["market-data"]["kind"], "COMPONENT")
        self.assertEqual(by_id["market-data"]["component_role"], "service")
        self.assertEqual(by_id["domain"]["kind"], "DOMAIN")  # unknown passes through
        self.assertEqual(graph["edges"][0]["relation"], "FEEDS")

    def test_real_universe_assets_unify(self):
        asset_root = ROOT / ".ai" / "universe"
        if not (asset_root / "functional-graph.json").is_file():
            self.skipTest("universe seed assets not present")
        functional = json.loads((asset_root / "functional-graph.json").read_text("utf-8"))
        implementation = json.loads(
            (asset_root / "implementation-graph.json").read_text("utf-8")
        )
        bindings = json.loads((asset_root / "bindings.json").read_text("utf-8"))
        seed = {
            "nodes": functional["nodes"],
            "edges": functional["edges"],
            "implementation_nodes": implementation["nodes"],
            "implementation_bindings": bindings["bindings"],
        }
        graph = unify_seed_graph(seed)
        self.assertGreater(len(graph["nodes"]), 5)
        self.assertTrue(any(n["kind"] == "COMPONENT" for n in graph["nodes"]))
        self.assertTrue(any(e["relation"] == "SUPPORTS" for e in graph["edges"]))
        # every edge endpoint resolves
        ids = {n["node_id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            self.assertIn(edge["from_node"], ids)
            self.assertIn(edge["to_node"], ids)


class DocumentGraphTests(unittest.TestCase):
    def test_documents_become_nodes_and_edges(self):
        docs = [
            {"document_id": "spec", "path": "docs/spec.md", "title": "Spec",
             "role": "SPECIFICATION", "node_ids": ["structure"], "sha256": "a"},
            {"document_id": "arch", "path": "docs/arch.md", "title": "Arch",
             "role": "ARCHITECTURE", "node_ids": ["structure", "ghost"], "sha256": "b"},
        ]
        graph = document_graph(docs, {"structure"})
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertTrue(all(n["kind"] == "DOCUMENT" for n in graph["nodes"]))
        rels = {(e["from_node"], e["to_node"], e["relation"]) for e in graph["edges"]}
        self.assertIn(("doc:spec", "structure", "SPECIFIES"), rels)
        self.assertIn(("doc:arch", "structure", "INFORMS"), rels)
        # edge to an unknown structural node is dropped
        self.assertNotIn("ghost", {e["to_node"] for e in graph["edges"]})

    def test_duplicate_document_id_rejected(self):
        docs = [
            {"document_id": "d", "path": "a", "role": "REFERENCE", "node_ids": []},
            {"document_id": "d", "path": "b", "role": "REFERENCE", "node_ids": []},
        ]
        with self.assertRaises(NodeGraphError):
            document_graph(docs, set())


class ComputeViewsTests(unittest.TestCase):
    def _graph(self):
        graph = unify_seed_graph(_seed())
        graph["nodes"].append(
            {"node_id": "future", "kind": "FEATURE", "state": "PROPOSED",
             "title": "Future", "refs": []}
        )
        return graph

    def test_all_views_present(self):
        graph = self._graph()
        views = {v["view"] for v in compute_views(graph["nodes"], graph["edges"])}
        self.assertEqual(views, set(VIEW_NAMES))

    def test_structural_view_excludes_functional_and_proposed(self):
        graph = self._graph()
        views = {v["view"]: v for v in compute_views(graph["nodes"], graph["edges"])}
        structural = set(views["structural"]["node_ids"])
        self.assertEqual(structural, {"server", "pkg"})

    def test_galaxy_view_has_features_not_structure(self):
        graph = self._graph()
        views = {v["view"]: v for v in compute_views(graph["nodes"], graph["edges"])}
        galaxy = set(views["galaxy"]["node_ids"])
        self.assertIn("future", galaxy)  # PROPOSED feature shows in galaxy
        self.assertNotIn("server", galaxy)
        self.assertNotIn("pkg", galaxy)

    def test_kanban_excludes_proposed(self):
        graph = self._graph()
        views = {v["view"]: v for v in compute_views(graph["nodes"], graph["edges"])}
        self.assertNotIn("future", views["kanban"]["node_ids"])

    def test_functional_view_keeps_its_edges(self):
        graph = self._graph()
        views = {v["view"]: v for v in compute_views(graph["nodes"], graph["edges"])}
        self.assertIn("e1", views["functional"]["edge_ids"])
        self.assertIn("e2", views["functional"]["edge_ids"])

    def test_knowledge_view_pulls_document_nodes(self):
        graph = unify_seed_graph(_seed())
        doc = document_graph(
            [{"document_id": "spec", "path": "p", "role": "SPECIFICATION",
              "node_ids": ["structure"], "sha256": "x"}],
            {n["node_id"] for n in graph["nodes"]},
        )
        nodes = graph["nodes"] + doc["nodes"]
        edges = graph["edges"] + doc["edges"]
        views = {v["view"]: v for v in compute_views(nodes, edges)}
        knowledge = set(views["knowledge"]["node_ids"])
        self.assertIn("doc:spec", knowledge)
        self.assertIn("structure", knowledge)  # endpoint pulled in


if __name__ == "__main__":
    unittest.main()
