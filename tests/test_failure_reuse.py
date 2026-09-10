from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_memory import (
    MemoryError,
    consolidate_memory_candidates,
    normalize_memory_candidate,
    synthesize_memory_candidates,
)
from universe_server import UniverseStore, create_server
from universe_app.failure_reuse import FailureReuseError, normalize_failure_query


def case_payload() -> dict:
    return json.loads(
        (ROOT / "docs/examples/goal-completion-failure.json").read_text(
            encoding="utf-8"
        )
    )


def query_payload() -> dict:
    failure = case_payload()["failure"]
    return {
        **{key: failure[key] for key in ("component", "operation", "error_code")},
        "cause_key": failure["cause"]["key"],
        "context": failure["remedy"]["applicability"],
    }


def reuse_payload(candidate: dict) -> dict:
    return {
        "reuse_ref": "http-storage-reuse-fixture-001",
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": candidate["candidate_digest"],
        "query": query_payload(),
        "outcome": "RESOLVED",
        "validation": {"plane": "http_storage", "status": "PASS"},
        "evidence_refs": ["test-run://failure-reuse/contract-fixture"],
        "limitations": ["Fixture evidence is not resident deployment proof."],
    }


class FailureKnowledgeContractTests(unittest.TestCase):
    def test_case_identity_and_digest_preserve_validation_limits(self) -> None:
        candidate = normalize_memory_candidate(case_payload(), project_id="TEST")
        self.assertEqual(candidate, normalize_memory_candidate(candidate))
        changed = case_payload()
        changed["failure"]["limitations"].append("Additional bounded limit")
        other = normalize_memory_candidate(changed, project_id="TEST")
        self.assertEqual(candidate["candidate_id"], other["candidate_id"])
        self.assertNotEqual(candidate["candidate_digest"], other["candidate_digest"])
        self.assertEqual("REVIEW_REQUIRED", candidate["state"])
        self.assertEqual(
            "RETROSPECTIVE_OBSERVATION", candidate["failure"]["observation_kind"]
        )
        self.assertFalse(candidate["effects"]["auto_adoption"])

    def test_validation_raw_input_and_secret_values_fail_closed(self) -> None:
        invalid = []
        payload = case_payload()
        payload["failure"]["validation"][0]["evidence_refs"] = []
        invalid.append(payload)
        payload = case_payload()
        payload["failure"]["cause"]["raw_transcript"] = "do not retain"
        invalid.append(payload)
        payload = case_payload()
        payload["failure"]["remedy"]["summary"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        invalid.append(payload)
        payload = case_payload()
        payload["failure"]["evidence_refs"] = [
            "https://user:password@example.com/private"
        ]
        invalid.append(payload)
        payload = case_payload()
        payload["failure"]["validation"].append(payload["failure"]["validation"][0])
        invalid.append(payload)
        payload = case_payload()
        payload["kind"] = "PRODUCT"
        invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(MemoryError):
                    normalize_memory_candidate(payload, project_id="TEST")

    def test_pipeline_does_not_merge_different_causes_or_synthesize_products(
        self,
    ) -> None:
        first = normalize_memory_candidate(case_payload(), project_id="TEST")
        payload = case_payload()
        payload["failure"]["failure_ref"] = "different-incident"
        payload["failure"]["cause"]["key"] = "different-root-cause"
        second = normalize_memory_candidate(payload, project_id="TEST")
        result = consolidate_memory_candidates([first, first, second])
        self.assertEqual(
            sorted([first, second], key=lambda item: item["candidate_id"]), result
        )
        self.assertEqual([], synthesize_memory_candidates(result))

    def test_query_requires_exact_structured_dimensions(self) -> None:
        for query in (
            {},
            {**query_payload(), "limit": True},
            {**query_payload(), "context": []},
            {**query_payload(), "authority": "APPROVED"},
        ):
            with self.assertRaises(FailureReuseError):
                normalize_failure_query(query)

    def test_malformed_or_oversized_evidence_is_rejected(self) -> None:
        payload = case_payload()
        payload["failure"]["evidence_refs"] = ["https://[broken"]
        with self.assertRaises(MemoryError) as caught:
            normalize_memory_candidate(payload, project_id="TEST")
        self.assertEqual("FAILURE_REUSE_EVIDENCE_REF_INVALID", caught.exception.code)
        payload = case_payload()
        refs = [f"repo://universe/{index}/" + "x" * 500 for index in range(16)]
        payload["failure"]["evidence_refs"] = refs
        payload["failure"]["validation"][0]["evidence_refs"] = refs
        with self.assertRaises(MemoryError) as caught:
            normalize_memory_candidate(payload, project_id="TEST")
        self.assertEqual("FAILURE_KNOWLEDGE_TOO_LARGE", caught.exception.code)


class FailureReuseHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = create_server(
            database_path=self.root / "universe.sqlite3",
            token="failure-test-token",
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
        )
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        for project in ("TEST", "OTHER"):
            path = self.root / project
            path.mkdir()
            (path / "REPOSITORY_MANIFEST.md").write_text(
                f"# {project}\n", encoding="utf-8"
            )
            self.server.store.register_project(
                {"project_id": project, "project_root": str(path)}
            )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.temp.cleanup()

    def request(
        self, suffix: str, payload: dict, *, project: str = "TEST", remote: bool = False
    ):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer failure-test-token",
        }
        if remote:
            headers["X-Universe-Access-Surface"] = "REMOTE_BROWSER"
        request = Request(
            self.endpoint + f"/v1/projects/{project}" + suffix,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def register_case(self) -> dict:
        status, result = self.request("/memory-candidates", case_payload())
        self.assertEqual(201, status, result)
        return result["candidate"]

    def test_http_recall_feedback_replay_and_reopen(self) -> None:
        candidate = self.register_case()
        status, replay = self.request("/memory-candidates", case_payload())
        self.assertEqual(200, status, replay)
        self.assertEqual(candidate["candidate_id"], replay["candidate"]["candidate_id"])
        status, recalled = self.request("/failure-reuse/query", query_payload())
        self.assertEqual(200, status, recalled)
        hit = recalled["matches"][0]
        self.assertEqual(candidate["failure"], hit["failure"])
        self.assertEqual("CONTEXT_MATCHED", hit["match"]["applicability"])
        self.assertEqual("CANDIDATE_ONLY", recalled["policy"])
        self.assertFalse(recalled["effects"]["automatic_patch"])
        status, recorded = self.request(
            "/failure-reuse/observations", reuse_payload(candidate)
        )
        self.assertEqual(201, status, recorded)
        status, replay = self.request(
            "/failure-reuse/observations", reuse_payload(candidate)
        )
        self.assertEqual(200, status, replay)
        self.assertEqual(recorded["observation"], replay["observation"])
        reopened = UniverseStore(self.server.store.database_path)
        recalled = reopened.recall_failure_evidence("TEST", query_payload())
        hit = recalled["matches"][0]
        self.assertEqual({"RESOLVED": 1}, hit["reuse_outcomes"])
        self.assertEqual(
            "ALL_RECORDED_CONTEXTS_AND_VALIDATION_PLANES", hit["reuse_outcomes_scope"]
        )
        self.assertEqual(candidate["failure"], hit["failure"])
        planes = {
            item["plane"]: item["status"] for item in hit["failure"]["validation"]
        }
        self.assertEqual("NOT_RUN", planes["resident_deployment"])
        self.assertEqual([], reopened.list_project_memories("TEST"))
        self.assertEqual([], reopened.list_project_goals("TEST"))
        request = Request(
            self.endpoint
            + "/v1/projects/TEST/failure-reuse/observations?candidate_id="
            + candidate["candidate_id"]
        )
        with urlopen(request, timeout=5) as response:
            evidence = json.load(response)
        self.assertEqual(recorded["observation"], evidence["observations"][0])

    def test_mismatched_cause_context_signature_or_project_never_links(self) -> None:
        candidate = self.register_case()
        variants = [
            {**query_payload(), "cause_key": "another-cause"},
            {**query_payload(), "component": "another-component"},
            {**query_payload(), "error_code": "another-error"},
            {**query_payload(), "context": {"completion_route": "GOAL_ONLY"}},
        ]
        for query in variants:
            with self.subTest(query=query):
                status, result = self.request("/failure-reuse/query", query)
                self.assertEqual(200, status, result)
                self.assertEqual([], result["matches"])
                status, result = self.request(
                    "/failure-reuse/observations",
                    {**reuse_payload(candidate), "query": query},
                )
                self.assertEqual(409, status, result)
                self.assertEqual("FAILURE_REUSE_MATCH_REQUIRED", result["error_code"])
        status, result = self.request(
            "/failure-reuse/query", query_payload(), project="OTHER"
        )
        self.assertEqual([], result["matches"])
        status, result = self.request(
            "/failure-reuse/observations", reuse_payload(candidate), project="OTHER"
        )
        self.assertEqual(404, status, result)

    def test_missing_cause_or_context_stays_unconfirmed(self) -> None:
        candidate = self.register_case()
        query = {
            key: value
            for key, value in query_payload().items()
            if key not in {"cause_key", "context"}
        }
        status, result = self.request("/failure-reuse/query", query)
        match = result["matches"][0]["match"]
        self.assertTrue(match["cause_confirmation_required"])
        self.assertEqual("CONTEXT_REQUIRED", match["applicability"])
        status, result = self.request(
            "/failure-reuse/observations", {**reuse_payload(candidate), "query": query}
        )
        self.assertEqual(409, status, result)
        self.assertEqual("FAILURE_REUSE_MATCH_UNCONFIRMED", result["error_code"])

    def test_conflicting_feedback_or_candidate_does_not_overwrite(self) -> None:
        candidate = self.register_case()
        changed = case_payload()
        changed["failure"]["cause"]["summary"] = "Different claimed cause"
        status, result = self.request("/memory-candidates", changed)
        self.assertEqual(409, status, result)
        status, result = self.request(
            "/failure-reuse/observations", reuse_payload(candidate)
        )
        self.assertEqual(201, status, result)
        changed = reuse_payload(candidate)
        changed.update(
            outcome="NOT_RESOLVED",
            validation={"plane": "http_storage", "status": "FAIL"},
        )
        status, result = self.request("/failure-reuse/observations", changed)
        self.assertEqual(409, status, result)
        self.assertEqual("FAILURE_REUSE_CONFLICT", result["error_code"])
        status, result = self.request(
            "/failure-reuse/observations",
            {
                **reuse_payload(candidate),
                "reuse_ref": "stale",
                "candidate_digest": "a" * 64,
            },
        )
        self.assertEqual(409, status, result)

    def test_rejected_candidates_are_not_recalled_or_used(self) -> None:
        candidate = self.register_case()
        status, result = self.request(
            "/memory-candidates/review",
            {"candidate_id": candidate["candidate_id"], "decision": "IGNORE"},
        )
        self.assertEqual(200, status, result)
        status, result = self.request("/failure-reuse/query", query_payload())
        self.assertEqual([], result["matches"])
        status, result = self.request(
            "/failure-reuse/observations", reuse_payload(candidate)
        )
        self.assertEqual(409, status, result)

    def test_validation_gate_and_local_operator_boundary(self) -> None:
        candidate = self.register_case()
        invalid = reuse_payload(candidate)
        invalid["validation"]["status"] = "NOT_RUN"
        status, result = self.request("/failure-reuse/observations", invalid)
        self.assertEqual(400, status, result)
        status, result = self.request(
            "/failure-reuse/observations", reuse_payload(candidate), remote=True
        )
        self.assertEqual(403, status, result)
        self.assertEqual("LOCAL_OPERATOR_REQUIRED", result["error_code"])

    def test_unsuccessful_or_unrun_reuse_retains_its_own_evidence(self) -> None:
        candidate = self.register_case()
        for outcome, validation_status in (
            ("NOT_RESOLVED", "FAIL"),
            ("UNKNOWN", "NOT_RUN"),
        ):
            payload = reuse_payload(candidate)
            payload.update(
                reuse_ref="attempt-" + outcome.lower(),
                outcome=outcome,
                validation={"plane": "http_storage", "status": validation_status},
                evidence_refs=[]
                if validation_status == "NOT_RUN"
                else payload["evidence_refs"],
            )
            status, result = self.request("/failure-reuse/observations", payload)
            self.assertEqual(201, status, result)
            self.assertEqual(
                validation_status, result["observation"]["validation"]["status"]
            )
        recalled = self.server.store.recall_failure_evidence("TEST", query_payload())
        hit = recalled["matches"][0]
        self.assertEqual({"NOT_RESOLVED": 1, "UNKNOWN": 1}, hit["reuse_outcomes"])
        self.assertEqual(candidate["failure"], hit["failure"])

    def test_superseding_evidence_excludes_old_case_without_rewriting_it(self) -> None:
        candidate = self.register_case()
        payload = case_payload()
        payload["failure"]["failure_ref"] = "replacement-case"
        payload["relations"] = [
            {"relation": "SUPERSEDES", "candidate_id": candidate["candidate_id"]}
        ]
        status, replacement = self.request("/memory-candidates", payload)
        self.assertEqual(201, status, replacement)
        status, recalled = self.request("/failure-reuse/query", query_payload())
        self.assertEqual(
            [replacement["candidate"]["candidate_id"]],
            [item["candidate_id"] for item in recalled["matches"]],
        )
        status, result = self.request(
            "/failure-reuse/observations", reuse_payload(candidate)
        )
        self.assertEqual(409, status, result)
        self.assertEqual("FAILURE_REUSE_CANDIDATE_STALE", result["error_code"])

    def test_new_case_cannot_claim_prior_review(self) -> None:
        status, result = self.request(
            "/memory-candidates", {**case_payload(), "state": "KEEP"}
        )
        self.assertEqual(400, status, result)
        self.assertEqual("FAILURE_KNOWLEDGE_REVIEW_REQUIRED", result["error_code"])

    def test_concurrent_registration_and_feedback_are_exactly_once(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _: self.request("/memory-candidates", case_payload()),
                    range(4),
                )
            )
        self.assertEqual([200, 200, 200, 201], sorted(status for status, _ in results))
        candidate = results[0][1]["candidate"]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _: self.request(
                        "/failure-reuse/observations", reuse_payload(candidate)
                    ),
                    range(4),
                )
            )
        self.assertEqual([200, 200, 200, 201], sorted(status for status, _ in results))
        recalled = self.server.store.recall_failure_evidence("TEST", query_payload())
        self.assertEqual({"RESOLVED": 1}, recalled["matches"][0]["reuse_outcomes"])

    def test_explicit_retrieval_context_keeps_candidates_separate(self) -> None:
        candidate = self.register_case()
        context = self.server.store.build_project_llm_retrieval_context(
            "TEST",
            query="Goal failure",
            failure=query_payload(),
        )
        self.assertEqual([], context["memory"]["hits"])
        self.assertEqual(
            candidate["candidate_id"],
            context["failure_reuse"]["matches"][0]["candidate_id"],
        )
        self.assertEqual("CANDIDATE_ONLY", context["failure_reuse"]["policy"])
        ordinary = self.server.store.build_project_llm_retrieval_context(
            "TEST", query="ordinary work"
        )
        self.assertNotIn("failure_reuse", ordinary)


if __name__ == "__main__":
    unittest.main()
