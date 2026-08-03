"""Deterministic pre-execution guard and process-local permit ledger.

The guard does not create authority, write scope, execution assignment, Host
capability, approval, or currentness. It intersects evidence already attached
to the current Runtime Anchor snapshot with one concrete mutation request.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GUARD_RESULT_SCHEMA = "ai-career.execution-guard-result.v1"
PERMIT_RECEIPT_SCHEMA = "ai-career.execution-permit-receipt.v1"
MUTATION_OPERATIONS = frozenset({"CREATE", "MODIFY", "DELETE", "MOVE"})
RECEIPT_TTL_SECONDS = 30

SCHEMA = """
CREATE TABLE permit_receipt (
    receipt_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    frame_id TEXT NOT NULL,
    anchor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    target TEXT NOT NULL,
    boundary TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    target_preimage_json TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CONSUMED'))
);
"""


@dataclass(frozen=True)
class ExecutionGuardError(Exception):
    """A malformed guard request or snapshot."""

    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


class ExecutionGuardRuntime:
    """One process-local permit ledger owned by a single Host session."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def check(
        self,
        *,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
        observed_at: str,
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate one mutation request and issue a one-time receipt if permitted."""

        normalized_snapshot = _mapping_copy(snapshot, "snapshot")
        normalized_request = _normalize_request(
            request,
            snapshot=normalized_snapshot,
            task_frame_lineage_verification=task_frame_lineage_verification,
        )
        checked_at = _timestamp(observed_at)
        reasons = _blocking_reasons(normalized_snapshot, normalized_request)
        enforcement = _mutation_enforcement(normalized_request)
        common = {
            "schema": GUARD_RESULT_SCHEMA,
            "session_id": normalized_request["session_id"],
            "frame_id": normalized_request["frame_id"],
            "anchor_id": normalized_request["anchor_id"],
            "operation": normalized_request["operation"],
            "target": normalized_request["target"],
            "boundary": normalized_request["boundary"],
            "checked_at": checked_at,
            "mutation_enforcement": enforcement,
            "authority_created": False,
            "repository_write": False,
        }
        if "task_frame_lineage" in normalized_request:
            common["task_frame_lineage"] = normalized_request[
                "task_frame_lineage"
            ]
        if reasons:
            execution_host_reasons = {
                "HOST_WRITE_CAPABILITY_REQUIRED",
                "PRE_WRITE_HOOK_REQUIRED",
                "HOST_CAPABILITY_EVIDENCE_REQUIRED",
            }
            return {
                **common,
                "status": (
                    "BLOCKED_EXECUTION_HOST_REQUIRED"
                    if execution_host_reasons.intersection(reasons)
                    else "EXECUTION_GUARD_BLOCKED"
                ),
                "decision": "BLOCKED",
                "execution_permission": "BLOCKED",
                "reasons": reasons,
                "permit_receipt": None,
            }

        request_hash = _digest(normalized_request)
        snapshot_hash = _digest(normalized_snapshot)
        expires_at = _add_seconds(checked_at, RECEIPT_TTL_SECONDS)
        ordinal = int(
            self.conn.execute("SELECT COUNT(*) AS count FROM permit_receipt").fetchone()[
                "count"
            ]
        ) + 1
        receipt_id = "permit_" + hashlib.sha256(
            f"{request_hash}:{snapshot_hash}:{checked_at}:{ordinal}".encode("utf-8")
        ).hexdigest()[:24]
        self.conn.execute(
            """
            INSERT INTO permit_receipt(
                receipt_id, request_hash, snapshot_hash, session_id, frame_id,
                anchor_id, operation, target, boundary, payload_sha256,
                target_preimage_json, issued_at, expires_at, consumed_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'ACTIVE')
            """,
            (
                receipt_id,
                request_hash,
                snapshot_hash,
                normalized_request["session_id"],
                normalized_request["frame_id"],
                normalized_request["anchor_id"],
                normalized_request["operation"],
                normalized_request["target"],
                normalized_request["boundary"],
                normalized_request["payload_sha256"],
                _canonical_json(normalized_request["target_preimage"]),
                checked_at,
                expires_at,
            ),
        )
        receipt = {
            "schema": PERMIT_RECEIPT_SCHEMA,
            "receipt_id": receipt_id,
            "status": "ACTIVE",
            "session_id": normalized_request["session_id"],
            "frame_id": normalized_request["frame_id"],
            "anchor_id": normalized_request["anchor_id"],
            "operation": normalized_request["operation"],
            "target": normalized_request["target"],
            "boundary": normalized_request["boundary"],
            "payload_sha256": normalized_request["payload_sha256"],
            "target_preimage": normalized_request["target_preimage"],
            "source_commit": normalized_request["source_commit"],
            "validation_ref": normalized_request["validation_ref"],
            "authority_digest": _digest(_mapping_copy(normalized_snapshot["authority"], "snapshot.authority")),
            "write_scope_digest": _digest(_mapping_copy(normalized_snapshot["write_scope"], "snapshot.write_scope")),
            "assignment_digest": _digest(_mapping_copy(normalized_snapshot["execution_assignment"], "snapshot.execution_assignment")),
            "approval_ref": normalized_request["approval"]["evidence_ref"],
            "issued_at": checked_at,
            "expires_at": expires_at,
            "one_time": True,
            "authority": False,
        }
        if "task_frame_lineage" in normalized_request:
            receipt["task_frame_lineage"] = normalized_request[
                "task_frame_lineage"
            ]
            receipt["task_frame_lineage_digest"] = normalized_request[
                "task_frame_lineage"
            ].get("lineage_digest", "UNKNOWN")
        receipt["receipt_digest"] = _digest(receipt)
        return {
            **common,
            "status": "EXECUTION_GUARD_PERMITTED",
            "decision": "PERMIT",
            "execution_permission": "PERMITTED",
            "reasons": [],
            "permit_receipt": receipt,
        }

    def consume(
        self,
        *,
        receipt_id: str,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
        observed_at: str,
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Consume one matching active receipt immediately before Host mutation."""

        normalized_receipt_id = _required_text(receipt_id, "receipt_id")
        normalized_snapshot = _mapping_copy(snapshot, "snapshot")
        normalized_request = _normalize_request(
            request,
            snapshot=normalized_snapshot,
            task_frame_lineage_verification=task_frame_lineage_verification,
        )
        consumed_at = _timestamp(observed_at)
        row = self.conn.execute(
            "SELECT * FROM permit_receipt WHERE receipt_id = ?",
            (normalized_receipt_id,),
        ).fetchone()
        reasons: list[str] = []
        if row is None:
            reasons.append("PERMIT_RECEIPT_UNKNOWN")
        else:
            if row["status"] != "ACTIVE":
                reasons.append("PERMIT_RECEIPT_ALREADY_CONSUMED")
            if _parse_timestamp(consumed_at) > _parse_timestamp(str(row["expires_at"])):
                reasons.append("PERMIT_RECEIPT_EXPIRED")
            if row["request_hash"] != _digest(normalized_request):
                reasons.append("PERMIT_RECEIPT_REQUEST_MISMATCH")
            if row["snapshot_hash"] != _digest(normalized_snapshot):
                reasons.append("PERMIT_RECEIPT_SNAPSHOT_STALE")
            reasons.extend(_blocking_reasons(normalized_snapshot, normalized_request))
        if reasons:
            return {
                "schema": GUARD_RESULT_SCHEMA,
                "status": "PERMIT_RECEIPT_REJECTED",
                "decision": "BLOCKED",
                "execution_permission": "BLOCKED",
                "receipt_id": normalized_receipt_id,
                "reasons": _unique(reasons),
                "consumed_at": consumed_at,
                "repository_write": False,
            }

        self.conn.execute(
            """
            UPDATE permit_receipt
            SET status = 'CONSUMED', consumed_at = ?
            WHERE receipt_id = ? AND status = 'ACTIVE'
            """,
            (consumed_at, normalized_receipt_id),
        )
        return {
            "schema": GUARD_RESULT_SCHEMA,
            "status": "PERMIT_RECEIPT_CONSUMED",
            "decision": "PERMIT",
            "execution_permission": "PERMITTED",
            "receipt_id": normalized_receipt_id,
            "reasons": [],
            "consumed_at": consumed_at,
            "repository_write": False,
            "next": "HOST_MUTATION_MUST_MATCH_CONSUMED_RECEIPT",
        }

    def close(self) -> None:
        self.conn.close()


