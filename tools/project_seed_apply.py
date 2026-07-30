from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from project_seed_assets import (
    ASSET_FILES,
    ASSET_PROPOSAL_SCHEMA,
    ASSET_ROOT,
    ProjectSeedAssetError,
    canonical_json,
    load_project_seed_assets,
    sha256_bytes,
)


ASSET_APPROVAL_SCHEMA = "universe.project-seed-asset-approval.v1"
ASSET_APPLY_RECEIPT_SCHEMA = "universe.project-seed-asset-apply-receipt.v1"


class ProjectSeedMutationGateway(Protocol):
    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
    ) -> Mapping[str, Any]: ...


def apply_project_seed_asset_proposal(
    *,
    project_root: Path,
    project_id: str,
    proposal: Any,
    approval: Any,
    mutation_gateway: ProjectSeedMutationGateway,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    normalized_proposal, payloads = _proposal(project_id, proposal)
    normalized_approval = _approval(normalized_proposal, approval)
    asset_root = root / ASSET_ROOT
    if not asset_root.is_dir() or asset_root.is_symlink():
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_ROOT_REQUIRED")
    _assert_contained(root, asset_root)

    ordered_paths = [
        path for path in sorted(payloads) if path != (ASSET_ROOT / "manifest.json")
    ]
    ordered_paths.append(ASSET_ROOT / "manifest.json")
    boundary = (
        "Apply approved Universe Project Seed asset proposal "
        + normalized_proposal["proposal_id"]
    )
    mutation_receipts: list[dict[str, Any]] = []
    unchanged: list[str] = []

    for relative_path in ordered_paths:
        target = root / relative_path
        _assert_contained(root, target)
        if target.exists() and (not target.is_file() or target.is_symlink()):
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_TARGET_INVALID")
        content = payloads[relative_path]
        if target.exists() and sha256_bytes(target.read_bytes()) == sha256_bytes(content):
            unchanged.append(relative_path.as_posix())
            continue
        operation = "MODIFY" if target.exists() else "CREATE"
        result = mutation_gateway.apply_file(
            target=target,
            content=content,
            operation=operation,
            boundary=boundary,
            approval_evidence_ref=normalized_approval["evidence_ref"],
            request_ref=(
                "universe://project-seed-asset-proposals/"
                + normalized_proposal["proposal_id"]
            ),
        )
        if result.get("status") != "FILE_MUTATION_APPLIED":
            raise ProjectSeedAssetError(
                "PROJECT_SEED_ASSET_MUTATION_BLOCKED:"
                + str(result.get("status", "UNKNOWN"))
            )
        mutation_receipts.append(dict(result))

    load_project_seed_assets(root)
    expected_files = {
        relative.relative_to(ASSET_ROOT).as_posix(): sha256_bytes(content)
        for relative, content in payloads.items()
    }
    for relative_path, content in payloads.items():
        target = root / relative_path
        if (
            not target.is_file()
            or target.is_symlink()
            or sha256_bytes(target.read_bytes()) != sha256_bytes(content)
        ):
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_VALIDATION_FAILED")

    status = (
        "PROJECT_SEED_ASSETS_ALREADY_APPLIED"
        if not mutation_receipts
        else "PROJECT_SEED_ASSETS_APPLIED"
    )
    receipt_material = {
        "project_id": project_id,
        "proposal_id": normalized_proposal["proposal_id"],
        "proposal_digest": normalized_proposal["proposal_digest"],
        "approval_evidence_ref": normalized_approval["evidence_ref"],
        "manifest_ref": ASSET_ROOT.joinpath("manifest.json").as_posix(),
        "files": expected_files,
        "mutation_receipt_ids": [
            str(receipt.get("receipt_id", "UNKNOWN"))
            for receipt in mutation_receipts
        ],
    }
    return {
        "schema": ASSET_APPLY_RECEIPT_SCHEMA,
        "status": status,
        **receipt_material,
        "receipt_digest": hashlib.sha256(
            canonical_json(receipt_material)
        ).hexdigest(),
        "mutation_receipts": mutation_receipts,
        "unchanged": unchanged,
        "project_source_write": "NONE",
        "project_runtime_state_write": (
            "NOT_REQUIRED" if not mutation_receipts else "APPLIED"
        ),
    }


def build_project_seed_asset_approval(
    *,
    project_id: str,
    proposal: Mapping[str, Any],
    evidence_ref: str,
) -> dict[str, str]:
    return {
        "schema": ASSET_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "project_id": _text(project_id, "project_id"),
        "proposal_id": _text(proposal.get("proposal_id"), "proposal.proposal_id"),
        "proposal_digest": _sha256(
            proposal.get("proposal_digest"), "proposal.proposal_digest"
        ),
        "evidence_ref": _text(evidence_ref, "evidence_ref"),
    }


def _proposal(
    expected_project_id: str, value: Any
) -> tuple[dict[str, Any], dict[Path, bytes]]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "project_id",
        "seed_id",
        "seed_digest",
        "assets",
        "proposal_id",
        "proposal_digest",
        "target_root",
        "source",
        "effects",
        "apply_contract",
    }:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
    if value.get("schema") != ASSET_PROPOSAL_SCHEMA:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
    project_id = _text(value.get("project_id"), "proposal.project_id")
    if project_id != _text(expected_project_id, "project_id"):
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROJECT_MISMATCH")
    if value.get("target_root") != ASSET_ROOT.as_posix():
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_ROOT_INVALID")
    if value.get("effects") != {
        "project_source_write": "NONE",
        "project_runtime_state_write": "PROPOSED",
        "universe_publish": "NONE",
        "career_promotion": "NONE",
    }:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
    if value.get("apply_contract") != {
        "owner": "PROJECT_MASTER",
        "approval": "EXACT_USER_APPROVAL_REQUIRED",
        "write_path": "RECEIPT_AWARE_PROJECT_RUNTIME_STATE_WRITE",
        "validation": "MANIFEST_AND_ASSET_DIGESTS_REQUIRED",
    }:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")

    raw_assets = value.get("assets")
    if not isinstance(raw_assets, list):
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
    expected_targets = {
        (ASSET_ROOT / filename).as_posix()
        for filename in (*ASSET_FILES.values(), "manifest.json")
    }
    descriptors: list[dict[str, str]] = []
    payloads: dict[Path, bytes] = {}
    for raw in raw_assets:
        if not isinstance(raw, Mapping) or set(raw) != {
            "target_path",
            "operation",
            "sha256",
            "content_base64",
        }:
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
        target_path = _text(raw.get("target_path"), "asset.target_path")
        if target_path not in expected_targets or target_path in {
            path.as_posix() for path in payloads
        }:
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_TARGET_INVALID")
        if raw.get("operation") != "CREATE_OR_REPLACE":
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
        expected_digest = _sha256(raw.get("sha256"), "asset.sha256")
        encoded = raw.get("content_base64")
        if not isinstance(encoded, str):
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as error:
            raise ProjectSeedAssetError(
                "PROJECT_SEED_ASSET_CONTENT_INVALID"
            ) from error
        if sha256_bytes(content) != expected_digest:
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_DIGEST_MISMATCH")
        payloads[Path(target_path)] = content
        descriptors.append(
            {
                "target_path": target_path,
                "operation": "CREATE_OR_REPLACE",
                "sha256": expected_digest,
            }
        )
    if {path.as_posix() for path in payloads} != expected_targets:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_INVALID")

    material = {
        "schema": ASSET_PROPOSAL_SCHEMA,
        "project_id": project_id,
        "seed_id": _text(value.get("seed_id"), "proposal.seed_id"),
        "seed_digest": _sha256(value.get("seed_digest"), "proposal.seed_digest"),
        "assets": sorted(descriptors, key=lambda item: item["target_path"]),
    }
    proposal_digest = hashlib.sha256(canonical_json(material)).hexdigest()
    if value.get("proposal_digest") != proposal_digest:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_DIGEST_MISMATCH")
    if value.get("proposal_id") != "seed_assets_" + proposal_digest[:24]:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_PROPOSAL_ID_MISMATCH")
    return dict(value), payloads


