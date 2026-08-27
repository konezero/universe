from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


INTENT_DECISION_SCHEMA = "universe.intent-decision.v1"
SKILL_REGISTRY_SCHEMA = "universe.skill-registry-snapshot.v1"
SKILL_RESOLUTION_SCHEMA = "universe.skill-resolution.v1"
STRUCTURED_PLAN_SCHEMA = "universe.structured-plan.v1"
SKILL_GAP_SCHEMA = "universe.skill-gap-observation.v1"
SKILL_CANDIDATE_SCHEMA = "universe.skill-candidate.v1"
SKILL_PACK_ARTIFACT_SCHEMA = "ai-career.skill-pack-artifact.v1"
SKILL_PACK_MANIFEST_SCHEMA = "universe.skill-pack-manifest.v1"
SKILL_RELEASE_ADOPTION_SCHEMA = "universe.skill-release-adoption.v1"

ROUTES = frozenset({"RESOLVE_SKILL", "READ_ONLY_RESPONSE", "PROPOSE", "ASK", "WAIT", "STRICT_GATE"})
EFFECTS = frozenset({"NONE", "RUNTIME_STATE_WRITE", "USER_ARTIFACT_WRITE", "BOUNDED_LOCAL_WORK", "STRICT_MUTATION"})
READ_ONLY_INTENTS = frozenset({"QUESTION", "REVIEW", "REVIEW_OR_EXPLAIN", "DESIGN_DISCUSSION", "STATUS_REQUEST"})
CANDIDATE_STATES = frozenset({"OBSERVED", "ELIGIBLE", "DRAFTED", "VALIDATED", "RELEASE_CANDIDATE", "ADOPTED", "REJECTED", "SUPERSEDED"})
FALLBACK_HANDLERS = {
    ("PLAN_CREATE", "NONE"): ("GENERIC_STRUCTURED_REASONING", STRUCTURED_PLAN_SCHEMA, False),
    ("TODO_WRITE", "RUNTIME_STATE_WRITE"): ("TODO_ADAPTER", "universe.todo.v1", True),
    ("DOCUMENT_CREATE", "USER_ARTIFACT_WRITE"): ("DOCUMENT_ARTIFACT_WRITER", "universe.document-artifact.v1", True),
    ("SKILL_AUTHOR", "BOUNDED_LOCAL_WORK"): ("DIRECT_INSTRUCTION_WORK_RECEIPT", "universe.skill-author-result.v1", True),
    ("FILE_DELETE", "STRICT_MUTATION"): ("STRICT_ASSIGNMENT_EXECUTION_GUARD", "universe.strict-mutation-result.v1", True),
}


class IntentRoutingError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", f"{field} must be an object")
    return value


def _text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", f"{field} is required")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", f"{field} is too long")
    return normalized


def _identifier(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=160)
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/" for character in normalized):
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", f"{field} contains unsupported characters")
    return normalized


def _timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _exact_fields(value: Mapping[str, Any], required: set[str], optional: set[str], code: str) -> None:
    if not required.issubset(value) or set(value) - required - optional:
        raise IntentRoutingError(code, "request fields do not match the contract")


def normalize_planning_phrase(value: str) -> dict[str, str] | None:
    text = " ".join(str(value).casefold().strip().split())
    planning_tokens = ("계획", "플랜", "plan", "순서", "로드맵")
    request_tokens = ("짜", "만들", "세워", "정리", "create", "build")
    if any(token in text for token in planning_tokens) and any(token in text for token in request_tokens):
        return {"intent_class": "PLAN_REQUEST", "required_capability": "PLAN_CREATE", "effect_class": "NONE"}
    return None


