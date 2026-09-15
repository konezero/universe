"""Typed Persona Master/Worker conflict coordination contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.persona_coordination import PersonaCoordinationError, PersonaCoordinationStore


class PersonaCoordinationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = PersonaCoordinationStore(Path(self.temp.name) / "universe.sqlite3")
        self.base = {
            "project_id": "TEST",
            "node_ref": "feature-1",
            "todo_id": "todo-1",
            "task_frame_id": "frame-1",
            "participant_anchors": ["master-anchor", "worker-anchor"],
            "initiator_anchor_ref": "master-anchor",
            "file_scope": ["src/example.py"],
            "evidence": ["activity:one"],
            "proposal": {"action": "keep exact scope"},
            "request_id": "open-1",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_open_is_idempotent_and_conflicts_on_changed_scope(self) -> None:
        record, created = self.store.create(self.base)
        self.assertTrue(created)
        replay, replay_created = self.store.create(dict(self.base))
        self.assertFalse(replay_created)
        self.assertEqual(record["coordination_id"], replay["coordination_id"])
        with self.assertRaises(PersonaCoordinationError) as context:
            self.store.create({**self.base, "file_scope": ["src/other.py"]})
        self.assertEqual("PERSONA_COORDINATION_IDEMPOTENCY_CONFLICT", context.exception.code)

    def test_proposal_and_responses_use_version_cas_and_escalate(self) -> None:
        record, _ = self.store.create(self.base)
        proposed = self.store.propose({
            "coordination_id": record["coordination_id"],
            "proposer_anchor_ref": "master-anchor",
            "expected_proposal_version": 1,
            "proposal": {"action": "revised exact scope"},
            "evidence": ["task-frame:frame-1"],
            "request_id": "proposal-1",
        })
        self.assertEqual("PROPOSED", proposed["state"])
        self.assertEqual(2, proposed["proposal_version"])
        with self.assertRaises(PersonaCoordinationError) as context:
            self.store.propose({
                "coordination_id": record["coordination_id"],
                "proposer_anchor_ref": "worker-anchor",
                "expected_proposal_version": 1,
                "proposal": {"action": "stale"},
                "evidence": ["stale"],
                "request_id": "proposal-stale",
            })
        self.assertEqual("PERSONA_COORDINATION_VERSION_CONFLICT", context.exception.code)
        escalated = self.store.respond({
            "coordination_id": record["coordination_id"],
            "responder_anchor_ref": "worker-anchor",
            "expected_proposal_version": 2,
            "outcome": "UNRESOLVED",
            "evidence": ["worker:scope-conflict"],
            "request_id": "response-1",
        })
        self.assertEqual("ESCALATED", escalated["state"])
        self.assertIn("Conductor/user", escalated["next_action"])
        self.assertEqual("UNRESOLVED", escalated["escalation"]["reason"])

    def test_all_participants_agree_accepts_only_recorded_scope(self) -> None:
        record, _ = self.store.create(self.base)
        self.store.propose({
            "coordination_id": record["coordination_id"],
            "proposer_anchor_ref": "master-anchor",
            "expected_proposal_version": 1,
            "proposal": {"action": "bounded"},
            "evidence": ["todo:todo-1"],
            "request_id": "proposal-accept",
        })
        first = self.store.respond({
            "coordination_id": record["coordination_id"],
            "responder_anchor_ref": "master-anchor",
            "expected_proposal_version": 2,
            "outcome": "AGREE",
            "evidence": ["master:reviewed"],
            "request_id": "response-master",
        })
        self.assertEqual("PROPOSED", first["state"])
        accepted = self.store.respond({
            "coordination_id": record["coordination_id"],
            "responder_anchor_ref": "worker-anchor",
            "expected_proposal_version": 2,
            "outcome": "AGREE",
            "evidence": ["worker:reviewed"],
            "request_id": "response-worker",
        })
        self.assertEqual("ACCEPTED", accepted["state"])
        self.assertIn("recorded node/Todo/Task Frame/file scope", accepted["next_action"])


if __name__ == "__main__":
    unittest.main()
