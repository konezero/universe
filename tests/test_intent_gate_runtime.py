from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / ".ai" / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from reference_runtime.anchor_session_memory_adapter import (  # noqa: E402
    AnchorSessionMemoryHostServer,
    call_host_adapter,
)
from reference_runtime.intent_gate_runtime import evaluate_intent_gate  # noqa: E402


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def payload(**classification_overrides: object) -> dict[str, object]:
    classification = {
        "classifier_kind": "HOST",
        "classifier_ref": "host://classifier/test",
        "intent_class": "PLAN_REQUEST",
        "route": "SKILL_RESOLUTION",
        "effect_class": "NONE",
        "explicit_imperative": True,
        "target_state": "EXACT",
        "permission_shaped": False,
        "token_match_only": False,
        "mentioned_runtime_tokens": [],
    }
    classification.update(classification_overrides)
    return {
        "session_id": "session-intent-gate",
        "frame_id": "current",
        "anchor_id": "MASTER-CURRENT-INTENT-GATE",
        "utterance_ref": "session-bus:message-intent-gate",
        "current_message": {
            "message_id": "message-intent-gate",
            "role": "USER",
            "digest": hashlib.sha256("계획 짜줘".encode("utf-8")).hexdigest(),
            "observed_at": now(),
        },
        "classification": classification,
    }


class IntentGateRuntimeTests(unittest.TestCase):
    def test_active_commander_wait_blocks_before_intent_routing(self) -> None:
        request = payload(classifier_kind="UNTRUSTED", route="MODE_CHANGE")
        decision = evaluate_intent_gate(
            wait_state={
                "schema": "ai-career.commander-wait.v1",
                "status": "WAITING",
                "session_id": request["session_id"],
                "anchor_id": request["anchor_id"],
            },
            payload=request,
            observed_at=now(),
        )
        self.assertEqual("INTENT_GATE_WAITING_COMMANDER", decision["status"])
        self.assertEqual("COMMANDER_WAIT", decision["stage"])
        self.assertFalse(decision["routing_allowed"])

    def test_released_wait_allows_host_classified_skill_route(self) -> None:
        request = payload()
        decision = evaluate_intent_gate(
            wait_state={
                "schema": "ai-career.commander-wait.v1",
                "status": "CLOSED",
                "session_id": request["session_id"],
                "anchor_id": request["anchor_id"],
            },
            payload=request,
            observed_at=now(),
        )
        self.assertEqual("INTENT_GATE_PASSED", decision["status"])
        self.assertTrue(decision["routing_allowed"])
        self.assertEqual("UNASSIGNED", decision["authority"])

    def test_token_only_and_permission_shaped_execution_are_blocked(self) -> None:
        token_only = evaluate_intent_gate(
            wait_state=None,
            payload=payload(intent_class="MODE_SWITCH", route="MODE_CHANGE", effect_class="RUNTIME_STATE_WRITE", token_match_only=True, mentioned_runtime_tokens=["MASTER"]),
            observed_at=now(),
        )
        self.assertEqual("TOKEN_MATCH_IS_NOT_ROUTING_AUTHORITY", token_only["reason"])
        permission = evaluate_intent_gate(
            wait_state=None,
            payload=payload(intent_class="TASK_EXECUTE", route="COMMAND", effect_class="BOUNDED_LOCAL_WORK", permission_shaped=True, explicit_imperative=False),
            observed_at=now(),
        )
        self.assertEqual("PERMISSION_IS_NOT_EXECUTION_AUTHORIZATION", permission["reason"])


class IntentGateHostAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = AnchorSessionMemoryHostServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        request = payload()
        status, result = call_host_adapter(
            endpoint=self.server.endpoint,
            token=self.server.token,
            method="POST",
            path="/v1/anchor-session-memory/activate",
            payload={
                "session_id": request["session_id"],
                "anchor_mode": "MASTER",
                "source_ref": "source:intent-gate-test",
                "snapshot": {
                    "frame_id": request["frame_id"],
                    "anchor_id": request["anchor_id"],
                    "state": "READY",
                    "observed_at": now(),
                },
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("HOST_SESSION_MEMORY_ACTIVATED", result["status"])

    def post(self, path: str, value: dict[str, object]) -> dict[str, object]:
        status, result = call_host_adapter(
            endpoint=self.server.endpoint,
            token=self.server.token,
            method="POST",
            path=path,
            payload=value,
        )
        self.assertEqual(200, status)
        return result

    def test_http_gate_observes_wait_then_persists_released_decision(self) -> None:
        request = payload()
        wait = {
            "session_id": request["session_id"],
            "anchor_id": request["anchor_id"],
            "operation": "OPEN",
            "event_id": "wait-open",
            "evidence_ref": "user://wait",
        }
        self.assertEqual("COMMANDER_WAIT_WAITING", self.post("/v1/anchor-session-memory/commander-wait", wait)["status"])
        self.assertEqual("INTENT_GATE_WAITING_COMMANDER", self.post("/v1/anchor-session-memory/intent-gate", request)["status"])
        wait.update({"operation": "CLOSE", "event_id": "wait-close", "evidence_ref": "user://continue"})
        self.assertEqual("COMMANDER_WAIT_CLOSED", self.post("/v1/anchor-session-memory/commander-wait", wait)["status"])
        passed = self.post("/v1/anchor-session-memory/intent-gate", payload())
        self.assertEqual("INTENT_GATE_PASSED", passed["status"])
        self.assertEqual("INTENT", passed["decision"]["stage"])
        self.assertEqual("NONE", passed["decision"]["mutation_permission"])


if __name__ == "__main__":
    unittest.main()