def normalize_intent_decision(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    request = _mapping(value, "intent_decision")
    required = {"session_id", "frame_id", "anchor_id", "utterance_ref", "context_digest", "intent_class", "imperative_state", "target_state", "required_capability", "effect_class", "route", "confirmation_of", "evidence"}
    optional = {"project_id", "node_ref"}
    _exact_fields(request, required, optional, "INTENT_EVIDENCE_INVALID")
    evidence = _mapping(request["evidence"], "evidence")
    _exact_fields(evidence, {"current_message", "prior_messages", "coordinates"}, {"pending_proposal_ref"}, "INTENT_EVIDENCE_INVALID")
    current = _mapping(evidence["current_message"], "evidence.current_message")
    _exact_fields(current, {"message_id", "role", "digest", "observed_at"}, set(), "INTENT_EVIDENCE_INVALID")
    if current["role"] != "USER":
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "current message provenance must be USER")
    current_digest = _text(current["digest"], "evidence.current_message.digest", maximum=64).lower()
    if len(current_digest) != 64 or any(c not in "0123456789abcdef" for c in current_digest):
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "current message digest must be SHA-256")
    coordinates = _mapping(evidence["coordinates"], "evidence.coordinates")
    _exact_fields(coordinates, {"session_id", "frame_id", "anchor_id"}, set(), "INTENT_EVIDENCE_INVALID")
    session_id = _identifier(request["session_id"], "session_id")
    frame_id = _identifier(request["frame_id"], "frame_id")
    anchor_id = _identifier(request["anchor_id"], "anchor_id")
    if (coordinates["session_id"], coordinates["frame_id"], coordinates["anchor_id"]) != (session_id, frame_id, anchor_id):
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "decision coordinates do not match evidence coordinates")
    prior = evidence["prior_messages"]
    if not isinstance(prior, list) or len(prior) > 20:
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "prior_messages must be a bounded list")
    context_material = [{"message_id": _identifier(current["message_id"], "message_id"), "role": "USER", "digest": current_digest}]
    for index, item in enumerate(prior):
        message = _mapping(item, f"prior_messages[{index}]")
        _exact_fields(message, {"message_id", "role", "digest"}, set(), "INTENT_EVIDENCE_INVALID")
        if message["role"] not in {"USER", "ASSISTANT"}:
            raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "prior message role is unsupported")
        context_material.append({"message_id": _identifier(message["message_id"], "message_id"), "role": message["role"], "digest": _text(message["digest"], "digest", maximum=64).lower()})
    context_digest = _text(request["context_digest"], "context_digest", maximum=64).lower()
    if context_digest != digest(context_material):
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "context_digest does not match bounded evidence")
    observed_at = _timestamp(current["observed_at"], "observed_at")
    physical_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if abs((physical_now - observed_at).total_seconds()) > 300:
        raise IntentRoutingError("INTENT_DECISION_STALE", "current message evidence is stale")
    intent_class = _identifier(request["intent_class"], "intent_class").upper()
    effect_class = _identifier(request["effect_class"], "effect_class").upper()
    route = _identifier(request["route"], "route").upper()
    if effect_class not in EFFECTS or route not in ROUTES:
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "effect_class or route is unsupported")
    if intent_class in READ_ONLY_INTENTS and (effect_class != "NONE" or route not in {"READ_ONLY_RESPONSE", "ASK"}):
        raise IntentRoutingError("SKILL_EFFECT_MISMATCH", "read-only intent cannot select an effectful route")
    confirmation_of = request["confirmation_of"]
    if confirmation_of is not None:
        confirmation_of = _identifier(confirmation_of, "confirmation_of")
        if evidence.get("pending_proposal_ref") != confirmation_of:
            raise IntentRoutingError("INTENT_TARGET_AMBIGUOUS", "confirmation does not match the exact pending proposal")
    material = {
        "schema": INTENT_DECISION_SCHEMA,
        "session_id": session_id,
        "frame_id": frame_id,
        "anchor_id": anchor_id,
        "utterance_ref": _identifier(request["utterance_ref"], "utterance_ref"),
        "context_digest": context_digest,
        "intent_class": intent_class,
        "imperative_state": _identifier(request["imperative_state"], "imperative_state").upper(),
        "target_state": _identifier(request["target_state"], "target_state").upper(),
        "required_capability": _identifier(request["required_capability"], "required_capability").upper(),
        "effect_class": effect_class,
        "route": route,
        "confirmation_of": confirmation_of,
        "project_id": request.get("project_id"),
        "node_ref": request.get("node_ref"),
        "evidence_digest": digest(evidence),
    }
    material["decision_id"] = "intent_" + digest(material)[:24]
    return material


