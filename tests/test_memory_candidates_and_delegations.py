from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from provider_model_catalog import ProviderModelCatalogStore, empty_catalog  # noqa: E402
from host_profile import HostProfileStore  # noqa: E402
from universe_memory import (  # noqa: E402
    MemoryError,
    consolidate_memory_candidates,
    extract_memory_candidates_from_activity_batch,
    normalize_memory_batch_config,
    normalize_memory_candidate,
    resolve_memory_batch_config,
    synthesize_memory_candidates,
)
from universe_server import UniverseError, create_server  # noqa: E402
from test_memory_source_review import attest_current_fixture
from session_anchor_transport import SessionAnchorTransportError  # noqa: E402


def available_catalog() -> dict:
    catalog = empty_catalog()
    for provider in ("GROK", "CODEX", "CLAUDE"):
        catalog["providers"][provider].update(
            {
                "status": "AVAILABLE",
                "default": "test-model",
                "models": ["test-model", "other-model"],
            }
        )
    return catalog


class MemoryCandidateContractTests(unittest.TestCase):
    def test_config_resolution_and_invalid_values_fail_closed(self) -> None:
        catalog = available_catalog()
        config = normalize_memory_batch_config(
            {
                "stage": "FAST_EXTRACT",
                "provider": "CODEX",
                "model_ref": "test-model",
                "schedule": {"kind": "DAILY"},
                "quota_or_budget": {"max_runs": 2},
                "fallback": "DETERMINISTIC",
            }
        )
        resolved = resolve_memory_batch_config(config, catalog)
        self.assertEqual("AVAILABLE", resolved["resolution"]["status"])
        self.assertEqual("CODEX", resolved["resolution"]["resolved_provider"])
        self.assertEqual(2, config["quota_or_budget"]["max_runs"])
        with self.assertRaisesRegex(MemoryError, "schedule.interval_minutes"):
            normalize_memory_batch_config(
                {
                    "stage": "FAST_EXTRACT",
                    "schedule": {"kind": "DAILY", "interval_minutes": 0},
                }
            )
        invalid = dict(config)
        invalid["model_ref"] = "missing-model"
        with self.assertRaisesRegex(MemoryError, "model"):
            resolve_memory_batch_config(invalid, catalog)

    def test_knowledge_metadata_roundtrips_and_participates_in_identity(self):
        raw = {"project_id": "TEST", "stage": "FAST_EXTRACT", "kind": "MEMORY", "summary": "Apply the procedure only after confirming its precondition.", "source_session": "test", "ref_digests": ["a" * 64]}
        legacy = normalize_memory_candidate(raw)
        self.assertNotIn("knowledge", legacy)
        knowledge = {"kind": "REUSABLE_PROCEDURE", "topic": "검증 절차", "applicability": "판단 전에 근거를 확인할 때"}
        candidate = normalize_memory_candidate({**raw, "knowledge": knowledge})
        self.assertEqual(knowledge, candidate["knowledge"])
        self.assertEqual(candidate["candidate_digest"], normalize_memory_candidate(candidate)["candidate_digest"])
        self.assertNotEqual(legacy["candidate_digest"], candidate["candidate_digest"])
        self.assertTrue(all(x["knowledge"] == knowledge for x in consolidate_memory_candidates([candidate])))
        for invalid in [{**knowledge, "kind": "SOURCE_CHANGE"}, {**knowledge, "applicability": ""}, {**knowledge, "topic": "x" * 101}]:
            with self.assertRaises(MemoryError):
                normalize_memory_candidate({**raw, "knowledge": invalid})

    def test_candidate_pipeline_is_redacted_typed_and_deterministic(self) -> None:
        with self.assertRaises(MemoryError) as raw_error:
            normalize_memory_candidate(
                {
                    "project_id": "TEST",
                    "stage": "FAST_EXTRACT",
                    "summary": "bounded",
                    "prompt": "must not persist",
                }
            )
        self.assertEqual("MEMORY_CANDIDATE_RAW_INPUT_FORBIDDEN", raw_error.exception.code)
        batch = {
            "source": {
                "provider": "CODEX",
                "provider_session_id": "session-1",
                "source_id": "source-1",
            },
            "activity_refs": [
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 1,
                    "activity_digest": "a" * 64,
                },
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 2,
                    "activity_digest": "b" * 64,
                },
            ],
        }
        extracted = extract_memory_candidates_from_activity_batch(
            batch, project_id="TEST"
        )
        self.assertEqual(2, len(extracted))
        self.assertEqual("MEMORY", extracted[0]["kind"])
        self.assertNotIn("body", json.dumps(extracted))
        consolidated = consolidate_memory_candidates(extracted + [extracted[0]])
        self.assertTrue(any(item["state"] == "SUPERSEDED" for item in consolidated))
        self.assertTrue(
            any(
                relation["relation"] == "DUPLICATE_OF"
                for item in consolidated
                for relation in item["relations"]
            )
        )
        # Unreviewed extraction must not multiply into synthetic knowledge.
        self.assertEqual([], synthesize_memory_candidates(consolidated))
        reviewed = [{**item, "state": "KEEP"} for item in consolidated
                    if item["state"] != "SUPERSEDED"]
        synthesized = synthesize_memory_candidates(reviewed)
        self.assertEqual({"IDEA", "HYPOTHESIS", "PRODUCT"}, {item["kind"] for item in synthesized})
        self.assertTrue(
            all(
                relation["relation"] == "DERIVED_FROM"
                for item in synthesized
                for relation in item["relations"]
            )
        )
        self.assertTrue(all(item["effects"]["auto_adoption"] is False for item in synthesized))


