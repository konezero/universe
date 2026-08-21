from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from release_runtime import ReleaseRuntime, ReleaseRuntimeError
from windows_native_cli import NativeCliRequest, run_native_cli


RELEASE_APPROVAL_SCHEMA = "universe.project-release-approval.v1"
RELEASE_APPLY_RECEIPT_SCHEMA = "universe.project-release-apply-receipt.v1"
DIRECT_LIFECYCLE_RECEIPT_SCHEMA = "universe.project-runtime-lifecycle-receipt.v1"
LIFECYCLE_PLAN_SCHEMA = "universe.project-runtime-lifecycle-plan.v1"
INSTALLATION_MANIFEST_PATH = (
    ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
)
INSTALLER_PATH = (
    ".ai/distribution/context_management_runtime_pack/"
    "project_runtime_installer.py"
)


class ProjectReleaseApplyError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        lifecycle_result: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.lifecycle_result = (
            dict(lifecycle_result) if lifecycle_result is not None else None
        )


def plan_project_release_lifecycle(
    *,
    project_root: Path,
    project_id: str,
    release_id: str,
    source_commit: str,
) -> dict[str, Any]:
    root = _project_root(project_root)
    normalized_project = _text(project_id, "project_id")
    installation_manifest = root / INSTALLATION_MANIFEST_PATH
    if installation_manifest.exists():
        if not installation_manifest.is_file() or installation_manifest.is_symlink():
            raise ProjectReleaseApplyError(
                "PROJECT_RUNTIME_INSTALLATION_STATE_INVALID",
                "installed Runtime manifest is not a real file",
            )
        try:
            installed = json.loads(installation_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProjectReleaseApplyError(
                "PROJECT_RUNTIME_INSTALLATION_STATE_INVALID",
                str(error),
            ) from error
        if (
            not isinstance(installed, Mapping)
            or installed.get("schema")
            != "ai-career.project-runtime-installation.v1"
        ):
            raise ProjectReleaseApplyError(
                "PROJECT_RUNTIME_INSTALLATION_STATE_INVALID",
                "installed Runtime manifest schema is unsupported",
            )
        coordinates = installed.get("installation")
        if not isinstance(coordinates, Mapping):
            raise ProjectReleaseApplyError(
                "PROJECT_RUNTIME_INSTALLATION_STATE_INVALID",
                "installed Runtime coordinates are unavailable",
            )
        if coordinates.get("project") != normalized_project:
            raise ProjectReleaseApplyError(
                "PROJECT_RUNTIME_IDENTITY_MISMATCH",
                "installed Runtime project identity does not match the connection",
            )
        source = installed.get("source")
        installed_commit = (
            str(source.get("commit", "UNKNOWN"))
            if isinstance(source, Mapping)
            else "UNKNOWN"
        )
        manifest_sha256 = hashlib.sha256(
            installation_manifest.read_bytes()
        ).hexdigest()
        operation = "RUNTIME_UPDATE"
        user_command = "OS_UPDATE"
        installed_state = "MANAGED"
    else:
        installed_commit = "NONE"
        manifest_sha256 = "NONE"
        operation = "FRESH_INSTALL"
        user_command = "OS_INSTALL"
        installed_state = "ABSENT"

    material = {
        "schema": LIFECYCLE_PLAN_SCHEMA,
        "project_id": normalized_project,
        "target_root": str(root),
        "release_id": _text(release_id, "release_id"),
        "source_commit": _commit(source_commit),
        "operation": operation,
        "user_command": user_command,
        "installed_runtime": {
            "state": installed_state,
            "source_commit": installed_commit,
            "manifest_sha256": manifest_sha256,
        },
        "project_host_preflight": "REQUIRED",
        "candidate_execution": "FORBIDDEN",
    }
    material["plan_digest"] = _digest(material)
    material["status"] = "PROJECT_RUNTIME_LIFECYCLE_PLAN_READY"
    return material


def build_project_release_approval(
    *,
    project_id: str,
    proposal: Mapping[str, Any],
    evidence_ref: str,
) -> dict[str, str]:
    plan = proposal.get("plan")
    if not isinstance(plan, Mapping):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PROPOSAL_INVALID",
            "proposal plan is unavailable",
        )
    return {
        "schema": RELEASE_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "project_id": _text(project_id, "project_id"),
        "proposal_id": _text(proposal.get("proposal_id"), "proposal.proposal_id"),
        "proposal_digest": _sha256(
            proposal.get("proposal_digest"),
            "proposal.proposal_digest",
        ),
        "release_id": _text(proposal.get("release_id"), "proposal.release_id"),
        "plan_digest": _sha256(plan.get("plan_digest"), "proposal.plan.plan_digest"),
        "evidence_ref": _text(evidence_ref, "evidence_ref"),
    }