def normalize_registry_snapshot(value: Any) -> dict[str, Any]:
    request = _mapping(value, "registry_snapshot")
    _exact_fields(request, {"skills"}, {"source_ref"}, "SKILL_REGISTRY_UNAVAILABLE")
    if not isinstance(request["skills"], list):
        raise IntentRoutingError("SKILL_REGISTRY_UNAVAILABLE", "skills must be a list")
    skills = []
    for index, raw in enumerate(request["skills"]):
        skill = _mapping(raw, f"skills[{index}]")
        _exact_fields(skill, {"skill_id", "version", "scope", "intents", "capabilities", "effects", "output_contract", "priority"}, {"project_id", "node_ref"}, "SKILL_REGISTRY_UNAVAILABLE")
        scope = _identifier(skill["scope"], "scope").upper()
        if scope not in {"NODE", "PROJECT", "UNIVERSE", "COMMON"}:
            raise IntentRoutingError("SKILL_REGISTRY_UNAVAILABLE", "skill scope is unsupported")
        priority = skill["priority"]
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise IntentRoutingError("SKILL_REGISTRY_UNAVAILABLE", "skill priority must be an integer")
        skills.append({
            "skill_id": _identifier(skill["skill_id"], "skill_id"),
            "version": _identifier(skill["version"], "version"),
            "scope": scope,
            "intents": sorted({_identifier(v, "intent").upper() for v in skill["intents"]}),
            "capabilities": sorted({_identifier(v, "capability").upper() for v in skill["capabilities"]}),
            "effects": sorted({_identifier(v, "effect").upper() for v in skill["effects"]}),
            "output_contract": _identifier(skill["output_contract"], "output_contract"),
            "priority": priority,
            "project_id": skill.get("project_id"),
            "node_ref": skill.get("node_ref"),
        })
    material = {"schema": SKILL_REGISTRY_SCHEMA, "skills": sorted(skills, key=lambda x: (x["skill_id"], x["version"], x["scope"])), "source_ref": request.get("source_ref") or "universe://skill-registry/manual"}
    material["registry_digest"] = digest(material)
    material["snapshot_id"] = "skill_registry_" + material["registry_digest"][:24]
    return material


