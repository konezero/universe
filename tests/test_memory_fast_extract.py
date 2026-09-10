from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from memory_fast_extract import (  # noqa: E402
    FAST_EXTRACT_EFFORT,
    FAST_EXTRACT_MODEL,
    FAST_EXTRACT_RESULT_SCHEMA,
    FastExtractError,
    build_provider_request,
    normalize_provider_candidates,
    redact_activity_batch,
)
from provider_model_catalog import ProviderModelCatalogStore, empty_catalog  # noqa: E402
from universe_runtime_host import UniverseRuntimeHost  # noqa: E402
from universe_runtime_worker_dispatch import (  # noqa: E402
    RuntimeWorkerDispatcher,
    WorkerDispatchError,
)
from worker_failure_evidence import WorkerFailureEvidenceStore  # noqa: E402
from universe_server import create_server  # noqa: E402


def catalog_with_luna() -> dict:
    catalog = empty_catalog()
    for provider in ("GROK", "CODEX", "CLAUDE"):
        catalog["providers"][provider].update(
            {
                "status": "AVAILABLE",
                "default": "test-model",
                "models": ["test-model", FAST_EXTRACT_MODEL],
            }
        )
    return catalog


class FastExtractDispatcher(RuntimeWorkerDispatcher):
    def __init__(self, root: Path) -> None:
        super().__init__(
            root,
            post=self._post,
            failure_evidence_store=WorkerFailureEvidenceStore(
                root / "fast-extract-worker-failures.sqlite3"
            ),
        )
        self.calls: list[dict[str, object]] = []
        self.events: list[str] = []
        self.fail_once = False

    def provider_capability(self, provider: str) -> dict[str, str]:
        return {
            "provider": provider,
            "status": "AVAILABLE",
            "model": FAST_EXTRACT_MODEL,
            "capability_evidence_ref": "host://codex/luna-max",
        }

    def _post(self, _endpoint, _token, path, payload):
        if path == "/v1/task-frame/worker-result":
            self.events.append("terminal_result")
            return {"status": "TASK_COMPLETED"}
        operation = payload["operation"]
        self.events.append(operation["operation"])
        if operation["operation"] == "worker_invocation_plan":
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "WORKER_INVOCATION_READY",
                    "worker_invocation": {
                        "provider": "CODEX",
                        "model": FAST_EXTRACT_MODEL,
                        "input_bundle": {},
                    },
                },
            }
        if operation["operation"] == "claim_turn":
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {
                    "status": "TURN_CLAIMED",
                    "turn": {
                        "turn_id": operation["turn_id"],
                        "state": "CLAIMED",
                        "claimed_by": operation["worker_id"],
                    },
                },
            }
        return {
            "status": "TASK_FRAME_OPERATION_APPLIED",
            "output": {"status": "WORKER_INITIALIZATION_FAILURE_RECORDED"},
        }

    def _invoke_provider(self, _provider, request):
        self.calls.append(request)
        if self.fail_once:
            self.fail_once = False
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED", "WORKER_ADAPTER", "TRANSIENT_TEST_FAILURE"
            )
        ref_digest = request["context_pack"]["activity_batches"][0]["activity_refs"][0][
            "activity_digest"
        ]
        return {
            "status": "COMPLETED",
            "worker_id": "codex-app-server:worker-fast-extract",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": (
                "codex-app-server:receipt-fast-extract:" + request["worker_run_ref"]
            ),
            "result": {
                "text": json.dumps(
                    {
                        "schema": FAST_EXTRACT_RESULT_SCHEMA,
                        "candidates": [
                            {
                                "kind": "MEMORY",
                                "summary": "Keep extracted memories review-only before publication.",
                                "source_range": {"start": 2, "end": 2},
                                "ref_digests": [ref_digest],
                            }
                        ],
                    }
                )
            },
            "session_persistence": "EPHEMERAL",
            "persistent_session_ref": "UNKNOWN",
            "universe_coordinate_persisted": False,
            "provider_durable_chat_state": "NOT_PERSISTED",
        }


