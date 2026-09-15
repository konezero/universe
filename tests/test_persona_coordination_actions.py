"""HTTP Action integration for exact Persona collaboration scope."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures  # noqa: E402


class PersonaCoordinationActionTests(unittest.TestCase):
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
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": dict(request)})

    def register(self, mode, session_id):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": "TEST", "mode": mode, "provider": "CODEX",
        })
        return material["session_anchor_ref"]

    def setup_scope(self):
        master = self.register("MASTER", "coord-master-" + uuid.uuid4().hex)
        worker = self.register("WORKER", "coord-worker-" + uuid.uuid4().hex)
        feature, _ = self.server.store.create_feature_node("TEST", {
            "idempotency_key": "coord-feature-" + uuid.uuid4().hex,
            "title": "Coordination feature", "intent_text": "bounded conflict",
            "created_by_role": "USER",
        })
        persona_status, persona_result = self.act("persona.create", {
            "title": "Coordination Master", "body": "Keep node scope exact.",
            "request_id": "coord-persona-" + uuid.uuid4().hex,
        })
        self.assertEqual(200, persona_status, persona_result)
        assign_status, assign_result = self.act("persona.assign", {
            "session_anchor_ref": master, "project_id": "TEST",
            "persona_id": persona_result["persona"]["persona_id"],
            "expected_persona_revision": persona_result["persona"]["revision"],
            "expected_assignment_revision": 0, "node_ref": feature["feature_id"],
            "request_id": "coord-assign-" + uuid.uuid4().hex,
        })
        self.assertEqual(200, assign_status, assign_result)
        todo = self.server.store.create_todo({
            "project_id": "TEST", "scope_kind": "NODE", "node_ref": feature["feature_id"],
            "title": "Coordination Todo", "detail": "bounded", "priority": "P2",
            "state": "READY", "source_kind": "USER", "sort_order": 0,
        })
        return master, worker, feature["feature_id"], todo["todo_id"]

    def test_open_propose_and_unresolved_response_are_typed_and_scoped(self):
        master, worker, node_ref, todo_id = self.setup_scope()
        request_id = "coord-open-" + uuid.uuid4().hex
        status, opened = self.act("persona.collaboration.open", {
            "project_id": "TEST", "node_ref": node_ref, "todo_id": todo_id,
            "participant_anchors": [master, worker], "initiator_anchor_ref": master,
            "file_scope": ["docs/fixture.md"], "evidence": ["todo:" + todo_id],
            "proposal": {"action": "bounded review"}, "request_id": request_id,
        })
        self.assertEqual(200, status, opened)
        self.assertEqual("PERSONA_COORDINATION_OPENED", opened["status"])
        coordination = opened["coordination"]
        self.assertEqual(node_ref, coordination["node_ref"])
        self.assertEqual(todo_id, coordination["todo_id"])
        self.assertEqual("OPEN", coordination["state"])
        status, proposed = self.act("persona.collaboration.propose", {
            "coordination_id": coordination["coordination_id"],
            "proposer_anchor_ref": master, "expected_proposal_version": 1,
            "proposal": {"action": "bounded review with evidence"},
            "evidence": ["activity:coord"], "request_id": "coord-propose-" + uuid.uuid4().hex,
        })
        self.assertEqual(200, status, proposed)
        self.assertEqual("PROPOSED", proposed["coordination"]["state"])
        status, responded = self.act("persona.collaboration.respond", {
            "coordination_id": coordination["coordination_id"],
            "responder_anchor_ref": worker, "expected_proposal_version": 2,
            "outcome": "UNRESOLVED", "evidence": ["worker:conflict"],
            "request_id": "coord-response-" + uuid.uuid4().hex,
        })
        self.assertEqual(200, status, responded)
        self.assertEqual("ESCALATED", responded["coordination"]["state"])
        self.assertIn("Conductor/user", responded["coordination"]["next_action"])

    def test_open_replay_does_not_emit_a_second_coordination_record(self):
        master, worker, node_ref, todo_id = self.setup_scope()
        request = {
            "project_id": "TEST", "node_ref": node_ref, "todo_id": todo_id,
            "participant_anchors": [master, worker], "initiator_anchor_ref": master,
            "file_scope": ["docs/replay.md"], "evidence": ["todo:" + todo_id],
            "proposal": {"action": "replay"}, "request_id": "coord-replay-" + uuid.uuid4().hex,
        }
        status, first = self.act("persona.collaboration.open", request)
        self.assertEqual(200, status, first)
        status, replay = self.act("persona.collaboration.open", request)
        self.assertEqual(200, status, replay)
        self.assertEqual("PERSONA_COORDINATION_REPLAYED", replay["status"])
        self.assertEqual(first["coordination"]["coordination_id"], replay["coordination"]["coordination_id"])

    def test_orphan_reissue_is_cas_bound_to_old_and_current_owner(self):
        old_master, _worker, node_ref, _todo_id = self.setup_scope()
        new_master = self.register("MASTER", "coord-new-master-" + uuid.uuid4().hex)
        persona_status, persona_result = self.act("persona.create", {
            "title": "Second node Master", "body": "Reissue only to current owner.",
            "request_id": "coord-second-persona-" + uuid.uuid4().hex,
        })
        self.assertEqual(200, persona_status, persona_result)
        status, old_assignment = self.act("persona.assignment-read", {"session_anchor_ref": old_master})
        self.assertEqual(200, status, old_assignment)
        old_revision = old_assignment["assignment"]["assignment_revision"]
        queue_status, queued = self.request("POST", "/v1/projects/TEST/master-messages", {
            "title": "Orphan coordination queue", "instruction": "bounded orphan test",
            "node_ref": node_ref, "idempotency_key": "coord-queue-" + uuid.uuid4().hex,
        })
        self.assertEqual(201, queue_status, queued)
        message = queued["message"]
        self.assertEqual(old_master, message["node_owner_session_anchor_ref"])
        status, handoff = self.act("persona.assign", {
            "session_anchor_ref": new_master, "project_id": "TEST",
            "persona_id": persona_result["persona"]["persona_id"],
            "expected_persona_revision": persona_result["persona"]["revision"],
            "expected_assignment_revision": 0, "node_ref": node_ref,
            "handoff_from": {"session_anchor_ref": old_master, "expected_assignment_revision": old_revision},
            "request_id": "coord-handoff-" + uuid.uuid4().hex,
        })
        self.assertEqual(200, status, handoff)
        new_revision = handoff["assignment"]["assignment_revision"]
        request = {
            "project_id": "TEST", "message_id": message["message_id"],
            "actor_anchor_ref": new_master,
            "expected_owner_session_anchor_ref": old_master,
            "expected_owner_assignment_revision": old_revision,
            "current_owner_session_anchor_ref": new_master,
            "current_owner_assignment_revision": new_revision,
            "request_id": "coord-reissue-" + uuid.uuid4().hex,
            "idempotency_key": "coord-reissue-key-" + uuid.uuid4().hex,
            "reason": "owner handoff completed",
        }
        status, reissued = self.act("master-message.orphan-reissue", request)
        self.assertEqual(200, status, reissued)
        self.assertEqual("MASTER_MESSAGE_ORPHAN_REISSUED", reissued["status"])
        self.assertEqual("CANCELLED", reissued["old_message"]["delivery_state"])
        self.assertEqual("QUEUED", reissued["message"]["delivery_state"])
        self.assertEqual(new_master, reissued["message"]["node_owner_session_anchor_ref"])
        replay_status, replay = self.act("master-message.orphan-reissue", request)
        self.assertEqual(200, replay_status, replay)
        self.assertEqual("MASTER_MESSAGE_REISSUE_REPLAYED", replay["status"])
        self.assertEqual(reissued["message"]["message_id"], replay["message"]["message_id"])


if __name__ == "__main__":
    unittest.main()