def _artifact_relative_path(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=512)
    path = PurePosixPath(normalized)
    if (
        "\\" in normalized
        or path.is_absolute()
        or path.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", f"{field} must be a canonical relative POSIX path")
    return normalized


def normalize_skill_pack_artifact(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact must be an object")
    _exact_fields(
        value,
        {"schema", "pack_id", "version", "files"},
        set(),
        "SKILL_PACK_ARTIFACT_INVALID",
    )
    if value["schema"] != SKILL_PACK_ARTIFACT_SCHEMA:
        raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact schema is unsupported")
    files_value = value["files"]
    if not isinstance(files_value, list) or not 1 <= len(files_value) <= 64:
        raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "files must be a non-empty bounded list")
    files: list[dict[str, Any]] = []
    paths: set[str] = set()
    total_bytes = 0
    for index, file_value in enumerate(files_value):
        if not isinstance(file_value, Mapping):
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", f"files[{index}] must be an object")
        _exact_fields(
            file_value,
            {"path", "role", "sha256", "byte_length", "content_base64", "source_path"},
            set(),
            "SKILL_PACK_ARTIFACT_INVALID",
        )
        path = _artifact_relative_path(file_value["path"], f"files[{index}].path")
        source_path = _artifact_relative_path(file_value["source_path"], f"files[{index}].source_path")
        if path in paths:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact contains a duplicate file path")
        paths.add(path)
        role = _identifier(file_value["role"], f"files[{index}].role").upper()
        if role not in {"SKILL", "ASSET", "REFERENCE"}:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact file role is unsupported")
        byte_length = file_value["byte_length"]
        if isinstance(byte_length, bool) or not isinstance(byte_length, int) or not 0 <= byte_length <= 1024 * 1024:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact file byte_length is invalid")
        content_base64 = _text(file_value["content_base64"], f"files[{index}].content_base64", maximum=2 * 1024 * 1024)
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact file content_base64 is invalid") from error
        if len(content) != byte_length:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact file byte_length does not match content")
        file_sha256 = _text(file_value["sha256"], f"files[{index}].sha256", maximum=64).lower()
        if len(file_sha256) != 64 or any(character not in "0123456789abcdef" for character in file_sha256):
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact file sha256 must be SHA-256")
        if hashlib.sha256(content).hexdigest() != file_sha256:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact file sha256 does not match content")
        total_bytes += byte_length
        if total_bytes > 8 * 1024 * 1024:
            raise IntentRoutingError("SKILL_PACK_ARTIFACT_INVALID", "artifact decoded content exceeds the size limit")
        files.append(
            {
                "path": path,
                "role": role,
                "sha256": file_sha256,
                "byte_length": byte_length,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "source_path": source_path,
            }
        )
    return {
        "schema": SKILL_PACK_ARTIFACT_SCHEMA,
        "pack_id": _identifier(value["pack_id"], "pack_id"),
        "version": _identifier(value["version"], "version"),
        "files": sorted(files, key=lambda item: item["path"]),
    }


def canonical_skill_pack_artifact_bytes(value: Any) -> bytes:
    return (canonical_json(normalize_skill_pack_artifact(value)) + "\n").encode("utf-8")


def normalize_skill_pack_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "manifest must be an object")
    request = value
    _exact_fields(
        request,
        {"schema", "pack_id", "version", "scope", "artifact", "skills"},
        {"project_id", "node_ref", "manifest_digest", "release_id"},
        "SKILL_PACK_MANIFEST_INVALID",
    )
    if request["schema"] != SKILL_PACK_MANIFEST_SCHEMA:
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "manifest schema is unsupported")
    scope = _identifier(request["scope"], "scope").upper()
    if scope not in {"NODE", "PROJECT", "UNIVERSE", "COMMON"}:
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "manifest scope is unsupported")
    project_id = (
        _identifier(request["project_id"], "project_id")
        if request.get("project_id") is not None
        else None
    )
    node_ref = (
        _identifier(request["node_ref"], "node_ref")
        if request.get("node_ref") is not None
        else None
    )
    if scope == "PROJECT" and not project_id:
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "PROJECT pack requires project_id")
    if scope == "NODE" and (not project_id or not node_ref):
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "NODE pack requires project_id and node_ref")
    if scope in {"UNIVERSE", "COMMON"} and (project_id is not None or node_ref is not None):
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "global pack cannot declare Project coordinates")
    artifact = request["artifact"]
    if not isinstance(artifact, Mapping):
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "artifact must be an object")
    _exact_fields(
        artifact,
        {"source_ref", "sha256"},
        set(),
        "SKILL_PACK_MANIFEST_INVALID",
    )
    artifact_sha256 = _text(artifact["sha256"], "artifact.sha256", maximum=64).lower()
    if len(artifact_sha256) != 64 or any(c not in "0123456789abcdef" for c in artifact_sha256):
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "artifact.sha256 must be SHA-256")
    if not isinstance(request["skills"], list) or not 1 <= len(request["skills"]) <= 64:
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "skills must be a non-empty bounded list")
    try:
        skills = normalize_registry_snapshot(
            {"skills": request["skills"], "source_ref": "universe://skill-pack/validation"}
        )["skills"]
    except IntentRoutingError as error:
        raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", error.detail) from error
    identities: set[tuple[Any, ...]] = set()
    for skill in skills:
        if skill["scope"] != scope:
            raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "Skill scope must match pack scope")
        if skill.get("project_id") != project_id or skill.get("node_ref") != node_ref:
            raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "Skill coordinates must match pack coordinates")
        identity = (skill["skill_id"], skill["scope"], skill.get("project_id"), skill.get("node_ref"))
        if identity in identities:
            raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "pack contains duplicate Skill identity")
        identities.add(identity)
    material = {
        "schema": SKILL_PACK_MANIFEST_SCHEMA,
        "pack_id": _identifier(request["pack_id"], "pack_id"),
        "version": _identifier(request["version"], "version"),
        "scope": scope,
        "project_id": project_id,
        "node_ref": node_ref,
        "artifact": {
            "source_ref": _identifier(artifact["source_ref"], "artifact.source_ref"),
            "sha256": artifact_sha256,
        },
        "skills": skills,
    }
    material["manifest_digest"] = digest(material)
    material["release_id"] = "skill_pack_" + material["manifest_digest"][:24]
    if request.get("manifest_digest") is not None:
        claimed_digest = _text(request["manifest_digest"], "manifest_digest", maximum=64).lower()
        if claimed_digest != material["manifest_digest"]:
            raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "manifest_digest does not match the manifest")
    if request.get("release_id") is not None:
        claimed_release_id = _identifier(request["release_id"], "release_id")
        if claimed_release_id != material["release_id"]:
            raise IntentRoutingError("SKILL_PACK_MANIFEST_INVALID", "release_id does not match the manifest")
    return material