class FastExtractContractTests(unittest.TestCase):
    def test_activity_projection_rejects_raw_transcript_and_provider_input_is_redacted(
        self,
    ) -> None:
        batch = redact_activity_batch(
            {
                "source": {
                    "provider": "CODEX",
                    "provider_session_id": "session-1",
                    "source_id": "source-1",
                    "cursor": {"offset": 40, "ordinal": 2},
                },
                "activity_refs": [
                    {
                        "activity_id": "activity-1",
                        "activity_digest": "a" * 64,
                        "ordinal": 2,
                        "event_kind": "TURN_COMPLETED",
                        "activity_state": "COMPLETED",
                    }
                ],
                "raw_transcript": "EXCLUDED",
            }
        )
        request = build_provider_request(
            project_id="TEST",
            activity_batches=[batch],
            semantic_evidence=[
                {
                    "excerpt_id": "semantic-1",
                    "activity_digest": "a" * 64,
                    "ordinal": 2,
                    "role": "USER",
                    "text": "Use a review-only candidate before publishing memory.",
                    "text_digest": __import__("hashlib")
                    .sha256(
                        json.dumps(
                            "Use a review-only candidate before publishing memory.",
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                    .hexdigest(),
                }
            ],
            runtime_binding={
                "task_frame_ref": "task-frame-1",
                "session_id": "session-1",
                "frame_id": "task-frame-1",
                "turn_id": "turn-1",
                "endpoint": "http://127.0.0.1:17777",
                "token": "transient-token",
                "invoker_actor_ref": "boss-1",
            },
            invocation_id="invocation-1",
            config_digest="b" * 64,
            skill_binding_digest="c" * 64,
        )
        encoded = json.dumps(request["context_pack"], sort_keys=True)
        self.assertNotIn("transient-token", encoded)
        self.assertIn("review-only candidate", encoded)
        self.assertNotIn("prompt", encoded.lower())
        self.assertEqual("gpt-5.6-luna", request["context_pack"]["model_ref"])
        candidate_schema = request["output_contract"]["json_schema"]["properties"][
            "candidates"
        ]["items"]
        self.assertEqual("MEMORY", candidate_schema["properties"]["kind"]["const"])
        with self.assertRaises(FastExtractError) as captured:
            redact_activity_batch({**batch, "prompt": "secret"})
        self.assertEqual("FAST_EXTRACT_RAW_INPUT_FORBIDDEN", captured.exception.code)

        copied_span = (
            "one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen"
        )
        with self.assertRaises(FastExtractError) as captured:
            normalize_provider_candidates(
                {
                    "schema": FAST_EXTRACT_RESULT_SCHEMA,
                    "candidates": [{"summary": copied_span, "kind": "MEMORY"}],
                },
                project_id="TEST",
                activity_batches=[batch],
                semantic_evidence=[{"text": copied_span}],
            )
        self.assertEqual(
            "FAST_EXTRACT_RESULT_VERBATIM_FORBIDDEN", captured.exception.code
        )

    def test_provider_result_is_typed_and_redacted(self) -> None:
        batch = redact_activity_batch(
            {
                "source": {
                    "provider": "CODEX",
                    "provider_session_id": "session-1",
                    "source_id": "source-1",
                },
                "activity_refs": [
                    {
                        "activity_id": "activity-1",
                        "activity_digest": "a" * 64,
                        "ordinal": 1,
                        "event_kind": "TURN_COMPLETED",
                        "activity_state": "COMPLETED",
                    }
                ],
            }
        )
        candidates = normalize_provider_candidates(
            {
                "schema": FAST_EXTRACT_RESULT_SCHEMA,
                "candidates": [{"summary": "bounded", "kind": "MEMORY"}],
            },
            project_id="TEST",
            activity_batches=[batch],
            semantic_evidence=[
                {
                    "text": "A different bounded source sentence.",
                }
            ],
        )
        self.assertEqual(1, len(candidates))
        self.assertEqual("REVIEW_REQUIRED", candidates[0]["state"])
        self.assertNotIn("prompt", json.dumps(candidates).lower())
        with self.assertRaises(FastExtractError) as captured:
            normalize_provider_candidates(
                {
                    "schema": FAST_EXTRACT_RESULT_SCHEMA,
                    "candidates": [
                        {
                            "summary": "bounded",
                            "kind": "runtime_entry_policy",
                            "ref_digests": ["a" * 64],
                        }
                    ],
                },
                project_id="TEST",
                activity_batches=[batch],
                semantic_evidence=[{"text": "A different bounded source sentence."}],
            )
        self.assertEqual("MEMORY_CANDIDATE_KIND_INVALID", captured.exception.code)
        with self.assertRaises(FastExtractError) as captured:
            normalize_provider_candidates(
                {
                    "schema": FAST_EXTRACT_RESULT_SCHEMA,
                    "candidates": [{"summary": "bounded", "transcript": "secret"}],
                },
                project_id="TEST",
                activity_batches=[batch],
                semantic_evidence=[{"text": "A different bounded source sentence."}],
            )
        self.assertEqual("FAST_EXTRACT_RAW_INPUT_FORBIDDEN", captured.exception.code)


class FastExtractServerTests(unittest.TestCase):
    def attached_binding(self, session_id, frame_id, token):
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": session_id,
                "node": "TEST",
                "project_id": "TEST",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "provider-" + session_id,
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        class FixtureRuntime:
            def __init__(self, _root, _session):
                pass

            def start(self):
                return {
                    "endpoint": "http://127.0.0.1:17777",
                    "token": token,
                    "session_id": session_id,
                    "origin_anchor_ref": session["session_anchor_ref"],
                    "origin_frame_id": "current",
                    "runtime_currentness_observation": "CURRENT",
                    "binding_evidence_ref": "test-fixture://attachment/" + session_id,
                }

            def stop(self):
                pass

        self.server._session_runtime_factory = FixtureRuntime
        attachment = self.server.ensure_session_runtime_attachment(session)
        return {
            "task_frame_ref": frame_id,
            "frame_id": frame_id,
            "session_id": session_id,
            "session_anchor_ref": attachment["session_anchor_ref"],
            "credential_ref": attachment["credential_ref"],
            "turn_id": "/root/boss/sub1",
            "invoker_actor_ref": "/root/boss",
        }

    def test_runtime_reference_rejects_inline_secret_stale_ref_and_wrong_anchor(self):
        self.configure()
        binding = self.attached_binding(
            "session-credentials", "frame-credentials", "must-not-leak"
        )
        for extra, expected in (
            ({"token": "must-not-leak"}, "ACTION_CREDENTIAL_REF_ONLY"),
            ({"credential_ref": "wrong-reference"}, "RUNTIME_CREDENTIAL_REF_INVALID"),
            (
                {"session_anchor_ref": "wrong-anchor"},
                "RUNTIME_CREDENTIAL_ATTACHMENT_STALE",
            ),
        ):
            status, result = self.request(
                "POST",
                "/v1/projects/TEST/memory-batches/run",
                {
                    "stage": "FAST_EXTRACT",
                    "source_ids": ["unused"],
                    "runtime_binding": {**binding, **extra},
                },
            )
            self.assertIn(status, {400, 409}, result)
            self.assertEqual(expected, result["error_code"])
            self.assertNotIn("must-not-leak", json.dumps(result))
        self.assertEqual([], self.dispatcher.calls)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        catalog_path = root / "provider-models.json"
        catalog_path.write_text(json.dumps(catalog_with_luna()), encoding="utf-8")
        self.dispatcher = FastExtractDispatcher(root)
        self.runtime = UniverseRuntimeHost(root, worker_dispatcher=self.dispatcher)
        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token="fast-extract-test-token",
            runtime_host=self.runtime,
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            provider_model_catalog=ProviderModelCatalogStore(path=catalog_path),
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
            {"project_id": "TEST", "project_root": str(root / "TEST")}
        )
        self.source_path = root / "rollout-fast-extract.jsonl"
        self.source_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": "session-1", "cwd": str(root / "TEST")},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "response_item",
                            "id": "event-1",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "Adopt a review-only candidate boundary. "
                                            "api_key=verysecretvalue"
                                        ),
                                    }
                                ],
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None):
        data = None
        headers = {"Authorization": "Bearer fast-extract-test-token"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.endpoint + path, data=data, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def configure(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/TEST/memory-batch-config",
            {
                "stage": "FAST_EXTRACT",
                "provider": "CODEX",
                "model_ref": FAST_EXTRACT_MODEL,
                "effort": FAST_EXTRACT_EFFORT,
                "schedule": {"kind": "MANUAL"},
                "quota_or_budget": {"max_runs": 2},
                "fallback": "NONE",
                "enabled": True,
                "dry_run": False,
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AVAILABLE", result["config"]["resolution"]["status"])

    def test_codex_activity_to_fast_extract_candidate_and_bench_is_idempotent(
        self,
    ) -> None:
        status, registered = self.request(
            "POST",
            "/v1/session-observer/sources",
            {
                "source_id": "source-1",
                "provider": "CODEX",
                "provider_session_id": "session-1",
                "source_path": str(self.source_path),
                "source_kind": "CODEX_ROLLOUT_JSONL",
                "source_version": "v1",
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual({"offset": 0, "ordinal": 0}, registered["source"]["cursor"])
        status, scanned = self.request(
            "POST", "/v1/session-observer/sources/source-1/scan", {}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(2, scanned["source"]["cursor"]["ordinal"])
        status, rescanned = self.request(
            "POST", "/v1/session-observer/sources/source-1/scan", {}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(0, rescanned["added"])
        self.assertEqual(scanned["source"]["cursor"], rescanned["source"]["cursor"])
        status, activities = self.request(
            "GET", "/v1/session-observer/activities?source_id=source-1"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(activities["activities"])
        self.assertNotIn("secret transcript", json.dumps(activities).lower())

        self.configure()
        runtime_binding = self.attached_binding(
            "session-1", "frame-1", "transient-task-frame-token"
        )
        run_body = {
            "stage": "FAST_EXTRACT",
            "source_ids": ["source-1"],
            "runtime_binding": runtime_binding,
        }
        status, completed = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", run_body
        )
        self.assertEqual(HTTPStatus.OK, status, completed)
        self.assertEqual("GOVERNED_TASK_FRAME", completed["execution"]["mode"])
        self.assertEqual("COMPLETED", completed["execution"]["provider_invocation"])
        self.assertEqual(
            f"provider://CODEX/model/{FAST_EXTRACT_MODEL}",
            completed["execution"]["model_ref"],
        )
        self.assertEqual(1, completed["run"]["created_count"])
        self.assertEqual(1, completed["skill_observation"]["observation_count"])
        self.assertEqual(1, len(self.dispatcher.calls))
        self.assertEqual(
            ["worker_invocation_plan", "claim_turn", "terminal_result"],
            self.dispatcher.events,
        )
        context_json = json.dumps(
            self.dispatcher.calls[0]["context_pack"], sort_keys=True
        )
        self.assertNotIn("transient-task-frame-token", context_json)
        self.assertIn("review-only candidate boundary", context_json.lower())
        self.assertNotIn("verysecretvalue", context_json.lower())
        self.assertIn("[redacted]", context_json.lower())
        status, runs = self.request("GET", "/v1/projects/TEST/memory-batches/runs")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(runs["runs"]))
        persisted_json = json.dumps(runs).lower()
        self.assertNotIn("transient-task-frame-token", persisted_json)
        self.assertNotIn("secret transcript", persisted_json)

        status, candidates = self.request("GET", "/v1/projects/TEST/memory-candidates")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("REVIEW_REQUIRED", candidates["candidates"][0]["state"])
        self.assertNotIn("secret transcript", json.dumps(candidates).lower())
        status, observations = self.request(
            "GET", "/v1/projects/TEST/skill-observations"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(observations["observations"]))
        observation = observations["observations"][0]
        self.assertEqual("universe.skill-run-observation.v1", observation["schema"])
        self.assertEqual(
            f"provider://CODEX/model/{FAST_EXTRACT_MODEL}", observation["model_ref"]
        )
        self.assertEqual("SUCCEEDED", observation["outcome"])
        self.assertEqual(
            "TASK_FRAME_WORKER", observation["execution_context"]["worker_role"]
        )
        self.assertEqual(
            completed["execution"]["evidence_refs"], observation["evidence_refs"]
        )
        self.assertGreaterEqual(observation["metrics"]["duration_ms"], 0)
        self.assertNotIn("secret transcript", json.dumps(observation).lower())
        status, bench = self.request("GET", "/v1/bench/skills")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(bench["bench"]))
        self.assertEqual(1, bench["bench"][0]["observation_count"])
        self.assertEqual(1, bench["bench"][0]["outcomes"]["SUCCEEDED"])
        self.assertEqual(
            observation["metrics"]["duration_ms"],
            bench["bench"][0]["metric_totals"]["duration_ms"],
        )
        self.assertNotIn("secret transcript", json.dumps(bench).lower())

        candidate_id = candidates["candidates"][0]["candidate_id"]
        status, reviewed = self.request(
            "POST",
            f"/v1/memory-candidates/{candidate_id}/review",
            {"decision": "KEEP"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("KEEP", reviewed["candidate"]["state"])
        self.assertEqual([], self.server.store.list_project_memories("TEST"))

        status, repeated = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", run_body
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("MEMORY_BATCH_RUN_ALREADY_RECORDED", repeated["status"])
        self.assertEqual(1, len(self.dispatcher.calls))

    def test_failed_fast_extract_run_can_retry_same_input_once(self) -> None:
        self._exercise_retry(reject_before_retry=False)

    def test_ignored_failure_candidate_is_not_in_retry_context(self) -> None:
        self._exercise_retry(reject_before_retry=True)

    def _exercise_retry(self, *, reject_before_retry: bool) -> None:
        status, _registered = self.request(
            "POST",
            "/v1/session-observer/sources",
            {
                "source_id": "source-retry",
                "provider": "CODEX",
                "provider_session_id": "session-retry",
                "source_path": str(self.source_path),
                "source_kind": "CODEX_ROLLOUT_JSONL",
                "source_version": "v1",
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        status, _scanned = self.request(
            "POST", "/v1/session-observer/sources/source-retry/scan", {}
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.configure()
        run_body = {
            "stage": "FAST_EXTRACT",
            "source_ids": ["source-retry"],
            "runtime_binding": self.attached_binding(
                "session-retry", "frame-retry", "transient-retry-token"
            ),
        }
        self.dispatcher.fail_once = True
        failure_candidate = json.loads(
            (ROOT / "docs/examples/goal-completion-failure.json").read_text(
                encoding="utf-8"
            )
        )
        failure_candidate["summary"] = (
            "Fixture-only transient Worker failure; retry after the adapter recovers."
        )
        failure_candidate["failure"].update(
            failure_ref="fixture-worker-failure",
            component="memory_batch",
            operation="fast_extract",
            error_code="WORKER_PROVIDER_FAILED",
            symptom="The test Worker adapter failed its first invocation.",
            cause={
                "key": "fixture-transient-adapter",
                "summary": "Injected first-attempt failure in the test adapter.",
                "state": "CONFIRMED",
            },
            remedy={
                "summary": "Retry after the fixture adapter has recovered.",
                "applicability": {"execution_plane": "governed_task_frame"},
            },
            validation=[
                {
                    "plane": "fixture",
                    "status": "PASS",
                    "evidence_refs": ["test-run://fast-extract/retry-fixture"],
                }
            ],
            limitations=[
                "A fixture, not a real provider failure or validated production remedy."
            ],
            evidence_refs=["test-run://fast-extract/retry-fixture"],
        )
        status, saved_candidate = self.request(
            "POST", "/v1/projects/TEST/memory-candidates", failure_candidate
        )
        self.assertEqual(201, status, saved_candidate)
        status, failed = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", run_body
        )
        self.assertEqual(HTTPStatus.CONFLICT, status, failed)
        status, runs = self.request("GET", "/v1/projects/TEST/memory-batches/runs")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FAILED", runs["runs"][0]["status"])
        self.assertEqual(1, runs["runs"][0]["result"]["attempt"])
        first_evidence = runs["runs"][0]["result"]["attempt_evidence"]
        recalled = first_evidence["failure_recall"]
        self.assertEqual(
            saved_candidate["candidate"]["candidate_id"],
            recalled["matches"][0]["candidate_id"],
        )
        self.assertTrue(recalled["matches"][0]["match"]["cause_confirmation_required"])
        self.assertEqual({}, recalled["matches"][0]["reuse_outcomes"])

        if reject_before_retry:
            status, ignored = self.request(
                "POST",
                "/v1/projects/TEST/memory-candidates/review",
                {
                    "candidate_id": saved_candidate["candidate"]["candidate_id"],
                    "decision": "IGNORE",
                },
            )
            self.assertEqual(200, status, ignored)

        status, completed = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", run_body
        )
        self.assertEqual(HTTPStatus.OK, status, completed)
        self.assertEqual("COMPLETED", completed["run"]["status"])
        self.assertEqual(2, completed["run"]["attempt"])
        terminal_evidence = completed["run"]["attempt_evidence"]
        self.assertEqual(
            first_evidence["source_ref"], terminal_evidence["previous_attempt_ref"]
        )
        self.assertEqual("COMPLETED", terminal_evidence["status"])
        self.assertEqual("UNKNOWN", terminal_evidence["remedy_application"])
        self.assertEqual("UNKNOWN", terminal_evidence["cause_resolution"])
        self.assertEqual(
            "CONTEXT_PREPARED_NOT_PROVEN_APPLIED",
            terminal_evidence["candidate_use_basis"],
        )
        self.assertEqual(
            terminal_evidence["retry_failure_recall"],
            self.dispatcher.calls[1]["context_pack"]["failure_reuse"]["recall"],
        )
        self.assertEqual(
            0 if reject_before_retry else 1,
            len(terminal_evidence["retry_failure_recall"]["matches"]),
        )
        status, history = self.request(
            "GET",
            "/v1/projects/TEST/failure-reuse/batch-attempts?run_id="
            + completed["run"]["run_id"],
        )
        self.assertEqual(200, status, history)
        self.assertEqual([terminal_evidence, first_evidence], history["attempts"])
        # The append-only evidence survives reopening; original failure and
        # candidate validation are not replaced by the successful retry.
        from universe_server import UniverseStore

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            history,
            reopened.failure_reuse_batch_attempts("TEST", completed["run"]["run_id"]),
        )
        replay_status, replay_result = self.request(
            "POST", "/v1/projects/TEST/memory-batches/run", run_body
        )
        self.assertEqual(200, replay_status, replay_result)
        self.assertEqual("MEMORY_BATCH_RUN_ALREADY_RECORDED", replay_result["status"])
        self.assertEqual(2, len(self.dispatcher.calls))
        status, repeated_history = self.request(
            "GET",
            "/v1/projects/TEST/failure-reuse/batch-attempts?run_id="
            + completed["run"]["run_id"],
        )
        self.assertEqual(history, repeated_history)
        self.assertEqual(1, completed["run"]["created_count"])
        status, observations = self.request(
            "GET", "/v1/projects/TEST/skill-observations"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(observations["observations"]))


if __name__ == "__main__":
    unittest.main()