def _blocking_reasons(
    snapshot: Mapping[str, Any], request: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    for field, reason in (
        ("session_id", "SESSION_ID_MISMATCH"),
        ("frame_id", "FRAME_ID_MISMATCH"),
        ("anchor_id", "ANCHOR_ID_MISMATCH"),
    ):
        if snapshot.get(field) != request[field]:
            reasons.append(reason)

    if snapshot.get("state") != "READY":
        reasons.append("RUNTIME_STATE_NOT_READY")
    currentness = snapshot.get("executable_runtime_currentness")
    if not isinstance(currentness, Mapping) or currentness.get("status") != "CURRENT":
        reasons.append("CURRENTNESS_NOT_CURRENT")
    coordinates = snapshot.get("coordinates")
    binding = snapshot.get("execution_binding")
    if not isinstance(coordinates, Mapping):
        reasons.append("SESSION_SURFACE_COORDINATES_REQUIRED")
    if not isinstance(binding, Mapping):
        reasons.append("EXECUTION_BINDING_REQUIRED")
    if isinstance(coordinates, Mapping) and isinstance(binding, Mapping):
        for field, reason in (
            ("session_location", "SESSION_LOCATION_BINDING_MISMATCH"),
            ("commander_surface", "COMMANDER_SURFACE_BINDING_MISMATCH"),
            ("execution_surface", "EXECUTION_SURFACE_BINDING_MISMATCH"),
            ("repository_location", "REPOSITORY_LOCATION_BINDING_MISMATCH"),
        ):
            if coordinates.get(field) != binding.get(field):
                reasons.append(reason)
    if snapshot.get("source_commit") != request["source_commit"]:
        reasons.append("SOURCE_COMMIT_MISMATCH")
    evidence_refs = snapshot.get("evidence_refs")
    if (
        not isinstance(evidence_refs, Mapping)
        or evidence_refs.get("validation") != request["validation_ref"]
    ):
        reasons.append("VALIDATION_REF_MISMATCH")
    preimage_status = request["target_preimage"]["status"]
    if request["operation"] == "CREATE" and preimage_status != "ABSENT":
        reasons.append("CREATE_PREIMAGE_MUST_BE_ABSENT")
    if request["operation"] in {"MODIFY", "DELETE"} and preimage_status != "PRESENT":
        reasons.append("MUTATION_PREIMAGE_MUST_BE_PRESENT")
    if request["operation"] == "DELETE" and request["payload_sha256"] != "NONE":
        reasons.append("DELETE_PAYLOAD_FORBIDDEN")
    if request["operation"] in {"CREATE", "MODIFY"} and request["payload_sha256"] == "NONE":
        reasons.append("MUTATION_PAYLOAD_REQUIRED")

    capability = request["host_capability"]
    if capability.get("filesystem_write") != "AVAILABLE":
        reasons.append("HOST_WRITE_CAPABILITY_REQUIRED")
    if capability.get("pre_write_hook") != "AVAILABLE":
        reasons.append("PRE_WRITE_HOOK_REQUIRED")
    if not _evidence_ref(capability):
        reasons.append("HOST_CAPABILITY_EVIDENCE_REQUIRED")

    authority = snapshot.get("authority")
    if not isinstance(authority, Mapping) or authority.get("status") != "VERIFIED":
        reasons.append("AUTHORITY_REQUIRED")
    elif not _evidence_ref(authority):
        reasons.append("AUTHORITY_EVIDENCE_REQUIRED")

    write_scope = snapshot.get("write_scope")
    if not isinstance(write_scope, Mapping) or write_scope.get("status") != "DELEGATED":
        reasons.append("WRITE_SCOPE_REQUIRED")
    else:
        if request["operation"] not in _string_set(write_scope.get("operations")):
            reasons.append("WRITE_SCOPE_OPERATION_MISMATCH")
        if write_scope.get("boundary") != request["boundary"]:
            reasons.append("WRITE_SCOPE_BOUNDARY_MISMATCH")
        roots = write_scope.get("roots")
        if not isinstance(roots, Sequence) or isinstance(roots, (str, bytes)):
            reasons.append("WRITE_SCOPE_TARGET_MISMATCH")
        elif not any(
            isinstance(root, str) and _path_within(request["target"], root)
            for root in roots
        ):
            reasons.append("WRITE_SCOPE_TARGET_MISMATCH")
        if not _evidence_ref(write_scope):
            reasons.append("WRITE_SCOPE_EVIDENCE_REQUIRED")

    lineage = request.get("task_frame_lineage")
    lineage_verified = (
        isinstance(lineage, Mapping) and lineage.get("status") == "VERIFIED"
    )
    if isinstance(lineage, Mapping):
        if not lineage_verified:
            reasons.append(
                str(lineage.get("reason", "TASK_FRAME_LINEAGE_VERIFICATION_REQUIRED"))
            )
        else:
            if lineage.get("operation") != request["operation"]:
                reasons.append("TASK_FRAME_LINEAGE_OPERATION_MISMATCH")
            if lineage.get("target") != request["target"]:
                reasons.append("TASK_FRAME_LINEAGE_TARGET_MISMATCH")

    assignment = snapshot.get("execution_assignment")
    if not isinstance(assignment, Mapping) or assignment.get("status") != "ASSIGNED":
        reasons.append("EXECUTION_ASSIGNMENT_REQUIRED")
    else:
        project_source_work = _is_project_source_work(snapshot)
        if lineage_verified:
            if assignment.get("assignment_id") != lineage.get(
                "parent_assignment_id"
            ):
                reasons.append("TASK_FRAME_LINEAGE_ASSIGNMENT_MISMATCH")
        if project_source_work:
            if request["operation"] not in _string_set(
                assignment.get("write_operations")
            ):
                reasons.append("WORK_RECEIPT_OPERATION_MISMATCH")
            if _is_work_receipt_protected_target(request["target"]):
                reasons.append("WORK_RECEIPT_PROTECTED_PATH")
            roots = assignment.get("write_roots")
            if not isinstance(roots, Sequence) or isinstance(roots, (str, bytes)):
                reasons.append("WORK_RECEIPT_TARGET_MISMATCH")
            elif not any(
                isinstance(root, str) and _path_within(request["target"], root)
                for root in roots
            ):
                reasons.append("WORK_RECEIPT_TARGET_MISMATCH")
        elif not lineage_verified:
            if assignment.get("operation") != request["operation"]:
                reasons.append("EXECUTION_ASSIGNMENT_OPERATION_MISMATCH")
            if assignment.get("target") != request["target"]:
                reasons.append("EXECUTION_ASSIGNMENT_TARGET_MISMATCH")
        if assignment.get("boundary") != request["boundary"]:
            reasons.append("EXECUTION_ASSIGNMENT_BOUNDARY_MISMATCH")
        if not _evidence_ref(assignment):
            reasons.append("EXECUTION_ASSIGNMENT_EVIDENCE_REQUIRED")

    approval = request["approval"]
    if approval.get("status") != "APPROVED":
        reasons.append("APPROVAL_REQUIRED")
    else:
        project_source_work = _is_project_source_work(snapshot)
        if not isinstance(coordinates, Mapping) or approval.get(
            "commander_surface"
        ) != coordinates.get("commander_surface"):
            reasons.append("APPROVAL_COMMANDER_SURFACE_MISMATCH")
        if project_source_work and isinstance(assignment, Mapping):
            if approval.get("approval_kind") != "INSTRUCTION_RECEIPT":
                reasons.append("WORK_RECEIPT_APPROVAL_KIND_MISMATCH")
            if approval.get("instruction_receipt_id") != assignment.get(
                "instruction_receipt_id"
            ):
                reasons.append("WORK_RECEIPT_INSTRUCTION_MISMATCH")
            if approval.get("work_receipt_id") != assignment.get("work_receipt_id"):
                reasons.append("WORK_RECEIPT_ID_MISMATCH")
            if approval.get("evidence_ref") != assignment.get("evidence_ref"):
                reasons.append("WORK_RECEIPT_APPROVAL_EVIDENCE_MISMATCH")
        elif lineage_verified and isinstance(assignment, Mapping):
            if approval.get("operation") != assignment.get("operation"):
                reasons.append("APPROVAL_OPERATION_MISMATCH")
            if approval.get("target") != assignment.get("target"):
                reasons.append("APPROVAL_TARGET_MISMATCH")
            if approval.get("boundary") != assignment.get("boundary"):
                reasons.append("APPROVAL_BOUNDARY_MISMATCH")
            if approval.get("evidence_ref") != assignment.get("evidence_ref"):
                reasons.append("APPROVAL_ASSIGNMENT_EVIDENCE_MISMATCH")
        else:
            if approval.get("operation") != request["operation"]:
                reasons.append("APPROVAL_OPERATION_MISMATCH")
            if approval.get("target") != request["target"]:
                reasons.append("APPROVAL_TARGET_MISMATCH")
            if approval.get("boundary") != request["boundary"]:
                reasons.append("APPROVAL_BOUNDARY_MISMATCH")
        if not _evidence_ref(approval):
            reasons.append("APPROVAL_EVIDENCE_REQUIRED")
    return _unique(reasons)


def _normalize_request(
    value: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    task_frame_lineage_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request = _mapping_copy(value, "request")
    project_source_work = _is_project_source_work(snapshot)
    boundary = request.get("boundary")
    if boundary is None and project_source_work:
        assignment = _mapping_copy(
            snapshot.get("execution_assignment"),
            "snapshot.execution_assignment",
        )
        boundary = assignment.get("boundary")
    normalized = {
        "session_id": _required_text(request.get("session_id"), "request.session_id"),
        "frame_id": _required_text(request.get("frame_id"), "request.frame_id"),
        "anchor_id": _required_text(request.get("anchor_id"), "request.anchor_id"),
        "operation": _required_text(request.get("operation"), "request.operation").upper(),
        "target": _absolute_target(request.get("target")),
        "boundary": _required_text(boundary, "request.boundary"),
        "source_commit": _required_text(
            request.get("source_commit"), "request.source_commit"
        ),
        "validation_ref": _required_text(
            request.get("validation_ref"), "request.validation_ref"
        ),
        "payload_sha256": _sha256_or_none(request.get("payload_sha256")),
        "target_preimage": _normalize_preimage(request.get("target_preimage")),
        "host_capability": _mapping_copy(
            request.get("host_capability"), "request.host_capability"
        ),
    }
    if normalized["operation"] not in MUTATION_OPERATIONS:
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            f"unsupported mutation operation: {normalized['operation']}",
        )
    lineage_candidate = request.get("task_frame_lineage")
    if lineage_candidate is not None:
        candidate = _normalize_lineage_candidate(lineage_candidate)
        normalized["task_frame_lineage"] = _normalize_lineage_verification(
            candidate,
            task_frame_lineage_verification,
        )
    elif task_frame_lineage_verification is not None:
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "verified Task Frame lineage requires request.task_frame_lineage",
        )
    approval = request.get("approval")
    if approval is None and project_source_work:
        normalized["approval"] = _derived_work_receipt_approval(
            snapshot=snapshot, request=normalized
        )
    else:
        normalized["approval"] = _mapping_copy(approval, "request.approval")
    return normalized