def build_adopted_registry_snapshot(
    snapshot: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    incoming = {
        (skill["skill_id"], skill["scope"], skill.get("project_id"), skill.get("node_ref"))
        for skill in manifest["skills"]
    }
    retained = [
        skill
        for skill in snapshot.get("skills", [])
        if (skill["skill_id"], skill["scope"], skill.get("project_id"), skill.get("node_ref"))
        not in incoming
    ]
    return normalize_registry_snapshot(
        {
            "skills": [*retained, *manifest["skills"]],
            "source_ref": "skill-pack-adoption:" + manifest["manifest_digest"],
        }
    )


def empty_registry_snapshot() -> dict[str, Any]:
    return normalize_registry_snapshot({"skills": [], "source_ref": "universe://skill-registry/empty"})


def resolve_skill(decision: Mapping[str, Any], snapshot: Mapping[str, Any], request: Any) -> dict[str, Any]:
    options = _mapping(request, "skill_resolution")
    _exact_fields(options, set(), {"explicit_skill_id", "project_id", "node_ref", "available_fallback_handlers"}, "SKILL_REGISTRY_UNAVAILABLE")
    explicit = options.get("explicit_skill_id")
    project_id = options.get("project_id") or decision.get("project_id")
    node_ref = options.get("node_ref") or decision.get("node_ref")
    candidates = []
    for skill in snapshot.get("skills", []):
        if decision["required_capability"] not in skill["capabilities"] or decision["effect_class"] not in skill["effects"] or decision["intent_class"] not in skill["intents"]:
            continue
        if explicit is not None:
            if skill["skill_id"] != explicit:
                continue
            precedence = 0
            selection_scope = "EXPLICIT"
        elif skill["scope"] == "NODE" and project_id == skill.get("project_id") and node_ref == skill.get("node_ref"):
            precedence, selection_scope = 1, "NODE"
        elif skill["scope"] == "PROJECT" and project_id == skill.get("project_id"):
            precedence, selection_scope = 2, "PROJECT"
        elif skill["scope"] == "UNIVERSE":
            precedence, selection_scope = 3, "UNIVERSE"
        elif skill["scope"] == "COMMON":
            precedence, selection_scope = 4, "COMMON"
        else:
            continue
        candidates.append((precedence, -skill["priority"], skill, selection_scope))
    if explicit is not None and not candidates:
        raise IntentRoutingError("SKILL_EFFECT_MISMATCH", "explicit Skill is unavailable or incompatible")
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2]["skill_id"], item[2]["version"]))
        best = candidates[0]
        tied = [item for item in candidates if item[:2] == best[:2]]
        if len(tied) > 1:
            raise IntentRoutingError("SKILL_RESOLUTION_AMBIGUOUS", "multiple installed Skills have equal precedence and priority")
        selected = best[2]
        handler_kind, skill_id, version, scope, fallback, output_contract = "SKILL", selected["skill_id"], selected["version"], best[3], False, selected["output_contract"]
    else:
        fallback_spec = FALLBACK_HANDLERS.get((decision["required_capability"], decision["effect_class"]))
        if fallback_spec is None:
            raise IntentRoutingError("CAPABILITY_UNAVAILABLE", "no safe fallback is registered for the Capability and effect")
        handler, output_contract, host_required = fallback_spec
        available = options.get("available_fallback_handlers") or []
        if host_required and handler not in available:
            raise IntentRoutingError("FALLBACK_HANDLER_UNAVAILABLE", "required effect adapter is unavailable")
        handler_kind, skill_id, version, scope, fallback = "FALLBACK", None, None, "GENERIC", True
    material = {
        "schema": SKILL_RESOLUTION_SCHEMA,
        "intent_decision_id": decision["decision_id"],
        "required_capability": decision["required_capability"],
        "registry_digest": snapshot["registry_digest"],
        "selected_handler_kind": handler_kind,
        "selected_skill_id": skill_id,
        "selected_skill_version": version,
        "selection_scope": scope,
        "fallback_handler": handler if fallback else None,
        "fallback_used": fallback,
        "effect_class": decision["effect_class"],
        "output_contract": output_contract,
        "effects": {"authority": "NONE", "execution_assignment": "NONE", "mutation_permission": "NONE"},
    }
    material["resolution_id"] = "resolution_" + digest(material)[:24]
    return material


