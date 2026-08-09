from __future__ import annotations

import json
import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_memory import (  # noqa: E402
    propose_node_links,
    run_nightly_memory_rag_batch,
)
from universe_server import (  # noqa: E402
    create_server,
    normalize_skill_observation_candidate,
    universe_mode_contract,
)


class UniverseMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_root = root / "GCS"
        runtime = self.project_root / ".ai" / "runtime" / "project_instance"
        runtime.mkdir(parents=True)
        (self.project_root / ".ai" / "runtime" / "anchor_store").mkdir(parents=True)
        (self.project_root / ".ai" / "inbox" / "MASTER").mkdir(parents=True)
        (self.project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# GCS\n", encoding="utf-8"
        )
        (runtime / "mode_registry.json").write_text(
            json.dumps(
                {
                    "schema": "ai-career.mode-registry.v1",
                    "owner": "GCS",
                    "repository_kind": "PROJECT",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 1,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (runtime / "status.md").write_text("Status: READY\n", encoding="utf-8")
        self.token = "memory-test-token"
        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token=self.token,
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            mode_contract=universe_mode_contract(
                {
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 3,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                    },
                }
            ),
        )
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"
        import threading

        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.store = self.server.store
        self.store.register_project(
            {
                "project_id": "GCS",
                "project_root": str(self.project_root),
            }
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode())
        except HTTPError as error:
            return int(error.code), json.loads(error.read().decode())

    def test_create_list_and_link_memory(self) -> None:
        status, created = self.request(
            "POST",
            "/v1/projects/GCS/memories",
            {
                "title": "Order risk boundary note",
                "body": "Need to document order-risk functional node constraints",
                "state": "BRAINSTORM",
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        memory = created["memory"]
        self.assertEqual("UNLINKED", memory["link_state"])
        self.assertEqual("NONE", memory["effects"]["seed_write"])

        status, listed = self.request(
            "GET", "/v1/projects/GCS/memories?link_state=UNLINKED"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(listed["memories"]))

        status, linked = self.request(
            "POST",
            "/v1/projects/GCS/memories/link",
            {
                "memory_id": memory["memory_id"],
                "node_ref": "order-risk",
                "graph": "functional",
                "link_state": "LINKED",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("LINKED", linked["memory"]["link_state"])
        self.assertEqual("order-risk", linked["memory"]["node_ref"])

    def test_token_overlap_proposals(self) -> None:
        proposals = propose_node_links(
            memories=[
                {
                    "memory_id": "memory_1",
                    "link_state": "UNLINKED",
                    "title": "order risk",
                    "body": "order risk validation path",
                }
            ],
            nodes=[
                {"node_id": "order-risk", "label": "Order Risk", "kind": "system"},
                {"node_id": "market-data", "label": "Market Data", "kind": "system"},
            ],
        )
        self.assertTrue(proposals)
        self.assertEqual("order-risk", proposals[0]["node_ref"])
        self.assertEqual("NONE", proposals[0]["effects"]["seed_write"])


    def test_memory_maintain_batch_propose_and_apply_proposed(self) -> None:
        # Seed a node via projection-free path: just create memory and force nodes
        # through maintain against empty projection (no proposals) first.
        status, created = self.request(
            "POST",
            "/v1/projects/GCS/memories",
            {
                "title": "order risk note",
                "body": "order risk functional constraints for the order-risk node",
                "state": "OBSERVED",
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        memory_id = created["memory"]["memory_id"]

        status, maintain = self.request(
            "POST",
            "/v1/projects/GCS/memories/maintain",
            {"apply_proposals": False, "limit": 10},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROJECT_MEMORY_MAINTAIN_COMPLETED", maintain["status"])
        self.assertEqual("DETERMINISTIC_TOKEN_OVERLAP", maintain["batch_kind"])
        self.assertEqual("NOT_RUN", maintain["llm_batch"])
        self.assertFalse(maintain["apply_proposals"])
        self.assertEqual("NONE", maintain["effects"]["seed_write"])

        # Without projection nodes, proposals may be empty; unit helper still covers
        # selection. Apply path must remain safe (no crash).
        status, applied = self.request(
            "POST",
            "/v1/projects/GCS/memories/maintain",
            {"apply_proposals": True, "limit": 10, "per_memory": 1},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROJECT_MEMORY_MAINTAIN_COMPLETED", applied["status"])
        for item in applied.get("applied") or []:
            self.assertEqual("PROPOSED", item["link_state"])

        # Direct PROPOSED link still works for user confirm path.
        status, linked = self.request(
            "POST",
            "/v1/projects/GCS/memories/link",
            {
                "memory_id": memory_id,
                "node_ref": "order-risk",
                "graph": "functional",
                "link_state": "PROPOSED",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROPOSED", linked["memory"]["link_state"])

    def test_select_best_proposals_helper(self) -> None:
        from universe_memory import select_best_proposals

        selected = select_best_proposals(
            [
                {"memory_id": "m1", "node_ref": "b", "score": 2},
                {"memory_id": "m1", "node_ref": "a", "score": 3},
                {"memory_id": "m2", "node_ref": "c", "score": 1},
                {"memory_id": "m2", "node_ref": "d", "score": 0},
            ],
            per_memory=1,
            min_score=1,
        )
        self.assertEqual(2, len(selected))
        self.assertEqual("a", selected[0]["node_ref"])
        self.assertEqual("c", selected[1]["node_ref"])


    def test_heuristic_and_llm_maintain(self) -> None:
        from universe_memory import (
            filter_llm_proposals,
            propose_node_links_heuristic,
            merge_proposals,
        )

        memories = [
            {
                "memory_id": "memory_1",
                "link_state": "UNLINKED",
                "title": "order risk note",
                "body": "document order-risk constraints carefully",
            }
        ]
        nodes = [
            {"node_id": "order-risk", "label": "Order Risk", "kind": "system"},
            {"node_id": "market-data", "label": "Market Data", "kind": "system"},
        ]
        heuristic = propose_node_links_heuristic(memories=memories, nodes=nodes)
        self.assertTrue(heuristic)
        self.assertEqual("order-risk", heuristic[0]["node_ref"])
        self.assertEqual("HEURISTIC_WEIGHTED", heuristic[0]["proposal_kind"])

        llm = filter_llm_proposals(
            llm_proposals=[
                {
                    "memory_id": "memory_1",
                    "node_ref": "order-risk",
                    "graph": "functional",
                    "score": 9,
                    "reason": "nightly",
                    "proposal_kind": "LLM_BATCH",
                    "effects": {
                        "seed_write": "NONE",
                        "candidate": "NONE",
                        "authority": "NONE",
                    },
                },
                {
                    "memory_id": "memory_1",
                    "node_ref": "unknown-node",
                    "graph": "functional",
                    "score": 9,
                    "reason": "bad",
                    "proposal_kind": "LLM_BATCH",
                    "effects": {
                        "seed_write": "NONE",
                        "candidate": "NONE",
                        "authority": "NONE",
                    },
                },
            ],
            memories=memories,
            nodes=nodes,
        )
        self.assertEqual(1, len(llm))
        merged = merge_proposals(heuristic, llm)
        self.assertEqual("order-risk", merged[0]["node_ref"])

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/memories/maintain",
            {"apply_proposals": False, "scorer": "HEURISTIC", "limit": 10},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("HEURISTIC_WEIGHTED", result["batch_kind"])

        status, llm_run = self.request(
            "POST",
            "/v1/projects/GCS/memories/maintain",
            {
                "apply_proposals": False,
                "scorer": "LLM",
                "llm_proposals": [
                    {
                        "memory_id": "does-not-exist",
                        "node_ref": "order-risk",
                        "graph": "functional",
                        "score": 5,
                    }
                ],
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertIn(llm_run["llm_batch"], {
            "UNAVAILABLE_FALLBACK_DETERMINISTIC",
            "APPLIED_EXTERNAL_PROPOSALS",
        })

    def test_nightly_memory_rag_batch_redacts_provenance_and_calls_sink(self) -> None:
        sink_records: list[dict[str, Any]] = []
        result = run_nightly_memory_rag_batch(
            project_id="GCS",
            memory_loader=lambda: [
                {
                    "memory_id": "memory-secret",
                    "link_state": "UNLINKED",
                    "title": "order risk note",
                    "body": "private prompt and source command must never escape",
                }
            ],
            node_loader=lambda: [
                {"node_id": "order-risk", "label": "Order Risk", "kind": "system"}
            ],
            proposal_sink=lambda record: sink_records.append(dict(record)) or {
                "queued": True,
                "internal_path": "C:/private/queue.db",
            },
            source_ref="C:/private/source/repository",
            observed_at="2026-08-10T00:00:00Z",
        )
        self.assertEqual("MEMORY_RAG_PROPOSAL_BATCH_READY", result["status"])
        self.assertEqual(1, result["proposal_count"])
        self.assertEqual(1, len(sink_records))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("private/queue.db", serialized)
        self.assertNotIn("private/source/repository", serialized)
        self.assertEqual("NOT_RETAINED", result["provenance"]["raw_prompts"])
        self.assertEqual("NOT_RETAINED", result["provenance"]["raw_source"])
        self.assertEqual("NOT_RETAINED", result["provenance"]["raw_commands"])
        self.assertEqual(
            result["provenance"]["proposal_digest"],
            sink_records[0]["provenance"]["proposal_digest"],
        )

    def test_local_dogfood_skill_observation_fixture_enters_project_queue(self) -> None:
        fixture_path = ROOT / "tests" / "fixtures" / "skill_observation_dogfood.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        normalized = normalize_skill_observation_candidate("GCS", fixture)
        approval = {
            "schema": "universe.skill-observation-publication-approval.v1",
            "status": "APPROVED",
            "operation_class": "UNIVERSE_OBSERVATION_QUEUE",
            "project_ref": "project://GCS",
            "candidate_id": fixture["candidate_id"],
            "candidate_digest": normalized["candidate_digest"],
            "selection_ref": "local://dogfood/selection-001",
            "approver": "PROJECT_MASTER",
            "evidence_ref": "local://dogfood/approval-001",
        }
        status, queued = self.request(
            "POST",
            "/v1/projects/GCS/skill-observation-queue",
            {**fixture, "publication_approval": approval},
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("SKILL_OBSERVATION_QUEUED", queued["status"])
        self.assertEqual("QUEUED", queued["item"]["status"])
        self.assertEqual(
            normalized["candidate_digest"], queued["item"]["candidate_digest"]
        )


if __name__ == "__main__":
    unittest.main()