def _is_project_source_work(snapshot: Mapping[str, Any]) -> bool:
    assignment = snapshot.get("execution_assignment")
    return (
        isinstance(assignment, Mapping)
        and assignment.get("status") == "ASSIGNED"
        and assignment.get("assignment_kind") == "PROJECT_SOURCE_WORK"
    )


def _derived_work_receipt_approval(
    *, snapshot: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, str]:
    assignment = _mapping_copy(
        snapshot.get("execution_assignment"), "snapshot.execution_assignment"
    )
    coordinates = _mapping_copy(snapshot.get("coordinates"), "snapshot.coordinates")
    return {
        "status": "APPROVED",
        "approval_kind": "INSTRUCTION_RECEIPT",
        "instruction_receipt_id": _required_text(
            assignment.get("instruction_receipt_id"),
            "snapshot.execution_assignment.instruction_receipt_id",
        ),
        "work_receipt_id": _required_text(
            assignment.get("work_receipt_id"),
            "snapshot.execution_assignment.work_receipt_id",
        ),
        "commander_surface": _required_text(
            coordinates.get("commander_surface"),
            "snapshot.coordinates.commander_surface",
        ),
        "operation": _required_text(request.get("operation"), "request.operation"),
        "target": _required_text(request.get("target"), "request.target"),
        "boundary": _required_text(request.get("boundary"), "request.boundary"),
        "evidence_ref": _required_text(
            assignment.get("evidence_ref"), "snapshot.execution_assignment.evidence_ref"
        ),
    }


