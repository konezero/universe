from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.connection import UniverseError  # noqa: E402
from universe_app.feature_node_proposal import build_feature_node_proposals  # noqa: E402
from universe_app.memory_ownership import (  # noqa: E402
    UNASSIGNED,
    build_project_catalog,
    classify_candidate_ownership,
    resolve_registered_project,
    resolve_source_ownership,
)
from universe_app.review_inbox_next_work import build_review_inbox_next_work  # noqa: E402
from universe_memory import synthesize_memory_candidates  # noqa: E402
from universe_server import UniverseStore  # noqa: E402


class MemoryOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.career_root = root / "career"
        self.app_root = root / "app"
        self.other_root = root / "other"
        self.legacy_root = root / "legacy"
        self.private_root = root / "universe-private"
        self.projects = [
            {
                "project_id": "career",
                "project_root": str(self.career_root),
                "metadata": {"legacy_project_ids": ["ai-career"]},
            },
            {"project_id": "APP", "project_root": str(self.app_root), "metadata": {}},
            {"project_id": "OTHER", "project_root": str(self.other_root), "metadata": {}},
            {
                "project_id": "ai-career",
                "project_root": str(self.legacy_root),
                "metadata": {
                    "visibility": "MIGRATED_LEGACY",
                    "migrated_to_project_id": "career",
                },
            },
            {
                "project_id": "universe-private",
                "project_root": str(self.private_root),
                "metadata": {"network_role": "CONTAINER"},
            },
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_catalog_resolves_aliases_and_excludes_non_owner_roots(self) -> None:
        catalog = build_project_catalog(self.projects)

        self.assertEqual("career", resolve_registered_project("ai-career", catalog))
        self.assertEqual("career", resolve_registered_project(str(self.legacy_root), catalog))
        self.assertIsNone(resolve_registered_project("app-feature-folder", catalog))
        self.assertNotIn("ai-career", catalog["owner_project_ids"])
        self.assertNotIn("universe-private", catalog["owner_project_ids"])

    def test_source_scope_and_workspace_conflict_is_held(self) -> None:
        catalog = build_project_catalog(self.projects)

        scoped = resolve_source_ownership(
            {}, catalog, project_id="APP"
        )
        self.assertEqual("APP", scoped["owner_project_id"])
        self.assertEqual("ASSIGNED", scoped["ownership_state"])

        workspace = resolve_source_ownership(
            {"workspace": str(self.other_root)}, catalog
        )
        self.assertEqual("OTHER", workspace["owner_project_id"])
        self.assertEqual("ASSIGNED", workspace["ownership_state"])

        conflict = resolve_source_ownership(
            {"workspace": str(self.other_root)}, catalog, project_id="APP"
        )
        self.assertEqual(UNASSIGNED, conflict["owner_project_id"])
        self.assertEqual("CONFLICTED", conflict["ownership_state"])
        self.assertEqual("OTHER", conflict["proposed_owner_project_id"])

        private = resolve_source_ownership(
            {"workspace": str(self.private_root)}, catalog
        )
        self.assertEqual(UNASSIGNED, private["owner_project_id"])
        self.assertEqual("UNASSIGNED", private["ownership_state"])

    def test_candidate_classification_separates_origin_and_owner(self) -> None:
        catalog = build_project_catalog(self.projects)
        base = {
            "candidate_id": "candidate-1",
            "candidate_digest": "digest-1",
            "revision": 1,
            "project_id": "APP",
            "summary": "A durable implementation idea",
        }

        unresolved = classify_candidate_ownership(base, catalog)
        self.assertEqual("APP", unresolved["origin_project_id"])
        self.assertEqual(UNASSIGNED, unresolved["owner_project_id"])
        self.assertEqual("UNASSIGNED", unresolved["ownership_state"])

        other = classify_candidate_ownership(
            {
                **base,
                "source_review": {
                    "status": "CURRENT",
                    "evidence": [{"root_project_id": "OTHER"}],
                },
            },
            catalog,
        )
        self.assertEqual("OTHER", other["proposed_owner_project_id"])
        self.assertEqual("PROPOSED", other["ownership_state"])
        self.assertEqual(UNASSIGNED, other["owner_project_id"])

        same = classify_candidate_ownership(
            {
                **base,
                "source_review": {
                    "status": "CURRENT",
                    "evidence": [{"root_project_id": "APP"}],
                },
            },
            catalog,
        )
        self.assertEqual("APP", same["owner_project_id"])
        self.assertEqual("ASSIGNED", same["ownership_state"])

        noise = classify_candidate_ownership(
            {**base, "summary": "test-only response fixture"}, catalog
        )
        self.assertEqual("NOISE", noise["judgment"])

        history = classify_candidate_ownership(
            {**base, "summary": "completed todo historical record"}, catalog
        )
        self.assertEqual("PAST_HISTORY", history["judgment"])

    def test_store_preserves_source_and_exposes_cross_project_owner(self) -> None:
        store = UniverseStore(Path(self.temp.name) / "universe.sqlite3")
        for project_id, project_root in (("APP", self.app_root), ("OTHER", self.other_root)):
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / "REPOSITORY_MANIFEST.md").write_text(
                f"# {project_id}\n", encoding="utf-8"
            )
            store.register_project(
                {"project_id": project_id, "project_root": str(project_root)}
            )

        candidate, created = store.create_memory_candidate(
            "APP",
            {
                "stage": "FAST_EXTRACT",
                "kind": "PRODUCT",
                "summary": "Cross-project product candidate",
                "ref_digests": ["a" * 64],
                "origin_project_id": "APP",
                "owner_project_id": "OTHER",
                "ownership_state": "ASSIGNED",
            },
        )
        self.assertTrue(created)
        self.assertEqual("OTHER", candidate["ownership"]["owner_project_id"])
        self.assertEqual([], store.list_owned_memory_candidates("APP"))
        self.assertEqual(
            [candidate["candidate_id"]],
            [item["candidate_id"] for item in store.list_owned_memory_candidates("OTHER")],
        )

        ownership = store.record_memory_candidate_ownership("APP")
        self.assertEqual(1, ownership["reviewed_count"])
        self.assertFalse(ownership["effects"]["auto_adoption"])
        self.assertTrue(ownership["source_preserved"])
        self.assertEqual(
            [candidate["candidate_id"]],
            [item["candidate_id"] for item in store.list_memory_candidate_ownership("OTHER")],
        )

    def test_provider_source_workspace_owner_and_conflict_are_enforced(self) -> None:
        store = UniverseStore(Path(self.temp.name) / "provider.sqlite3")
        for project_id, project_root in (("APP", self.app_root), ("OTHER", self.other_root)):
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / "REPOSITORY_MANIFEST.md").write_text(
                f"# {project_id}\n", encoding="utf-8"
            )
            store.register_project(
                {"project_id": project_id, "project_root": str(project_root)}
            )

        source_path = self.app_root / "rollout-ownership.jsonl"
        source_path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "provider-session-1", "cwd": str(self.app_root)},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        source = store.register_provider_session_source(
            {
                "provider": "CODEX",
                "provider_session_id": "provider-session-1",
                "source_path": str(source_path),
                "source_kind": "CODEX_ROLLOUT_JSONL",
                "source_version": "v1",
            }
        )
        self.assertEqual("APP", source["owner_project_id"])
        self.assertEqual("ASSIGNED", source["ownership_state"])

        with self.assertRaises(UniverseError) as caught:
            store.register_provider_session_source(
                {
                    "provider": "CODEX",
                    "provider_session_id": "provider-session-1",
                    "source_path": str(source_path),
                    "source_kind": "CODEX_ROLLOUT_JSONL",
                    "source_version": "v1",
                    "project_id": "OTHER",
                }
            )
        self.assertEqual("SOURCE_OWNERSHIP_CONFLICT", caught.exception.code)

    def test_unresolved_candidates_are_excluded_from_downstream_projections(self) -> None:
        assigned = {
            "candidate_id": "candidate-app",
            "kind": "PRODUCT",
            "state": "START_PRODUCT_DESIGN",
            "summary": "APP semantic editor product",
            "ownership": {"ownership_state": "ASSIGNED", "owner_project_id": "APP"},
        }
        other = {
            "candidate_id": "candidate-other",
            "kind": "PRODUCT",
            "state": "START_PRODUCT_DESIGN",
            "summary": "OTHER semantic editor product",
            "ownership": {"ownership_state": "ASSIGNED", "owner_project_id": "OTHER"},
        }
        unresolved = {
            "candidate_id": "candidate-unassigned",
            "kind": "PRODUCT",
            "state": "START_PRODUCT_DESIGN",
            "summary": "Unassigned semantic editor product",
            "ownership": {"ownership_state": "UNASSIGNED", "owner_project_id": UNASSIGNED},
        }
        proposals = build_feature_node_proposals(
            project_id="APP",
            memories=[],
            memory_candidates=[assigned, other, unresolved],
            feature_nodes=[],
        )
        refs = {
            ref
            for proposal in proposals
            for ref in proposal["evidence_refs"]
        }
        self.assertIn("universe://memory-candidates/candidate-app", refs)
        self.assertNotIn("universe://memory-candidates/candidate-other", refs)
        self.assertNotIn("universe://memory-candidates/candidate-unassigned", refs)

        inbox = build_review_inbox_next_work(
            project_id="APP",
            memory_candidates=[assigned, other, unresolved],
            predictions=[],
            result_reviews=[],
            feature_nodes=[],
            todos=[],
        )
        inbox_refs = {
            ref for bundle in inbox["bundles"] for ref in bundle["item_refs"]
        }
        self.assertIn("universe://memory-candidates/candidate-app", inbox_refs)
        self.assertNotIn("universe://memory-candidates/candidate-other", inbox_refs)
        self.assertNotIn("universe://memory-candidates/candidate-unassigned", inbox_refs)

    def test_contentless_synthesis_is_not_a_durable_candidate(self) -> None:
        self.assertEqual(
            [],
            synthesize_memory_candidates(
                [
                    {
                        "candidate_id": "candidate-summary-only",
                        "candidate_digest": "d" * 64,
                        "project_id": "APP",
                        "stage": "FAST_EXTRACT",
                        "kind": "MEMORY",
                        "state": "REVIEW_REQUIRED",
                        "summary": "derived from 2 Memory candidates",
                        "relations": [],
                    }
                ],
                kinds=["IDEA"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
