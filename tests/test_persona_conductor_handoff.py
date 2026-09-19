"""Project-wide (Conductor) persona.assign handoff_from (2026-09-19 fix).

2026-09-19 operator finding: assign_persona's handoff_from clearing of the
old owner's row was gated behind `if node_ref is not None:`
(tools/universe_server.py), so it only ever ran for a node-scoped MASTER
handoff. A project-wide Conductor handoff (node_ref is None) validated and
accepted handoff_from, but silently never acted on it -- the old Conductor
Anchor's session_persona_assignment row stayed ACTIVE forever. Repeated
Conductor handoffs stacked up several simultaneously-ACTIVE project-wide
assignments, which is exactly what
tools/universe_ui/app.js::fleetProjectConductorAssignments() treats as
ambiguous (>1 active -> ERROR) -- the Fleet Conductor panel flickered
between showing the real assignment and an ERROR/UNKNOWN read-failure state.
The old Conductor's persona_automation_run was also never auto-stopped
(_stop_superseded_persona_automation_run was called only inside the same
node_ref-gated branch), leaving its automation running under an Anchor that
had just lost its Persona.

Fixed by adding a parallel project-wide branch in assign_persona (retiring
the named old Anchor's assignment the same way unassign_persona does, since
a project-wide assignment has no node binding to partially clear) and a
parallel _stop_superseded_persona_automation_run call in
_handle_persona_assign_action for the node_ref is None case.
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


class ProjectWideConductorHandoffTests(unittest.TestCase):
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
        if action_id in {"persona.create", "persona.assign", "persona.unassign", "persona.automation.start"}:
            body.setdefault("request_id", f"conductor-handoff-{action_id.replace('.', '-')}-{uuid.uuid4().hex}")
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": body})

    def register(self, session_id, mode="CONDUCTOR"):
        material, _ = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": "TEST", "mode": mode, "provider": "CODEX",
        })
        return material["session_anchor_ref"]

    def make_persona(self, title="project lead"):
        status, result = self.act("persona.create", {"title": title, "body": "프로젝트 전체 목표와 골격을 유지한다."})
        self.assertEqual(200, status, result)
        return result["persona"]

    def assign(self, anchor, persona, expected_assignment_revision=0, handoff_from=None):
        request = {
            "session_anchor_ref": anchor, "project_id": "TEST", "persona_id": persona["persona_id"],
            "expected_persona_revision": persona["revision"],
            "expected_assignment_revision": expected_assignment_revision,
        }
        if handoff_from is not None:
            request["handoff_from"] = handoff_from
        return self.act("persona.assign", request)

    def test_handoff_from_retires_the_old_project_wide_owner(self):
        persona = self.make_persona()
        old_anchor = self.register("conductor-handoff-old-1")
        new_anchor = self.register("conductor-handoff-new-1")
        status, old_assignment = self.assign(old_anchor, persona)
        self.assertEqual(200, status, old_assignment)

        status, new_assignment = self.assign(new_anchor, persona, handoff_from={
            "session_anchor_ref": old_anchor,
            "expected_assignment_revision": old_assignment["assignment"]["assignment_revision"],
        })
        self.assertEqual(200, status, new_assignment)
        self.assertIsNotNone(new_assignment.get("handoff_cleared"))
        self.assertEqual(old_anchor, new_assignment["handoff_cleared"]["session_anchor_ref"])

        status, old_read = self.act("persona.assignment-read", {"session_anchor_ref": old_anchor})
        self.assertEqual(200, status, old_read)
        self.assertEqual("UNASSIGNED", old_read["assignment"]["state"])

        status, new_read = self.act("persona.assignment-read", {"session_anchor_ref": new_anchor})
        self.assertEqual(200, status, new_read)
        self.assertEqual("ACTIVE", new_read["assignment"]["state"])

    def test_repeated_handoffs_never_leave_two_active_project_wide_owners(self):
        persona = self.make_persona()
        anchors = [self.register(f"conductor-handoff-chain-{i}") for i in range(4)]
        status, first = self.assign(anchors[0], persona)
        self.assertEqual(200, status, first)
        previous_anchor, previous_revision = anchors[0], first["assignment"]["assignment_revision"]
        for anchor in anchors[1:]:
            status, result = self.assign(anchor, persona, handoff_from={
                "session_anchor_ref": previous_anchor, "expected_assignment_revision": previous_revision,
            })
            self.assertEqual(200, status, result)
            previous_anchor, previous_revision = anchor, result["assignment"]["assignment_revision"]

        active_count = 0
        for anchor in anchors:
            status, read = self.act("persona.assignment-read", {"session_anchor_ref": anchor})
            self.assertEqual(200, status, read)
            if read["assignment"]["state"] == "ACTIVE":
                active_count += 1
        self.assertEqual(1, active_count)

    def test_stale_handoff_from_is_rejected_and_changes_nothing(self):
        persona = self.make_persona()
        old_anchor = self.register("conductor-handoff-stale-old")
        new_anchor = self.register("conductor-handoff-stale-new")
        status, old_assignment = self.assign(old_anchor, persona)
        self.assertEqual(200, status, old_assignment)

        status, result = self.assign(new_anchor, persona, handoff_from={
            "session_anchor_ref": old_anchor,
            "expected_assignment_revision": old_assignment["assignment"]["assignment_revision"] + 1,
        })
        self.assertEqual(409, status, result)
        self.assertEqual("PERSONA_ASSIGNMENT_HANDOFF_STALE", result.get("error_code"))

        status, old_read = self.act("persona.assignment-read", {"session_anchor_ref": old_anchor})
        self.assertEqual(200, status, old_read)
        self.assertEqual("ACTIVE", old_read["assignment"]["state"])

        status, new_read = self.act("persona.assignment-read", {"session_anchor_ref": new_anchor})
        self.assertEqual(200, status, new_read)
        self.assertIsNone(new_read["assignment"])

    def test_handoff_auto_stops_the_old_conductors_active_automation_run(self):
        persona = self.make_persona()
        old_anchor = self.register("conductor-handoff-autostop-old")
        new_anchor = self.register("conductor-handoff-autostop-new")
        status, old_assignment = self.assign(old_anchor, persona)
        self.assertEqual(200, status, old_assignment)

        status, started = self.act("persona.automation.start", {
            "project_id": "TEST", "session_anchor_ref": old_anchor,
            "scope": "project-wide design/goal facilitation", "instruction": "keep the backlog aligned",
        })
        self.assertEqual(201, status, started)
        self.assertEqual("RUNNING", started["run"]["state"])

        status, result = self.assign(new_anchor, persona, handoff_from={
            "session_anchor_ref": old_anchor,
            "expected_assignment_revision": old_assignment["assignment"]["assignment_revision"],
        })
        self.assertEqual(200, status, result)

        status, run_status = self.act("persona.automation.status", {"run_id": started["run"]["run_id"]})
        self.assertEqual(200, status, run_status)
        self.assertEqual("STOPPED", run_status["run"]["state"])


if __name__ == "__main__":
    unittest.main()
