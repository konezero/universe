"""Node-scoped MASTER persona automation ownership (2026-09-15 design).

사용자 확정 설계: 프로젝트 Conductor는 목표/상위 설계/전체 조율(project-wide,
node_ref 없음, 기존 동작 그대로), 노드 Master는 담당 feature_node의 세부
계획/Todo/Worker 실행을 소유한다(session_persona_assignment.node_ref로
durable하게 결속, 요청 시 client가 임의로 주장할 수 없다).

This is not a rename of the CONDUCTOR-only check to also accept the string
"MASTER" -- a MASTER Anchor must carry an ACTIVE assignment whose node_ref
names a real feature_node in the same project (tools/universe_server.py's
_validate_persona_assignment_node_ref / _validate_persona_automation_anchor),
and persona_automation_run pins that node_ref at start_run so it cannot be
widened later by a request field. Goal/Todo selection during
persona.automation.plan is confined to that node via the existing
Goal.scope_kind/node_ref graph contract -- no new enum, no bypass write.

Runs against a real in-process server/DB via the existing
MemoryCandidateApiTests fixture (project "TEST").
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures  # noqa: E402


class NodeMasterAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def act(self, action_id, request):
        body = dict(request)
        if action_id in {
            "persona.create", "persona.assign", "persona.automation.start",
            "persona.automation.pause", "persona.automation.resume",
            "persona.automation.stop", "persona.automation.complete",
        }:
            body.setdefault("request_id", f"node-master-{action_id.replace('.', '-')}-{uuid.uuid4().hex}")
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": body})

    def register(self, mode, session_id):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": "TEST", "mode": mode, "provider": "CODEX",
        })
        return material["session_anchor_ref"]

    def make_feature_node(self, key):
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": key, "title": key, "intent_text": "테스트용 노드",
            "created_by_role": "USER",
        })
        return feature["feature_id"]

    def make_persona(self, title="node lead"):
        status, result = self.act("persona.create", {"title": title, "body": "담당 노드 범위 안의 작업만 다룬다."})
        self.assertEqual(200, status, result)
        return result["persona"]

    def make_node_goal(self, node_ref, title="Node Goal"):
        return self.server.store.create_goal("TEST", {
            "title": title, "description": "node-scoped", "owner": "node master",
            "state": "READY", "sort_order": 0, "scope_kind": "NODE", "node_ref": node_ref,
        })

    # -- assignment: durable node binding --------------------------------

    def test_master_assign_without_node_ref_still_works_like_before(self):
        # Ordinary P1 persona assignment (a natural-language persona applied
        # to any session, regardless of mode) is unchanged -- node_ref is
        # optional here. The narrower requirement only bites later, at
        # automation start (see test_master_without_node_assignment_cannot_start_automation).
        anchor = self.register("MASTER", "node-master-no-noderef")
        persona = self.make_persona()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, result)
        self.assertIsNone(result["assignment"]["node_ref"])

    def test_master_assign_with_nonexistent_node_is_rejected(self):
        anchor = self.register("MASTER", "node-master-bad-noderef")
        persona = self.make_persona()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": "feature_does_not_exist",
        })
        self.assertEqual(404, status, result)
        self.assertEqual("FEATURE_NODE_NOT_FOUND", result["error_code"])

    def test_master_assign_with_a_different_projects_node_is_rejected(self):
        other_root = Path(self.fixture.temp.name) / "OTHER_NODE_MASTER"
        if not other_root.exists():
            other_root.mkdir()
            (other_root / "REPOSITORY_MANIFEST.md").write_text("# OTHER\n", encoding="utf-8")
            self.server.store.register_project({"project_id": "OTHER_NODE_MASTER", "project_root": str(other_root)})
        other_feature, _ = self.server.store.create_feature_node("OTHER_NODE_MASTER", {
            "idempotency_key": "other-node", "title": "other", "intent_text": "다른 프로젝트 노드",
            "created_by_role": "USER",
        })
        anchor = self.register("MASTER", "node-master-cross-project")
        persona = self.make_persona()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": other_feature["feature_id"],
        })
        self.assertEqual(409, status, result)
        self.assertEqual("PERSONA_ASSIGNMENT_NODE_PROJECT_MISMATCH", result["error_code"])

    def test_conductor_automation_ignores_any_stored_node_ref(self):
        # Even if a CONDUCTOR Anchor's assignment happened to carry a
        # node_ref, automation ownership resolution never consults it for
        # CONDUCTOR mode -- the project-wide path is unconditional.
        anchor = self.register("CONDUCTOR", "node-master-conductor-with-node")
        persona = self.make_persona()
        node_ref = self.make_feature_node("conductor-node-ignored")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "x", "instruction": "x",
        })
        self.assertEqual(201, status, started)
        self.assertIsNone(started["run"]["node_ref"])
        # Free the project-wide bucket for other tests in this class.
        status, stopped = self.act("persona.automation.stop", {"run_id": started["run"]["run_id"]})
        self.assertEqual(200, status, stopped)

    def test_master_assign_with_valid_node_ref_succeeds_and_reads_back(self):
        anchor = self.register("MASTER", "node-master-valid")
        persona = self.make_persona()
        node_ref = self.make_feature_node("valid-node-assign")
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, result)
        self.assertEqual(node_ref, result["assignment"]["node_ref"])
        status, read_back = self.act("persona.assignment-read", {"session_anchor_ref": anchor})
        self.assertEqual(200, status, read_back)
        self.assertEqual(node_ref, read_back["assignment"]["node_ref"])

    # -- automation start: server-verified, not client-claimed -----------

    def test_master_without_node_assignment_cannot_start_automation(self):
        anchor = self.register("MASTER", "node-master-no-run-noderef")
        persona = self.make_persona()
        # No node_ref given -> assign itself is already rejected upstream,
        # so there is no assignment at all for this Anchor.
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor,
            "scope": "x", "instruction": "x",
        })
        self.assertEqual(409, status, started)
        self.assertEqual("PERSONA_AUTOMATION_NODE_ASSIGNMENT_REQUIRED", started["error_code"])

    def test_master_with_node_assignment_can_start_a_node_scoped_run(self):
        anchor = self.register("MASTER", "node-master-run-ok")
        persona = self.make_persona()
        node_ref = self.make_feature_node("run-ok-node")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor,
            "scope": "node bounded test", "instruction": "work only within the owned node",
        })
        self.assertEqual(201, status, started)
        self.assertEqual(node_ref, started["run"]["node_ref"])

    def test_two_different_nodes_can_each_run_concurrently(self):
        persona = self.make_persona()
        node_a = self.make_feature_node("concurrent-node-a")
        node_b = self.make_feature_node("concurrent-node-b")
        anchor_a = self.register("MASTER", "node-master-concurrent-a")
        anchor_b = self.register("MASTER", "node-master-concurrent-b")
        for anchor, node_ref in ((anchor_a, node_a), (anchor_b, node_b)):
            status, assigned = self.act("persona.assign", {
                "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
                "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
                "node_ref": node_ref,
            })
            self.assertEqual(200, status, assigned)
        status_a, started_a = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor_a, "scope": "a", "instruction": "a",
        })
        status_b, started_b = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor_b, "scope": "b", "instruction": "b",
        })
        self.assertEqual(201, status_a, started_a)
        self.assertEqual(201, status_b, started_b)
        self.assertNotEqual(started_a["run"]["run_id"], started_b["run"]["run_id"])

    def test_same_node_cannot_be_assigned_to_a_second_master(self):
        # 2026-09-15 follow-up (Conductor review of the queue slice): node
        # ownership is exclusive at the assignment layer itself now (a
        # partial unique index on session_persona_assignment), not merely
        # "only one automation run" -- two different Masters can no longer
        # both hold an ACTIVE assignment on the same node at all.
        persona = self.make_persona()
        node_ref = self.make_feature_node("same-node-conflict")
        anchor_1 = self.register("MASTER", "node-master-same-node-1")
        anchor_2 = self.register("MASTER", "node-master-same-node-2")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor_1, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        status, rejected = self.act("persona.assign", {
            "session_anchor_ref": anchor_2, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(409, status, rejected)
        self.assertEqual("PERSONA_ASSIGNMENT_NODE_ALREADY_OWNED", rejected["error_code"])

    def test_same_node_rejects_a_second_concurrent_run(self):
        persona = self.make_persona()
        node_ref = self.make_feature_node("same-node-run-conflict")
        anchor = self.register("MASTER", "node-master-same-node-run")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        status1, started1 = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "first", "instruction": "first",
        })
        self.assertEqual(201, status1, started1)
        status2, started2 = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "second", "instruction": "second",
        })
        self.assertEqual(409, status2, started2)
        self.assertEqual("PERSONA_AUTOMATION_ACTIVE_RUN_EXISTS", started2["error_code"])

    # -- plan: Goal/Todo selection confined to the owned node -------------

    def test_node_scoped_run_cannot_select_a_goal_outside_its_node(self):
        anchor = self.register("MASTER", "node-master-plan-outside")
        persona = self.make_persona()
        own_node = self.make_feature_node("plan-own-node")
        other_node = self.make_feature_node("plan-other-node")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": own_node,
        })
        self.assertEqual(200, status, assigned)
        outside_goal = self.make_node_goal(other_node, "Outside Goal")
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "x", "instruction": "x",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, planned = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor, "decision_id": "plan-outside-node",
            "goal_id": outside_goal["goal_id"],
        })
        self.assertEqual(404, status, planned)
        self.assertEqual("PERSONA_AUTOMATION_GOAL_NOT_FOUND", planned["error_code"])

    def test_node_scoped_run_can_select_its_own_nodes_goal(self):
        anchor = self.register("MASTER", "node-master-plan-own")
        persona = self.make_persona()
        own_node = self.make_feature_node("plan-own-node-2")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": own_node,
        })
        self.assertEqual(200, status, assigned)
        own_goal = self.make_node_goal(own_node, "Own Goal")
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "x", "instruction": "x",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, tick = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor, "tick_id": "plan-own-node-tick",
        })
        self.assertEqual(200, status, tick)
        status, planned = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor, "decision_id": "plan-own-node",
            "goal_id": own_goal["goal_id"], "goal_version": str(own_goal["revision"]),
            "scope_ref": own_goal["goal_id"], "work_owner_ref": anchor,
        })
        self.assertEqual(200, status, planned)
        self.assertEqual(
            own_goal["goal_id"], planned["decision"]["target"]["selection"]["selected_goal_id"]
        )

    def test_dispatch_from_a_node_scoped_run_creates_a_node_scoped_queue_item(self):
        # 2026-09-15 follow-up (Conductor review): dispatch_work's
        # message_value never carried node_ref, so a node-scoped run's
        # actual dispatched work silently fell into the project-wide queue
        # bucket -- any live Master for the project could claim it, not
        # just the owning node Master. Fixed in
        # tools/persona_automation.py::dispatch_work (node_ref copied from
        # the run's own immutable column, never from the dispatch request).
        anchor = self.register("MASTER", "node-master-dispatch-node-scoped")
        persona = self.make_persona()
        own_node = self.make_feature_node("dispatch-node-scoped")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": own_node,
        })
        self.assertEqual(200, status, assigned)
        own_goal = self.make_node_goal(own_node, "Dispatch Node Goal")
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "x", "instruction": "x",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, tick = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor, "tick_id": "dispatch-node-scoped-tick",
        })
        self.assertEqual(200, status, tick)
        status, decided = self.act("persona.automation.decide", {
            "run_id": run["run_id"], "owner_ref": anchor, "decision_id": "dispatch-node-scoped-decision",
            "kind": "EXECUTE", "rationale": "bounded node-scoped test work", "evidence_refs": [own_goal["goal_id"]],
        })
        self.assertEqual(200, status, decided)
        status, dispatched = self.act("persona.automation.dispatch", {
            "run_id": run["run_id"], "owner_ref": anchor, "dispatch_id": "dispatch-node-scoped-dispatch",
            "title": "node scoped bounded task", "instruction": "run the isolated node-scoped check",
            "completion_conditions": ["result evidence"],
        })
        self.assertEqual(201, status, dispatched)
        message = self.server.store.get_master_message(dispatched["message"]["message_id"])
        self.assertEqual(own_node, message["node_ref"])
        self.assertEqual(anchor, message["node_owner_session_anchor_ref"])
        self.assertEqual(assigned["assignment"]["assignment_revision"], message["node_owner_assignment_revision"])
        # And it is claimable only by the owning node Master -- an
        # unrelated Master's anonymous poll must not see it.
        outsider = self.register("MASTER", "node-master-dispatch-outsider")
        outsider_status, outsider_result = self.act("persona.automation.status", {"run_id": run["run_id"]})
        self.assertEqual(200, outsider_status, outsider_result)  # sanity: run itself is readable
        from unittest.mock import Mock
        host = Mock()
        host.list_sessions.return_value = [{
            "terminal_id": "term-dispatch-outsider", "session_anchor_ref": outsider,
            "project_id": "TEST", "provider": "CLAUDE", "mode": "MASTER", "state": "LIVE",
        }]
        host.get.return_value = host.list_sessions.return_value[0]
        host.list_hosts.return_value = []
        self.server.terminal_host = host
        claim_status, claim_result = self.request("POST", "/v1/projects/TEST/master-messages/claim", {
            "provider": "CLAUDE", "terminal_id": "term-dispatch-outsider", "session_anchor_ref": outsider,
        })
        self.assertEqual(200, claim_status, claim_result)
        self.assertEqual("MASTER_MESSAGE_QUEUE_EMPTY", claim_result["status"])

    # -- regression: the existing project-wide CONDUCTOR path is unchanged -

    def test_conductor_project_wide_automation_still_works_unchanged(self):
        anchor = self.register("CONDUCTOR", "node-master-conductor-regression")
        persona = self.make_persona()
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, assigned)
        self.assertIsNone(assigned["assignment"]["node_ref"])
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor,
            "scope": "project wide", "instruction": "project wide",
        })
        self.assertEqual(201, status, started)
        self.assertIsNone(started["run"]["node_ref"])


if __name__ == "__main__":
    unittest.main()