def apply_project_release_proposal(
    *,
    project_root: Path,
    project_id: str,
    proposal: Any,
    approval: Any,
    database_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    root = _project_root(project_root)
    normalized_proposal = _proposal(project_id, proposal)
    normalized_approval = _approval(normalized_proposal, approval)

    try:
        with ReleaseRuntime(
            database_path=database_path,
            manifest_path=manifest_path,
        ) as runtime:
            if runtime.release_id != normalized_proposal["release_id"]:
                raise ProjectReleaseApplyError(
                    "PROJECT_RELEASE_ARTIFACT_MISMATCH",
                    "release artifact identity does not match the proposal",
                )
            if (
                runtime.verification["database_sha256"]
                != normalized_proposal["release_database_sha256"]
            ):
                raise ProjectReleaseApplyError(
                    "PROJECT_RELEASE_ARTIFACT_MISMATCH",
                    "release database digest does not match the proposal",
                )
            install_plan = runtime.plan_project_install(root)
            if install_plan["collisions"]:
                collision = install_plan["collisions"][0]
                raise ProjectReleaseApplyError(
                    "UNMANAGED_TARGET_COLLISION",
                    f"unmanaged file at {collision['path']}",
                )
            install_result = runtime.apply_project_install(
                target_root=root,
                approved_plan_digest=install_plan["plan_digest"],
            )
            runtime_surface_result = _rehydrate_runtime_surfaces(
                runtime=runtime,
                root=root,
                project_id=normalized_proposal["project_id"],
            )
    except ProjectReleaseApplyError:
        raise
    except (OSError, ReleaseRuntimeError) as error:
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_ARTIFACT_INVALID",
            str(error),
        ) from error

    material = {
        "project_id": normalized_proposal["project_id"],
        "proposal_id": normalized_proposal["proposal_id"],
        "proposal_digest": normalized_proposal["proposal_digest"],
        "release_id": normalized_proposal["release_id"],
        "plan_digest": normalized_proposal["plan"]["plan_digest"],
        "approval_evidence_ref": normalized_approval["evidence_ref"],
        "operation": install_result["operation"],
        "changed_count": install_result["changed_count"],
        "changed": install_result["changed"],
        "runtime_surface_result": runtime_surface_result,
    }
    return {
        "schema": RELEASE_APPLY_RECEIPT_SCHEMA,
        "status": "PROJECT_RELEASE_APPLIED",
        **material,
        "receipt_digest": _digest(material),
    }


