"""Node-scoped Master queue items (2026-09-15 follow-up to node-Master
automation ownership).

User's follow-up: "큐에 노드도 지정해야겠넹" -- bind project_id, the real
node_ref, and the owning node's Master persona assignment identifier +
revision into the queue item itself, so only the currently-assigned node
Master can claim it. preferred_provider/preferred_terminal_id (existing
metadata fields) are a hint, not an ownership boundary -- the server must
verify the claiming Anchor's actual, current node assignment and its
revision at the claim boundary.

- create_master_message (tools/universe_server.py): a node_ref on the
  message is validated against a real feature_node, and the current owning
  assignment (session_persona_assignment row for that node) is stamped
  onto the message server-side (node_owner_session_anchor_ref,
  node_owner_assignment_revision) -- never trusted from the request.
- claim_master_message: a node-scoped item is claimable only by the exact
  Anchor whose CURRENT assignment still matches that captured revision.
  Reassignment or unassignment always bumps assignment_revision
  (session_persona_assignment already does this), so a stale claimant can
  never match -- no separate cancel/expiry mechanism needed for that
  invariant. A node_ref-less item keeps its original, unnarrowed meaning
  (claimable by any live project Master) -- explicit backward-compat
  boundary, not a silent behavior change.
- The existing atomic QUEUED->PROCESSING CAS transition
  (_transition_master_message) is reused unchanged for the actual
  single-winner race; this only narrows which candidates a claimant even
  attempts.

Runs against a real in-process server/DB via the existing
MemoryCandidateApiTests fixture (project "TEST"), with the terminal host
mocked the same way tests/test_universe_server.py's master-message tests
do (match_live_terminals reads from a real terminal host, not the
persona-automation session_supervisor registry).
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures  # noqa: E402


class NodeScopedMasterQueueTests(unittest.TestCase):
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
        if action_id in {"persona.create", "persona.assign", "persona.unassign"}:
            body.setdefault("request_id", f"queue-node-{action_id.replace('.', '-')}-{uuid.uuid4().hex}")
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": body})

    def register(self, session_id):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": "TEST", "mode": "MASTER", "provider": "CODEX",
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

    def assign_master_to_node(self, anchor, node_ref, persona=None):
        persona = persona or self.make_persona()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 0,
            "node_ref": node_ref,
        })
        self.assertEqual(200, status, result)
        return result["assignment"]

    def set_terminal_host(self, terminals):
        host = Mock()
        host.list_sessions.return_value = terminals
        host.get.return_value = terminals[0] if terminals else None
        host.list_hosts.return_value = []
        self.server.terminal_host = host

    def live_master_terminal(self, terminal_id, anchor):
        return {
            "terminal_id": terminal_id, "session_anchor_ref": anchor,
            "project_id": "TEST", "provider": "CLAUDE", "mode": "MASTER", "state": "LIVE",
        }

    def queue_message(self, key, node_ref=None):
        return self.server.store.create_master_message("TEST", {
            "idempotency_key": key, "title": key, "instruction": "bounded fixture work",
            **({"node_ref": node_ref} if node_ref is not None else {}),
        })

    def claim(self, terminal_id, anchor, message_id=""):
        request = {"provider": "CLAUDE", "terminal_id": terminal_id, "session_anchor_ref": anchor}
        if message_id:
            request["message_id"] = message_id
        return self.request("POST", "/v1/projects/TEST/master-messages/claim", request)

    # -- creation: server-derived, real graph contract ---------------------

    def test_node_scoped_message_rejects_nonexistent_node(self):
        with self.assertRaises(Exception) as ctx:
            self.queue_message("no-such-node-msg", node_ref="feature_does_not_exist")
        self.assertEqual("FEATURE_NODE_NOT_FOUND", getattr(ctx.exception, "code", None))

    def test_node_scoped_message_requires_an_active_node_assignment(self):
        node_ref = self.make_feature_node("queue-node-unassigned")
        with self.assertRaises(Exception) as ctx:
            self.queue_message("unassigned-node-msg", node_ref=node_ref)
        self.assertEqual("MASTER_MESSAGE_NODE_UNASSIGNED", getattr(ctx.exception, "code", None))

    def test_node_scoped_message_stamps_current_owner_and_revision(self):
        anchor = self.register("stamp-owner")
        node_ref = self.make_feature_node("queue-node-stamp")
        assignment = self.assign_master_to_node(anchor, node_ref)
        message, created = self.queue_message("stamped-owner-msg", node_ref=node_ref)
        self.assertTrue(created)
        self.assertEqual(node_ref, message["node_ref"])
        self.assertEqual(anchor, message["node_owner_session_anchor_ref"])
        self.assertEqual(assignment["assignment_revision"], message["node_owner_assignment_revision"])

    # -- claim: real ownership, not a role string or a claimed node_ref -----

    def test_project_wide_message_stays_claimable_by_any_live_master(self):
        # No node_ref (the only shape any caller used before this change):
        # unnarrowed backward-compat boundary, explicit and tested.
        message, _ = self.queue_message("project-wide-msg-unchanged")
        self.assertIsNone(message["node_ref"])
        self.set_terminal_host([self.live_master_terminal("term-anywide", "anchor-anywide")])
        status, claimed = self.claim("term-anywide", "anchor-anywide")
        self.assertEqual(200, status, claimed)
        self.assertEqual(message["message_id"], claimed["message"]["message_id"])

    def test_correct_node_master_can_claim_its_own_item(self):
        anchor = self.register("claim-correct")
        node_ref = self.make_feature_node("queue-node-claim-correct")
        self.assign_master_to_node(anchor, node_ref)
        message, _ = self.queue_message("claim-correct-msg", node_ref=node_ref)
        self.set_terminal_host([self.live_master_terminal("term-claim-correct", anchor)])
        status, claimed = self.claim("term-claim-correct", anchor)
        self.assertEqual(200, status, claimed)
        self.assertEqual(message["message_id"], claimed["message"]["message_id"])

    def test_unassigned_master_polling_does_not_receive_a_node_scoped_item(self):
        node_ref = self.make_feature_node("queue-node-poll-hidden")
        self.assign_master_to_node(self.register("poll-owner"), node_ref)
        self.queue_message("poll-hidden-msg", node_ref=node_ref)
        # A different, unrelated Master with no node assignment at all polls
        # generically (no message_id) -- must not receive this node's item.
        outsider = self.register("poll-outsider")
        self.set_terminal_host([self.live_master_terminal("term-poll-outsider", outsider)])
        status, result = self.claim("term-poll-outsider", outsider)
        self.assertEqual(200, status, result)
        self.assertEqual("MASTER_MESSAGE_QUEUE_EMPTY", result["status"])

    def test_different_nodes_master_is_rejected_on_explicit_claim(self):
        node_a = self.make_feature_node("queue-node-cross-a")
        node_b = self.make_feature_node("queue-node-cross-b")
        self.assign_master_to_node(self.register("cross-a"), node_a)
        anchor_b = self.register("cross-b")
        self.assign_master_to_node(anchor_b, node_b)
        message_a, _ = self.queue_message("cross-node-msg-a", node_ref=node_a)
        self.set_terminal_host([self.live_master_terminal("term-cross-b", anchor_b)])
        status, rejected = self.claim("term-cross-b", anchor_b, message_id=message_a["message_id"])
        self.assertEqual(409, status, rejected)
        self.assertEqual("MASTER_MESSAGE_NODE_OWNER_MISMATCH", rejected["error_code"])

    def test_unassignment_makes_the_old_item_permanently_unclaimable_by_the_old_owner(self):
        anchor = self.register("stale-after-unassign")
        node_ref = self.make_feature_node("queue-node-stale-unassign")
        assignment = self.assign_master_to_node(anchor, node_ref)
        message, _ = self.queue_message("stale-after-unassign-msg", node_ref=node_ref)
        status, unassigned = self.act("persona.unassign", {
            "session_anchor_ref": anchor, "expected_assignment_revision": assignment["assignment_revision"],
        })
        self.assertEqual(200, status, unassigned)
        # The old owner's Anchor is no longer ACTIVE-assigned to this node at
        # all, so the claim handler resolves it to project-wide-only
        # eligibility -- it cannot even present the old node_ref, let alone
        # the old revision. An explicit claim of the now-orphaned item must
        # be rejected, not silently handed back to whoever asks.
        self.set_terminal_host([self.live_master_terminal("term-stale-unassign", anchor)])
        status, rejected = self.claim("term-stale-unassign", anchor, message_id=message["message_id"])
        self.assertEqual(409, status, rejected)
        self.assertEqual("MASTER_MESSAGE_NODE_OWNER_MISMATCH", rejected["error_code"])

    def test_reassigning_the_node_does_not_transfer_the_old_queued_item(self):
        old_anchor = self.register("reassign-old")
        new_anchor = self.register("reassign-new")
        node_ref = self.make_feature_node("queue-node-reassign")
        persona = self.make_persona("shared persona")
        self.assign_master_to_node(old_anchor, node_ref, persona=persona)
        message, _ = self.queue_message("reassign-msg", node_ref=node_ref)
        # Unassign the old owner, then assign the same node to a new Anchor
        # -- this is the real "reassignment" path (unassign + assign),
        # producing a fresh assignment_revision for the node.
        status, unassign_result = self.act("persona.assign", {
            "session_anchor_ref": old_anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"], "expected_assignment_revision": 1,
            "node_ref": None,
        })
        self.assertEqual(200, status, unassign_result)
        new_assignment = self.assign_master_to_node(new_anchor, node_ref, persona=persona)
        self.set_terminal_host([self.live_master_terminal("term-reassign-new", new_anchor)])
        status, rejected = self.claim("term-reassign-new", new_anchor, message_id=message["message_id"])
        # The queued item still carries the OLD assignment_revision captured
        # at creation time -- reassignment does not retroactively hand it to
        # the new owner either. It is orphaned until explicitly re-queued.
        self.assertEqual(409, status, rejected)
        self.assertEqual("MASTER_MESSAGE_NODE_OWNER_MISMATCH", rejected["error_code"])
        # The new Anchor's assignment_revision can coincidentally equal the
        # old stamped one (each Anchor's revision counter starts at 1) --
        # that must not matter, since the owning Anchor identity differs.
        self.assertEqual(1, new_assignment["assignment_revision"])
        self.assertEqual(1, message["node_owner_assignment_revision"])
        self.assertNotEqual(new_anchor, message["node_owner_session_anchor_ref"])

    def test_second_claim_of_an_already_claimed_item_finds_nothing(self):
        # Documents that the existing atomic CAS transition is reused
        # unchanged -- this test's two calls are sequential (not truly
        # concurrent), but the second call observes PROCESSING left by the
        # first exactly as two racing Masters would.
        anchor = self.register("single-winner")
        node_ref = self.make_feature_node("queue-node-single-winner")
        self.assign_master_to_node(anchor, node_ref)
        message, _ = self.queue_message("single-winner-msg", node_ref=node_ref)
        self.set_terminal_host([self.live_master_terminal("term-single-winner", anchor)])
        status1, claimed1 = self.claim("term-single-winner", anchor)
        self.assertEqual(200, status1, claimed1)
        self.assertEqual(message["message_id"], claimed1["message"]["message_id"])
        status2, claimed2 = self.claim("term-single-winner", anchor, message_id=message["message_id"])
        self.assertEqual(200, status2, claimed2)
        self.assertEqual("MASTER_MESSAGE_QUEUE_EMPTY", claimed2["status"])

    # -- wake eligibility (has_queued_master_message_for) -------------------

    def test_wake_eligibility_check_matches_claim_eligibility(self):
        node_ref = self.make_feature_node("queue-node-wake-eligibility")
        assignment = self.assign_master_to_node(self.register("wake-eligible"), node_ref)
        self.queue_message("wake-eligibility-msg", node_ref=node_ref)
        self.assertTrue(
            self.server.store.has_queued_master_message_for(
                "TEST", node_ref, assignment["assignment_revision"], assignment["session_anchor_ref"]
            )
        )
        self.assertFalse(
            self.server.store.has_queued_master_message_for(
                "TEST", node_ref, 999999, assignment["session_anchor_ref"]
            )
        )
        self.assertFalse(
            # Same node_ref/revision but a DIFFERENT Anchor identity must not
            # match -- the exact bug a coincidental revision-number
            # collision across two different Anchors would otherwise cause.
            self.server.store.has_queued_master_message_for(
                "TEST", node_ref, assignment["assignment_revision"], "some-other-anchor"
            )
        )
        self.assertFalse(
            self.server.store.has_queued_master_message_for("TEST", None, None)
        )


if __name__ == "__main__":
    unittest.main()