class ConductorDelegationMigrationTests(unittest.TestCase):
    def test_legacy_delegation_table_accepts_cancellation_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "legacy-universe.sqlite3"
            host_profile_path = Path(temp) / "legacy-host.json"
            catalog_path = Path(temp) / "legacy-provider-models.json"
            catalog_path.write_text(json.dumps(empty_catalog()), encoding="utf-8")
            host_profile = HostProfileStore(host_profile_path)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE conductor_delegation (
                        delegation_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        project_id TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        state TEXT NOT NULL CHECK(state IN (
                            'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED'
                        )),
                        progress_json TEXT NOT NULL DEFAULT '{}',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
            server = None
            thread = None
            try:
                server = create_server(
                    database_path=database_path,
                    token="legacy-test-token",
                    service_state_path=Path(temp) / "legacy-server.json",
                    remote_gateway_state_path=Path(temp) / "legacy-remote-gateway.json",
                    remote_connector_state_path=Path(temp) / "legacy-remote-connector.json",
                    remote_connector_config_path=Path(temp) / "legacy-remote-connector-config.json",
                    host_profile=host_profile,
                    provider_model_catalog=ProviderModelCatalogStore(
                        path=catalog_path,
                        host_profile=host_profile,
                    ),
                    auto_start_project_masters=False,
                    auto_start_conductor_runtime=False,
                    auto_start_goal_scheduler=False,
                )
                thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    daemon=True,
                )
                thread.start()
                project_root = Path(temp) / "TEST"
                project_root.mkdir()
                (project_root / "REPOSITORY_MANIFEST.md").write_text(
                    "# TEST\n", encoding="utf-8"
                )
                server.store.register_project(
                    {"project_id": "TEST", "project_root": str(project_root)}
                )
                delegation, created = server.store.create_conductor_delegation(
                    {
                        "project_id": "TEST",
                        "summary": "Exercise migrated cancellation state",
                        "idempotency_key": "legacy-cancellation-state",
                        "origin_session_anchor_ref": "session_anchor_origin",
                        "target_session_anchor_ref": "session_anchor_target",
                        "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
                    }
                )
                self.assertTrue(created)
                server.store.start_conductor_delegation(delegation["delegation_id"])
                cancelled = server.store.cancel_conductor_delegation(
                    delegation["delegation_id"], {}
                )
                with closing(sqlite3.connect(database_path)) as connection:
                    schema = connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'conductor_delegation'"
                    ).fetchone()[0]
                self.assertIn("CANCELLATION_REQUESTED", schema)
                self.assertEqual("CANCELLATION_REQUESTED", cancelled["state"])
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=2)


