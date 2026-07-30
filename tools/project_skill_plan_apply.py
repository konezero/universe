from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SKILL_PLAN_APPROVAL_SCHEMA = "universe.project-skill-plan-master-approval.v1"
SKILL_PLAN_CONTEXT_SCHEMA = "universe.project-skill-plan-master-context.v1"
SKILL_PLAN_RECEIPT_SCHEMA = "universe.project-skill-plan-master-receipt.v1"
HANDOFF_SCHEMA = "universe.project-master-handoff.v1"
ADOPTION_SCHEMA = "universe.project-skill-plan-adoption.v1"


class ProjectSkillPlanApplyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_project_skill_plan_approval(
    *,
    project_id: str,
    handoff: Mapping[str, Any],
    evidence_ref: str,
) -> dict[str, str]:
    normalized = normalize_skill_plan_handoff(project_id, handoff)
    adoption = normalized["source"]["adoption"]
    return {
        "schema": SKILL_PLAN_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "project_id": normalized["project_id"],
        "handoff_id": normalized["handoff_id"],
        "handoff_digest": normalized["handoff_digest"],
        "adoption_id": adoption["adoption_id"],
        "selection_digest": adoption["selection_digest"],
        "evidence_ref": _text(evidence_ref, "evidence_ref"),
    }


def build_project_skill_plan_context(
    *,
    project_id: str,
    handoff: Any,
    approval: Any,
) -> dict[str, Any]:
    normalized = normalize_skill_plan_handoff(project_id, handoff)
    normalized_approval = _approval(normalized, approval)
    adoption = normalized["source"]["adoption"]
    binding_candidates = []
    for candidate in adoption["selected_candidates"]:
        skill = candidate["skill"]
        binding_candidates.append(
            {
                "candidate_id": candidate["candidate_id"],
                "skill_id": skill["skill_id"],
                "skill_version": skill["skill_version"],
                "operation_class": skill["operation_class"],
                "context_pack_digest": skill["context_pack_digest"],
                "model_ref": candidate["model_ref"],
                "provider_ref": candidate["provider_ref"],
                "skill_ref": "UNRESOLVED",
                "skill_ref_state": "PROJECT_MASTER_RESOLUTION_REQUIRED",
            }
        )
    material = {
        "schema": SKILL_PLAN_CONTEXT_SCHEMA,
        "project_id": normalized["project_id"],
        "handoff_id": normalized["handoff_id"],
        "handoff_digest": normalized["handoff_digest"],
        "adoption_id": adoption["adoption_id"],
        "selection_digest": adoption["selection_digest"],
        "context_pack_id": adoption["context_pack_id"],
        "approval_evidence_ref": normalized_approval["evidence_ref"],
        "binding_candidates": binding_candidates,
        "binding_state": "PROJECT_MASTER_CONTEXT_BOUND",
        "skill_ref_resolution": "REQUIRED",
        "task_frame_binding": "NOT_CREATED",
        "repository_write": False,
    }
    return {
        **material,
        "context_digest": _digest(material),
        "status": "PROJECT_SKILL_PLAN_CONTEXT_READY",
    }


def project_skill_plan_receipt(context: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "project_id",
        "handoff_id",
        "handoff_digest",
        "adoption_id",
        "selection_digest",
        "context_pack_id",
        "approval_evidence_ref",
        "binding_candidates",
        "binding_state",
        "skill_ref_resolution",
        "task_frame_binding",
        "repository_write",
        "context_digest",
        "status",
    }
    allowed = required | {"applied_at"}
    if (
        not isinstance(context, Mapping)
        or not required.issubset(context)
        or set(context) - allowed
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_CONTEXT_INVALID",
            "Project Skill Plan context fields are invalid",
        )
    material = {
        "project_id": context["project_id"],
        "handoff_id": context["handoff_id"],
        "adoption_id": context["adoption_id"],
        "selection_digest": context["selection_digest"],
        "context_digest": context["context_digest"],
        "binding_state": context["binding_state"],
        "skill_ref_resolution": context["skill_ref_resolution"],
        "task_frame_binding": context["task_frame_binding"],
        "repository_write": context["repository_write"],
    }
    if "applied_at" in context:
        material["applied_at"] = _text(context["applied_at"], "applied_at")
    return {
        "schema": SKILL_PLAN_RECEIPT_SCHEMA,
        "status": "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
        **material,
        "receipt_digest": _digest(material),
    }