def _is_work_receipt_protected_target(target: str) -> bool:
    return bool({".ai", ".git"} & {part.casefold() for part in Path(target).parts})


def _normalize_lineage_candidate(value: Any) -> dict[str, str]:
    candidate = _mapping_copy(value, "request.task_frame_lineage")
    fields = {
        "task_frame_id",
        "parent_assignment_id",
        "boss_allocation_id",
        "sub_turn_id",
        "sub_worker_id",
        "worker_path",
    }
    if set(candidate) != fields:
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "request.task_frame_lineage fields are invalid",
        )
    return {
        field: _required_text(
            candidate.get(field), f"request.task_frame_lineage.{field}"
        )
        for field in fields
    }


def _normalize_lineage_verification(
    candidate: Mapping[str, str],
    verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if verification is None:
        return {
            **dict(candidate),
            "status": "UNKNOWN",
            "reason": "TASK_FRAME_LINEAGE_VERIFICATION_REQUIRED",
        }
    status = str(verification.get("status", "UNKNOWN")).strip()
    if status != "VERIFIED":
        return {
            **dict(candidate),
            "status": "BLOCKED",
            "reason": status or "TASK_FRAME_LINEAGE_VERIFICATION_REQUIRED",
        }
    verified_fields = {
        "status",
        "task_frame_id",
        "parent_assignment_id",
        "boss_allocation_id",
        "boss_allocation_digest",
        "boss_turn_id",
        "boss_worker_id",
        "sub_turn_id",
        "sub_worker_id",
        "worker_path",
        "operation",
        "target",
        "lineage_digest",
    }
    if set(verification) != verified_fields:
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "Task Frame lineage verification fields are invalid",
        )
    normalized = {
        field: _required_text(
            verification.get(field), f"task_frame_lineage_verification.{field}"
        )
        for field in verified_fields
        if field != "status"
    }
    for field, value in candidate.items():
        if normalized.get(field) != value:
            return {
                **dict(candidate),
                "status": "BLOCKED",
                "reason": "TASK_FRAME_LINEAGE_CANDIDATE_MISMATCH",
            }
    normalized["operation"] = normalized["operation"].upper()
    normalized["target"] = _absolute_target(normalized["target"])
    material = {"status": "VERIFIED", **normalized}
    supplied_digest = material.pop("lineage_digest")
    expected_digest = _digest(material)
    if supplied_digest != expected_digest:
        return {
            **dict(candidate),
            "status": "BLOCKED",
            "reason": "TASK_FRAME_LINEAGE_DIGEST_MISMATCH",
        }
    return {**material, "lineage_digest": supplied_digest}


