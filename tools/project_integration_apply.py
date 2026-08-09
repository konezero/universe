"""Receipt-aware application for a Universe project-integration proposal."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from project_integration_catalog import PROPOSAL_SCHEMA, PROJECT_ID_PATTERN


INTEGRATION_APPROVAL_SCHEMA = "universe.project-integration-approval.v1"
INTEGRATION_APPLY_RECEIPT_SCHEMA = "universe.project-integration-apply-receipt.v1"
EXPECTED_ASSETS = {
    ".universe/project.json": "PROJECT_SOURCE",
    ".ai/universe/install_binding.json": "LOCAL_RUNTIME",
    ".ai/universe/TODO_TRACKING_POLICY.md": "LOCAL_RUNTIME",
    ".ai/universe/connection.md": "LOCAL_RUNTIME",
    ".ai/memory/universe_nodes/README.md": "LOCAL_RUNTIME",
}


class ProjectIntegrationApplyError(ValueError):
    pass


class ProjectIntegrationMutationGateway(Protocol):
    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
        write_roots: tuple[Path, ...],
        task_summary: str,
    ) -> Mapping[str, Any]: ...


def build_project_integration_approval(
    *,
    project_id: str,
    proposal: Mapping[str, Any],
    project_source_evidence_ref: str,
    local_runtime_evidence_ref: str,
) -> dict[str, str]:
    normalized_project_id = _project_id(project_id)
    proposal_id = _text(proposal.get("proposal_id"), "proposal.proposal_id")
    proposal_digest = _sha256(proposal.get("proposal_digest"), "proposal.proposal_digest")
    return {
        "schema": INTEGRATION_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "project_id": normalized_project_id,
        "proposal_id": proposal_id,
        "proposal_digest": proposal_digest,
        "project_source_evidence_ref": _text(
            project_source_evidence_ref,
            "project_source_evidence_ref",
        ),
        "local_runtime_evidence_ref": _text(
            local_runtime_evidence_ref,
            "local_runtime_evidence_ref",
        ),
    }


def apply_project_integration_proposal(
    *,
    project_root: Path,
    project_id: str,
    proposal: Any,
    approval: Any,
    mutation_gateway: ProjectIntegrationMutationGateway,
) -> dict[str, Any]:
    """Apply one exact approved proposal through a receipt-aware gateway."""

    root = project_root.expanduser().resolve(strict=True)
    normalized_proposal, payloads = _proposal(project_id, proposal)
    normalized_approval = _approval(normalized_proposal, approval)
    boundary = (
        "Apply approved Universe Project integration proposal "
        + normalized_proposal["proposal_id"]
    )
    mutation_receipts: list[dict[str, Any]] = []
    unchanged: list[str] = []
    changed_scopes: set[str] = set()

    for target_path in sorted(payloads):
        content, scope = payloads[target_path]
        target = root / target_path
        _assert_contained(root, target)
        if target.exists() and (not target.is_file() or target.is_symlink()):
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_TARGET_INVALID")
        if target.exists() and _sha256_bytes(target.read_bytes()) == _sha256_bytes(content):
            unchanged.append(target_path.as_posix())
            continue
        approval_evidence_ref = normalized_approval[
            "project_source_evidence_ref"
            if scope == "PROJECT_SOURCE"
            else "local_runtime_evidence_ref"
        ]
        result = mutation_gateway.apply_file(
            target=target,
            content=content,
            operation="MODIFY" if target.exists() else "CREATE",
            boundary=boundary,
            approval_evidence_ref=approval_evidence_ref,
            request_ref=(
                "universe://project-integration-proposals/"
                + normalized_proposal["proposal_id"]
            ),
            write_roots=(target.parent,),
            task_summary="Apply one approved Universe Project integration asset",
        )
        if result.get("status") != "FILE_MUTATION_APPLIED":
            raise ProjectIntegrationApplyError(
                "PROJECT_INTEGRATION_MUTATION_BLOCKED:"
                + str(result.get("status", "UNKNOWN"))
            )
        mutation_receipts.append(dict(result))
        changed_scopes.add(scope)

    for target_path, (content, _scope) in payloads.items():
        target = root / target_path
        if (
            not target.is_file()
            or target.is_symlink()
            or _sha256_bytes(target.read_bytes()) != _sha256_bytes(content)
        ):
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_VALIDATION_FAILED")

    status = (
        "PROJECT_INTEGRATION_ALREADY_APPLIED"
        if not mutation_receipts
        else "PROJECT_INTEGRATION_APPLIED"
    )
    receipt_material = {
        "project_id": normalized_proposal["project_id"],
        "proposal_id": normalized_proposal["proposal_id"],
        "proposal_digest": normalized_proposal["proposal_digest"],
        "project_source_evidence_ref": normalized_approval[
            "project_source_evidence_ref"
        ],
        "local_runtime_evidence_ref": normalized_approval[
            "local_runtime_evidence_ref"
        ],
        "files": {
            path.as_posix(): _sha256_bytes(content)
            for path, (content, _scope) in sorted(payloads.items())
        },
        "mutation_receipt_ids": [
            str(receipt.get("receipt_id", "UNKNOWN"))
            for receipt in mutation_receipts
        ],
    }
    return {
        "schema": INTEGRATION_APPLY_RECEIPT_SCHEMA,
        "status": status,
        **receipt_material,
        "receipt_digest": _sha256_json(receipt_material),
        "mutation_receipts": mutation_receipts,
        "unchanged": unchanged,
        "project_source_write": (
            "APPLIED" if "PROJECT_SOURCE" in changed_scopes else "NONE"
        ),
        "project_runtime_state_write": (
            "APPLIED" if "LOCAL_RUNTIME" in changed_scopes else "NONE"
        ),
    }


def _proposal(
    expected_project_id: str,
    value: Any,
) -> tuple[dict[str, Any], dict[Path, tuple[bytes, str]]]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "project_id",
        "catalog_digest",
        "assets",
        "proposal_id",
        "proposal_digest",
        "effects",
        "apply_contract",
    }:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
    if value.get("schema") != PROPOSAL_SCHEMA:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
    project_id = _project_id(value.get("project_id"))
    if project_id != _project_id(expected_project_id):
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROJECT_MISMATCH")
    catalog_digest = _sha256(value.get("catalog_digest"), "proposal.catalog_digest")
    if value.get("effects") != {
        "project_source_write": "PROPOSED",
        "project_runtime_state_write": "PROPOSED",
        "career_release_write": "NONE",
    }:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
    if value.get("apply_contract") != {
            "owner": "UNIVERSE_PROJECT_LIFECYCLE_HOST",
            "project_binding": "PROJECT_SOURCE_APPROVAL_REQUIRED",
            "local_runtime": "INSTALLED_CAREER_RUNTIME_REQUIRED",
            "execution": "NOT_STARTED",
        }:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
    raw_assets = value.get("assets")
    if not isinstance(raw_assets, list):
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
    payloads: dict[Path, tuple[bytes, str]] = {}
    descriptors: list[dict[str, str]] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, Mapping) or set(raw_asset) != {
            "target_path",
            "scope",
            "operation",
            "sha256",
            "content_base64",
        }:
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
        target_path = _relative_path(raw_asset.get("target_path"))
        scope = _text(raw_asset.get("scope"), "asset.scope")
        if EXPECTED_ASSETS.get(target_path.as_posix()) != scope:
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_TARGET_INVALID")
        if target_path in payloads or raw_asset.get("operation") != "CREATE_OR_REPLACE":
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
        digest = _sha256(raw_asset.get("sha256"), "asset.sha256")
        encoded = raw_asset.get("content_base64")
        if not isinstance(encoded, str):
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_CONTENT_INVALID")
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as error:
            raise ProjectIntegrationApplyError(
                "PROJECT_INTEGRATION_CONTENT_INVALID"
            ) from error
        if _sha256_bytes(content) != digest:
            raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_DIGEST_MISMATCH")
        payloads[target_path] = (content, scope)
        descriptors.append(
            {
                "target_path": target_path.as_posix(),
                "scope": scope,
                "operation": "CREATE_OR_REPLACE",
                "sha256": digest,
            }
        )
    if {path.as_posix() for path in payloads} != set(EXPECTED_ASSETS):
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_INVALID")
    material = {
        "schema": PROPOSAL_SCHEMA,
        "project_id": project_id,
        "catalog_digest": catalog_digest,
        "assets": sorted(descriptors, key=lambda item: item["target_path"]),
    }
    proposal_digest = _sha256_json(material)
    if value.get("proposal_digest") != proposal_digest:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_DIGEST_MISMATCH")
    if value.get("proposal_id") != "project_integration_" + proposal_digest[:24]:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROPOSAL_ID_MISMATCH")
    return dict(value), payloads


def _approval(proposal: Mapping[str, Any], value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "status",
        "project_id",
        "proposal_id",
        "proposal_digest",
        "project_source_evidence_ref",
        "local_runtime_evidence_ref",
    }:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_APPROVAL_INVALID")
    normalized = {
        "schema": _text(value.get("schema"), "approval.schema"),
        "status": _text(value.get("status"), "approval.status"),
        "project_id": _project_id(value.get("project_id")),
        "proposal_id": _text(value.get("proposal_id"), "approval.proposal_id"),
        "proposal_digest": _sha256(
            value.get("proposal_digest"), "approval.proposal_digest"
        ),
        "project_source_evidence_ref": _text(
            value.get("project_source_evidence_ref"),
            "approval.project_source_evidence_ref",
        ),
        "local_runtime_evidence_ref": _text(
            value.get("local_runtime_evidence_ref"),
            "approval.local_runtime_evidence_ref",
        ),
    }
    if (
        normalized["schema"] != INTEGRATION_APPROVAL_SCHEMA
        or normalized["status"] != "APPROVED"
        or normalized["project_id"] != proposal["project_id"]
        or normalized["proposal_id"] != proposal["proposal_id"]
        or normalized["proposal_digest"] != proposal["proposal_digest"]
    ):
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_APPROVAL_MISMATCH")
    return normalized


def _assert_contained(root: Path, target: Path) -> None:
    try:
        target.resolve(strict=target.exists()).relative_to(root)
    except (OSError, ValueError) as error:
        raise ProjectIntegrationApplyError(
            "PROJECT_INTEGRATION_BOUNDARY_VIOLATION"
        ) from error


def _relative_path(value: Any) -> Path:
    text = _text(value, "asset.target_path").replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_TARGET_INVALID")
    return path


def _project_id(value: Any) -> str:
    project_id = _text(value, "project_id")
    if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise ProjectIntegrationApplyError("PROJECT_INTEGRATION_PROJECT_ID_INVALID")
    return project_id


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectIntegrationApplyError(field.upper().replace(".", "_") + "_REQUIRED")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProjectIntegrationApplyError(field.upper().replace(".", "_") + "_INVALID")
    return normalized


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_json(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
