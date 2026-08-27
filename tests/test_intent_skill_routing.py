from __future__ import annotations

import hashlib
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

from host_profile import HostProfileStore  # noqa: E402
from intent_skill_routing import (  # noqa: E402
    IntentRoutingError,
    SKILL_PACK_MANIFEST_SCHEMA,
    build_adopted_registry_snapshot,
    digest,
    empty_registry_snapshot,
    execute_plan_fallback,
    normalize_intent_decision,
    normalize_planning_phrase,
    normalize_registry_snapshot,
    normalize_skill_pack_manifest,
    resolve_skill,
)
from universe_server import create_server, universe_mode_contract  # noqa: E402


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def decision_request(*, intent_class: str = "PLAN_REQUEST", capability: str = "PLAN_CREATE", effect: str = "NONE", route: str = "RESOLVE_SKILL") -> dict:
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    current = {"message_id": "msg-plan-1", "role": "USER", "digest": sha("계획 좀 짜볼래"), "observed_at": observed_at}
    prior = [{"message_id": "msg-context-1", "role": "ASSISTANT", "digest": sha("bounded context")}]
    context = [{"message_id": current["message_id"], "role": "USER", "digest": current["digest"]}, *prior]
    return {
        "session_id": "session-intent-test",
        "frame_id": "current",
        "anchor_id": "MASTER-CURRENT-TEST",
        "utterance_ref": "session-bus:msg-plan-1",
        "context_digest": digest(context),
        "intent_class": intent_class,
        "imperative_state": "EXPLICIT",
        "target_state": "EXACT",
        "required_capability": capability,
        "effect_class": effect,
        "route": route,
        "confirmation_of": None,
        "project_id": "universe",
        "node_ref": None,
        "evidence": {
            "current_message": current,
            "prior_messages": prior,
            "coordinates": {"session_id": "session-intent-test", "frame_id": "current", "anchor_id": "MASTER-CURRENT-TEST"},
        },
    }


