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
    graft_feature_nodes,
    graft_knowledge_nodes,
    graft_work_rollup,
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


class GraftKnowledgeTests(unittest.TestCase):
    def _projection(self):
        graph = unify_seed_graph(_seed())
        return {
            "unified_graph": graph,
            "views": compute_views(graph["nodes"], graph["edges"]),
        }

    def test_decision_and_memory_nodes_added_and_counted(self):
        projection = self._projection()
        memories = [
            {"memory_id": "m1", "state": "DECISION_NOTE", "title": "D1",
             "node_ref": "structure"},
            {"memory_id": "m2", "state": "OBSERVED", "title": "O1",
             "node_ref": "unknown-root"},
        ]
        out = graft_knowledge_nodes(projection, memories)
        by_id = {n["node_id"]: n for n in out["unified_graph"]["nodes"]}
        self.assertEqual(by_id["mem:m1"]["kind"], "DECISION")
        self.assertEqual(by_id["mem:m2"]["kind"], "MEMORY")
        self.assertEqual(out["knowledge_grafted"],
                         {"decisions": 1, "memories": 1, "linked": 1})

    def test_edge_only_when_node_ref_is_a_real_node(self):
        projection = self._projection()
        out = graft_knowledge_nodes(projection, [
            {"memory_id": "m1", "state": "DECISION_NOTE", "node_ref": "structure"},
            {"memory_id": "m2", "state": "OBSERVED", "node_ref": "universe"},
        ])
        froms = {e["from_node"] for e in out["unified_graph"]["edges"]}
        self.assertIn("mem:m1", froms)
        self.assertNotIn("mem:m2", froms)  # node_ref "universe" is not a graph node

    def test_knowledge_view_includes_grafted_nodes(self):
        projection = self._projection()
        out = graft_knowledge_nodes(projection, [
            {"memory_id": "m1", "state": "OBSERVED", "node_ref": "universe"},
        ])
        views = {v["view"]: v for v in out["views"]}
        self.assertIn("mem:m1", views["knowledge"]["node_ids"])
        self.assertNotIn("mem:m1", views["kanban"]["node_ids"])

    def test_no_unified_graph_is_safe(self):
        out = graft_knowledge_nodes({"views": []}, [{"memory_id": "m1"}])
        self.assertEqual(out["knowledge_grafted"],
                         {"decisions": 0, "memories": 0, "linked": 0})


class GraftFeatureTests(unittest.TestCase):
    def _projection_with_product_and_memory(self):
        graph = unify_seed_graph(_seed())
        graph["nodes"].append(
            {"node_id": "universe", "kind": "PRODUCT", "state": "ADOPTED",
             "title": "Universe", "refs": []}
        )
        proj = {"unified_graph": graph,
                "views": compute_views(graph["nodes"], graph["edges"])}
        return graft_knowledge_nodes(proj, [
            {"memory_id": "m9", "state": "OBSERVED", "node_ref": "structure"},
        ])

    def test_features_added_with_state_and_product_edge(self):
        proj = self._projection_with_product_and_memory()
        out = graft_feature_nodes(proj, [
            {"feature_id": "f1", "intent_text": "Adopted thing", "state": "ADOPTED",
             "evidence_refs": []},
            {"feature_id": "f2", "intent_text": "Exploring thing", "state": "EXPLORING",
             "evidence_refs": ["universe://memories/m9"]},
        ])
        by_id = {n["node_id"]: n for n in out["unified_graph"]["nodes"]}
        self.assertEqual(by_id["feat:f1"]["kind"], "FEATURE")
        self.assertEqual(by_id["feat:f1"]["state"], "ADOPTED")
        self.assertEqual(by_id["feat:f2"]["state"], "PROPOSED")
        self.assertEqual(out["feature_grafted"], {"adopted": 1, "proposed": 1})
        rels = {(e["from_node"], e["to_node"], e["relation"])
                for e in out["unified_graph"]["edges"]}
        self.assertIn(("universe", "feat:f1", "CONTAINS"), rels)
        self.assertIn(("feat:f2", "mem:m9", "DERIVED_FROM"), rels)

    def test_no_unified_graph_is_safe(self):
        out = graft_feature_nodes({"views": []}, [{"feature_id": "f1"}])
        self.assertEqual(out["feature_grafted"], {"adopted": 0, "proposed": 0})


class GraftWorkRollupTests(unittest.TestCase):
    def _projection_with_feature(self):
        graph = unify_seed_graph(_seed())
        graph["nodes"].append(
            {"node_id": "universe", "kind": "PRODUCT", "state": "ADOPTED",
             "title": "Universe", "refs": []}
        )
        proj = {"unified_graph": graph, "views": []}
        return graft_feature_nodes(proj, [
            {"feature_id": "feature_abc", "intent_text": "A feature",
             "state": "EXPLORING", "evidence_refs": []},
        ])

    def test_rollup_by_lane_and_ships(self):
        proj = self._projection_with_feature()
        goals = [{
            "goal_id": "g1", "node_ref": "feature_abc", "todos": [
                {"todo_id": "t1", "state": "IN_PROGRESS", "node_ref": "feature_abc",
                 "task_frame_id": "tf1", "title": "wip"},
                {"todo_id": "t2", "state": "DONE", "node_ref": "feature_abc"},
                {"todo_id": "t3", "state": "BACKLOG", "node_ref": "structure"},
            ],
            "milestones": [],
        }]
        out = graft_work_rollup(proj, goals)
        by_id = {n["node_id"]: n for n in out["unified_graph"]["nodes"]}
        self.assertEqual(by_id["feat:feature_abc"]["work"]["executing"], 1)
        self.assertEqual(by_id["feat:feature_abc"]["work"]["done"], 1)
        self.assertEqual(by_id["feat:feature_abc"]["work"]["total"], 2)
        self.assertEqual(by_id["structure"]["work"]["planned"], 1)
        self.assertEqual(len(out["ships"]), 1)
        self.assertEqual(out["ships"][0]["target_node"], "feat:feature_abc")
        self.assertEqual(out["ships"][0]["state"], "EXECUTING")

    def test_no_unified_graph_is_safe(self):
        out = graft_work_rollup({"views": []}, [{"goal_id": "g"}])
        self.assertEqual(out["ships"], [])


if __name__ == "__main__":
    unittest.main()
