from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

from release_runtime import ReleaseRuntime, ReleaseRuntimeError
from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


RELEASE_APPROVAL_SCHEMA = "universe.project-release-approval.v1"
RELEASE_APPLY_RECEIPT_SCHEMA = "universe.project-release-apply-receipt.v1"
LIFECYCLE_PLAN_SCHEMA = "universe.project-runtime-lifecycle-plan.v1"
INSTALLATION_MANIFEST_PATH = (
    ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
)
HOST_LIFECYCLE_SOURCE_PATH = (
    ".ai/distribution/context_management_runtime_pack/host_fresh_install.py"
)
PROJECT_INSTALLER_SOURCE_PATH = (
    ".ai/distribution/context_management_runtime_pack/project_runtime_installer.py"
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
    native_runner: Any = run_native_cli,
    timeout_seconds: float = 900,
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
            current_plan = plan_project_release_lifecycle(
                project_root=root,
                project_id=project_id,
                release_id=runtime.release_id,
                source_commit=runtime.metadata["source_commit"],
            )
            if current_plan != normalized_proposal["plan"]:
                raise ProjectReleaseApplyError(
                    "PROJECT_RELEASE_PROPOSAL_STALE",
                    "project lifecycle state changed after the proposal was created",
                )

            with tempfile.TemporaryDirectory(
                prefix="universe-project-release-"
            ) as temporary:
                working = Path(temporary)
                bundle = working / "source-bundle"
                bundle.mkdir()
                bundle_evidence = runtime.materialize_source_bundle(bundle)
                runner_root = working / "runner"
                runner_root.mkdir()
                host_script = runner_root / "host_fresh_install.py"
                installer_script = runner_root / "project_runtime_installer.py"
                host_script.write_bytes(
                    runtime.release_file(HOST_LIFECYCLE_SOURCE_PATH).content
                )
                installer_script.write_bytes(
                    runtime.release_file(PROJECT_INSTALLER_SOURCE_PATH).content
                )
                lifecycle_request = _lifecycle_request(
                    project_root=root,
                    project_id=project_id,
                    proposal=normalized_proposal,
                    approval=normalized_approval,
                    bundle=bundle,
                )
                request_path = working / "lifecycle-request.json"
                request_path.write_text(
                    json.dumps(lifecycle_request, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                result = native_runner(
                    NativeCliRequest(
                        executable=Path(sys.executable),
                        arguments=(
                            str(host_script),
                            "--request",
                            str(request_path),
                        ),
                        cwd=working,
                        timeout_seconds=timeout_seconds,
                        max_output_chars=2_000_000,
                    )
                )
    except ProjectReleaseApplyError:
        raise
    except (OSError, ReleaseRuntimeError) as error:
        raise ProjectReleaseApplyError(
            "PROJECT_RELEASE_ARTIFACT_INVALID",
            str(error),
        ) from error

    lifecycle_result = _native_result(result)
    if (
        result.status != "COMPLETED"
        or lifecycle_result.get("result") != "PASS"
        or lifecycle_result.get("repository_runtime") != "VERIFIED"
    ):
        lifecycle_error = lifecycle_result.get("error")
        error_code = (
            str(lifecycle_error.get("code"))
            if isinstance(lifecycle_error, Mapping) and lifecycle_error.get("code")
            else "PROJECT_RUNTIME_LIFECYCLE_FAILED"
        )
        raise ProjectReleaseApplyError(
            error_code,
            "Project Runtime lifecycle did not complete",
            lifecycle_result=lifecycle_result,
        )
    source_result = lifecycle_result.get("source")
    if (
        lifecycle_result.get("target") != str(root)
        or lifecycle_result.get("operation")
        != normalized_proposal["plan"]["operation"]
        or lifecycle_result.get("user_command")
        != normalized_proposal["plan"]["user_command"]
        or not isinstance(source_result, Mapping)
        or source_result.get("provider") != "universe-release-db"
        or source_result.get("binding") != "provider-attested"
        or source_result.get("commit")
        != normalized_proposal["plan"]["source_commit"]
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_LIFECYCLE_RESULT_MISMATCH",
            "Project Runtime lifecycle result does not match the approved proposal",
            lifecycle_result=lifecycle_result,
        )
    boot_handoff = lifecycle_result.get("boot_handoff")
    if (
        not isinstance(boot_handoff, Mapping)
        or boot_handoff.get("status") != "READY_FOR_BOOT"
    ):
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_BOOT_HANDOFF_INVALID",
            "Project Runtime lifecycle did not produce READY_FOR_BOOT",
            lifecycle_result=lifecycle_result,
        )

    validate_result = lifecycle_result.get("validate")
    permit_receipt = lifecycle_result.get("permit_receipt")
    material = {
        "project_id": normalized_proposal["project_id"],
        "proposal_id": normalized_proposal["proposal_id"],
        "proposal_digest": normalized_proposal["proposal_digest"],
        "release_id": normalized_proposal["release_id"],
        "plan_digest": normalized_proposal["plan"]["plan_digest"],
        "approval_evidence_ref": normalized_approval["evidence_ref"],
        "operation": lifecycle_result["operation"],
        "user_command": lifecycle_result["user_command"],
        "repository_runtime": lifecycle_result["repository_runtime"],
        "validation_id": (
            str(validate_result.get("validation_id", "UNKNOWN"))
            if isinstance(validate_result, Mapping)
            else "UNKNOWN"
        ),
        "boot_handoff": "READY_FOR_BOOT",
        "bundle_manifest_sha256": bundle_evidence["bundle_manifest_sha256"],
        "permit_receipt_id": (
            str(permit_receipt.get("receipt_id", "UNKNOWN"))
            if isinstance(permit_receipt, Mapping)
            else "UNKNOWN"
        ),
    }
    return {
        "schema": RELEASE_APPLY_RECEIPT_SCHEMA,
        "status": "PROJECT_RELEASE_APPLIED",
        **material,
        "receipt_digest": _digest(material),
        "lifecycle_result": lifecycle_result,
    }


def _lifecycle_request(
    *,
    project_root: Path,
    project_id: str,
    proposal: Mapping[str, Any],
    approval: Mapping[str, str],
    bundle: Path,
) -> dict[str, Any]:
    operation = str(proposal["plan"]["operation"])
    request: dict[str, Any] = {
        "schema": "ai-career.host-runtime-lifecycle-request.v1",
        "operation": operation,
        "source": {
            "kind": "source-bundle",
            "path": str(bundle.resolve()),
            "commit": proposal["plan"]["source_commit"],
        },
        "target": str(project_root),
        "project": project_id,
        "node": project_id,
        "mode": "MASTER",
        "host": "universe-project-lifecycle-host",
        "commander_surface": "UNIVERSE_UI",
        "execution_surface": "LOCAL_PROJECT_LIFECYCLE_HOST",
        "repository_location": str(project_root),
        "approval": {
            "status": "APPROVED",
            "operation": operation,
            "target": str(project_root),
            "source_commit": proposal["plan"]["source_commit"],
            "project": project_id,
            "node": project_id,
            "mode": "MASTER",
            "evidence_ref": approval["evidence_ref"],
        },
    }
    if operation == "RUNTIME_UPDATE":
        force_collisions: list[str] = []
        request["force_collisions"] = force_collisions
        request["approval"]["force_collisions_sha256"] = hashlib.sha256(
            (json.dumps(force_collisions, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
        ).hexdigest()
    return request


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


def _native_result(result: NativeCliResult) -> dict[str, Any]:
    if result.stdout_truncated:
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_LIFECYCLE_OUTPUT_TRUNCATED",
            "Project Runtime lifecycle output exceeded the Host bound",
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_LIFECYCLE_RESULT_INVALID",
            result.stderr[:500] or str(error),
        ) from error
    if not isinstance(payload, dict):
        raise ProjectReleaseApplyError(
            "PROJECT_RUNTIME_LIFECYCLE_RESULT_INVALID",
            "Project Runtime lifecycle result must be an object",
        )
    return payload


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