def normalize_skill_plan_handoff(
    expected_project_id: str,
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_HANDOFF_INVALID",
            "Project Skill Plan handoff must be an object",
        )
    required = {
        "schema",
        "project_id",
        "source",
        "delivery_state",
        "effects",
        "next_operation",
        "handoff_digest",
        "handoff_id",
        "status",
    }
    allowed = required | {"purpose", "created_at", "delivered_at", "room_message_id"}
    if not required.issubset(value) or set(value) - allowed:
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_HANDOFF_INVALID",
            "Project Skill Plan handoff fields are invalid",
        )
    project_id = _text(expected_project_id, "project_id")
    if (
        value.get("schema") != HANDOFF_SCHEMA
        or value.get("project_id") != project_id
        or value.get("delivery_state") != "PROPOSAL_ONLY"
        or value.get("status") != "PROJECT_MASTER_HANDOFF_PROPOSAL_READY"
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_HANDOFF_INVALID",
            "Project Skill Plan handoff contract is invalid",
        )
    source = value.get("source")
    if (
        not isinstance(source, Mapping)
        or set(source) != {"kind", "adoption"}
        or source.get("kind") != "SKILL_PLAN"
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_HANDOFF_INVALID",
            "handoff source must be one adopted Skill Plan",
        )
    adoption = _adoption(project_id, source.get("adoption"))
    material: dict[str, Any] = {
        "schema": value["schema"],
        "project_id": project_id,
        "source": {"kind": "SKILL_PLAN", "adoption": adoption},
        "delivery_state": "PROPOSAL_ONLY",
        "effects": value["effects"],
        "next_operation": value["next_operation"],
    }
    if "purpose" in value:
        material["purpose"] = value["purpose"]
    handoff_digest = _digest(material)
    if (
        value.get("handoff_digest") != handoff_digest
        or value.get("handoff_id") != "handoff_" + handoff_digest[:24]
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_HANDOFF_DIGEST_MISMATCH",
            "Project Skill Plan handoff digest is invalid",
        )
    return {
        **dict(value),
        "source": {"kind": "SKILL_PLAN", "adoption": adoption},
    }


def _adoption(project_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_ADOPTION_INVALID",
            "Skill Plan adoption must be an object",
        )
    material_fields = (
        "schema",
        "project_id",
        "proposal_id",
        "proposal_digest",
        "context_pack_id",
        "selected_candidates",
        "binding_state",
        "effects",
        "next_operation",
    )
    required = set(material_fields) | {"selection_digest", "adoption_id", "status"}
    if not required.issubset(value) or set(value) - (required | {"adopted_at"}):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_ADOPTION_INVALID",
            "Skill Plan adoption fields are invalid",
        )
    if (
        value.get("schema") != ADOPTION_SCHEMA
        or value.get("project_id") != project_id
        or value.get("status") != "SKILL_PLAN_ADOPTED"
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_ADOPTION_INVALID",
            "Skill Plan adoption contract is invalid",
        )
    selected = value.get("selected_candidates")
    if not isinstance(selected, list) or not selected:
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_ADOPTION_INVALID",
            "Skill Plan adoption must select at least one candidate",
        )
    candidate_ids: set[str] = set()
    for candidate in selected:
        if not isinstance(candidate, Mapping):
            raise ProjectSkillPlanApplyError(
                "PROJECT_SKILL_PLAN_CANDIDATE_INVALID",
                "selected Skill candidate must be an object",
            )
        candidate_id = _text(candidate.get("candidate_id"), "candidate_id")
        if candidate_id in candidate_ids:
            raise ProjectSkillPlanApplyError(
                "PROJECT_SKILL_PLAN_CANDIDATE_INVALID",
                "selected Skill candidates contain a duplicate",
            )
        candidate_ids.add(candidate_id)
        skill = candidate.get("skill")
        if not isinstance(skill, Mapping):
            raise ProjectSkillPlanApplyError(
                "PROJECT_SKILL_PLAN_CANDIDATE_INVALID",
                "selected Skill identity is unavailable",
            )
        for field in (
            "skill_id",
            "skill_version",
            "operation_class",
            "context_pack_digest",
        ):
            _text(skill.get(field), f"skill.{field}")
        if str(skill["operation_class"]).upper() not in {"READ", "PROPOSE", "EXECUTE"}:
            raise ProjectSkillPlanApplyError(
                "PROJECT_SKILL_PLAN_CANDIDATE_INVALID",
                "selected Skill operation class is unsupported",
            )
        _sha256(skill["context_pack_digest"], "skill.context_pack_digest")
        _text(candidate.get("model_ref"), "candidate.model_ref")
        _text(candidate.get("provider_ref"), "candidate.provider_ref")
    material = {field: value[field] for field in material_fields}
    selection_digest = _digest(material)
    if (
        value.get("selection_digest") != selection_digest
        or value.get("adoption_id") != "skilladopt_" + selection_digest[:24]
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_ADOPTION_DIGEST_MISMATCH",
            "Skill Plan adoption digest is invalid",
        )
    return dict(value)


def _approval(
    handoff: Mapping[str, Any],
    value: Any,
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "status",
        "project_id",
        "handoff_id",
        "handoff_digest",
        "adoption_id",
        "selection_digest",
        "evidence_ref",
    }:
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_APPROVAL_INVALID",
            "Project Skill Plan approval fields are invalid",
        )
    adoption = handoff["source"]["adoption"]
    expected = {
        "schema": SKILL_PLAN_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "project_id": handoff["project_id"],
        "handoff_id": handoff["handoff_id"],
        "handoff_digest": handoff["handoff_digest"],
        "adoption_id": adoption["adoption_id"],
        "selection_digest": adoption["selection_digest"],
    }
    normalized = {
        **expected,
        "evidence_ref": _text(value.get("evidence_ref"), "approval.evidence_ref"),
    }
    if any(
        value.get(field) != expected_value for field, expected_value in expected.items()
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_APPROVAL_MISMATCH",
            "approval does not match the exact Skill Plan handoff",
        )
    return normalized


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_FIELD_INVALID",
            f"{field} must be non-empty text",
        )
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProjectSkillPlanApplyError(
            "PROJECT_SKILL_PLAN_FIELD_INVALID",
            f"{field} must be a lowercase SHA-256",
        )
    return normalized


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