def execute_plan_fallback(resolution: Mapping[str, Any], value: Any) -> dict[str, Any]:
    if resolution.get("fallback_handler") != "GENERIC_STRUCTURED_REASONING" or resolution.get("required_capability") != "PLAN_CREATE":
        raise IntentRoutingError("CAPABILITY_UNAVAILABLE", "resolution is not the non-mutating planning fallback")
    request = _mapping(value, "plan_request")
    _exact_fields(request, {"goal"}, {"constraints", "success_criteria"}, "INTENT_EVIDENCE_INVALID")
    goal = _text(request["goal"], "goal", maximum=1000)
    constraints = request.get("constraints") or []
    success = request.get("success_criteria") or []
    if not isinstance(constraints, list) or not isinstance(success, list) or len(constraints) > 20 or len(success) > 20:
        raise IntentRoutingError("INTENT_EVIDENCE_INVALID", "plan lists must be bounded")
    plan = {
        "schema": STRUCTURED_PLAN_SCHEMA,
        "resolution_id": resolution["resolution_id"],
        "goal": goal,
        "constraints": [_text(item, "constraint", maximum=500) for item in constraints],
        "success_criteria": [_text(item, "success_criterion", maximum=500) for item in success],
        "steps": [
            {"order": 1, "kind": "SCOPE", "title": "Confirm scope and evidence", "effect_class": "NONE"},
            {"order": 2, "kind": "SEQUENCE", "title": "Break work into bounded dependencies", "effect_class": "NONE"},
            {"order": 3, "kind": "VALIDATE", "title": "Define verification checkpoints", "effect_class": "NONE"},
        ],
        "effects": {"authority": "NONE", "execution_assignment": "NONE", "mutation_permission": "NONE"},
    }
    plan["plan_digest"] = digest(plan)
    return plan