def _mutation_enforcement(request: Mapping[str, Any]) -> str:
    capability = request["host_capability"]
    if (
        capability.get("filesystem_write") == "AVAILABLE"
        and capability.get("pre_write_hook") == "AVAILABLE"
        and _evidence_ref(capability)
    ):
        return "HOST_ATTESTED"
    return "UNKNOWN"


def _normalize_command_argv(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID", "request.command_argv must be an array"
        )
    argv = list(value)
    if not argv or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "request.command_argv must contain non-empty text values",
        )
    return argv


def _command_payload_sha256(command_argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(command_argv), ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _absolute_target(value: Any) -> str:
    text = _required_text(value, "request.target")
    path = Path(text)
    if not path.is_absolute():
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID", "request.target must be absolute"
        )
    return os.path.normpath(str(path))


def _path_within(target: str, root: str) -> bool:
    try:
        normalized_target = os.path.normcase(os.path.abspath(target))
        normalized_root = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([normalized_target, normalized_root]) == normalized_root
    except (ValueError, OSError):
        return False


def _evidence_ref(value: Mapping[str, Any]) -> bool:
    evidence = value.get("evidence_ref")
    return isinstance(evidence, str) and bool(evidence.strip()) and evidence != "UNKNOWN"


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {item for item in value if isinstance(item, str)}