def _approval(
    proposal: Mapping[str, Any], value: Any
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "status",
        "project_id",
        "proposal_id",
        "proposal_digest",
        "evidence_ref",
    }:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_APPROVAL_INVALID")
    normalized = {
        "schema": _text(value.get("schema"), "approval.schema"),
        "status": _text(value.get("status"), "approval.status"),
        "project_id": _text(value.get("project_id"), "approval.project_id"),
        "proposal_id": _text(value.get("proposal_id"), "approval.proposal_id"),
        "proposal_digest": _sha256(
            value.get("proposal_digest"), "approval.proposal_digest"
        ),
        "evidence_ref": _text(value.get("evidence_ref"), "approval.evidence_ref"),
    }
    if (
        normalized["schema"] != ASSET_APPROVAL_SCHEMA
        or normalized["status"] != "APPROVED"
        or normalized["project_id"] != proposal["project_id"]
        or normalized["proposal_id"] != proposal["proposal_id"]
        or normalized["proposal_digest"] != proposal["proposal_digest"]
    ):
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_APPROVAL_MISMATCH")
    return normalized


def _assert_contained(root: Path, target: Path) -> None:
    try:
        target.resolve(strict=target.exists()).relative_to(root)
    except (OSError, ValueError) as error:
        raise ProjectSeedAssetError(
            "PROJECT_SEED_ASSET_BOUNDARY_VIOLATION"
        ) from error
    current = target if target.is_dir() else target.parent
    while current != root:
        if current.exists() and current.is_symlink():
            raise ProjectSeedAssetError(
                "PROJECT_SEED_ASSET_SYMLINK_FORBIDDEN"
            )
        current = current.parent


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectSeedAssetError(field.upper().replace(".", "_") + "_REQUIRED")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProjectSeedAssetError(field.upper().replace(".", "_") + "_INVALID")
    return normalized