class IntentSkillRoutingUnitTests(unittest.TestCase):
    def test_colloquial_planning_phrases_share_one_capability(self) -> None:
        for phrase in ("계획 좀 짜볼래", "플랜 만들어줘", "build a plan"):
            self.assertEqual("PLAN_CREATE", normalize_planning_phrase(phrase)["required_capability"])

    def test_read_only_intent_cannot_be_reinterpreted_as_effectful(self) -> None:
        with self.assertRaises(IntentRoutingError) as caught:
            normalize_intent_decision(decision_request(intent_class="QUESTION", effect="RUNTIME_STATE_WRITE", route="RESOLVE_SKILL"))
        self.assertEqual("SKILL_EFFECT_MISMATCH", caught.exception.code)

    def test_intent_evidence_rejects_provenance_staleness_and_coordinate_mismatch(self) -> None:
        invalid_provenance = decision_request()
        invalid_provenance["evidence"]["current_message"]["role"] = "ASSISTANT"
        with self.assertRaises(IntentRoutingError) as caught:
            normalize_intent_decision(invalid_provenance)
        self.assertEqual("INTENT_EVIDENCE_INVALID", caught.exception.code)

        stale = decision_request()
        with self.assertRaises(IntentRoutingError) as caught:
            normalize_intent_decision(stale, now=datetime.now(timezone.utc) + timedelta(minutes=6))
        self.assertEqual("INTENT_DECISION_STALE", caught.exception.code)

        mismatched = decision_request()
        mismatched["evidence"]["coordinates"]["anchor_id"] = "MASTER-CURRENT-OTHER"
        with self.assertRaises(IntentRoutingError) as caught:
            normalize_intent_decision(mismatched)
        self.assertEqual("INTENT_EVIDENCE_INVALID", caught.exception.code)

    def test_project_skill_precedes_universe_and_equal_priority_is_ambiguous(self) -> None:
        decision = normalize_intent_decision(decision_request())
        snapshot = normalize_registry_snapshot({
            "skills": [
                {"skill_id": "common-plan", "version": "1", "scope": "COMMON", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 100},
                {"skill_id": "universe-plan", "version": "1", "scope": "UNIVERSE", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 100},
                {"skill_id": "project-plan", "version": "1", "scope": "PROJECT", "project_id": "universe", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 10},
            ]
        })
        selected = resolve_skill(decision, snapshot, {"project_id": "universe"})
        self.assertEqual("project-plan", selected["selected_skill_id"])
        ambiguous = normalize_registry_snapshot({"skills": [
            {"skill_id": "plan-a", "version": "1", "scope": "UNIVERSE", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 100},
            {"skill_id": "plan-b", "version": "1", "scope": "UNIVERSE", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 100},
        ]})
        with self.assertRaises(IntentRoutingError) as caught:
            resolve_skill(decision, ambiguous, {})
        self.assertEqual("SKILL_RESOLUTION_AMBIGUOUS", caught.exception.code)

    def test_explicit_compatible_skill_selection_outranks_automatic_scope(self) -> None:
        decision = normalize_intent_decision(decision_request())
        snapshot = normalize_registry_snapshot({"skills": [
            {"skill_id": "project-plan", "version": "1", "scope": "PROJECT", "project_id": "universe", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 100},
            {"skill_id": "common-plan", "version": "1", "scope": "COMMON", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 1},
        ]})
        selected = resolve_skill(decision, snapshot, {"explicit_skill_id": "common-plan"})
        self.assertEqual("common-plan", selected["selected_skill_id"])
        self.assertEqual("EXPLICIT", selected["selection_scope"])

    def test_skill_pack_manifest_is_digest_addressed_and_scope_bound(self) -> None:
        manifest = normalize_skill_pack_manifest({
            "schema": SKILL_PACK_MANIFEST_SCHEMA,
            "pack_id": "common.memory-sync",
            "version": "1",
            "scope": "COMMON",
            "artifact": {"source_ref": "release://common.memory-sync/1", "sha256": sha("artifact")},
            "skills": [
                {"skill_id": "memory-sync", "version": "1", "scope": "COMMON", "intents": ["MEMORY_SYNC_REQUEST"], "capabilities": ["MEMORY_SYNC"], "effects": ["NONE"], "output_contract": "universe.memory-candidates.v1", "priority": 100},
            ],
        })
        self.assertEqual("skill_pack_" + manifest["manifest_digest"][:24], manifest["release_id"])
        adopted = build_adopted_registry_snapshot(empty_registry_snapshot(), manifest)
        self.assertEqual("memory-sync", adopted["skills"][0]["skill_id"])
        invalid = dict(manifest)
        invalid.pop("manifest_digest")
        invalid.pop("release_id")
        invalid["artifact"] = {"source_ref": "release://common.memory-sync/1", "sha256": "not-a-digest"}
        with self.assertRaises(IntentRoutingError) as caught:
            normalize_skill_pack_manifest(invalid)
        self.assertEqual("SKILL_PACK_MANIFEST_INVALID", caught.exception.code)

    def test_effectful_fallback_fails_closed_without_registered_adapter(self) -> None:
        for capability, effect, handler in (
            ("TODO_WRITE", "RUNTIME_STATE_WRITE", "TODO_ADAPTER"),
            ("DOCUMENT_CREATE", "USER_ARTIFACT_WRITE", "DOCUMENT_ARTIFACT_WRITER"),
        ):
            decision = normalize_intent_decision(decision_request(intent_class="TASK_EXECUTE", capability=capability, effect=effect))
            with self.assertRaises(IntentRoutingError) as caught:
                resolve_skill(decision, empty_registry_snapshot(), {})
            self.assertEqual("FALLBACK_HANDLER_UNAVAILABLE", caught.exception.code)
            resolved = resolve_skill(decision, empty_registry_snapshot(), {"available_fallback_handlers": [handler]})
            self.assertEqual(handler, resolved["fallback_handler"])
            self.assertEqual("NONE", resolved["effects"]["mutation_permission"])

    def test_missing_skill_uses_contract_compatible_non_mutating_fallback(self) -> None:
        decision = normalize_intent_decision(decision_request())
        resolution = resolve_skill(decision, empty_registry_snapshot(), {})
        self.assertTrue(resolution["fallback_used"])
        plan = execute_plan_fallback(resolution, {"goal": "Ship the slice", "constraints": ["No mutation"]})
        self.assertEqual("universe.structured-plan.v1", plan["schema"])
        self.assertEqual("NONE", plan["effects"]["mutation_permission"])


class IntentSkillRoutingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project_root = root / "universe"
        project_root.mkdir()
        (project_root / "REPOSITORY_MANIFEST.md").write_text("# universe\n", encoding="utf-8")
        self.token = "intent-test-token"
        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token=self.token,
            auto_start_project_masters=False,
            mode_contract=universe_mode_contract({"owner": "universe", "policy": "MASTER_MANAGED", "root_mode": "MASTER", "revision": 1, "modes": {"MASTER": {"role": "MASTER", "scope": "architecture/governance", "mode_profile": "GOVERNANCE_ONLY"}, "CONDUCTOR": {"role": "CONDUCTOR", "scope": "project-network/navigation/distribution", "mode_profile": "GOVERNANCE_ONLY"}}}),
            host_profile=HostProfileStore(root / "host.json"),
            service_state_path=root / "server.json",
            remote_gateway_state_path=root / "remote-gateway.json",
            remote_connector_state_path=root / "remote-connector.json",
            remote_connector_config_path=root / "remote-connector-config.json",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, method: str, path: str, payload: object | None = None) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def test_plan_fallback_gap_candidate_and_registry_adoption_slice(self) -> None:
        status, recorded = self.request("POST", "/v1/intent-decisions", decision_request())
        self.assertEqual(HTTPStatus.CREATED, status)
        decision = recorded["decision"]
        status, resolved = self.request("POST", "/v1/skill-resolutions", {"intent_decision_id": decision["decision_id"], "project_id": "universe"})
        self.assertEqual(HTTPStatus.CREATED, status)
        fallback = resolved["resolution"]
        self.assertTrue(fallback["fallback_used"])
        status, completed = self.request("POST", f"/v1/skill-resolutions/{fallback['resolution_id']}/fallback", {"goal": "Implement routing", "success_criteria": ["API and UI agree"]})
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("universe.structured-plan.v1", completed["result"]["schema"])
        observation = {
            "observation_id": "skill_gap_plan_1",
            "intent_decision_id": decision["decision_id"],
            "resolution_id": fallback["resolution_id"],
            "intent_class": "PLAN_REQUEST",
            "capability": "PLAN_CREATE",
            "effect_class": "NONE",
            "fallback_handler": "GENERIC_STRUCTURED_REASONING",
            "output_contract": "universe.structured-plan.v1",
            "outcome": "SUCCESS",
            "validation_state": "VALIDATED",
            "user_revision_state": "NOT_REQUIRED",
            "context_fingerprint": sha("context-a"),
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        status, first = self.request("POST", "/v1/projects/universe/skill-gap-observations", observation)
        self.assertEqual(HTTPStatus.CREATED, status)
        status, repeated = self.request("POST", "/v1/projects/universe/skill-gap-observations", observation)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(first["observation"]["observation_digest"], repeated["observation"]["observation_digest"])
        status, summary = self.request("GET", "/v1/projects/universe/skill-gap-summary")
        self.assertEqual(1, summary["summary"]["observation_count"])
        status, candidate_result = self.request("POST", "/v1/projects/universe/skill-candidates", {"capability": "PLAN_CREATE", "output_contract": "universe.structured-plan.v1", "threshold_policy": {"version": "dogfood-v1", "min_distinct_contexts": 1, "min_validated_successes": 1, "max_high_severity_failures": 0}})
        self.assertEqual(HTTPStatus.CREATED, status)
        candidate = candidate_result["candidate"]
        self.assertEqual("ELIGIBLE", candidate["candidate_state"])
        self.assertEqual("NOT_INSTALLED", candidate["installation_state"])
        manifest = {
            "schema": SKILL_PACK_MANIFEST_SCHEMA,
            "pack_id": "universe.project-planning",
            "version": "1",
            "scope": "PROJECT",
            "project_id": "universe",
            "artifact": {"source_ref": "release://planning-v1", "sha256": sha("planning-v1-artifact")},
            "skills": [{"skill_id": "project-planning", "version": "1", "scope": "PROJECT", "project_id": "universe", "intents": ["PLAN_REQUEST"], "capabilities": ["PLAN_CREATE"], "effects": ["NONE"], "output_contract": "universe.structured-plan.v1", "priority": 100}],
        }
        adoption_request = {
            "manifest": manifest,
            "actor": {"kind": "USER", "actor_ref": "user.konezero", "decision_ref": "ui-action.adopt-planning-v1"},
        }
        denied_request = {
            **adoption_request,
            "actor": {"kind": "CONDUCTOR", "actor_ref": "conductor.codex", "decision_ref": "automation.try-adopt"},
        }
        status, denied = self.request("POST", "/v1/skill-release-adoptions", denied_request)
        self.assertEqual(HTTPStatus.FORBIDDEN, status)
        self.assertEqual("SKILL_RELEASE_ADOPTION_USER_REQUIRED", denied["error_code"])
        status, adopted = self.request("POST", "/v1/skill-release-adoptions", adoption_request)
        self.assertEqual(HTTPStatus.CREATED, status)
        adoption = adopted["adoption"]
        self.assertEqual("ADOPTED", adoption["status"])
        self.assertEqual("NONE", adoption["effects"]["runtime_mutation_permission"])
        status, repeated = self.request("POST", "/v1/skill-release-adoptions", adoption_request)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(adoption["adoption_id"], repeated["adoption"]["adoption_id"])
        status, adoptions = self.request("GET", "/v1/skill-release-adoptions")
        self.assertEqual(1, len(adoptions["adoptions"]))
        status, installed_resolution = self.request("POST", "/v1/skill-resolutions", {"intent_decision_id": decision["decision_id"], "project_id": "universe"})
        self.assertEqual("project-planning", installed_resolution["resolution"]["selected_skill_id"])
        conflicting = json.loads(json.dumps(adoption_request))
        conflicting["manifest"]["artifact"]["sha256"] = sha("different-artifact")
        status, conflict = self.request("POST", "/v1/skill-release-adoptions", conflicting)
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("SKILL_RELEASE_ADOPTION_VERSION_CONFLICT", conflict["error_code"])
        status, old = self.request("GET", f"/v1/skill-resolutions/{fallback['resolution_id']}")
        self.assertTrue(old["resolution"]["fallback_used"])


if __name__ == "__main__":
    unittest.main()