class MemoryCandidateApiTests(unittest.TestCase):
    def test_saved_memory_ignore_restore_and_retrieval(self):
        store = self.server.store
        memory = store.create_project_memory("TEST", {"title": "Old progress", "body": "Only a completed build report", "state": "OBSERVED"})
        value = {"project_id": "TEST", "memory_id": memory["memory_id"], "expected_memory_digest": memory["memory_digest"], "expected_revision": 0, "decision": "IGNORE", "note": "Build progress has no reusable knowledge"}
        def call(v):
            return self.request("POST", "/v1/actions", {"action_id": "rag.memory-retention", "request": v})
        self.assertEqual(409, call({**value, "expected_memory_digest": "0" * 64})[0])
        status, result = call(value)
        self.assertEqual(200, status, result)
        self.assertEqual("IGNORE", result["memory"]["retention"]["decision"])
        self.assertEqual([], store.list_project_memories("TEST", query="completed"))
        self.assertEqual([], store.propose_memory_links("TEST")["proposals"])
        self.assertEqual(memory["body"], store.list_project_memories("TEST", include_ignored=True)[0]["body"])
        self.assertEqual("MEMORY_RETENTION_REPLAYED", call(value)[1]["status"])
        self.assertEqual(409, call({**value, "note": "Changed reason"})[0])
        self.assertEqual(400, call({**value, "expected_revision": True})[0])
        status, result = call({**value, "expected_revision": 1, "decision": "ACTIVE", "note": "Reviewed again"})
        self.assertEqual(200, status, result)
        self.assertEqual(1, len(store.list_project_memories("TEST")))
        self.assertEqual(409, call(value)[0])
        with store._connection() as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM project_memory_retention").fetchone()[0])
        self.assertEqual(1, len(self.request("GET", "/v1/projects/TEST/memories?include_ignored=true")[1]["memories"]))

    def test_ignore_reviewed_adopted_candidate_is_atomic_and_suppresses_recollection(self):
        store = self.server.store
        items, _ = store._insert_memory_candidates("TEST", [{"project_id":"TEST", "kind":"MEMORY", "stage":"FAST_EXTRACT", "summary":"Old incident completion", "source_session":"original", "ref_digests":["a" * 64]}])
        candidate = items[0]
        attest_current_fixture(store, candidate["candidate_id"])
        candidate, _ = store.review_memory_candidate(candidate["candidate_id"], {"decision":"KEEP"})
        memory = store.create_project_memory("TEST", {"title": candidate["summary"], "body": candidate["summary"], "state":"OBSERVED", "origin_ref":"universe://memory-candidates/"+candidate["candidate_digest"]+"/"+candidate["candidate_id"]})
        value = {"project_id":"TEST", "candidate_id":candidate["candidate_id"], "expected_candidate_digest":candidate["candidate_digest"], "expected_candidate_revision":candidate["revision"], "note":"Completed incident without reusable procedure"}
        status, result = self.request("POST", "/v1/actions", {"action_id":"rag.archive-candidate", "request":{**value,"expected_candidate_revision":1}})
        self.assertEqual(409,status,result)
        status, result = self.request("POST", "/v1/actions", {"action_id":"rag.archive-candidate", "request":value})
        self.assertEqual(200,status,result)
        self.assertEqual("IGNORE",store.get_memory_candidate(candidate["candidate_id"])["state"])
        self.assertEqual([],store.list_project_memories("TEST"))
        self.assertEqual("IGNORE",store.get_project_memory("TEST",memory["memory_id"])["retention"]["decision"])
        again, created = store._insert_memory_candidates("TEST", [{"project_id":"TEST", "kind":"MEMORY", "stage":"FAST_EXTRACT", "summary":candidate["summary"], "source_session":"different", "ref_digests":["b" * 64]}])
        self.assertEqual(([],0),(again,created))
        with self.assertRaisesRegex(UniverseError, "identical claim"):
            store.create_memory_candidate("TEST", {"kind":"MEMORY", "stage":"FAST_EXTRACT", "summary":candidate["summary"], "source_session":"third", "ref_digests":["c" * 64]})
        with store._connection() as connection:
            self.assertEqual(2,connection.execute("SELECT COUNT(*) FROM memory_candidate_review_history WHERE candidate_id=?",(candidate["candidate_id"],)).fetchone()[0])

    def test_saved_adopted_memory_ignore_also_withdraws_candidate(self):
        store = self.server.store
        items,_=store._insert_memory_candidates("TEST",[{"project_id":"TEST","kind":"MEMORY","stage":"FAST_EXTRACT","summary":"Old source-change report","source_session":"s","ref_digests":["a"*64]}])
        c=items[0];attest_current_fixture(store,c["candidate_id"])
        store.review_memory_candidate(c["candidate_id"],{"decision":"KEEP"})
        m=store.create_project_memory("TEST",{"title":c["summary"],"body":c["summary"],"state":"OBSERVED","origin_ref":"universe://memory-candidates/"+c["candidate_digest"]+"/"+c["candidate_id"]})
        status,result=self.request("POST","/v1/actions",{"action_id":"rag.memory-retention","request":{"project_id":"TEST","memory_id":m["memory_id"],"expected_memory_digest":m["memory_digest"],"expected_revision":0,"decision":"IGNORE","note":"Source history only"}})
        self.assertEqual(200,status,result)
        self.assertEqual("IGNORE",store.get_memory_candidate(c["candidate_id"])["state"])
        self.assertEqual([],store.list_project_memories("TEST"))

    def test_governed_archive_preserves_record_and_checks_identity(self):
        items, _ = self.server.store._insert_memory_candidates("TEST", [{
            "project_id": "TEST", "stage": "FAST_EXTRACT", "kind": "MEMORY",
            "summary": "Test-only response marker", "source_session": "test",
            "ref_digests": ["a" * 64]}])
        item = items[0]
        request = {"project_id": "TEST", "candidate_id": item["candidate_id"],
                   "expected_candidate_digest": item["candidate_digest"], "note": "Test response is not project knowledge."}
        def call(value):
            return self.request("POST", "/v1/actions", {"action_id": "rag.archive-candidate", "request": value})
        status, _ = call({**request, "expected_candidate_digest": "0" * 64})
        self.assertEqual(HTTPStatus.CONFLICT, status)
        status, _ = call({**request, "project_id": "OTHER"})
        self.assertEqual(HTTPStatus.CONFLICT, status)
        status, result = call(request)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("RAG_CANDIDATE_ARCHIVED", result["status"])
        status, replay = call(request)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("RAG_CANDIDATE_ARCHIVE_REPLAYED", replay["status"])
        self.assertEqual(result["revision"], replay["revision"])
        status, _ = call({**request, "note": "different reason"})
        self.assertEqual(HTTPStatus.CONFLICT, status)
        stored = self.server.store.get_memory_candidate(item["candidate_id"])
        self.assertEqual("IGNORE", stored["state"])
        self.assertEqual(item["summary"], stored["summary"])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.fixture_root = root
        self.database_path = root / "universe.sqlite3"
        self.service_state_path = root / "server.json"
        self.host_profile_path = root / "host.json"
        self.remote_gateway_state_path = root / "remote-gateway.json"
        self.remote_connector_state_path = root / "remote-connector.json"
        self.remote_connector_config_path = root / "remote-connector-config.json"
        self.catalog_path = root / "provider-models.json"
        self.catalog_path.write_text(json.dumps(available_catalog()), encoding="utf-8")
        self.host_profile = HostProfileStore(self.host_profile_path)
        self.release = threading.Event()
        self.executor_started = threading.Event()

        def delegation_executor(_record: dict) -> dict:
            self.executor_started.set()
            self.release.wait(timeout=10)
            return {
                "result_summary": "bounded delegated result",
                "result_digest": "c" * 64,
            }

        self.server = create_server(
            database_path=self.database_path,
            token="candidate-test-token",
            service_state_path=self.service_state_path,
            remote_gateway_state_path=self.remote_gateway_state_path,
            remote_connector_state_path=self.remote_connector_state_path,
            remote_connector_config_path=self.remote_connector_config_path,
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            host_profile=self.host_profile,
            provider_model_catalog=ProviderModelCatalogStore(
                path=self.catalog_path,
                host_profile=self.host_profile,
            ),
            auto_start_goal_scheduler=False,
            conductor_delegation_executor=delegation_executor,
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
        (root / "TEST" / "REPOSITORY_MANIFEST.md").write_text(
            "# TEST\n", encoding="utf-8"
        )
        self.server.store.register_project(
            {
                "project_id": "TEST",
                "project_root": str(root / "TEST"),
            }
        )
        host, port = self.server.server_address[:2]
        self.fixture_coordinates = {
            "owner_pid": os.getpid(),
            "endpoint": self.endpoint,
            "host": str(host),
            "port": int(port),
            "database_path": str(self.database_path),
            "service_state_path": str(self.service_state_path),
        }
        self._assert_fixture_coordinates()

    def _assert_fixture_coordinates(self) -> None:
        root = self.fixture_root
        paths = {
            "database": self.database_path,
            "service state": self.service_state_path,
            "host profile": self.host_profile_path,
            "provider catalog": self.catalog_path,
            "remote gateway state": self.remote_gateway_state_path,
            "remote connector state": self.remote_connector_state_path,
            "remote connector config": self.remote_connector_config_path,
        }
        for label, path in paths.items():
            resolved = path.expanduser().resolve()
            if resolved == root or root not in resolved.parents:
                raise AssertionError(
                    "fixture resource escaped its temporary root "
                    f"({label}={resolved}, root={root}, "
                    f"owner_pid={os.getpid()})"
                )
        if self.server.store.database_path != self.database_path:
            raise AssertionError(
                "fixture store/database ownership mismatch "
                f"(store={self.server.store.database_path}, "
                f"expected={self.database_path}, owner_pid={os.getpid()})"
            )
        if self.server.service_state_path != self.service_state_path:
            raise AssertionError(
                "fixture service-state ownership mismatch "
                f"(service={self.server.service_state_path}, "
                f"expected={self.service_state_path}, owner_pid={os.getpid()})"
            )
        if self.server.host_profile.path != self.host_profile_path:
            raise AssertionError(
                "fixture host-profile ownership mismatch "
                f"(service={self.server.host_profile.path}, "
                f"expected={self.host_profile_path}, owner_pid={os.getpid()})"
            )
        if self.server.provider_model_catalog.path != self.catalog_path:
            raise AssertionError(
                "fixture provider-catalog ownership mismatch "
                f"(service={self.server.provider_model_catalog.path}, "
                f"expected={self.catalog_path}, owner_pid={os.getpid()})"
            )
        host, port = self.server.server_address[:2]
        if str(host) not in {"127.0.0.1", "::1", "localhost"} or int(port) <= 0:
            raise AssertionError(
                "fixture endpoint is not an owned loopback socket "
                f"(endpoint={self.endpoint}, owner_pid={os.getpid()})"
            )

    def test_fixture_service_coordinates_are_temp_owned(self) -> None:
        self._assert_fixture_coordinates()
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(os.getpid(), self.fixture_coordinates["owner_pid"])

    def tearDown(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        shutdown_errors = list(getattr(self.server, "_shutdown_errors", []))
        self.thread.join(timeout=5)
        if self.thread.is_alive() or shutdown_errors:
            coordinates = self.fixture_coordinates
            raise AssertionError(
                "fixture service cleanup did not complete; "
                f"thread_alive={self.thread.is_alive()} "
                f"shutdown_errors={shutdown_errors!r} "
                f"cleanup owner pid={coordinates['owner_pid']} "
                f"endpoint={coordinates['endpoint']} "
                f"database_path={coordinates['database_path']} "
                f"service_state_path={coordinates['service_state_path']}"
            )
        self.temp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None):
        data = None
        headers = {"Authorization": "Bearer candidate-test-token"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def configure(self, stage: str, **overrides):
        body = {
            "stage": stage,
            "provider": "CODEX",
            "model_ref": "test-model",
            "effort": "LOW",
            "schedule": {"kind": "MANUAL"},
            "quota_or_budget": None,
            "fallback": "DETERMINISTIC",
            "enabled": True,
            "dry_run": False,
        }
        body.update(overrides)
        return self.request("POST", "/v1/projects/TEST/memory-batch-config", body)

    def test_semantic_graph_projects_redacted_room_event_and_bench_facts(self) -> None:
        todo = self.server.store.create_todo(
            {
                "scope_kind": "PROJECT",
                "project_id": "TEST",
                "title": "Graph projection fixture",
                "detail": "Fixture only",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            }
        )
        message, _created = self.server.store.create_room_message(
            "TEST",
            {
                "kind": "STATUS",
                "sender": "MASTER",
                "body": "raw room text must not be projected",
                "todo_id": todo["todo_id"],
                "idempotency_key": "semantic-graph-room-fixture",
            },
        )
        self.server.store.append_event(
            "TEST",
            {
                "event_id": "semantic_graph_fixture_event",
                "event_type": "HOOK_OBSERVED",
                "payload": {"secret": "must-not-project"},
            },
        )
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "skill_observation_dogfood.json").read_text(
                encoding="utf-8"
            )
        )
        fixture["candidate"]["project_ref"] = "project://TEST"
        fixture["candidate"]["observations"][0]["execution_context"][
            "failure_kind"
        ] = "TIMEOUT"
        self.server.store.ingest_skill_observations("TEST", fixture)

        graph = self.server.store.semantic_project_graph("TEST")
        node_ids = {node["id"] for node in graph["nodes"]}
        edge_types = {edge["edge_type"] for edge in graph["edges"]}
        rendered = json.dumps(graph, sort_keys=True)
        self.assertIn(f"room_message:{message['message_id']}", node_ids)
        self.assertIn("event:semantic_graph_fixture_event", node_ids)
        self.assertIn("skill_observation:", rendered)
        self.assertIn("PROJECT_HAS_BENCH_OBSERVATION", edge_types)
        self.assertIn("SKILL_OBSERVATION_REPORTED_FAILURE", edge_types)
        self.assertIn("ROOM_MESSAGE_REFERENCES_TODO", edge_types)
        self.assertNotIn("raw room text must not be projected", rendered)
        self.assertNotIn("must-not-project", rendered)

    def test_config_roundtrip_quota_and_candidates_review(self) -> None:
        status, saved = self.configure(
            "FAST_EXTRACT",
            schedule={"kind": "DAILY"},
            quota_or_budget={"max_runs": 1},
            dry_run=True,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AVAILABLE", saved["config"]["resolution"]["status"])
        status, listed = self.request("GET", "/v1/projects/TEST/memory-batch-config")
        self.assertEqual(HTTPStatus.OK, status)
        fast = next(item for item in listed["configs"] if item["stage"] == "FAST_EXTRACT")
        self.assertTrue(fast["persisted"])
        self.assertEqual(1, fast["quota_or_budget"]["max_runs"])
        self.assertTrue(fast["dry_run"])
        status, invalid = self.configure("CONSOLIDATE", model_ref="missing-model")
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("MEMORY_BATCH_MODEL_NOT_FOUND", invalid["error_code"])
        self.configure("CONSOLIDATE")
        self.configure("SYNTHESIZE")
        self.configure("INDEPENDENT_CHECK")

        activity_batch = {
            "source": {
                "provider": "CODEX",
                "provider_session_id": "session-1",
                "source_id": "source-1",
            },
            "activity_refs": [
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 1,
                    "activity_digest": "d" * 64,
                },
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 2,
                    "activity_digest": "e" * 64,
                },
            ],
        }
        # Dry-run config proves the quota is persisted without writing candidates.
        status, dry_run = self.request(
            "POST",
            "/v1/projects/TEST/memory-batches/run",
            {"stage": "FAST_EXTRACT", "activity_batches": [activity_batch]},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("DRY_RUN_COMPLETED", dry_run["run"]["status"])
        status, listed = self.request("GET", "/v1/projects/TEST/memory-candidates")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual([], listed["candidates"])

        self.configure("FAST_EXTRACT", quota_or_budget={"max_runs": 2})
        status, fast_run = self.request(
            "POST",
            "/v1/projects/TEST/memory-batches/run",
            {"stage": "FAST_EXTRACT", "activity_batches": [activity_batch]},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertGreaterEqual(fast_run["run"]["created_count"], 2)
        self.assertEqual("DETERMINISTIC", fast_run["execution"]["mode"])
        self.assertEqual("NOT_RUN", fast_run["execution"]["provider_invocation"])
        status, consolidate = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", {"stage": "CONSOLIDATE"}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("CONSOLIDATE", consolidate["run"]["stage"])
        status, synthesize = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", {"stage": "SYNTHESIZE"}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(0, synthesize["run"]["candidate_count"])
        input_id = consolidate["run"]["candidate_ids"][0]
        attest_current_fixture(self.server.store, input_id)
        status, _ = self.request("POST", f"/v1/memory-candidates/{input_id}/review", {"decision": "KEEP"})
        self.assertEqual(HTTPStatus.OK, status)
        status, synthesize = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", {"stage": "SYNTHESIZE"}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(3, synthesize["run"]["candidate_count"])
        self.assertTrue(
            all(
                "transcript" not in json.dumps(item).lower()
                for item in synthesize["run"]["candidates"]
            )
        )
        candidate_id = synthesize["run"]["candidate_ids"][0]
        attest_current_fixture(self.server.store, candidate_id)
        status, reviewed = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "KEEP"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("KEEP", reviewed["candidate"]["state"])
        status, conflict = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "IGNORE"},
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("MEMORY_CANDIDATE_STATE_CONFLICT", conflict["error_code"])

        self.configure(
            "INDEPENDENT_CHECK",
            quota_or_budget={"max_tokens": 1000},
        )
        status, unsupported_budget = self.request(
            "POST",
            "/v1/projects/TEST/memory-batches/run",
            {"stage": "INDEPENDENT_CHECK"},
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "MEMORY_BATCH_BUDGET_ENFORCEMENT_UNAVAILABLE",
            unsupported_budget["error_code"],
        )

    def _create_memory_kind_candidate(self) -> dict:
        batch = {
            "source": {
                "provider": "CODEX",
                "provider_session_id": "session-reopen",
                "source_id": "source-reopen",
            },
            "activity_refs": [
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 1,
                    "activity_digest": "f" * 64,
                },
            ],
        }
        extracted = extract_memory_candidates_from_activity_batch(
            batch, project_id="TEST"
        )
        self.assertEqual("MEMORY", extracted[0]["kind"])
        candidate, _created = self.server.store.create_memory_candidate(
            "TEST", extracted[0]
        )
        return candidate

    def test_memory_candidate_rejects_explore_and_exposes_decision_contract(
        self,
    ) -> None:
        candidate = self._create_memory_kind_candidate()
        candidate_id = candidate["candidate_id"]

        status, fetched = self.request(
            "GET", f"/v1/projects/TEST/memory-candidates"
        )
        self.assertEqual(HTTPStatus.OK, status)
        listed = next(
            item for item in fetched["candidates"] if item["candidate_id"] == candidate_id
        )
        self.assertEqual(
            ["IGNORE"],
            listed["decision_contract"]["allowed_actions"],
        )
        self.assertEqual(
            {"EXPLORE": "MEMORY_EXPLORE_NO_AUTOMATION", "KEEP": "MEMORY_SOURCE_REVIEW_REQUIRED", "START_PRODUCT_DESIGN": "MEMORY_SOURCE_REVIEW_REQUIRED"},
            listed["decision_contract"]["disabled_actions"],
        )

        status, rejected = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "EXPLORE"},
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "MEMORY_CANDIDATE_DECISION_NOT_ALLOWED_FOR_KIND", rejected["error_code"]
        )

        status, reviewed = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "IGNORE"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("IGNORE", reviewed["candidate"]["state"])
        self.assertEqual(2, reviewed["candidate"]["revision"])
        self.assertEqual(
            "IGNORE", reviewed["candidate"]["decision_contract"]["current_decision"]
        )
        self.assertEqual([], reviewed["candidate"]["decision_contract"]["allowed_actions"])

    def test_memory_candidate_reopen_round_trip_and_concurrency_guards(self) -> None:
        candidate = self._create_memory_kind_candidate()
        candidate_id = candidate["candidate_id"]
        digest = candidate["candidate_digest"]

        status, reviewed = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "IGNORE"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(2, reviewed["candidate"]["revision"])
        self.assertTrue(reviewed["candidate"]["decision_contract"]["reopen"]["allowed"])

        # Stale digest is rejected before revision is even considered.
        status, stale_digest = self.request(
            "POST",
            "/v1/projects/TEST/memory-candidates/reopen",
            {
                "candidate_id": candidate_id,
                "expected_candidate_digest": "0" * 64,
                "expected_candidate_revision": 2,
                "reason": "wrong digest",
            },
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("MEMORY_CANDIDATE_DIGEST_STALE", stale_digest["error_code"])

        # Correct digest, stale revision.
        status, stale_revision = self.request(
            "POST",
            "/v1/projects/TEST/memory-candidates/reopen",
            {
                "candidate_id": candidate_id,
                "expected_candidate_digest": digest,
                "expected_candidate_revision": 1,
                "reason": "wrong revision",
            },
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("MEMORY_CANDIDATE_REVISION_STALE", stale_revision["error_code"])

        # Correct digest and revision: reopen succeeds and preserves history.
        status, reopened = self.request(
            "POST",
            "/v1/projects/TEST/memory-candidates/reopen",
            {
                "candidate_id": candidate_id,
                "expected_candidate_digest": digest,
                "expected_candidate_revision": 2,
                "reason": "user asked to reconsider",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("REVIEW_REQUIRED", reopened["candidate"]["state"])
        self.assertEqual(3, reopened["candidate"]["revision"])
        self.assertIsNone(reopened["candidate"]["decision_contract"]["current_decision"])

        history = reopened["candidate"]["review_history"]
        self.assertEqual(["REVIEWED", "REOPENED"], [item["event"] for item in history])
        self.assertEqual("IGNORE", history[0]["decision"])
        self.assertEqual("user asked to reconsider", history[1]["reason"])

        # Reopening an already-REVIEW_REQUIRED candidate is a conflict.
        status, already_open = self.request(
            "POST",
            "/v1/projects/TEST/memory-candidates/reopen",
            {
                "candidate_id": candidate_id,
                "expected_candidate_digest": digest,
                "expected_candidate_revision": 3,
                "reason": "again",
            },
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("MEMORY_CANDIDATE_STATE_CONFLICT", already_open["error_code"])

        # A fresh decision after source review keeps history and bumps revision again.
        attest_current_fixture(self.server.store, candidate_id)
        status, redecided = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "KEEP"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("KEEP", redecided["candidate"]["state"])
        self.assertEqual(4, redecided["candidate"]["revision"])
        self.assertEqual(
            "RAG_ADOPT_AVAILABLE", redecided["candidate"]["decision_contract"]["next_action"]["kind"]
        )
        self.assertEqual(
            digest, redecided["candidate"]["decision_contract"]["next_action"]["target_ref"]
        )

    def test_memory_candidate_list_reopen_is_cheap_but_candidate_id_refetch_is_exact(
        self,
    ) -> None:
        """Codex B's integration finding: after a page refresh the UI only has
        the list endpoint, and a plain list keeps reopen.allowed=false/
        NOT_EVALUATED for every row (by design, to avoid an adoption lookup
        per row). ?candidate_id= is the single-item re-evaluation path a
        refreshed UI must call to get an accurate reopen block."""

        candidate = self._create_memory_kind_candidate()
        candidate_id = candidate["candidate_id"]
        status, reviewed = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "IGNORE"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(reviewed["candidate"]["decision_contract"]["reopen"]["allowed"])

        status, listed = self.request(
            "GET", "/v1/projects/TEST/memory-candidates"
        )
        self.assertEqual(HTTPStatus.OK, status)
        listed_row = next(
            item for item in listed["candidates"] if item["candidate_id"] == candidate_id
        )
        self.assertFalse(listed_row["decision_contract"]["reopen"]["allowed"])
        self.assertEqual(
            "NOT_EVALUATED", listed_row["decision_contract"]["reopen"]["reason"]
        )

        status, refetched = self.request(
            "GET", f"/v1/projects/TEST/memory-candidates?candidate_id={candidate_id}"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(refetched["candidates"]))
        exact = refetched["candidates"][0]
        self.assertEqual(candidate_id, exact["candidate_id"])
        self.assertTrue(exact["decision_contract"]["reopen"]["allowed"])
        self.assertIn("review_history", exact)

    def test_memory_batch_action_and_legacy_surface_share_the_run_envelope(self) -> None:
        self.configure("FAST_EXTRACT", dry_run=True)
        activity_batch = {
            "source": {
                "provider": "CODEX",
                "provider_session_id": "session-action-1",
                "source_id": "source-action-1",
            },
            "activity_refs": [
                {
                    "event_kind": "TURN_COMPLETED",
                    "activity_state": "DONE",
                    "ordinal": 1,
                    "activity_digest": "a" * 64,
                }
            ],
        }
        status, action_result = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "memory.batch.run",
                "request": {
                    "project_id": "TEST",
                    "stage": "FAST_EXTRACT",
                    "activity_batches": [activity_batch],
                },
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("memory.batch.run", action_result["action_id"])
        self.assertEqual("DRY_RUN_COMPLETED", action_result["run"]["status"])

        status, legacy_result = self.request(
            "POST",
            "/v1/projects/TEST/memory-batches/run",
            {"stage": "FAST_EXTRACT", "activity_batches": [activity_batch]},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("memory.batch.run", legacy_result["action_id"])
        self.assertEqual("DRY_RUN_COMPLETED", legacy_result["run"]["status"])
        self.assertEqual(action_result["run"]["run_id"], legacy_result["run"]["run_id"])
        self.assertEqual(action_result["config"], legacy_result["config"])

    def test_delegation_is_durable_and_does_not_block_chat(self) -> None:
        status, queued = self.request(
            "POST",
            "/v1/conductor/delegations",
            {
                "project_id": "TEST",
                "summary": "Run the approved bounded work",
                "idempotency_key": "delegation-test-1",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
                "task_frame_ref": "task-frame-1",
                "worker_role": "PROJECT_MASTER",
            },
        )
        self.assertEqual(HTTPStatus.ACCEPTED, status)
        delegation_id = queued["delegation"]["delegation_id"]
        self.assertTrue(self.executor_started.wait(timeout=5))
        status, message = self.request(
            "POST",
            "/v1/conductor-room/messages",
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "What is the current bounded status?",
                "idempotency_key": "chat-while-delegated-1",
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("QUEUED", message["message"]["delivery_state"])
        self.release.set()
        deadline = time.time() + 5
        latest = None
        while time.time() < deadline:
            _, latest = self.request(
                "GET", f"/v1/conductor/delegations/{delegation_id}"
            )
            if latest.get("state") == "COMPLETED":
                break
            time.sleep(0.05)
        self.assertEqual("COMPLETED", latest["state"])
        self.assertEqual("bounded delegated result", latest["result"]["summary"])

    def test_running_delegation_recovers_without_transcript(self) -> None:
        delegation, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "TEST",
                "summary": "Recover this bounded operation",
                "idempotency_key": "delegation-recovery-1",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )
        self.assertTrue(created)
        started = self.server.store.start_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("RUNNING", started["state"])
        recovered = self.server.store.recover_conductor_delegations()
        self.assertIn(delegation["delegation_id"], recovered)
        after = self.server.store.get_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("QUEUED", after["state"])
        self.assertIn("recovered_at", after["progress"])
        self.assertNotIn("body", json.dumps(after).lower())

    def test_cancellation_records_scope_without_claiming_provider_termination(
        self,
    ) -> None:
        queued, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "TEST",
                "summary": "Cancel before coordinator dispatch",
                "idempotency_key": "delegation-cancel-queued-1",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )
        self.assertTrue(created)
        status, cancellation = self.request(
            "POST",
            f"/v1/conductor-room/delegations/{queued['delegation_id']}/cancel",
            {"reason": "No longer needed"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        cancelled_queued = cancellation["delegation"]
        self.assertEqual("CANCELLED", cancelled_queued["state"])
        self.assertEqual(
            "QUEUED_NOT_DISPATCHED",
            cancelled_queued["result"]["cancellation_scope"],
        )

        running, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "TEST",
                "summary": "Cancel after provider handoff",
                "idempotency_key": "delegation-cancel-running-1",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )
        self.assertTrue(created)
        self.server.store.start_conductor_delegation(running["delegation_id"])
        cancelled_running = self.server.store.cancel_conductor_delegation(
            running["delegation_id"], {}
        )
        self.assertEqual("CANCELLATION_REQUESTED", cancelled_running["state"])
        self.assertEqual(
            "RESULT_ADOPTION_CANCELLATION_REQUESTED",
            cancelled_running["result"]["cancellation_scope"],
        )
        self.assertEqual(
            "CANCELLATION_REQUESTED", cancelled_running["progress"]["step"]
        )
        completed = self.server.store.complete_conductor_delegation(
            running["delegation_id"],
            {"result_summary": "late provider result"},
        )
        self.assertEqual(
            "CANCELLED",
            completed["state"],
        )
        self.assertEqual("PROVIDER_RESULT_IGNORED", completed["result"]["cancellation_scope"])

        review_pending, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "TEST",
                "summary": "Cancel a completed result before review",
                "idempotency_key": "delegation-cancel-review-1",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )
        self.assertTrue(created)
        self.server.store.start_conductor_delegation(review_pending["delegation_id"])
        self.server.store.update_conductor_delegation_progress(
            review_pending["delegation_id"],
            {
                "summary": "Result is awaiting Conductor review",
                "step": "WAITING_FOR_CONDUCTOR_REVIEW",
            },
        )
        cancelled_review = self.server.store.cancel_conductor_delegation(
            review_pending["delegation_id"], {}
        )
        self.assertEqual("CANCELLED", cancelled_review["state"])
        self.assertEqual(
            "TERMINAL_REVIEW_NOT_ADOPTED",
            cancelled_review["result"]["cancellation_scope"],
        )

    def test_project_master_model_override_is_rejected_before_persistence(
        self,
    ) -> None:
        status, rejected = self.request(
            "POST",
            "/v1/conductor/delegations",
            {
                "project_id": "TEST",
                "summary": "Use a different model than the resident Master.",
                "idempotency_key": "delegation-model-override-rejected-1",
                "model_ref": "other-model",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            },
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "CONDUCTOR_DELEGATION_MODEL_OVERRIDE_UNSUPPORTED",
            rejected["error_code"],
        )
        self.assertEqual(
            [],
            self.server.store.list_conductor_delegations(
                project_id="TEST",
                limit=10,
            ),
        )

    def test_project_master_provider_mismatch_is_rejected_before_persistence(
        self,
    ) -> None:
        self.server.store.set_provider_setting(
            "PROJECT_MASTER",
            "TEST",
            {"provider": "CODEX"},
        )
        status, rejected = self.request(
            "POST",
            "/v1/conductor/delegations",
            {
                "project_id": "TEST",
                "summary": "Route this through a different provider.",
                "idempotency_key": "delegation-provider-mismatch-rejected-1",
                "provider": "CLAUDE",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            },
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "CONDUCTOR_DELEGATION_PROVIDER_MISMATCH",
            rejected["error_code"],
        )
        self.assertEqual(
            [],
            self.server.store.list_conductor_delegations(
                project_id="TEST",
                limit=10,
            ),
        )

    def test_project_master_delegation_never_falls_back_to_project_room(self) -> None:
        self.server._conductor_delegation_executor = (
            self.server._dispatch_project_master_delegation
        )
        self.server.send_project_room_message = lambda *_args: (_ for _ in ()).throw(
            AssertionError("cross-session delegation must not enter Project Room")
        )
        self.server.session_anchor_transport.deliver = lambda *_args: (_ for _ in ()).throw(
            SessionAnchorTransportError(
                "TARGET_SESSION_DELEGATION_TRANSPORT_UNAVAILABLE",
                "target Session Anchor transport is unavailable",
            )
        )
        delegation, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "TEST",
                "summary": "Run bounded Project work",
                "idempotency_key": "delegation-project-master-1",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )
        self.assertTrue(created)
        with self.assertRaises(UniverseError) as raised:
            self.server._process_conductor_delegation(delegation["delegation_id"])
        self.assertEqual(
            "TARGET_SESSION_DELEGATION_TRANSPORT_UNAVAILABLE", raised.exception.code
        )
        running = self.server.store.get_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("RUNNING", running["state"])


if __name__ == "__main__":
    unittest.main()
