from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_skill_plan_apply import (  # noqa: E402
    ProjectSkillPlanApplyError,
    build_project_skill_plan_approval,
    build_project_skill_plan_context,
    project_skill_plan_receipt,
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def skill_plan_handoff() -> dict[str, object]:
    adoption: dict[str, object] = {
        "schema": "universe.project-skill-plan-adoption.v1",
        "project_id": "GCS",
        "proposal_id": "skillplan_test",
        "proposal_digest": "1" * 64,
        "context_pack_id": "context_test",
        "selected_candidates": [
            {
                "candidate_id": "skill_candidate_test",
                "skill": {
                    "skill_id": "source-review",
                    "skill_version": "1.0.0",
                    "operation_class": "READ",
                    "context_pack_digest": "2" * 64,
                },
                "model_ref": "provider://OPENAI/model/gpt-test",
                "provider_ref": "OPENAI",
            }
        ],
        "binding_state": "PROJECT_MASTER_BINDING_REQUIRED",
        "effects": {
            "project_source_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "PROJECT_MASTER_HANDOFF_CANDIDATE",
    }
    selection_digest = digest(adoption)
    adoption.update(
        {
            "selection_digest": selection_digest,
            "adoption_id": "skilladopt_" + selection_digest[:24],
            "status": "SKILL_PLAN_ADOPTED",
        }
    )
    handoff: dict[str, object] = {
        "schema": "universe.project-master-handoff.v1",
        "project_id": "GCS",
        "source": {"kind": "SKILL_PLAN", "adoption": adoption},
        "delivery_state": "PROPOSAL_ONLY",
        "effects": {
            "project_source_write": "NONE",
            "project_runtime_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "USER_APPROVAL_REQUIRED_FOR_MASTER_DELIVERY",
        "purpose": "Review the selected Skill Plan.",
    }
    handoff_digest = digest(handoff)
    handoff.update(
        {
            "handoff_digest": handoff_digest,
            "handoff_id": "handoff_" + handoff_digest[:24],
            "status": "PROJECT_MASTER_HANDOFF_PROPOSAL_READY",
        }
    )
    return handoff


class ProjectSkillPlanApplyTests(unittest.TestCase):
    def test_selected_plan_becomes_non_executing_master_context(self) -> None:
        handoff = skill_plan_handoff()
        approval = build_project_skill_plan_approval(
            project_id="GCS",
            handoff=handoff,
            evidence_ref="universe://projects/GCS/skill-plan-approval/test",
        )

        context = build_project_skill_plan_context(
            project_id="GCS",
            handoff=handoff,
            approval=approval,
        )
        receipt = project_skill_plan_receipt(context)

        self.assertEqual("PROJECT_MASTER_CONTEXT_BOUND", context["binding_state"])
        self.assertEqual("UNRESOLVED", context["binding_candidates"][0]["skill_ref"])
        self.assertEqual(
            "PROJECT_MASTER_RESOLUTION_REQUIRED",
            context["binding_candidates"][0]["skill_ref_state"],
        )
        self.assertEqual("NOT_CREATED", context["task_frame_binding"])
        self.assertFalse(context["repository_write"])
        self.assertEqual(
            "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
            receipt["status"],
        )

    def test_approval_is_bound_to_exact_handoff(self) -> None:
        handoff = skill_plan_handoff()
        approval = build_project_skill_plan_approval(
            project_id="GCS",
            handoff=handoff,
            evidence_ref="universe://projects/GCS/skill-plan-approval/test",
        )
        approval["handoff_digest"] = "f" * 64

        with self.assertRaisesRegex(
            ProjectSkillPlanApplyError,
            "approval does not match",
        ):
            build_project_skill_plan_context(
                project_id="GCS",
                handoff=handoff,
                approval=approval,
            )

    def test_tampered_adoption_is_rejected(self) -> None:
        handoff = skill_plan_handoff()
        handoff["source"]["adoption"]["selected_candidates"][0]["model_ref"] = (
            "provider://GROK/model/grok-build"
        )

        with self.assertRaisesRegex(
            ProjectSkillPlanApplyError,
            "adoption digest is invalid",
        ):
            build_project_skill_plan_approval(
                project_id="GCS",
                handoff=handoff,
                evidence_ref="universe://projects/GCS/skill-plan-approval/test",
            )


if __name__ == "__main__":
    unittest.main()
