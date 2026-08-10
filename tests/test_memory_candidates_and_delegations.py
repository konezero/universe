from __future__ import annotations

import json
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
from universe_memory import (  # noqa: E402
    MemoryError,
    consolidate_memory_candidates,
    extract_memory_candidates_from_activity_batch,
    normalize_memory_batch_config,
    normalize_memory_candidate,
    resolve_memory_batch_config,
    synthesize_memory_candidates,
)
from universe_server import create_server  # noqa: E402


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
        synthesized = synthesize_memory_candidates(consolidated)
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
                    auto_start_project_masters=False,
                    auto_start_conductor_runtime=False,
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
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        catalog_path = root / "provider-models.json"
        catalog_path.write_text(json.dumps(available_catalog()), encoding="utf-8")
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
            database_path=root / "universe.sqlite3",
            token="candidate-test-token",
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            provider_model_catalog=ProviderModelCatalogStore(path=catalog_path),
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

    def tearDown(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
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
        self.assertEqual(3, synthesize["run"]["candidate_count"])
        self.assertTrue(
            all(
                "transcript" not in json.dumps(item).lower()
                for item in synthesize["run"]["candidates"]
            )
        )
        candidate_id = synthesize["run"]["candidate_ids"][0]
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

    def test_delegation_is_durable_and_does_not_block_chat(self) -> None:
        status, queued = self.request(
            "POST",
            "/v1/conductor/delegations",
            {
                "project_id": "TEST",
                "summary": "Run the approved bounded work",
                "idempotency_key": "delegation-test-1",
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

    def test_project_master_delegation_waits_for_conductor_after_result(self) -> None:
        self.server._conductor_delegation_executor = (
            self.server._dispatch_project_master_delegation
        )
        queued = {
            "schema": "universe.project-room-message.v1",
            "message_id": "room_delegated001",
            "project_id": "TEST",
            "delivery_state": "QUEUED_FOR_MASTER",
        }
        self.server.send_project_room_message = lambda _project, _value: (
            queued,
            True,
        )
        delegation, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "TEST",
                "summary": "Run bounded Project work",
                "idempotency_key": "delegation-project-master-1",
            }
        )
        self.assertTrue(created)
        self.server._process_conductor_delegation(delegation["delegation_id"])
        running = self.server.store.get_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("RUNNING", running["state"])
        self.assertEqual(
            "WAITING_FOR_MASTER_ACCEPTANCE",
            running["progress"]["step"],
        )
        self.assertEqual(
            "room_delegated001",
            running["progress"]["project_room_message_id"],
        )

        self.server.store.create_room_message(
            "TEST",
            {
                "kind": "RESULT",
                "sender": "PROJECT_MASTER",
                "body": "bounded result body remains in the Project Room",
                "idempotency_key": "delegation-project-master-result-1",
                "in_reply_to": "room_delegated001",
            },
            delivery_state="RECORDED",
        )
        self.server._observe_project_master_completion(
            {
                "project_id": "TEST",
                "message_id": "room_delegated001",
                "status": "COMPLETED",
            }
        )
        waiting = self.server.store.get_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("RUNNING", waiting["state"])
        self.assertEqual(
            "WAITING_FOR_CONDUCTOR_REVIEW",
            waiting["progress"]["step"],
        )
        self.assertEqual({}, waiting["result"])
        wakeups = self.server.store.list_conductor_room_messages()
        self.assertEqual(1, len(wakeups))
        self.assertIn(delegation["delegation_id"], wakeups[0]["body"])
        self.assertNotIn("bounded result body", json.dumps(waiting))


if __name__ == "__main__":
    unittest.main()
