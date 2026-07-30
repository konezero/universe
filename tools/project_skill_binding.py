from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SKILL_BINDING_PROPOSAL_SCHEMA = "universe.project-master-skill-binding-proposal.v1"


class ProjectSkillBindingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_project_skill_binding_proposal(
    *,
    project_root: Path,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    try:
        skills_root = (root / ".ai" / "skills").resolve(strict=True)
    except OSError as error:
        raise ProjectSkillBindingError(
            "PROJECT_SKILL_ROOT_UNAVAILABLE",
            "installed Project Skill root is unavailable",
        ) from error
    if not skills_root.is_dir() or not skills_root.is_relative_to(root):
        raise ProjectSkillBindingError(
            "PROJECT_SKILL_ROOT_UNAVAILABLE",
            "installed Project Skill root is unavailable",
        )
    candidates = context.get("binding_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ProjectSkillBindingError(
            "PROJECT_SKILL_BINDING_CANDIDATES_INVALID",
            "Project Skill Plan has no binding candidates",
        )
    bindings: list[dict[str, str]] = []
    evidence: list[dict[str, str]] = []
    seen_skill_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ProjectSkillBindingError(
                "PROJECT_SKILL_BINDING_CANDIDATE_INVALID",
                "Project Skill binding candidate must be an object",
            )
        skill_id = _text(candidate.get("skill_id"), "skill_id")
        if skill_id in seen_skill_ids:
            raise ProjectSkillBindingError(
                "PROJECT_SKILL_BINDING_DUPLICATED",
                "Project Skill Plan contains a duplicate Skill",
            )
        seen_skill_ids.add(skill_id)
        skill_path = _resolve_skill_path(
            project_root=root,
            skills_root=skills_root,
            skill_id=skill_id,
        )
        relative_ref = skill_path.relative_to(root).as_posix()
        binding = {
            "skill_id": skill_id,
            "skill_version": _text(
                candidate.get("skill_version"),
                "skill_version",
            ),
            "skill_ref": relative_ref,
            "context_pack_digest": _sha256(
                candidate.get("context_pack_digest"),
                "context_pack_digest",
            ),
            "operation_class": _operation_class(candidate.get("operation_class")),
        }
        bindings.append(binding)
        evidence.append(
            {
                "candidate_id": _text(
                    candidate.get("candidate_id"),
                    "candidate_id",
                ),
                "skill_id": skill_id,
                "skill_ref": relative_ref,
                "skill_file_sha256": hashlib.sha256(
                    skill_path.read_bytes()
                ).hexdigest(),
                "resolution": "INSTALLED_PROJECT_SKILL",
                "skill_version_source": "ADOPTED_PLAN",
                "model_ref": _text(candidate.get("model_ref"), "model_ref"),
                "provider_ref": _text(
                    candidate.get("provider_ref"),
                    "provider_ref",
                ),
            }
        )
    bindings.sort(key=lambda item: (item["skill_id"], item["skill_ref"]))
    evidence.sort(key=lambda item: (item["skill_id"], item["skill_ref"]))
    material = {
        "schema": SKILL_BINDING_PROPOSAL_SCHEMA,
        "project_id": _text(context.get("project_id"), "project_id"),
        "handoff_id": _text(context.get("handoff_id"), "handoff_id"),
        "adoption_id": _text(context.get("adoption_id"), "adoption_id"),
        "context_digest": _sha256(context.get("context_digest"), "context_digest"),
        "binding_state": "PROJECT_MASTER_BINDING_PROPOSED",
        "skill_bindings": bindings,
        "resolution_evidence": evidence,
        "approval_required": True,
        "task_frame_started": False,
        "authority_created": False,
        "execution_assignment_created": False,
        "repository_write": False,
        "next_operation": "TASK_FRAME_PROPOSAL_REQUIRED",
    }
    proposal_digest = _digest(material)
    return {
        **material,
        "proposal_digest": proposal_digest,
        "proposal_id": "skillbind_" + proposal_digest[:24],
        "status": "PROJECT_SKILL_BINDING_PROPOSAL_READY",
    }


def _resolve_skill_path(
    *,
    project_root: Path,
    skills_root: Path,
    skill_id: str,
) -> Path:
    matches: dict[str, Path] = {}
    for candidate in skills_root.rglob("SKILL.md"):
        if candidate.parent.name != skill_id:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            raw = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(skills_root)
            or not resolved.is_relative_to(project_root)
            or _frontmatter_name(raw) != skill_id
        ):
            continue
        matches[str(resolved).casefold()] = resolved
    if not matches:
        raise ProjectSkillBindingError(
            "PROJECT_LOCAL_SKILL_REF_UNAVAILABLE",
            f"installed Project Skill is unavailable: {skill_id}",
        )
    if len(matches) != 1:
        raise ProjectSkillBindingError(
            "PROJECT_LOCAL_SKILL_REF_AMBIGUOUS",
            f"installed Project Skill is ambiguous: {skill_id}",
        )
    return next(iter(matches.values()))


def _frontmatter_name(value: str) -> str:
    lines = value.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return ""
        if stripped.startswith("name:"):
            name = stripped.removeprefix("name:").strip()
            if len(name) >= 2 and name[0] == name[-1] and name[0] in {"'", '"'}:
                name = name[1:-1].strip()
            return name
    return ""


def _operation_class(value: Any) -> str:
    result = _text(value, "operation_class").upper()
    if result not in {"READ", "PROPOSE", "EXECUTE"}:
        raise ProjectSkillBindingError(
            "PROJECT_SKILL_OPERATION_CLASS_INVALID",
            "operation_class is unsupported",
        )
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectSkillBindingError(
            "PROJECT_SKILL_BINDING_FIELD_INVALID",
            f"{field} must be non-empty text",
        )
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProjectSkillBindingError(
            "PROJECT_SKILL_BINDING_FIELD_INVALID",
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