def _rehydrate_runtime_surfaces(
    *, runtime: ReleaseRuntime, root: Path, project_id: str
) -> dict[str, Any]:
    """Regenerate Runtime-owned project surfaces from the applied release."""

    bundle_root = root / ".ai" / "runtime" / "release_db" / runtime.release_id
    if not bundle_root.exists():
        runtime.materialize_source_bundle(bundle_root)
    installer = root / INSTALLER_PATH
    result = run_native_cli(
        NativeCliRequest(
            executable=Path(sys.executable),
            arguments=(
                str(installer),
                "install",
                "--source-bundle", str(bundle_root),
                "--target", str(root),
                "--project", project_id,
                "--node", project_id,
                "--mode", "MASTER",
                "--host", "universe-release-db",
                "--commander-surface", "UNIVERSE_UI",
                "--execution-surface", "repo-local",
                "--repository-location", str(root),
                # ReleaseRuntime already checked this exact immutable release
                # against the prior managed inventory.  The Core installer may
                # otherwise see its previous DISTRIBUTION_MANIFEST hashes while
                # the release has already replaced a managed Core file.
                "--force",
            ),
            cwd=root,
            timeout_seconds=120,
        )
    )
    if result.status != "COMPLETED":
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_SURFACE_REHYDRATION_FAILED",
            result.stderr or "Runtime surface installer failed",
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_SURFACE_REHYDRATION_FAILED",
            "Runtime surface installer returned invalid JSON",
        ) from error
    if (
        not isinstance(payload, Mapping)
        or payload.get("result") != "PASS"
        or payload.get("repository_runtime") != "VERIFIED"
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_SURFACE_REHYDRATION_FAILED",
            "Runtime surface installer did not verify the applied Runtime",
        )
    return {"result": "REHYDRATED", "validation": dict(payload)}


