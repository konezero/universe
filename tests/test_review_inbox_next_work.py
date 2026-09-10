from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.feature_node_proposal import build_feature_node_proposals  # noqa: E402
from universe_app.review_inbox_next_work import (  # noqa: E402
    MEMORY_CANDIDATE_PAGE_LIMIT,
    build_review_inbox_next_work,
)
from universe_server import create_server  # noqa: E402


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


class ReviewInboxNextWorkTests(unittest.TestCase):
    def test_start_product_design_memory_links_existing_todo(self) -> None:
        inbox = build_review_inbox_next_work(
            project_id="universe",
            memory_candidates=[
                {
                    "candidate_id": "candidate-review-inbox",
                    "kind": "MEMORY",
                    "state": "START_PRODUCT_DESIGN",
                    "summary": "Connect Memory RAG review inbox to next development work",
                    "updated_at": "2026-09-10T00:00:00Z",
                }
            ],
            predictions=[],
            result_reviews=[],
            feature_nodes=[
                {
                    "feature_id": "feature_review_inbox",
                    "title": "Memory RAG review inbox",
                    "intent_text": "Connect review candidates to development work",
                }
            ],
            todos=[
                {
                    "todo_id": "todo_review_inbox",
                    "title": "메모·RAG 검토함을 다음 개발 작업으로 연결",
                    "detail": "Connect review inbox to existing TODO",
                    "state": "READY",
                    "node_ref": "feature_review_inbox",
                }
            ],
            now=NOW,
        )
        self.assertEqual("universe.review-inbox-next-work.v1", inbox["schema"])
        self.assertFalse(inbox["effects"]["auto_adopt"])
        self.assertFalse(inbox["effects"]["goal_started"])
        self.assertEqual("USER_REVIEW_ONLY", inbox["next_operation"])
        self.assertEqual(1, len(inbox["bundles"]))
        bundle = inbox["bundles"][0]
        self.assertEqual("OPEN_EXISTING_TODO", bundle["next_action"])
        self.assertEqual("todo_review_inbox", bundle["related_todo_id"])
        self.assertEqual("feature_review_inbox", bundle["related_feature_id"])
        self.assertIn("RELATED_FEATURE", bundle["labels"])

    def test_test_only_and_generic_summaries_are_not_product_intent(self) -> None:
        inbox = build_review_inbox_next_work(
            project_id="universe",
            memory_candidates=[
                {
                    "candidate_id": "candidate-test-only",
                    "kind": "PRODUCT",
                    "state": "START_PRODUCT_DESIGN",
                    "summary": "test-only respond with SESSION_READY",
                    "updated_at": "2026-09-10T00:00:00Z",
                },
                {
                    "candidate_id": "candidate-generic",
                    "kind": "IDEA",
                    "state": "EXPLORE",
                    "summary": "generic summary",
                    "updated_at": "2026-09-10T00:00:00Z",
                },
            ],
            predictions=[],
            result_reviews=[],
            feature_nodes=[],
            todos=[],
            now=NOW,
        )
        classifications = {
            ref: bundle["classifications"]
            for bundle in inbox["bundles"]
            for ref in bundle["item_refs"]
        }
        self.assertIn("TEST_ONLY", classifications["universe://memory-candidates/candidate-test-only"])
        self.assertIn("GENERIC", classifications["universe://memory-candidates/candidate-generic"])
        self.assertTrue(
            all(bundle["next_action"] != "DISCOVER_FEATURE_PROPOSAL" for bundle in inbox["bundles"])
        )

    def test_counts_report_api_page_limits(self) -> None:
        candidates = [
            {
                "candidate_id": f"candidate-{index}",
                "kind": "MEMORY",
                "state": "REVIEW_REQUIRED",
                "summary": f"Review candidate number {index} semantic editor",
                "updated_at": "2026-09-10T00:00:00Z",
            }
            for index in range(MEMORY_CANDIDATE_PAGE_LIMIT)
        ]
        inbox = build_review_inbox_next_work(
            project_id="universe",
            memory_candidates=candidates,
            predictions=[{"proposal_id": f"pred-{i}", "review_state": "PROPOSAL_ONLY", "suggestions": []} for i in range(50)],
            result_reviews=[{"candidate_id": f"result-{i}", "review_state": "PENDING_REVIEW", "sink_kind": "MEMORY", "outcome": f"done {i}"} for i in range(250)],
            feature_nodes=[],
            todos=[],
            now=NOW,
        )
        self.assertEqual(200, inbox["counts"]["memory_candidates"]["returned"])
        self.assertEqual(200, inbox["counts"]["memory_candidates"]["limit"])
        self.assertTrue(inbox["counts"]["memory_candidates"]["truncated"])
        self.assertTrue(inbox["counts"]["predictions"]["truncated"])
        self.assertTrue(inbox["counts"]["result_reviews"]["truncated"])

    def test_stale_conflict_and_duplicate_labels(self) -> None:
        stale_at = (NOW - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        inbox = build_review_inbox_next_work(
            project_id="universe",
            memory_candidates=[
                {
                    "candidate_id": "candidate-a",
                    "kind": "PRODUCT",
                    "state": "START_PRODUCT_DESIGN",
                    "summary": "Native semantic editor protocol",
                    "updated_at": stale_at,
                },
                {
                    "candidate_id": "candidate-b",
                    "kind": "IDEA",
                    "state": "CONFLICTED",
                    "summary": "Native semantic editor protocol revision",
                    "updated_at": "2026-09-10T00:00:00Z",
                    "relations": [{"kind": "CONFLICTS_WITH", "candidate_id": "candidate-a"}],
                },
            ],
            predictions=[],
            result_reviews=[],
            feature_nodes=[],
            todos=[],
            now=NOW,
        )
        self.assertEqual(1, len(inbox["bundles"]))
        labels = inbox["bundles"][0]["labels"]
        self.assertIn("DUPLICATE", labels)
        self.assertIn("STALE", labels)
        self.assertIn("CONFLICT", labels)

    def test_memory_start_product_design_is_compiler_evidence(self) -> None:
        proposals = build_feature_node_proposals(
            project_id="universe",
            memories=[],
            memory_candidates=[
                {
                    "candidate_id": "candidate-memory-design",
                    "kind": "MEMORY",
                    "state": "START_PRODUCT_DESIGN",
                    "summary": "Native semantic editor protocol for agents",
                }
            ],
            feature_nodes=[],
        )
        self.assertEqual(1, len(proposals))
        self.assertEqual(
            ["universe://memory-candidates/candidate-memory-design"],
            proposals[0]["evidence_refs"],
        )
        self.assertFalse(proposals[0]["effects"]["rag_adopted"])
        self.assertFalse(proposals[0]["effects"]["goal_created"])


class ReviewInboxApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token="review-inbox-test-token",
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            auto_start_goal_scheduler=False,
        )
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        (root / "TEST").mkdir()
        (root / "TEST" / "REPOSITORY_MANIFEST.md").write_text("# TEST\n", encoding="utf-8")
        self.server.store.register_project(
            {"project_id": "TEST", "project_root": str(root / "TEST")}
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None):
        data = None
        headers = {"Authorization": "Bearer review-inbox-test-token"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def test_work_loop_snapshot_includes_review_inbox(self) -> None:
        self.server.store.create_todo(
            {
                "scope_kind": "PROJECT",
                "project_id": "TEST",
                "title": "Native semantic editor protocol",
                "detail": "Existing development work",
                "priority": "P0",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            }
        )
        candidate, created = self.server.store.create_memory_candidate(
            "TEST",
            {
                "stage": "SYNTHESIZE",
                "kind": "PRODUCT",
                "summary": "Native semantic editor protocol for agents",
            },
        )
        self.assertTrue(created)
        status, snapshot = self.request("GET", "/v1/projects/TEST/work-loop")
        self.assertEqual(HTTPStatus.OK, status)
        inbox = snapshot["review_inbox"]
        self.assertEqual("universe.review-inbox-next-work.v1", inbox["schema"])
        self.assertEqual(200, inbox["counts"]["memory_candidates"]["limit"])
        self.assertEqual(50, inbox["counts"]["predictions"]["limit"])
        self.assertEqual(250, inbox["counts"]["result_reviews"]["limit"])
        self.assertFalse(inbox["effects"]["auto_adopt"])
        self.assertTrue(any(bundle["related_todo_id"] for bundle in inbox["bundles"]) or candidate)

    def test_start_product_design_review_returns_next_work(self) -> None:
        candidate, created = self.server.store.create_memory_candidate(
            "TEST",
            {
                "stage": "SYNTHESIZE",
                "kind": "PRODUCT",
                "summary": "Native semantic editor protocol for agents",
            },
        )
        self.assertTrue(created)
        status, payload = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate['candidate_id']}/review",
            {"decision": "START_PRODUCT_DESIGN"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("START_PRODUCT_DESIGN", payload["candidate"]["state"])
        self.assertEqual("USER_REVIEW_ONLY", payload["next_operation"])
        self.assertFalse(payload["effects"]["goal_created"])
        self.assertFalse(payload["effects"]["rag_adopted"])
        self.assertGreaterEqual(len(payload["feature_node_proposals"]["proposals"]), 1)
        self.assertIn("review_inbox", payload)
        keep, _ = self.server.store.create_memory_candidate(
            "TEST",
            {
                "stage": "FAST_EXTRACT",
                "kind": "MEMORY",
                "summary": "Keep this bounded project memory.",
            },
        )
        keep_status, keep_payload = self.request(
            "POST",
            f"/v1/memory-candidates/{keep['candidate_id']}/review",
            {"decision": "KEEP"},
        )
        self.assertEqual(HTTPStatus.OK, keep_status)
        self.assertNotIn("feature_node_proposals", keep_payload)


if __name__ == "__main__":
    unittest.main()