def _mapping_copy(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID", f"{context} must be an object"
        )
    return dict(value)


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            f"{context} must be a non-empty string",
        )
    return value.strip()


def _timestamp(value: str) -> str:
    normalized = _required_text(value, "observed_at").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID", "observed_at must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID", "observed_at must include a timezone"
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _add_seconds(value: str, seconds: int) -> str:
    return (_parse_timestamp(value) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _sha256_or_none(value: Any) -> str:
    text = _required_text(value, "request.payload_sha256")
    if text == "NONE":
        return text
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "request.payload_sha256 must be lowercase SHA-256 or NONE",
        )
    return text


def _normalize_preimage(
    value: Any, *, allow_not_applicable: bool = False
) -> dict[str, str]:
    preimage = _mapping_copy(value, "request.target_preimage")
    status = _required_text(preimage.get("status"), "request.target_preimage.status")
    if status == "ABSENT":
        return {"status": "ABSENT", "sha256": "NONE"}
    if status == "NOT_APPLICABLE" and allow_not_applicable:
        if preimage.get("sha256") != "NONE":
            raise ExecutionGuardError(
                "EXECUTION_GUARD_REQUEST_INVALID",
                "not-applicable target preimage requires NONE",
            )
        return {"status": "NOT_APPLICABLE", "sha256": "NONE"}
    if status != "PRESENT":
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "request.target_preimage.status must be ABSENT or PRESENT",
        )
    sha256 = _sha256_or_none(preimage.get("sha256"))
    if sha256 == "NONE":
        raise ExecutionGuardError(
            "EXECUTION_GUARD_REQUEST_INVALID",
            "present target preimage requires SHA-256",
        )
    return {
        "status": "PRESENT",
        "sha256": sha256,
    }


def _digest(value: Mapping[str, Any]) -> str:
    raw = _canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