def apply_project_release_plan(
    *,
    project_root: Path,
    project_id: str,
    plan: Mapping[str, Any],
    release_database_sha256: str,
    instruction_ref: str,
    database_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Apply an inspected lifecycle plan from a direct user command.

    Universe callers exchange only the immutable plan, the selected Release DB
    digest, and the direct instruction reference; no Proposal or approval
    evidence is returned or persisted by this route.
    """
    normalized_project = _text(project_id, "project_id")
    normalized_instruction_ref = _text(instruction_ref, "instruction_ref")
    normalized_database_sha256 = _sha256(
        release_database_sha256,
        "release_database_sha256",
    )
    if not isinstance(plan, Mapping):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PLAN_INVALID",
            "lifecycle plan must be an object",
        )
    material = {
        "schema": "universe.project-release-proposal.v1",
        "project_id": normalized_project,
        "release_id": _text(plan.get("release_id"), "plan.release_id"),
        "mode": "MASTER",
        "release_database_sha256": normalized_database_sha256,
        "plan": dict(plan),
        "approval": "REQUIRED",
        "execution_owner": "PROJECT_HOST",
        "effects": {"project_write": "NONE", "files_changed": 0},
        "next_operation": "USER_APPROVAL_AND_PROJECT_HOST_APPLY",
    }
    compatibility_digest = _digest(material)
    compatibility_payload = {
        **material,
        "proposal_digest": compatibility_digest,
        "proposal_id": "release_proposal_" + compatibility_digest[:20],
        "status": "PROJECT_RELEASE_PROPOSAL_READY",
    }
    compatibility_approval = build_project_release_approval(
        project_id=normalized_project,
        proposal=compatibility_payload,
        evidence_ref=normalized_instruction_ref,
    )
    legacy_receipt = apply_project_release_proposal(
        project_root=project_root,
        project_id=normalized_project,
        proposal=compatibility_payload,
        approval=compatibility_approval,
        database_path=database_path,
        manifest_path=manifest_path,
    )
    direct_material = {
        key: value
        for key, value in legacy_receipt.items()
        if key
        not in {
            "schema",
            "status",
            "receipt_digest",
            "proposal_id",
            "proposal_digest",
            "approval_evidence_ref",
        }
    }
    direct_material["instruction_ref"] = normalized_instruction_ref
    return {
        "schema": DIRECT_LIFECYCLE_RECEIPT_SCHEMA,
        "status": "PROJECT_RUNTIME_LIFECYCLE_APPLIED",
        **direct_material,
        "receipt_digest": _digest(direct_material),
    }



def _proposal(expected_project_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PROPOSAL_INVALID",
            "proposal must be an object",
        )
    required = {
        "schema",
        "project_id",
        "release_id",
        "mode",
        "release_database_sha256",
        "plan",
        "approval",
        "execution_owner",
        "effects",
        "next_operation",
        "proposal_digest",
        "proposal_id",
        "status",
    }
    if not required.issubset(value) or set(value) - (required | {"created_at"}):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PROPOSAL_INVALID",
            "proposal fields are invalid",
        )
    if (
        value.get("schema") != "universe.project-release-proposal.v1"
        or value.get("project_id") != _text(expected_project_id, "project_id")
        or value.get("mode") != "MASTER"
        or value.get("approval") != "REQUIRED"
        or value.get("execution_owner") != "PROJECT_HOST"
        or value.get("status") != "PROJECT_RELEASE_PROPOSAL_READY"
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PROPOSAL_INVALID",
            "proposal contract is invalid",
        )
    plan = value.get("plan")
    if (
        not isinstance(plan, Mapping)
        or plan.get("schema") != LIFECYCLE_PLAN_SCHEMA
        or plan.get("status") != "PROJECT_RUNTIME_LIFECYCLE_PLAN_READY"
        or plan.get("project_id") != value.get("project_id")
        or plan.get("release_id") != value.get("release_id")
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PROPOSAL_INVALID",
            "proposal lifecycle plan is invalid",
        )
    plan_material = dict(plan)
    plan_status = plan_material.pop("status", None)
    plan_digest = plan_material.pop("plan_digest", None)
    if (
        plan_status != "PROJECT_RUNTIME_LIFECYCLE_PLAN_READY"
        or plan_digest != _digest(plan_material)
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PLAN_DIGEST_MISMATCH",
            "proposal lifecycle plan digest is invalid",
        )
    proposal_material = {
        key: value[key]
        for key in (
            "schema",
            "project_id",
            "release_id",
            "mode",
            "release_database_sha256",
            "plan",
            "approval",
            "execution_owner",
            "effects",
            "next_operation",
        )
    }
    proposal_digest = _digest(proposal_material)
    if (
        value.get("proposal_digest") != proposal_digest
        or value.get("proposal_id")
        != "release_proposal_" + proposal_digest[:20]
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_PROPOSAL_DIGEST_MISMATCH",
            "proposal digest or identifier is invalid",
        )
    return dict(value)


def _approval(proposal: Mapping[str, Any], value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "status",
        "project_id",
        "proposal_id",
        "proposal_digest",
        "release_id",
        "plan_digest",
        "evidence_ref",
    }:
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_APPROVAL_INVALID",
            "approval fields are invalid",
        )
    normalized = {
        "schema": _text(value.get("schema"), "approval.schema"),
        "status": _text(value.get("status"), "approval.status"),
        "project_id": _text(value.get("project_id"), "approval.project_id"),
        "proposal_id": _text(value.get("proposal_id"), "approval.proposal_id"),
        "proposal_digest": _sha256(
            value.get("proposal_digest"),
            "approval.proposal_digest",
        ),
        "release_id": _text(value.get("release_id"), "approval.release_id"),
        "plan_digest": _sha256(
            value.get("plan_digest"),
            "approval.plan_digest",
        ),
        "evidence_ref": _text(value.get("evidence_ref"), "approval.evidence_ref"),
    }
    expected = {
        "schema": RELEASE_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "project_id": proposal["project_id"],
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "release_id": proposal["release_id"],
        "plan_digest": proposal["plan"]["plan_digest"],
    }
    if any(normalized[key] != expected[key] for key in expected):
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_APPROVAL_MISMATCH",
            "approval does not match the exact release proposal",
        )
    return normalized



def _project_root(value: Path) -> Path:
    root = value.expanduser().resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ProjectReleaseApplyError(
            "PROJECT_ROOT_UNAVAILABLE",
            "project root must be an existing real directory",
        )
    return root


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectReleaseApplyError(
            field.upper().replace(".", "_") + "_REQUIRED",
            f"{field} must be non-empty text",
        )
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProjectReleaseApplyError(
            field.upper().replace(".", "_") + "_INVALID",
            f"{field} must be a lowercase SHA-256",
        )
    return normalized


def _commit(value: Any) -> str:
    normalized = _text(value, "source_commit").lower()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProjectReleaseApplyError(
            "SOURCE_COMMIT_INVALID",
            "source_commit must be a full Git object ID",
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
