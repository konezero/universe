"""Deterministic process-local assignment proposal and execution binding.

The runtime never invents approval or canonical authority. It binds one
Host-attested approval to one exact proposal and the current Anchor snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROPOSAL_SCHEMA = "ai-career.execution-assignment-proposal.v1"
BINDING_SCHEMA = "ai-career.execution-binding-result.v1"
PLATFORM_APPROVAL_EVIDENCE_SCHEMA = "ai-career.platform-approval-evidence.v1"
INSTRUCTION_RECEIPT_SCHEMA = "ai-career.instruction-receipt.v1"
WORK_RECEIPT_SCHEMA = "ai-career.project-source-work-receipt.v1"
MUTATION_OPERATIONS = frozenset({"CREATE", "MODIFY", "DELETE", "MOVE"})
PROJECT_SOURCE_WORK_OPERATIONS = frozenset({"CREATE", "MODIFY"})
FRESH_PROJECT_DEFAULT_SOURCE_ROOT = "src"
RUNTIME_LAYOUT_ENTRIES = frozenset({".ai", ".git", "AGENTS.md", "REPOSITORY_MANIFEST.md"})
REPOSITORY_MANIFEST_NAME = "REPOSITORY_MANIFEST.md"
MANIFEST_APPLICATION_ROOT_PATH = ("layers", "application", "root")


@dataclass(frozen=True)
class ExecutionBindingError(Exception):
    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


def build_assignment_proposal(
    *, snapshot: Mapping[str, Any], request: Mapping[str, Any], observed_at: str
) -> dict[str, Any]:
    current = _current_snapshot(snapshot)
    operation = _required_text(request.get("operation"), "request.operation").upper()
    if operation not in MUTATION_OPERATIONS:
        raise ExecutionBindingError(
            "ASSIGNMENT_PROPOSAL_INVALID", f"unsupported operation: {operation}"
        )
    target = _absolute_path(request.get("target"), "request.target")
    boundary = _required_text(request.get("boundary"), "request.boundary")
    roots = _absolute_path_list(request.get("write_roots"), "request.write_roots")
    operations = _operation_list(
        request.get("write_operations"), "request.write_operations"
    )
    if operation not in operations:
        raise ExecutionBindingError(
            "ASSIGNMENT_PROPOSAL_INVALID",
            "request.operation must be present in request.write_operations",
        )
    if not any(_path_within(target, root) for root in roots):
        raise ExecutionBindingError(
            "ASSIGNMENT_PROPOSAL_INVALID",
            "request.target is outside request.write_roots",
        )
    task_summary = _required_text(request.get("task_summary"), "request.task_summary")
    request_ref = _required_text(request.get("request_ref"), "request.request_ref")
    proposed_at = _timestamp(observed_at)
    evidence_refs = _mapping(current.get("evidence_refs"), "snapshot.evidence_refs")
    coordinates = _surface_coordinates(current)
    material = {
        "session_id": _required_text(current.get("session_id"), "snapshot.session_id"),
        "frame_id": _required_text(current.get("frame_id"), "snapshot.frame_id"),
        "anchor_id": _required_text(current.get("anchor_id"), "snapshot.anchor_id"),
        "source_commit": _required_text(
            current.get("source_commit"), "snapshot.source_commit"
        ),
        "validation_ref": _required_text(
            evidence_refs.get("validation"), "snapshot.evidence_refs.validation"
        ),
        **coordinates,
        "operation": operation,
        "target": target,
        "boundary": boundary,
        "write_roots": roots,
        "write_operations": operations,
        "task_summary": task_summary,
        "request_ref": request_ref,
    }
    proposal_id = "assignment_" + _digest(material)[:24]
    return {
        "schema": PROPOSAL_SCHEMA,
        "status": "EXECUTION_ASSIGNMENT_PROPOSED",
        "assignment_state": "CANDIDATE",
        "proposal_id": proposal_id,
        **material,
        "proposed_at": proposed_at,
        "approval_required": True,
        "authority_created": False,
        "repository_write": False,
    }


def begin_project_source_work(
    *,
    snapshot: Mapping[str, Any],
    work: Mapping[str, Any],
    observed_at: str,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    """Bind a direct user instruction to bounded project-source work.

    This is deliberately narrower than a generic repository delegation.  It
    permits only CREATE/MODIFY inside declared project-source roots; every
    concrete write still obtains and consumes its own Guard receipt.
    """

    current = _current_snapshot(snapshot)
    normalized_work = _validated_project_source_work(
        work, repository_root=repository_root
    )
    evidence_refs = _mapping(current.get("evidence_refs"), "snapshot.evidence_refs")
    coordinates = _surface_coordinates(current)
    activated_at = _timestamp(observed_at)
    instruction_material = {
        "session_id": _required_text(current.get("session_id"), "snapshot.session_id"),
        "frame_id": _required_text(current.get("frame_id"), "snapshot.frame_id"),
        "anchor_id": _required_text(current.get("anchor_id"), "snapshot.anchor_id"),
        "source_commit": _required_text(
            current.get("source_commit"), "snapshot.source_commit"
        ),
        "validation_ref": _required_text(
            evidence_refs.get("validation"), "snapshot.evidence_refs.validation"
        ),
        **coordinates,
        **normalized_work,
    }
    instruction_receipt_id = "instruction_" + _digest(instruction_material)[:24]
    work_material = {
        "instruction_receipt_id": instruction_receipt_id,
        "activated_at": activated_at,
    }
    work_receipt_id = "work_" + _digest({**instruction_material, **work_material})[:24]
    binding_material = {
        "work_receipt_id": work_receipt_id,
        "instruction_receipt_id": instruction_receipt_id,
        **coordinates,
        "bound_at": activated_at,
    }
    binding_id = "binding_" + _digest(binding_material)[:24]
    authority_id = "authority_" + _digest(
        {"session_id": current["session_id"], **binding_material}
    )[:24]
    instruction_receipt = {
        "schema": INSTRUCTION_RECEIPT_SCHEMA,
        "instruction_receipt_id": instruction_receipt_id,
        "status": "ATTESTED",
        **instruction_material,
        "user_confirmation_required": False,
        "commit_policy": "EXPLICIT_APPROVAL_REQUIRED",
    }
    work_receipt = {
        "schema": WORK_RECEIPT_SCHEMA,
        "work_receipt_id": work_receipt_id,
        "status": "ACTIVE",
        "instruction_receipt_id": instruction_receipt_id,
        **normalized_work,
        "activated_at": activated_at,
        "scope_expansion": "ALLOW_WITHIN_DECLARED_ROOTS",
        "commit_policy": "EXPLICIT_APPROVAL_REQUIRED",
    }
    bound_snapshot = dict(current)
    bound_snapshot["observed_at"] = activated_at
    bound_snapshot["authority"] = {
        "status": "VERIFIED",
        "authority_id": authority_id,
        "evidence_ref": normalized_work["instruction_ref"],
        "approval_ref": normalized_work["instruction_ref"],
        "basis": "DIRECT_USER_INSTRUCTION",
    }
    bound_snapshot["authority_ref"] = normalized_work["instruction_ref"]
    bound_snapshot["write_scope"] = {
        "status": "DELEGATED",
        "scope_kind": "PROJECT_SOURCE_WORK",
        "roots": normalized_work["write_roots"],
        "operations": normalized_work["write_operations"],
        "boundary": normalized_work["boundary"],
        "source_root_selection": normalized_work["source_root_selection"],
        "evidence_ref": normalized_work["instruction_ref"],
    }
    bound_snapshot["execution_assignment"] = {
        "status": "ASSIGNED",
        "assignment_kind": "PROJECT_SOURCE_WORK",
        "assignment_id": work_receipt_id,
        "work_receipt_id": work_receipt_id,
        "instruction_receipt_id": instruction_receipt_id,
        "write_roots": normalized_work["write_roots"],
        "write_operations": normalized_work["write_operations"],
        "boundary": normalized_work["boundary"],
        "source_root_selection": normalized_work["source_root_selection"],
        "evidence_ref": normalized_work["instruction_ref"],
    }
    bound_snapshot["assignment_ref"] = normalized_work["instruction_ref"]
    bound_snapshot["instruction_receipt"] = instruction_receipt
    bound_snapshot["work_receipt"] = work_receipt
    bound_snapshot["execution_binding"] = {
        "binding_id": binding_id,
        "binding_kind": "INSTRUCTION_WORK",
        "work_receipt_id": work_receipt_id,
        "instruction_receipt_id": instruction_receipt_id,
        "approval_ref": normalized_work["instruction_ref"],
        **coordinates,
        "bound_at": activated_at,
        "scope": "process-local",
    }
    return {
        "schema": WORK_RECEIPT_SCHEMA,
        "status": "WORK_RECEIPT_ACTIVATED",
        "instruction_receipt": instruction_receipt,
        "work_receipt": work_receipt,
        "binding_id": binding_id,
        "snapshot": bound_snapshot,
        "authority_created": False,
        "canonical_authority_changed": False,
        "process_local_state_updated": True,
        "repository_write": False,
    }
def apply_execution_binding(
    *,
    snapshot: Mapping[str, Any],
    proposal: Mapping[str, Any],
    approval: Mapping[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    current = _current_snapshot(snapshot)
    normalized_proposal = _validated_proposal(proposal)
    for field in ("session_id", "frame_id", "anchor_id", "source_commit"):
        if current.get(field) != normalized_proposal[field]:
            raise ExecutionBindingError(
                "EXECUTION_BINDING_STALE", f"proposal {field} no longer matches snapshot"
            )
    coordinates = _surface_coordinates(current)
    for field, value in coordinates.items():
        if value != normalized_proposal[field]:
            raise ExecutionBindingError(
                "EXECUTION_BINDING_STALE",
                f"proposal {field} no longer matches snapshot",
            )
    evidence_refs = _mapping(current.get("evidence_refs"), "snapshot.evidence_refs")
    if evidence_refs.get("validation") != normalized_proposal["validation_ref"]:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_STALE",
            "proposal validation_ref no longer matches snapshot",
        )

    if approval.get("status") != "APPROVED":
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_REQUIRED", "approval.status must be APPROVED"
        )
    approved_proposal_id = _required_text(
        approval.get("proposal_id"), "approval.proposal_id"
    )
    if approved_proposal_id != normalized_proposal["proposal_id"]:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_MISMATCH",
            "approval does not reference the current proposal",
        )
    for field in ("commander_surface", "operation", "target", "boundary"):
        value = _required_text(approval.get(field), f"approval.{field}")
        if field == "target":
            value = os.path.normpath(value)
        if value != normalized_proposal[field]:
            raise ExecutionBindingError(
                "EXECUTION_BINDING_APPROVAL_MISMATCH",
                f"approval.{field} does not match the proposal",
            )
    approval_ref = _evidence_ref(approval.get("evidence_ref"), "approval.evidence_ref")
    authority_source_ref = _evidence_ref(
        approval.get("authority_source_ref"), "approval.authority_source_ref"
    )
    platform_evidence = _platform_approval_evidence(
        approval.get("platform_approval_evidence"),
        proposal=normalized_proposal,
        approval_ref=approval_ref,
    )
    bound_at = _timestamp(observed_at)
    binding_material = {
        "proposal_id": normalized_proposal["proposal_id"],
        "approval_ref": approval_ref,
        "authority_source_ref": authority_source_ref,
        **coordinates,
        "bound_at": bound_at,
    }
    binding_id = "binding_" + _digest(binding_material)[:24]
    authority_id = "authority_" + _digest(
        {"session_id": current["session_id"], **binding_material}
    )[:24]
    bound_snapshot = dict(current)
    bound_snapshot["observed_at"] = bound_at
    bound_snapshot["authority"] = {
        "status": "VERIFIED",
        "authority_id": authority_id,
        "evidence_ref": authority_source_ref,
        "approval_ref": approval_ref,
    }
    bound_snapshot["authority_ref"] = authority_source_ref
    bound_snapshot["write_scope"] = {
        "status": "DELEGATED",
        "roots": normalized_proposal["write_roots"],
        "operations": normalized_proposal["write_operations"],
        "boundary": normalized_proposal["boundary"],
        "evidence_ref": approval_ref,
    }
    bound_snapshot["execution_assignment"] = {
        "status": "ASSIGNED",
        "assignment_id": normalized_proposal["proposal_id"],
        "assignment_kind": normalized_proposal.get(
            "assignment_kind", "STRICT_EXACT"
        ),
        "operation": normalized_proposal["operation"],
        "target": normalized_proposal["target"],
        "boundary": normalized_proposal["boundary"],
        "evidence_ref": approval_ref,
    }
    bound_snapshot["assignment_ref"] = approval_ref
    bound_snapshot["execution_binding"] = {
        "binding_id": binding_id,
        "proposal_id": normalized_proposal["proposal_id"],
        "approval_ref": approval_ref,
        **({"platform_approval_evidence": platform_evidence} if platform_evidence else {}),
        **coordinates,
        "bound_at": bound_at,
        "scope": "process-local",
    }
    return {
        "schema": BINDING_SCHEMA,
        "status": "EXECUTION_BINDING_APPLIED",
        "binding_id": binding_id,
        "proposal_id": normalized_proposal["proposal_id"],
        "snapshot": bound_snapshot,
        **({"platform_approval_evidence": platform_evidence} if platform_evidence else {}),
        "authority_created": False,
        "canonical_authority_changed": False,
        "process_local_state_updated": True,
        "repository_write": False,
    }


def _validated_project_source_work(
    value: Mapping[str, Any], *, repository_root: Path | None
) -> dict[str, Any]:
    work = _mapping(value, "work")
    scope_kind = _required_text(work.get("scope_kind"), "work.scope_kind")
    if scope_kind != "PROJECT_SOURCE_WORK":
        raise ExecutionBindingError(
            "WORK_RECEIPT_INVALID", "work.scope_kind must be PROJECT_SOURCE_WORK"
        )
    roots, source_root_selection = _resolve_project_source_roots(
        work.get("write_roots"), repository_root=repository_root
    )
    operations = _operation_list(work.get("write_operations"), "work.write_operations")
    if not set(operations).issubset(PROJECT_SOURCE_WORK_OPERATIONS):
        raise ExecutionBindingError(
            "WORK_RECEIPT_INVALID",
            "PROJECT_SOURCE_WORK permits only CREATE and MODIFY",
        )
    return {
        "scope_kind": scope_kind,
        "write_roots": roots,
        "write_operations": operations,
        "source_root_selection": source_root_selection,
        "boundary": _required_text(work.get("boundary"), "work.boundary"),
        "task_summary": _required_text(work.get("task_summary"), "work.task_summary"),
        "instruction_ref": _evidence_ref(
            work.get("instruction_ref"), "work.instruction_ref"
        ),
    }


def _resolve_project_source_roots(
    value: Any, *, repository_root: Path | None
) -> tuple[list[str], dict[str, Any]]:
    """Resolve a bounded source root without inventing one for existing workspaces."""

    resolved_repository = None
    layout: dict[str, Any] | None = None
    if repository_root is not None:
        try:
            resolved_repository = repository_root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ExecutionBindingError(
                "WORK_RECEIPT_REPOSITORY_ROOT_INVALID",
                "repository root is unavailable for project-source work",
            ) from exc
        if not resolved_repository.is_dir():
            raise ExecutionBindingError(
                "WORK_RECEIPT_REPOSITORY_ROOT_INVALID",
                "repository root must be a directory for project-source work",
            )
        layout = _project_layout(resolved_repository)

    manifest_root = (
        _manifest_application_source_root(resolved_repository)
        if resolved_repository is not None
        else None
    )

    if value is not None:
        roots = _project_source_root_list(value, "work.write_roots")
        if resolved_repository is not None:
            for root in roots:
                if not _path_within(root, str(resolved_repository)):
                    raise ExecutionBindingError(
                        "WORK_RECEIPT_ROOT_OUTSIDE_REPOSITORY",
                        "PROJECT_SOURCE_WORK roots must remain inside the repository",
                    )
            if manifest_root is not None and roots != [manifest_root]:
                raise ExecutionBindingError(
                    "WORK_RECEIPT_MANIFEST_ROOT_MISMATCH",
                    "PROJECT_SOURCE_WORK root must match the repository manifest",
                )
            missing_roots = [root for root in roots if not Path(root).is_dir()]
            if missing_roots and manifest_root is not None:
                raise ExecutionBindingError(
                    "WORK_RECEIPT_DECLARED_SOURCE_ROOT_MISSING",
                    "the repository-declared source root must exist before bounded work begins",
                )
            if missing_roots and layout["classification"] == "EXISTING":
                raise ExecutionBindingError(
                    "WORK_RECEIPT_EXISTING_SOURCE_ROOT_MISSING",
                    "existing projects require an existing source root before bounded work begins",
                )
        return roots, {
            **(layout or {"classification": "UNKNOWN"}),
            "origin": (
                "REPOSITORY_MANIFEST" if manifest_root is not None else "EXPLICIT"
            ),
            "defaulted": False,
            "manifest_path": (
                str(resolved_repository / REPOSITORY_MANIFEST_NAME)
                if manifest_root is not None and resolved_repository is not None
                else "UNKNOWN"
            ),
        }

    if resolved_repository is None:
        raise ExecutionBindingError(
            "WORK_RECEIPT_SOURCE_ROOT_REQUIRED",
            "project-source work requires write_roots when the repository layout is unavailable",
        )

    if manifest_root is not None:
        if not Path(manifest_root).is_dir():
            raise ExecutionBindingError(
                "WORK_RECEIPT_DECLARED_SOURCE_ROOT_MISSING",
                "the repository-declared source root must exist before bounded work begins",
            )
        return [manifest_root], {
            **layout,
            "origin": "REPOSITORY_MANIFEST",
            "defaulted": False,
            "root": manifest_root,
            "manifest_path": str(resolved_repository / REPOSITORY_MANIFEST_NAME),
        }

    if layout["classification"] != "FRESH":
        raise ExecutionBindingError(
            "WORK_RECEIPT_SOURCE_ROOT_REQUIRED",
            "existing projects require an explicit existing source root",
        )
    root = resolved_repository / FRESH_PROJECT_DEFAULT_SOURCE_ROOT
    return [str(root)], {
        **layout,
        "origin": "FRESH_DEFAULT",
        "defaulted": True,
        "root": str(root),
    }


def _project_layout(repository_root: Path) -> dict[str, Any]:
    entries = sorted(
        child.name
        for child in repository_root.iterdir()
        if child.name not in RUNTIME_LAYOUT_ENTRIES
    )
    return {
        "classification": "FRESH" if not entries else "EXISTING",
        "observed_project_entries": entries,
        "runtime_excluded_entries": sorted(RUNTIME_LAYOUT_ENTRIES),
    }


def _manifest_application_source_root(repository_root: Path) -> str | None:
    """Return the canonical `layers.application.root` declaration when present."""

    manifest_path = repository_root / REPOSITORY_MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExecutionBindingError(
            "WORK_RECEIPT_MANIFEST_UNREADABLE",
            "repository manifest cannot be read for source-root resolution",
        ) from exc

    declared_roots = _yaml_mapping_scalars(text, MANIFEST_APPLICATION_ROOT_PATH)
    if not declared_roots:
        return None
    if len(set(declared_roots)) != 1:
        raise ExecutionBindingError(
            "WORK_RECEIPT_MANIFEST_ROOT_AMBIGUOUS",
            "repository manifest declares conflicting application roots",
        )
    declared = declared_roots[0]
    candidate = Path(declared)
    if candidate.is_absolute():
        raise ExecutionBindingError(
            "WORK_RECEIPT_MANIFEST_ROOT_INVALID",
            "repository manifest application root must be repository-relative",
        )
    resolved = (repository_root / candidate).resolve(strict=False)
    if not _path_within(str(resolved), str(repository_root)):
        raise ExecutionBindingError(
            "WORK_RECEIPT_MANIFEST_ROOT_INVALID",
            "repository manifest application root escapes the repository",
        )
    return _project_source_root_list(
        [str(resolved)], "manifest.layers.application.root"
    )[0]


def _yaml_mapping_scalars(text: str, expected_path: tuple[str, ...]) -> list[str]:
    """Read simple scalar mappings from YAML fences without a YAML dependency."""

    in_yaml_fence = False
    stack: list[tuple[int, str]] = []
    values: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            language = stripped[3:].strip().casefold()
            if not in_yaml_fence and language in {"yaml", "yml"}:
                in_yaml_fence = True
                stack = []
            elif in_yaml_fence:
                in_yaml_fence = False
                stack = []
            continue
        if not in_yaml_fence or not stripped or stripped.startswith("#"):
            continue
        match = re.match(
            r"^( *)([A-Za-z][A-Za-z0-9_-]*):(?:[ ]*(.*))?$", raw_line
        )
        if match is None:
            continue
        indent = len(match.group(1))
        key = match.group(2)
        value = (match.group(3) or "").strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        current_path = tuple(item[1] for item in stack) + (key,)
        if current_path == expected_path and value:
            values.append(_yaml_scalar(value))
        if not value:
            stack.append((indent, key))
    return values


def _yaml_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return _required_text(value, "manifest.layers.application.root")


def _validated_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    proposal = dict(_mapping(value, "proposal"))
    if proposal.get("schema") != PROPOSAL_SCHEMA:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_PROPOSAL_INVALID", "proposal schema is invalid"
        )
    material = {
        "session_id": _required_text(proposal.get("session_id"), "proposal.session_id"),
        "frame_id": _required_text(proposal.get("frame_id"), "proposal.frame_id"),
        "anchor_id": _required_text(proposal.get("anchor_id"), "proposal.anchor_id"),
        "source_commit": _required_text(
            proposal.get("source_commit"), "proposal.source_commit"
        ),
        "validation_ref": _required_text(
            proposal.get("validation_ref"), "proposal.validation_ref"
        ),
        "session_location": _required_text(
            proposal.get("session_location"), "proposal.session_location"
        ),
        "commander_surface": _required_text(
            proposal.get("commander_surface"), "proposal.commander_surface"
        ),
        "execution_surface": _required_text(
            proposal.get("execution_surface"), "proposal.execution_surface"
        ),
        "repository_location": _required_text(
            proposal.get("repository_location"), "proposal.repository_location"
        ),
        "operation": _required_text(proposal.get("operation"), "proposal.operation"),
        "target": _absolute_path(proposal.get("target"), "proposal.target"),
        "boundary": _required_text(proposal.get("boundary"), "proposal.boundary"),
        "write_roots": _absolute_path_list(
            proposal.get("write_roots"), "proposal.write_roots"
        ),
        "write_operations": _operation_list(
            proposal.get("write_operations"), "proposal.write_operations"
        ),
        "task_summary": _required_text(
            proposal.get("task_summary"), "proposal.task_summary"
        ),
        "request_ref": _required_text(proposal.get("request_ref"), "proposal.request_ref"),
    }
    expected_id = "assignment_" + _digest(material)[:24]
    legacy_id = "assignment_" + _digest(
        {**material, "proposed_at": proposal["proposed_at"]}
    )[:24]
    supplied_id = proposal.get("proposal_id")
    if supplied_id not in {expected_id, legacy_id}:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_PROPOSAL_INVALID", "proposal hash does not match content"
        )
    return {
        "proposal_id": _required_text(supplied_id, "proposal.proposal_id"),
        **material,
        "proposed_at": _timestamp(
            _required_text(proposal.get("proposed_at"), "proposal.proposed_at")
        ),
    }


def _platform_approval_evidence(
    value: Any,
    *,
    proposal: Mapping[str, Any],
    approval_ref: str,
) -> dict[str, str] | None:
    if value is None:
        return None
    evidence = dict(_mapping(value, "approval.platform_approval_evidence"))
    if evidence.get("schema") != PLATFORM_APPROVAL_EVIDENCE_SCHEMA:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_MISMATCH",
            "platform approval evidence schema is invalid",
        )
    normalized = {
        "schema": PLATFORM_APPROVAL_EVIDENCE_SCHEMA,
        "evidence_ref": _evidence_ref(
            evidence.get("evidence_ref"),
            "approval.platform_approval_evidence.evidence_ref",
        ),
        "provider": _required_text(
            evidence.get("provider"),
            "approval.platform_approval_evidence.provider",
        ).upper(),
        "request_id": _required_text(
            evidence.get("request_id"),
            "approval.platform_approval_evidence.request_id",
        ),
        "session_id": _required_text(
            evidence.get("session_id"),
            "approval.platform_approval_evidence.session_id",
        ),
        "proposal_id": _required_text(
            evidence.get("proposal_id") or proposal["proposal_id"],
            "approval.platform_approval_evidence.proposal_id",
        ),
        "decision": _required_text(
            evidence.get("decision"),
            "approval.platform_approval_evidence.decision",
        ).upper(),
    }
    if normalized["evidence_ref"] != approval_ref:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_MISMATCH",
            "platform approval evidence does not match approval.evidence_ref",
        )
    if normalized["proposal_id"] != proposal["proposal_id"]:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_MISMATCH",
            "platform approval evidence does not reference the current proposal",
        )
    if normalized["session_id"] != proposal["session_id"]:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_MISMATCH",
            "platform approval evidence session does not match the proposal",
        )
    if normalized["decision"] not in {"ALLOW_ONCE", "ALLOW_ALWAYS"}:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_APPROVAL_REQUIRED",
            "platform approval evidence must record an allow decision",
        )
    return normalized


def _current_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = dict(_mapping(value, "snapshot"))
    if snapshot.get("state") != "READY":
        raise ExecutionBindingError(
            "EXECUTION_BINDING_SNAPSHOT_NOT_READY", "snapshot state must be READY"
        )
    currentness = _mapping(
        snapshot.get("executable_runtime_currentness"),
        "snapshot.executable_runtime_currentness",
    )
    if currentness.get("status") != "CURRENT":
        raise ExecutionBindingError(
            "EXECUTION_BINDING_SNAPSHOT_STALE", "snapshot currentness must be CURRENT"
        )
    return snapshot


def _surface_coordinates(snapshot: Mapping[str, Any]) -> dict[str, str]:
    coordinates = _mapping(snapshot.get("coordinates"), "snapshot.coordinates")
    return {
        field: _required_text(
            coordinates.get(field), f"snapshot.coordinates.{field}"
        )
        for field in (
            "session_location",
            "commander_surface",
            "execution_surface",
            "repository_location",
        )
    }


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionBindingError("EXECUTION_BINDING_INVALID", f"{context} must be an object")
    return value


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID", f"{context} must be a non-empty string"
        )
    return value.strip()


def _evidence_ref(value: Any, context: str) -> str:
    text = _required_text(value, context)
    if text == "UNKNOWN":
        raise ExecutionBindingError(
            "EXECUTION_BINDING_EVIDENCE_REQUIRED", f"{context} cannot be UNKNOWN"
        )
    return text


def _absolute_path(value: Any, context: str) -> str:
    text = _required_text(value, context)
    path = Path(text)
    if not path.is_absolute():
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID", f"{context} must be absolute"
        )
    return os.path.normpath(str(path))


def _absolute_path_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID", f"{context} must be a non-empty array"
        )
    result = [_absolute_path(item, f"{context}[]") for item in value]
    return list(dict.fromkeys(result))


def _command_argv(value: Any, context: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
    ):
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID",
            f"{context} must be a non-empty string array",
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ExecutionBindingError(
                "EXECUTION_BINDING_INVALID",
                f"{context} must contain safe non-empty strings",
            )
        result.append(item)
    return result


def _command_payload_sha256(command_argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(
            list(command_argv),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _project_source_root_list(value: Any, context: str) -> list[str]:
    roots = _absolute_path_list(value, context)
    for root in roots:
        parts = {part.casefold() for part in Path(root).parts}
        if ".ai" in parts or ".git" in parts:
            raise ExecutionBindingError(
                "WORK_RECEIPT_INVALID",
                "PROJECT_SOURCE_WORK roots cannot include .ai or .git",
            )
    return roots


def _operation_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID", f"{context} must be a non-empty array"
        )
    result = []
    for item in value:
        operation = _required_text(item, f"{context}[]").upper()
        if operation not in MUTATION_OPERATIONS:
            raise ExecutionBindingError(
                "EXECUTION_BINDING_INVALID", f"unsupported write operation: {operation}"
            )
        if operation not in result:
            result.append(operation)
    return result


def _path_within(target: str, root: str) -> bool:
    try:
        normalized_target = os.path.normcase(os.path.abspath(target))
        normalized_root = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([normalized_target, normalized_root]) == normalized_root
    except (ValueError, OSError):
        return False


def _timestamp(value: str) -> str:
    normalized = _required_text(value, "observed_at").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID", "observed_at must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise ExecutionBindingError(
            "EXECUTION_BINDING_INVALID", "observed_at must include a timezone"
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