def normalize_gap_observation(project_id: str, value: Any) -> dict[str, Any]:
    request = _mapping(value, "skill_gap_observation")
    required = {"observation_id", "intent_decision_id", "resolution_id", "intent_class", "capability", "effect_class", "fallback_handler", "output_contract", "outcome", "validation_state", "user_revision_state", "context_fingerprint", "observed_at"}
    optional = {"node_ref", "severity"}
    _exact_fields(request, required, optional, "SKILL_GAP_OBSERVATION_INVALID")
    forbidden = {"prompt", "source", "command", "credential", "transcript", "response", "reasoning"}
    if forbidden.intersection({key.casefold() for key in request}):
        raise IntentRoutingError("SKILL_GAP_OBSERVATION_INVALID", "raw or secret-bearing fields are forbidden")
    material = {
        "schema": SKILL_GAP_SCHEMA,
        "observation_id": _identifier(request["observation_id"], "observation_id"),
        "project_id": _identifier(project_id, "project_id"),
        "node_ref": request.get("node_ref"),
        "intent_decision_id": _identifier(request["intent_decision_id"], "intent_decision_id"),
        "resolution_id": _identifier(request["resolution_id"], "resolution_id"),
        "intent_class": _identifier(request["intent_class"], "intent_class").upper(),
        "capability": _identifier(request["capability"], "capability").upper(),
        "effect_class": _identifier(request["effect_class"], "effect_class").upper(),
        "fallback_handler": _identifier(request["fallback_handler"], "fallback_handler"),
        "output_contract": _identifier(request["output_contract"], "output_contract"),
        "outcome": _identifier(request["outcome"], "outcome").upper(),
        "validation_state": _identifier(request["validation_state"], "validation_state").upper(),
        "user_revision_state": _identifier(request["user_revision_state"], "user_revision_state").upper(),
        "context_fingerprint": _text(request["context_fingerprint"], "context_fingerprint", maximum=64).lower(),
        "severity": _identifier(request.get("severity") or "NONE", "severity").upper(),
        "observed_at": _timestamp(request["observed_at"], "observed_at").isoformat().replace("+00:00", "Z"),
    }
    if len(material["context_fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in material["context_fingerprint"]):
        raise IntentRoutingError("SKILL_GAP_OBSERVATION_INVALID", "context_fingerprint must be SHA-256")
    material["observation_digest"] = digest(material)
    return material


def build_skill_candidate(project_id: str, observations: Sequence[Mapping[str, Any]], value: Any) -> dict[str, Any]:
    request = _mapping(value, "skill_candidate")
    _exact_fields(request, {"capability", "output_contract", "threshold_policy"}, {"node_ref"}, "SKILL_CANDIDATE_SUPPORT_INSUFFICIENT")
    policy = _mapping(request["threshold_policy"], "threshold_policy")
    _exact_fields(policy, {"version", "min_distinct_contexts", "min_validated_successes", "max_high_severity_failures"}, set(), "SKILL_CANDIDATE_SUPPORT_INSUFFICIENT")
    capability = _identifier(request["capability"], "capability").upper()
    output_contract = _identifier(request["output_contract"], "output_contract")
    support = [item for item in observations if item["capability"] == capability and item["output_contract"] == output_contract]
    contexts = {item["context_fingerprint"] for item in support}
    validated = sum(item["outcome"] == "SUCCESS" and item["validation_state"] == "VALIDATED" for item in support)
    high_failures = sum(item["outcome"] == "FAILED" and item.get("severity") == "HIGH" for item in support)
    eligible = len(contexts) >= int(policy["min_distinct_contexts"]) and validated >= int(policy["min_validated_successes"]) and high_failures <= int(policy["max_high_severity_failures"])
    material = {
        "schema": SKILL_CANDIDATE_SCHEMA,
        "project_id": project_id,
        "node_ref": request.get("node_ref"),
        "capability": capability,
        "output_contract": output_contract,
        "candidate_state": "ELIGIBLE" if eligible else "OBSERVED",
        "threshold_policy": dict(policy),
        "supporting_observation_ids": sorted(item["observation_id"] for item in support),
        "evidence": {"observation_count": len(support), "distinct_context_count": len(contexts), "validated_success_count": validated, "high_severity_failure_count": high_failures},
        "installation_state": "NOT_INSTALLED",
        "effects": {"authority": "NONE", "execution_assignment": "NONE", "registry_write": "NONE"},
    }
    material["candidate_digest"] = digest(material)
    material["candidate_id"] = "skill_candidate_" + material["candidate_digest"][:24]
    return material


__all__ = [name for name in globals() if name.isupper()] + [
    "IntentRoutingError", "build_adopted_registry_snapshot", "build_skill_candidate", "empty_registry_snapshot", "execute_plan_fallback", "normalize_gap_observation", "normalize_intent_decision", "normalize_planning_phrase", "normalize_registry_snapshot", "normalize_skill_pack_manifest", "resolve_skill"
]
