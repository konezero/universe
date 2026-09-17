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
            "persona.create", "persona.assign", "persona.unassign", "persona.automation.start",
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

    def test_conductor_cannot_claim_a_node_ref_assignment_at_all(self):
        # 2026-09-15 follow-up (Conductor review): a non-MASTER Anchor
        # claiming a node_ref would occupy the exclusive-owner slot (the
        # partial unique index is mode-agnostic) and permanently block the
        # real MASTER from ever being assigned that node -- the reverse of
        # "harmless, automation just won't start". Only a MASTER-mode
        # Anchor may hold a node-scoped assignment at all now; this
        # supersedes the older, weaker test that a CONDUCTOR's automation
        # merely ignored a node_ref it was allowed to hold.
        anchor = self.register("CONDUCTOR", "node-master-conductor-with-node")
        persona = self.make_persona()
        node_ref = self.make_feature_node("conductor-node-rejected")
        status, rejected = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(409, status, rejected)
        self.assertEqual("PERSONA_ASSIGNMENT_NODE_REQUIRES_MASTER", rejected["error_code"])
        # The node remains free for a real MASTER to claim.
        master_anchor = self.register("MASTER", "node-master-conductor-rejected-then-master")
        status, bound = self.act("persona.assign", {
            "session_anchor_ref": master_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, bound)

    def test_conductor_project_wide_persona_assignment_still_works_unchanged(self):
        # The MASTER-only requirement applies ONLY when node_ref is
        # present. Ordinary, node_ref-less persona assignment for a
        # CONDUCTOR (its actual, existing use) is completely unaffected.
        anchor = self.register("CONDUCTOR", "node-master-conductor-plain-assign")
        persona = self.make_persona()
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, assigned)
        self.assertIsNone(assigned["assignment"]["node_ref"])

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

    def test_node_writes_are_refused_when_uniqueness_index_not_enforced(self):
        # 2026-09-15 second follow-up (Conductor review): a diagnostic flag
        # that nothing actually checks is not a guarantee. Flip the flag
        # the way a failed migration would leave it, and confirm both
        # node-scoped write paths refuse rather than silently proceed as
        # if exclusivity still held -- then restore it so later tests in
        # this class are unaffected.
        node_ref = self.make_feature_node("integrity-gate-node")
        anchor = self.register("MASTER", "node-master-integrity-gate")
        persona = self.make_persona()
        original = self.server.store.node_owner_uniqueness_enforced
        self.server.store.node_owner_uniqueness_enforced = False
        try:
            status, rejected = self.act("persona.assign", {
                "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
                "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
                "node_ref": node_ref,
            })
            self.assertEqual(409, status, rejected)
            self.assertEqual("NODE_OWNERSHIP_INTEGRITY_UNCONFIRMED", rejected["error_code"])
        finally:
            self.server.store.node_owner_uniqueness_enforced = original
        # Ordinary node_ref-less assignment is unaffected by the flag.
        self.server.store.node_owner_uniqueness_enforced = False
        try:
            status, plain = self.act("persona.assign", {
                "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
                "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            })
            self.assertEqual(200, status, plain)
        finally:
            self.server.store.node_owner_uniqueness_enforced = original

    # -- reassignment: atomic handoff + revision-of-any-existing-row -----
    # (2026-09-15 follow-up: Conductor review of the node-binding UI)

    def test_release_then_reassign_to_the_same_session_uses_the_existing_rows_revision(self):
        # The exact bug the review caught: a caller that sends
        # expected_assignment_revision=0 for an anchor whose row EXISTS but
        # is UNASSIGNED (revision > 0) gets a stale-revision 409. The real
        # rule is "any existing row's real revision, 0 only if truly
        # absent" -- not "0 unless currently ACTIVE".
        anchor = self.register("MASTER", "node-master-release-reassign")
        persona = self.make_persona()
        node_ref = self.make_feature_node("release-reassign-node")
        status, bound = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, bound)
        status, released = self.act("persona.unassign", {
            "session_anchor_ref": anchor, "expected_assignment_revision": bound["assignment"]["assignment_revision"],
        })
        self.assertEqual(200, status, released)
        existing_revision = released["assignment"]["assignment_revision"]
        self.assertGreater(existing_revision, 0)
        # A naive "0 unless ACTIVE" client would send 0 here and 409.
        status, wrong = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(409, status, wrong)
        self.assertEqual("PERSONA_ASSIGNMENT_REVISION_CONFLICT", wrong["error_code"])
        status, correct = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": existing_revision,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, correct)

    def test_handoff_from_moves_node_ownership_in_one_atomic_call(self):
        old_anchor = self.register("MASTER", "node-master-handoff-old")
        new_anchor = self.register("MASTER", "node-master-handoff-new")
        persona = self.make_persona()
        node_ref = self.make_feature_node("handoff-node")
        status, bound = self.act("persona.assign", {
            "session_anchor_ref": old_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, bound)
        status, handed_off = self.act("persona.assign", {
            "session_anchor_ref": new_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
            "handoff_from": {
                "session_anchor_ref": old_anchor,
                "expected_assignment_revision": bound["assignment"]["assignment_revision"],
            },
        })
        self.assertEqual(200, status, handed_off)
        self.assertEqual(node_ref, handed_off["assignment"]["node_ref"])
        # The old owner's row is atomically cleared as part of the same call.
        status, old_read = self.act("persona.assignment-read", {"session_anchor_ref": old_anchor})
        self.assertEqual(200, status, old_read)
        self.assertIsNone(old_read["assignment"]["node_ref"])

    def test_handoff_from_with_stale_revision_is_rejected_and_changes_nothing(self):
        old_anchor = self.register("MASTER", "node-master-handoff-stale-old")
        new_anchor = self.register("MASTER", "node-master-handoff-stale-new")
        persona = self.make_persona()
        node_ref = self.make_feature_node("handoff-stale-node")
        status, bound = self.act("persona.assign", {
            "session_anchor_ref": old_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, bound)
        status, rejected = self.act("persona.assign", {
            "session_anchor_ref": new_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
            "handoff_from": {
                "session_anchor_ref": old_anchor,
                "expected_assignment_revision": bound["assignment"]["assignment_revision"] + 5,
            },
        })
        self.assertEqual(409, status, rejected)
        self.assertEqual("PERSONA_ASSIGNMENT_HANDOFF_STALE", rejected["error_code"])
        # Nothing changed: old owner still owns it, new anchor has no row.
        status, old_read = self.act("persona.assignment-read", {"session_anchor_ref": old_anchor})
        self.assertEqual(node_ref, old_read["assignment"]["node_ref"])
        status, new_read = self.act("persona.assignment-read", {"session_anchor_ref": new_anchor})
        self.assertIsNone(new_read["assignment"])

    def test_handoff_from_naming_the_wrong_owner_is_rejected(self):
        real_owner = self.register("MASTER", "node-master-handoff-wrong-real")
        bystander = self.register("MASTER", "node-master-handoff-wrong-bystander")
        new_anchor = self.register("MASTER", "node-master-handoff-wrong-new")
        persona = self.make_persona()
        node_ref = self.make_feature_node("handoff-wrong-owner-node")
        status, bound = self.act("persona.assign", {
            "session_anchor_ref": real_owner, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, bound)
        status, rejected = self.act("persona.assign", {
            "session_anchor_ref": new_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
            "handoff_from": {"session_anchor_ref": bystander, "expected_assignment_revision": 1},
        })
        self.assertEqual(409, status, rejected)
        self.assertEqual("PERSONA_ASSIGNMENT_NODE_ALREADY_OWNED", rejected["error_code"])

    # -- persona.assignments-list: authoritative, not live-terminal-derived -

    def test_assignments_list_includes_offline_owners_and_unassigned_rows(self):
        live_anchor = self.register("MASTER", "node-master-list-live")
        persona = self.make_persona()
        live_node = self.make_feature_node("list-live-node")
        status, live_bound = self.act("persona.assign", {
            "session_anchor_ref": live_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": live_node,
        })
        self.assertEqual(200, status, live_bound)
        # A second, distinct Anchor's assignment -- persona.assignments-list
        # must surface it purely from the durable table, with no dependency
        # on whether that Anchor still appears in any live-terminal probe
        # (a UI cross-referencing only currently-live terminals is exactly
        # what would misread an offline owner's node as unassigned).
        offline_anchor = self.register("MASTER", "node-master-list-offline")
        offline_node = self.make_feature_node("list-offline-node")
        status, offline_bound = self.act("persona.assign", {
            "session_anchor_ref": offline_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": offline_node,
        })
        self.assertEqual(200, status, offline_bound)
        status, listed = self.act("persona.assignments-list", {"project_id": "TEST"})
        self.assertEqual(200, status, listed)
        by_anchor = {a["session_anchor_ref"]: a for a in listed["assignments"]}
        self.assertIn(live_anchor, by_anchor)
        self.assertEqual(live_node, by_anchor[live_anchor]["node_ref"])
        self.assertIn(offline_anchor, by_anchor)
        self.assertEqual(offline_node, by_anchor[offline_anchor]["node_ref"])

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
        own_todo = self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": own_node,
            "title": "Dispatch Node Todo", "detail": "bounded node work",
            "priority": "P0", "state": "READY", "source_kind": "MASTER",
            "sort_order": 0,
        })
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor, "scope": "x", "instruction": "x",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, tick = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor, "tick_id": "dispatch-node-scoped-tick",
        })
        self.assertEqual(200, status, tick)
        status, decided = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "decision_id": "dispatch-node-scoped-decision",
        })
        self.assertEqual(200, status, decided)
        self.assertEqual("EXECUTE", decided["decision"]["kind"])
        self.assertEqual(own_todo["todo_id"], decided["decision"]["target"]["todo_id"])
        status, dispatched = self.act("persona.automation.dispatch", {
            "run_id": run["run_id"], "owner_ref": anchor, "dispatch_id": "dispatch-node-scoped-dispatch",
            "title": "node scoped bounded task", "instruction": "run the isolated node-scoped check",
            "completion_conditions": ["result evidence"],
        })
        self.assertEqual(201, status, dispatched)
        message = self.server.store.get_master_message(dispatched["message"]["message_id"])
        self.assertEqual(own_node, message["node_ref"])
        self.assertEqual(own_todo["todo_id"], message["todo_id"])
        self.assertEqual(own_todo["todo_id"], dispatched["dispatch"]["todo_id"])
        self.assertEqual(own_todo["todo_id"], message["metadata"]["todo_id"])
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

    def test_node_plan_waits_for_result_review_instead_of_redispatching_todo(self):
        anchor = self.register("MASTER", "node-master-review-cursor")
        persona = self.make_persona("node review cursor")
        node_ref = self.make_feature_node("review-cursor-node")
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"],
            "expected_assignment_revision": 0, "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        todo = self.server.store.create_todo({
            "scope_kind": "NODE", "project_id": "TEST", "node_ref": node_ref,
            "title": "Review cursor Todo", "detail": "one bounded result",
            "priority": "P0", "state": "READY", "source_kind": "MASTER",
            "sort_order": 0,
        })
        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": anchor,
            "scope": "review cursor", "instruction": "run one bounded task",
        })
        self.assertEqual(201, status, started)
        run = started["run"]
        status, tick = self.act("persona.automation.tick", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "tick_id": "review-cursor-tick",
        })
        self.assertEqual(200, status, tick)
        status, planned = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "decision_id": "review-cursor-plan",
        })
        self.assertEqual(200, status, planned)
        self.assertEqual("EXECUTE", planned["decision"]["kind"])
        self.assertEqual(todo["todo_id"], planned["decision"]["target"]["todo_id"])
        status, dispatched = self.act("persona.automation.dispatch", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "dispatch_id": "review-cursor-dispatch", "title": "cursor task",
            "instruction": "return evidence", "completion_conditions": ["result"],
        })
        self.assertEqual(201, status, dispatched)
        assignment = dispatched["dispatch"]
        self.server.persona_automation.record_master_completion({
            "run_id": run["run_id"], "dispatch_id": assignment["dispatch_id"],
            "assignment_revision": assignment["assignment_revision"],
            "source_message_id": assignment["message_id"],
            "result_ref": "result://review-cursor",
            "body_text_utf8_sha256": "a" * 64,
            "completed_at": "2026-09-17T00:00:00Z",
        })
        status, waiting = self.act("persona.automation.plan", {
            "run_id": run["run_id"], "owner_ref": anchor,
            "decision_id": "review-cursor-wait",
        })
        self.assertEqual(200, status, waiting)
        self.assertEqual("WAIT", waiting["decision"]["kind"])
        self.assertEqual(
            "RESULT_READY_FOR_REVIEW",
            waiting["decision"]["target"]["assignment"]["state"],
        )
        self.assertEqual(
            todo["todo_id"],
            waiting["decision"]["target"]["assignment"]["todo_id"],
        )

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

    # -- Host sync status (2026-09-15: assign/unassign/handoff -> Host) --

    def test_node_scoped_assign_records_offline_host_sync_when_no_live_host_exists(self):
        # This test fixture's session_supervisor.register_session has no
        # real Rust Host behind it -- _sync_node_projection's find_live must
        # come back empty, and that must be recorded as OFFLINE, never
        # silently left looking the same as a real Host confirmation
        # (2026-09-15 Conductor review: "저장됨/Host 확인됨/미확인·미지원·
        # 오류를 정확히 표시").
        anchor = self.register("MASTER", "node-master-host-sync-offline")
        node_ref = self.make_feature_node("host-sync-offline-node")
        persona = self.make_persona()
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        assignment = assigned["assignment"]
        # The Host sync attempt runs after the DB write this response
        # already reflects, so the receipt shows up on a fresh read, not on
        # this same response's echoed assignment (mirrors how applied_at/
        # delivery_status for P1 persona delivery are read back separately
        # too, never assumed synchronous with the write response).
        status, read_back = self.act("persona.assignment-read", {"session_anchor_ref": anchor})
        self.assertEqual(200, status, read_back)
        self.assertEqual("OFFLINE", read_back["assignment"]["host_sync_status"])
        self.assertEqual(
            assignment["assignment_revision"], read_back["assignment"]["host_sync_assignment_revision"]
        )

        status, unassigned = self.act("persona.unassign", {
            "session_anchor_ref": anchor, "expected_assignment_revision": assignment["assignment_revision"],
        })
        self.assertEqual(200, status, unassigned)
        status, read_back2 = self.act("persona.assignment-read", {"session_anchor_ref": anchor})
        self.assertEqual(200, status, read_back2)
        self.assertEqual("OFFLINE", read_back2["assignment"]["host_sync_status"])

    def test_record_host_sync_receipt_is_cas_guarded_against_late_responses(self):
        # A late Host acknowledgment for a superseded assignment_revision
        # must never be recorded as confirming the CURRENT one -- the write
        # is a documented no-op (2026-09-15 Conductor review: "늦은 응답이
        # 새 배정 성공으로 표시되지 않게 한다").
        anchor = self.register("MASTER", "node-master-host-sync-cas")
        node_ref = self.make_feature_node("host-sync-cas-node")
        persona = self.make_persona()
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, assigned)
        revision_1 = assigned["assignment"]["assignment_revision"]

        status, unassigned = self.act("persona.unassign", {
            "session_anchor_ref": anchor, "expected_assignment_revision": revision_1,
        })
        self.assertEqual(200, status, unassigned)
        revision_2 = unassigned["assignment"]["assignment_revision"]
        self.assertGreater(revision_2, revision_1)

        # A late CONFIRMED receipt for the now-superseded revision_1 push
        # must not overwrite the row's already-recorded (OFFLINE) status
        # for revision_2.
        late = self.server.store.record_host_sync_receipt(
            session_anchor_ref=anchor, assignment_revision=revision_1,
            status="CONFIRMED", detail="late reply from a slow Host",
        )
        self.assertEqual("SUPERSEDED", late["status"])
        self.assertEqual("OFFLINE", late["assignment"]["host_sync_status"])
        self.assertEqual(revision_2, late["assignment"]["host_sync_assignment_revision"])

        current = self.server.store.record_host_sync_receipt(
            session_anchor_ref=anchor, assignment_revision=revision_2,
            status="CONFIRMED", detail="",
        )
        self.assertEqual("RECORDED", current["status"])
        self.assertEqual("CONFIRMED", current["assignment"]["host_sync_status"])

    # -- Activity project_event producer (2026-09-15 Fleet/Activity
    # collaboration: smallest useful producer for "assignment/handoff") ---

    def test_assign_records_a_durable_activity_event(self):
        anchor = self.register("MASTER", "node-master-activity-event-assign")
        persona = self.make_persona()
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, assigned)
        events = self.server.store.list_events("TEST")
        matching = [
            e for e in events
            if e["event_type"] == "PERSONA_ASSIGNMENT_CHANGED"
            and e["payload"].get("session_anchor_ref") == anchor
        ]
        self.assertEqual(1, len(matching))
        payload = matching[0]["payload"]
        self.assertEqual("ACTIVE", payload["state"])
        self.assertEqual(persona["persona_id"], payload["persona_id"])
        self.assertEqual(
            assigned["assignment"]["assignment_revision"], payload["assignment_revision"]
        )

    def test_unassign_records_a_durable_activity_event(self):
        anchor = self.register("MASTER", "node-master-activity-event-unassign")
        persona = self.make_persona()
        status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
        })
        self.assertEqual(200, status, assigned)
        status, unassigned = self.act("persona.unassign", {
            "session_anchor_ref": anchor,
            "expected_assignment_revision": assigned["assignment"]["assignment_revision"],
        })
        self.assertEqual(200, status, unassigned)
        events = self.server.store.list_events("TEST")
        matching = [
            e for e in events
            if e["event_type"] == "PERSONA_ASSIGNMENT_CHANGED"
            and e["payload"].get("session_anchor_ref") == anchor
            and e["payload"].get("state") == "UNASSIGNED"
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual("ACTIVE", matching[0]["payload"]["from_state"])

    def test_handoff_records_activity_events_for_both_the_old_and_new_owner(self):
        node_ref = self.make_feature_node("activity-event-handoff-node")
        persona = self.make_persona()
        old_anchor = self.register("MASTER", "node-master-activity-event-handoff-old")
        new_anchor = self.register("MASTER", "node-master-activity-event-handoff-new")
        status, first = self.act("persona.assign", {
            "session_anchor_ref": old_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, first)
        status, handoff = self.act("persona.assign", {
            "session_anchor_ref": new_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
            "handoff_from": {
                "session_anchor_ref": old_anchor,
                "expected_assignment_revision": first["assignment"]["assignment_revision"],
            },
        })
        self.assertEqual(200, status, handoff)
        events = self.server.store.list_events("TEST")
        new_owner_events = [
            e for e in events
            if e["event_type"] == "PERSONA_ASSIGNMENT_CHANGED"
            and e["payload"].get("session_anchor_ref") == new_anchor
            and e["payload"].get("state") == "ACTIVE"
        ]
        old_owner_events = [
            e for e in events
            if e["event_type"] == "PERSONA_ASSIGNMENT_CHANGED"
            and e["payload"].get("session_anchor_ref") == old_anchor
            and e["payload"].get("state") == "UNASSIGNED"
        ]
        self.assertEqual(1, len(new_owner_events))
        self.assertEqual(1, len(old_owner_events))
        self.assertEqual(node_ref, new_owner_events[0]["payload"]["node_ref"])


if __name__ == "__main__":
    unittest.main()
