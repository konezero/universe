"""Deterministic Session Boot artifact assembly.

The caller supplies project-runtime status and installation-manifest evidence.
This module validates their structural agreement and creates a fresh,
process-local Runtime Anchor snapshot. It does not read source files, assign
authority, restore a prior session, or write repository state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

if __package__:
    from .mode_registry_runtime import (
        ModeRegistry,
        mode_definition_digest,
        mode_registry_digest,
        verify_mode_request,
    )
else:
    from mode_registry_runtime import (
        ModeRegistry,
        mode_definition_digest,
        mode_registry_digest,
        verify_mode_request,
    )


BOOT_EVIDENCE_SCHEMA = "ai-career.session-boot-evidence.v1"
BOOT_SNAPSHOT_SCHEMA = "ai-career.session-boot-anchor-snapshot.v1"
BOOT_RESULT_SCHEMA = "ai-career.session-boot-result.v1"
SESSION_PREPARATION_SCHEMA = "ai-career.session-preparation-result.v1"
RUNTIME_IMAGE_PROFILE = "sqlite_memory"
ANCHOR_INPUT_KINDS = frozenset({"NONE", "RUNTIME_SNAPSHOT", "BEYOND_ANCHOR"})
SESSION_PREPARATION_COMMANDS = frozenset({"BOOT", "REBOOT"})
GOVERNANCE_SOURCE_STATES = frozenset({"SOURCE_READY", "PARTIAL", "UNKNOWN"})
AI_CAREER_REPOSITORY = "konezero/ai-career"
HOST_EXECUTABLE_CAPABILITIES = frozenset({"AVAILABLE", "UNAVAILABLE", "UNKNOWN"})
MODE_PROFILES = frozenset({"GOVERNANCE_ONLY", "EXECUTABLE_PROOF_REQUIRED"})
TASK_REQUIREMENTS = frozenset({"NONE", "EXECUTABLE_PROOF_REQUIRED"})
EVIDENCE_PROFILES = frozenset({"NONE", "EXECUTABLE_PROOF_REQUIRED"})
EXECUTION_INTENTS = frozenset({"NONE", "IMPLEMENTATION"})

INSTALLATION_MANIFEST_REF = ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
RUNTIME_STATE_REF = ".ai/runtime/state/session.md"
RUNTIME_FRAME_REF = ".ai/runtime/state/current_anchor_frame.md"
ANCHOR_REF = ".ai/runtime/project_instance/project_anchor.md"
VALIDATION_REF = ".ai/runtime/project_instance/validation/latest.md"
NODE_MODE_REF = ".ai/core/NODE_MODE_COORDINATE_CONTRACT.md"
CURRENTNESS_REF = ".ai/core/SESSION_CURRENTNESS.md"
ANCHOR_TEMPORAL_REF = ".ai/core/ANCHOR_TEMPORAL_COORDINATE.md"


@dataclass(frozen=True)
class SessionBootError(Exception):
    """A source-backed Session Boot failure."""

    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class SessionPreparationRequest:
    """Host-provided governance alignment input; no Runtime identity is created."""

    command: str
    source_state: str
    source_ref: str
    source_commit: str
    mode: str
    role: str
    scope: str
    source_repository: str = "UNKNOWN"
    host_session_ref: str = "UNKNOWN"
    anchor_snapshot_ref: str = "UNKNOWN"
    host_executable_capability: str = "UNKNOWN"
    mode_profile: str = "GOVERNANCE_ONLY"
    task_requirement: str = "NONE"
    evidence_profile: str = "NONE"
    execution_intent: str = "NONE"

    def normalized(self) -> "SessionPreparationRequest":
        command = _required_text(self.command, "session_preparation.command").upper()
        if command not in SESSION_PREPARATION_COMMANDS:
            raise SessionBootError(
                "SESSION_PREPARATION_COMMAND_INVALID",
                f"unsupported governance boot command: {command}",
            )
        source_state = _required_text(
            self.source_state, "session_preparation.source_state"
        ).upper()
        if source_state not in GOVERNANCE_SOURCE_STATES:
            raise SessionBootError(
                "SESSION_PREPARATION_SOURCE_STATE_INVALID",
                f"unsupported source state: {source_state}",
            )
        host_capability = _required_text(
            self.host_executable_capability,
            "session_preparation.host_executable_capability",
        ).upper()
        if host_capability not in HOST_EXECUTABLE_CAPABILITIES:
            raise SessionBootError(
                "SESSION_PREPARATION_HOST_CAPABILITY_INVALID",
                f"unsupported Host executable capability: {host_capability}",
            )
        mode_profile = _required_text(
            self.mode_profile, "session_preparation.mode_profile"
        ).upper()
        if mode_profile not in MODE_PROFILES:
            raise SessionBootError(
                "SESSION_PREPARATION_MODE_PROFILE_INVALID",
                f"unsupported Mode profile: {mode_profile}",
            )
        task_requirement = _required_text(
            self.task_requirement, "session_preparation.task_requirement"
        ).upper()
        if task_requirement not in TASK_REQUIREMENTS:
            raise SessionBootError(
                "SESSION_PREPARATION_TASK_REQUIREMENT_INVALID",
                f"unsupported Task requirement: {task_requirement}",
            )
        evidence_profile = _required_text(
            self.evidence_profile, "session_preparation.evidence_profile"
        ).upper()
        if evidence_profile not in EVIDENCE_PROFILES:
            raise SessionBootError(
                "SESSION_PREPARATION_EVIDENCE_PROFILE_INVALID",
                f"unsupported Evidence profile: {evidence_profile}",
            )
        execution_intent = _required_text(
            self.execution_intent, "session_preparation.execution_intent"
        ).upper()
        if execution_intent not in EXECUTION_INTENTS:
            raise SessionBootError(
                "SESSION_PREPARATION_EXECUTION_INTENT_INVALID",
                f"unsupported execution intent: {execution_intent}",
            )
        return SessionPreparationRequest(
            command=command,
            source_state=source_state,
            source_ref=_required_text(self.source_ref, "session_preparation.source_ref"),
            source_commit=_required_text(
                self.source_commit, "session_preparation.source_commit"
            ),
            mode=_required_text(self.mode, "session_preparation.mode"),
            role=_required_text(self.role, "session_preparation.role"),
            scope=_required_text(self.scope, "session_preparation.scope"),
            source_repository=_required_text(
                self.source_repository, "session_preparation.source_repository"
            ),
            host_session_ref=_required_text(
                self.host_session_ref, "session_preparation.host_session_ref"
            ),
            anchor_snapshot_ref=_required_text(
                self.anchor_snapshot_ref,
                "session_preparation.anchor_snapshot_ref",
            ),
            host_executable_capability=host_capability,
            mode_profile=mode_profile,
            task_requirement=task_requirement,
            evidence_profile=evidence_profile,
            execution_intent=execution_intent,
        )


def evaluate_session_preparation(
    request: SessionPreparationRequest,
    *,
    mode_registry: ModeRegistry | None = None,
) -> dict[str, Any]:
    """Evaluate BOOT/REBOOT alignment without starting the local Runtime."""

    normalized = request.normalized()
    executable_runtime = _unknown_executable_runtime(normalized)
    base = {
        "schema": SESSION_PREPARATION_SCHEMA,
        "command": normalized.command,
        "repository_write": False,
        "execution_assignment": "UNASSIGNED",
        "session_preparation_state": "UNKNOWN",
        "governance_session": {
            "host_session_ref": normalized.host_session_ref,
            "anchor_snapshot_ref": normalized.anchor_snapshot_ref,
            "source_repository": normalized.source_repository,
            "source_ref": normalized.source_ref,
            "source_commit": normalized.source_commit,
        },
        "governance_authority": _unknown_governance_authority(),
        "anchor_snapshot": _anchor_snapshot_reference(normalized, rehydrated=False),
        "mode": {
            "requested": normalized.mode,
            "role": normalized.role,
            "scope": normalized.scope,
            "mode_context_active": False,
        },
        "executable_runtime": executable_runtime,
    }
    if normalized.source_state != "SOURCE_READY":
        return {
            **base,
            "status": "GOVERNANCE_SOURCE_NOT_READY",
            "source_state": normalized.source_state,
        }
    if normalized.mode == "UNKNOWN":
        return {
            **base,
            "status": "MODE_SELECTION_REQUIRED",
            "source_state": normalized.source_state,
        }
    if mode_registry is None:
        raise SessionBootError(
            "MODE_REGISTRY_UNAVAILABLE",
            "a selected Mode requires source-backed Mode Registry evidence",
        )
    definition = verify_mode_request(
        mode_registry,
        mode=normalized.mode,
        role=normalized.role,
        scope=normalized.scope,
        mode_profile=normalized.mode_profile,
    )
    base["mode_registry"] = {
        "status": "MODE_REGISTRY_ENTRY_RESOLVED",
        "owner": mode_registry.owner,
        "revision": mode_registry.revision,
        "mode": definition.mode,
        "definition_digest": mode_definition_digest(definition),
        "registry_digest": mode_registry_digest(mode_registry),
        "evidence_ref": str(mode_registry.path),
    }
    if "UNKNOWN" in {normalized.role, normalized.scope}:
        return {
            **base,
            "status": "MODE_PROFILE_REQUIRED",
            "source_state": normalized.source_state,
        }

    status = (
        "GOVERNANCE_REALIGNED"
        if normalized.command == "REBOOT"
        else "SESSION_PREPARED"
    )
    return {
        **base,
        "status": status,
        "source_state": normalized.source_state,
        "session_preparation_state": "PREPARED",
        "mode": {
            **base["mode"],
            "mode_context_active": True,
        },
        "governance_authority": _governance_authority(normalized),
        "anchor_snapshot": _anchor_snapshot_reference(normalized, rehydrated=False),
    }


def _anchor_snapshot_reference(
    request: SessionPreparationRequest, *, rehydrated: bool
) -> dict[str, str]:
    observed = request.anchor_snapshot_ref != "UNKNOWN"
    return {
        "status": "OBSERVED_REFERENCE" if observed else "NOT_OBSERVED",
        "evidence_ref": request.anchor_snapshot_ref,
        "governance_rehydration": (
            "CONTEXT_REHYDRATED" if observed and rehydrated else "NOT_PERFORMED"
        ),
        "executable_runtime_currentness": "UNKNOWN",
    }


def _unknown_executable_runtime(request: SessionPreparationRequest) -> dict[str, Any]:
    required_profiles = {
        request.mode_profile,
        request.task_requirement,
        request.evidence_profile,
    }
    task_evidence_requires_execution = (
        request.task_requirement == "EXECUTABLE_PROOF_REQUIRED"
        and request.evidence_profile == "EXECUTABLE_PROOF_REQUIRED"
    )
    implementation_intent_requires_proposal = (
        request.execution_intent == "IMPLEMENTATION"
        and not task_evidence_requires_execution
    )
    if task_evidence_requires_execution or implementation_intent_requires_proposal:
        decision = {
            "AVAILABLE": "EXECUTION_HOST_START_REQUIRED",
            "UNAVAILABLE": "EXECUTION_HOST_UNAVAILABLE",
            "UNKNOWN": "EXECUTION_HOST_BINDING_REQUIRED",
        }[request.host_executable_capability]
    elif "EXECUTABLE_PROOF_REQUIRED" in required_profiles:
        decision = "EXECUTION_HOST_PROFILE_INCOMPLETE"
    else:
        decision = "EXECUTION_HOST_START_NOT_REQUIRED"
    return {
        "executor_active": False,
        "runtime_instance_id": "UNKNOWN",
        "runtime_session_id": "UNKNOWN",
        "runtime_frame_id": "UNKNOWN",
        "endpoint": "UNKNOWN",
        "runtime_currentness": "UNKNOWN",
        "host_executable_capability": request.host_executable_capability,
        "start_decision": decision,
        "next_operation": (
            "EXECUTABLE_RUNTIME_START_PROPOSAL_REQUIRED"
            if (
                implementation_intent_requires_proposal
                and decision == "EXECUTION_HOST_START_REQUIRED"
            )
            else "EXECUTABLE_RUNTIME_START"
            if decision == "EXECUTION_HOST_START_REQUIRED"
            else "NONE"
        ),
        "execution_transition": (
            "PROPOSAL_REQUIRED"
            if implementation_intent_requires_proposal
            else "PROFILE_BOUND"
            if task_evidence_requires_execution
            else "NONE"
        ),
    }


def _unknown_governance_authority() -> dict[str, str]:
    return {
        "role_authority_scope": "UNKNOWN",
        "governance_read": "UNKNOWN",
        "repository_write": "UNKNOWN",
        "active_delegation": "UNASSIGNED",
    }


def _governance_authority(request: SessionPreparationRequest) -> dict[str, str]:
    if (
        request.role == "CONDUCTOR"
        and request.source_repository == AI_CAREER_REPOSITORY
    ):
        return {
            "role_authority_scope": "AI_CAREER_REPOSITORY_ROOT",
            "governance_read": "ALLOWED",
            "repository_write": "USER_APPROVAL_REQUIRED",
            "active_delegation": "UNASSIGNED",
        }
    return _unknown_governance_authority()


@dataclass(frozen=True)
class SessionBootCoordinates:
    """Host-supplied session coordinates; none of these fields grant authority."""

    session_id: str
    frame_id: str
    anchor_id: str
    host_action: str
    session_location: str
    commander_surface: str
    execution_surface: str
    repository_location: str

    def normalized(self) -> "SessionBootCoordinates":
        values = {
            field: _required_text(getattr(self, field), f"coordinates.{field}")
            for field in self.__dataclass_fields__
        }
        return SessionBootCoordinates(**values)


@dataclass(frozen=True)
class SessionBootAnchorInput:
    """Optional heritage evidence; never a Current Anchor or Authority source."""

    kind: str = "NONE"
    anchor_id: str = "UNKNOWN"
    evidence_ref: str = "UNKNOWN"
    source_commit: str = "UNKNOWN"

    def normalized(self) -> "SessionBootAnchorInput":
        kind = _required_text(self.kind, "anchor_input.kind").upper()
        if kind not in ANCHOR_INPUT_KINDS:
            raise SessionBootError(
                "SESSION_BOOT_ANCHOR_INPUT_INVALID",
                f"unsupported anchor input kind: {kind}",
            )
        if kind == "NONE":
            return SessionBootAnchorInput()
        values = {
            field: _required_text(getattr(self, field), f"anchor_input.{field}")
            for field in ("anchor_id", "evidence_ref", "source_commit")
        }
        if "UNKNOWN" in values.values():
            raise SessionBootError(
                "SESSION_BOOT_ANCHOR_INPUT_INVALID",
                "heritage anchor input coordinates cannot be UNKNOWN",
            )
        return SessionBootAnchorInput(kind=kind, **values)


def build_session_boot_artifacts(
    *,
    project_status: Mapping[str, Any],
    installation_manifest: Mapping[str, Any],
    coordinates: SessionBootCoordinates,
    observed_at: str,
    anchor_input: SessionBootAnchorInput | None = None,
) -> dict[str, Any]:
    """Build one fresh Session Boot evidence bundle and Anchor snapshot.

    ``CURRENT`` is created only for the new in-process ``session_id + frame_id``
    key. It is not inferred from the durable project's previous session state.
    Authority and execution assignment are copied unchanged from validated
    project-runtime status evidence.
    """

    normalized_coordinates = coordinates.normalized()
    normalized_anchor_input = (anchor_input or SessionBootAnchorInput()).normalized()
    if (
        normalized_anchor_input.kind != "NONE"
        and normalized_anchor_input.anchor_id == normalized_coordinates.anchor_id
    ):
        raise SessionBootError(
            "SESSION_BOOT_ANCHOR_REACTIVATION_FORBIDDEN",
            "a historical Anchor cannot be reused as the Session Boot Anchor",
        )
    normalized_observed_at = _timestamp(observed_at)
    status = _validated_project_status(project_status)
    manifest = _validated_installation_manifest(installation_manifest, status)

    managed_paths = _managed_paths(manifest)
    required_refs = {
        RUNTIME_STATE_REF,
        RUNTIME_FRAME_REF,
        ANCHOR_REF,
        VALIDATION_REF,
        NODE_MODE_REF,
        CURRENTNESS_REF,
        ANCHOR_TEMPORAL_REF,
    }
    available_refs = {row["target_path"] for row in managed_paths}
    missing_refs = sorted(required_refs.difference(available_refs))
    if missing_refs:
        raise SessionBootError(
            "SESSION_BOOT_SURFACE_MISSING",
            f"required boot surface is not managed: {missing_refs[0]}",
        )

    source = _mapping(manifest.get("source"), "installation_manifest.source")
    validation = _mapping(
        status.get("live_validation"), "project_status.live_validation"
    )
    source_ref = f"{status['source_repository']}@{status['source_commit']}"
    currentness_key = (
        f"{normalized_coordinates.session_id}+{normalized_coordinates.frame_id}"
    )

    core_rows = _surface_rows(managed_paths, "core_runtime")
    template_rows = _surface_rows(managed_paths, "contract_template")
    if not core_rows:
        raise SessionBootError(
            "SESSION_BOOT_CORE_EVIDENCE_MISSING",
            "installation manifest contains no managed Core surfaces",
        )

    current_interpretation_basis = {
        "source_repo": status["source_repository"],
        "source_commit": status["source_commit"],
        "validation_ref": VALIDATION_REF,
        "validation_id": validation.get("validation_id", "UNKNOWN"),
        "runtime_state_ref": RUNTIME_STATE_REF,
        "runtime_frame_ref": RUNTIME_FRAME_REF,
        "anchor_ref": ANCHOR_REF,
        "session_coordinates": _coordinate_payload(normalized_coordinates),
        "observed_at": normalized_observed_at,
        "authority": status.get("authority", "UNKNOWN"),
        "execution_assignment": status.get("execution_assignment", "UNKNOWN"),
        "authority_created": False,
    }

    boot_evidence = {
        "schema": BOOT_EVIDENCE_SCHEMA,
        "source_repo": status["source_repository"],
        "source_ref": source.get("requested_ref", "UNKNOWN"),
        "source_commit": status["source_commit"],
        "source_digest": _source_digest(source),
        "source_provider": status.get("source_provider", "UNKNOWN"),
        "source_binding": status.get("source_binding", "UNKNOWN"),
        "surface_hashes": _surface_hashes(core_rows + template_rows),
        "core_surface_refs": [row["target_path"] for row in core_rows],
        "contract_template_refs": [row["target_path"] for row in template_rows],
        "runtime_state_ref": RUNTIME_STATE_REF,
        "runtime_frame_ref": RUNTIME_FRAME_REF,
        "anchor_ref": ANCHOR_REF,
        "validation_ref": VALIDATION_REF,
        "node_mode_ref": NODE_MODE_REF,
        "currentness_ref": CURRENTNESS_REF,
        "anchor_temporal_ref": ANCHOR_TEMPORAL_REF,
        "installation_manifest_ref": INSTALLATION_MANIFEST_REF,
        "repository_runtime": status["repository_runtime"],
        "validation_result": status["result"],
        "validation_id": validation.get("validation_id", "UNKNOWN"),
        "source_session_state": {
            "session_runtime": status.get("session_runtime", "UNKNOWN"),
            "session_initialization": status.get(
                "session_initialization", "UNKNOWN"
            ),
            "session_preparation_state": status.get(
                "session_preparation_state", "UNKNOWN"
            ),
            "executable_runtime_currentness": status.get(
                "executable_runtime_currentness", "UNKNOWN"
            ),
        },
        "heritage_anchor_input": _anchor_input_payload(normalized_anchor_input),
        "current_interpretation_basis": current_interpretation_basis,
        "session_coordinates": _coordinate_payload(normalized_coordinates),
        "checked_at": normalized_observed_at,
    }

    snapshot = {
        "schema": BOOT_SNAPSHOT_SCHEMA,
        "session_id": normalized_coordinates.session_id,
        "frame_id": normalized_coordinates.frame_id,
        "anchor_id": normalized_coordinates.anchor_id,
        "state": "READY",
        "state_origin": "current_session",
        "state_freshness": "current",
        "entered_at": normalized_observed_at,
        "observed_at": normalized_observed_at,
        "state_updated_at": normalized_observed_at,
        "validated_at": normalized_observed_at,
        "coordinates": {
            "node": status["node"],
            "mode": status["mode"],
            "role": status["role"],
            "session_location": normalized_coordinates.session_location,
            "commander_surface": normalized_coordinates.commander_surface,
            "execution_surface": normalized_coordinates.execution_surface,
            "repository_location": normalized_coordinates.repository_location,
        },
        "executable_runtime_currentness": {
            "status": "CURRENT",
            "key": currentness_key,
            "origin": "SESSION_BOOT_FRAME_CREATED",
            "evidence_ref": CURRENTNESS_REF,
        },
        "runtime": {
            "session_runtime": "READY",
            "session_initialization": "INITIALIZED",
            "repository_runtime": status["repository_runtime"],
            "runtime_image_status": "assembled",
            "runtime_image_profile": RUNTIME_IMAGE_PROFILE,
            "runtime_image_authority": False,
            "host_action": normalized_coordinates.host_action,
        },
        "authority": status.get("authority", "UNKNOWN"),
        "authority_ref": status.get("authority_ref", "UNKNOWN"),
        "execution_assignment": status.get("execution_assignment", "UNKNOWN"),
        "assignment_ref": status.get("assignment_ref", "UNKNOWN"),
        "source_ref": source_ref,
        "source_commit": status["source_commit"],
        "evidence_refs": {
            "boot_evidence_schema": BOOT_EVIDENCE_SCHEMA,
            "installation_manifest": INSTALLATION_MANIFEST_REF,
            "runtime_state": RUNTIME_STATE_REF,
            "runtime_frame": RUNTIME_FRAME_REF,
            "project_anchor": ANCHOR_REF,
            "validation": VALIDATION_REF,
            "anchor_temporal_coordinate": ANCHOR_TEMPORAL_REF,
        },
    }

    runtime_state = {
        "session_id": normalized_coordinates.session_id,
        "frame_id": normalized_coordinates.frame_id,
        "anchor_id": normalized_coordinates.anchor_id,
        "node": status["node"],
        "mode": status["mode"],
        "role": status["role"],
        "session_runtime": "READY",
        "session_initialization": "INITIALIZED",
        "repository_runtime": status["repository_runtime"],
        "session_preparation_state": "PREPARED",
        "executable_runtime_currentness": "CURRENT",
        "currentness_key": currentness_key,
        "authority": status.get("authority", "UNKNOWN"),
        "execution_assignment": status.get("execution_assignment", "UNKNOWN"),
        "state_origin": "current_session",
        "state_freshness": "current",
        "entered_at": normalized_observed_at,
        "observed_at": normalized_observed_at,
        "state_updated_at": normalized_observed_at,
        "validated_at": normalized_observed_at,
        "runtime_image": {
            "status": "assembled",
            "activation_status": "active",
            "assembly_profile": RUNTIME_IMAGE_PROFILE,
            "session_artifact_ref": (
                "process-local://anchor-session-memory/"
                f"{normalized_coordinates.session_id}"
            ),
            "authority": False,
        },
    }

    return {
        "schema": BOOT_RESULT_SCHEMA,
        "boot_evidence_bundle": boot_evidence,
        "snapshot": snapshot,
        "runtime_state": runtime_state,
        "anchor_derivation": {
            "output": "SESSION_BOOT_ANCHOR_SNAPSHOT",
            "anchor_id": normalized_coordinates.anchor_id,
            "current_interpretation_basis": current_interpretation_basis,
            "heritage_anchor_input": _anchor_input_payload(
                normalized_anchor_input
            ),
            "historical_anchor_reactivated": False,
            "resume_required": False,
            "archive_required": False,
            "authority_created": False,
            "assignment_created": False,
        },
        "activation_payload": {
            "session_id": normalized_coordinates.session_id,
            "anchor_mode": status["mode"],
            "source_ref": source_ref,
            "snapshot": snapshot,
        },
    }


def _validated_project_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    status = dict(payload)
    for field in ("project", "node", "mode", "role", "source_repository"):
        status[field] = _required_text(status.get(field), f"project_status.{field}")
    source_commit = _required_text(
        status.get("source_commit"), "project_status.source_commit"
    )
    if source_commit == "UNKNOWN":
        raise SessionBootError(
            "SESSION_BOOT_SOURCE_UNKNOWN",
            "project runtime source commit is UNKNOWN",
        )
    status["source_commit"] = source_commit
    if status.get("result") != "PASS" or status.get("repository_runtime") != "VERIFIED":
        raise SessionBootError(
            "PROJECT_RUNTIME_NOT_VERIFIED",
            "project runtime status must report result PASS and repository_runtime VERIFIED",
        )
    for field in ("authority", "execution_assignment"):
        value = status.get(field, "UNKNOWN")
        status[field] = _required_text(value, f"project_status.{field}")
    return status


def _validated_installation_manifest(
    payload: Mapping[str, Any], status: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = dict(payload)
    source = _mapping(manifest.get("source"), "installation_manifest.source")
    installation = _mapping(
        manifest.get("installation"), "installation_manifest.installation"
    )
    expected = {
        "source.repository": (source.get("repository"), status["source_repository"]),
        "source.commit": (source.get("commit"), status["source_commit"]),
        "installation.project": (installation.get("project"), status["project"]),
        "installation.node": (installation.get("node"), status["node"]),
        "installation.mode": (installation.get("mode"), status["mode"]),
        "installation.role": (installation.get("role"), status["role"]),
    }
    for coordinate, (actual, wanted) in expected.items():
        if actual != wanted:
            raise SessionBootError(
                "SESSION_BOOT_SOURCE_BINDING_MISMATCH",
                f"{coordinate} does not match validated project status",
            )
    return manifest


def _managed_paths(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = manifest.get("managed_paths")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SessionBootError(
            "SESSION_BOOT_MANIFEST_INVALID",
            "installation_manifest.managed_paths must be an array",
        )
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        row = dict(_mapping(item, f"installation_manifest.managed_paths[{index}]"))
        row["target_path"] = _required_text(
            row.get("target_path"),
            f"installation_manifest.managed_paths[{index}].target_path",
        )
        row["class"] = _required_text(
            row.get("class"), f"installation_manifest.managed_paths[{index}].class"
        )
        rows.append(row)
    return rows


def _surface_rows(
    managed_paths: Sequence[Mapping[str, Any]], class_name: str
) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in managed_paths if row.get("class") == class_name),
        key=lambda row: row["target_path"],
    )


def _surface_hashes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        sha256 = row.get("local_sha256")
        if not isinstance(sha256, str) or not sha256:
            raise SessionBootError(
                "SESSION_BOOT_SURFACE_HASH_MISSING",
                f"managed surface has no local SHA-256: {row['target_path']}",
            )
        result.append({"path": str(row["target_path"]), "sha256": sha256})
    return result


def _source_digest(source: Mapping[str, Any]) -> str:
    for value in (
        source.get("bundle_manifest_sha256"),
        _mapping(source.get("source_index", {}), "source.source_index").get(
            "source_sha256"
        ),
    ):
        if isinstance(value, str) and value:
            return value
    return "UNKNOWN"


def _coordinate_payload(coordinates: SessionBootCoordinates) -> dict[str, str]:
    return {
        field: getattr(coordinates, field) for field in coordinates.__dataclass_fields__
    }


def _anchor_input_payload(anchor_input: SessionBootAnchorInput) -> dict[str, str]:
    return {
        field: getattr(anchor_input, field)
        for field in anchor_input.__dataclass_fields__
    }


def evaluate_runtime_image_currentness(
    *, assembled_snapshot: Mapping[str, Any], current_snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Distinguish physical observation touches from semantic image changes."""

    assembled = dict(_mapping(assembled_snapshot, "assembled_snapshot"))
    current = dict(_mapping(current_snapshot, "current_snapshot"))
    semantic_fields = (
        "session_id",
        "frame_id",
        "anchor_id",
        "state_updated_at",
        "source_commit",
    )
    changed = [
        field
        for field in semantic_fields
        if assembled.get(field) != current.get(field)
    ]
    assembled_refs = _mapping(
        assembled.get("evidence_refs"), "assembled_snapshot.evidence_refs"
    )
    current_refs = _mapping(
        current.get("evidence_refs"), "current_snapshot.evidence_refs"
    )
    if assembled_refs.get("validation") != current_refs.get("validation"):
        changed.append("evidence_refs.validation")
    assembled_coordinates = _mapping(
        assembled.get("coordinates"), "assembled_snapshot.coordinates"
    )
    current_coordinates = _mapping(
        current.get("coordinates"), "current_snapshot.coordinates"
    )
    for field in (
        "session_location",
        "execution_surface",
        "repository_location",
    ):
        if assembled_coordinates.get(field) != current_coordinates.get(field):
            changed.append(f"coordinates.{field}")
    observation_changed = assembled.get("observed_at") != current.get("observed_at")
    return {
        "status": "STALE" if changed else "CURRENT",
        "semantic_changes": changed,
        "observed_at_changed": observation_changed,
        "observed_at_only_invalidates_image": False,
        "authority_created": False,
    }


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SessionBootError(
            "SESSION_BOOT_EVIDENCE_INVALID", f"{context} must be an object"
        )
    return value


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SessionBootError(
            "SESSION_BOOT_EVIDENCE_INVALID",
            f"{context} must be a non-empty string",
        )
    return value.strip()


def _timestamp(value: str) -> str:
    normalized = _required_text(value, "observed_at").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SessionBootError(
            "SESSION_BOOT_EVIDENCE_INVALID", "observed_at must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise SessionBootError(
            "SESSION_BOOT_EVIDENCE_INVALID", "observed_at must include a timezone"
        )
    return parsed.isoformat()
