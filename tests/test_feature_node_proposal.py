from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.feature_node_proposal import build_feature_node_proposals  # noqa: E402


class FeatureNodeProposalTests(unittest.TestCase):
    def test_intent_memory_produces_stable_review_only_proposal(self) -> None:
        memories = [
            {
                "memory_id": "memory-editor",
                "title": "Rust native AI semantic editor",
                "body": "This body is not copied into the proposal.",
                "state": "DECISION_NOTE",
            }
        ]
        first = build_feature_node_proposals(
            project_id="universe",
            memories=memories,
            memory_candidates=[],
            feature_nodes=[],
        )
        second = build_feature_node_proposals(
            project_id="universe",
            memories=memories,
            memory_candidates=[],
            feature_nodes=[],
        )
        self.assertEqual(first, second)
        self.assertEqual(1, len(first))
        proposal = first[0]
        self.assertEqual("NEW_FEATURE", proposal["proposal_kind"])
        self.assertEqual("PROPOSAL_ONLY", proposal["state"])
        self.assertEqual(["universe://memories/memory-editor"], proposal["evidence_refs"])
        self.assertNotIn("This body", proposal["intent_text"])
        self.assertFalse(proposal["effects"]["feature_node_created"])
        self.assertFalse(proposal["effects"]["goal_created"])
        self.assertFalse(proposal["effects"]["rag_adopted"])

    def test_observed_result_memory_is_not_product_intent(self) -> None:
        proposals = build_feature_node_proposals(
            project_id="universe",
            memories=[
                {
                    "memory_id": "memory-room-result",
                    "title": "Project Room result",
                    "body": "terminal output",
                    "state": "OBSERVED",
                }
            ],
            memory_candidates=[],
            feature_nodes=[],
        )
        self.assertEqual([], proposals)

    def test_matching_existing_feature_proposes_link(self) -> None:
        proposals = build_feature_node_proposals(
            project_id="universe",
            memories=[
                {
                    "memory_id": "memory-editor",
                    "title": "Rust native semantic editor protocol",
                    "state": "BRAINSTORM",
                }
            ],
            memory_candidates=[],
            feature_nodes=[
                {
                    "feature_id": "feature-editor",
                    "title": "Native semantic editor",
                    "intent_text": "Rust editor protocol for agents",
                }
            ],
        )
        self.assertEqual("LINK_EXISTING", proposals[0]["proposal_kind"])
        self.assertEqual("feature-editor", proposals[0]["target_node_ref"])

    def test_related_memory_and_product_candidate_cluster(self) -> None:
        proposals = build_feature_node_proposals(
            project_id="universe",
            memories=[
                {
                    "memory_id": "memory-editor",
                    "title": "Native semantic editor protocol",
                    "state": "QUESTION",
                }
            ],
            memory_candidates=[
                {
                    "candidate_id": "candidate-editor",
                    "kind": "PRODUCT",
                    "state": "START_PRODUCT_DESIGN",
                    "summary": "Semantic editor protocol for native agents",
                }
            ],
            feature_nodes=[],
        )
        self.assertEqual(1, len(proposals))
        self.assertEqual(2, len(proposals[0]["evidence_refs"]))


if __name__ == "__main__":
    unittest.main()
