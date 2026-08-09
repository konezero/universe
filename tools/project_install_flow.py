#!/usr/bin/env python3
"""Plan and verify fresh-project Runtime installation through an adapter.

Universe owns the plan and postconditions. The caller-supplied lifecycle
adapter is the only component allowed to materialize Runtime files.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Protocol


INSTALL_FLOW_SCHEMA = "universe.project-install-flow.v1"
INSTALL_PLAN_SCHEMA = "universe.project-install-plan.v1"
INSTALL_REQUEST_SCHEMA = "universe.project-install-adapter-request.v1"
INSTALL_RESULT_SCHEMA = "universe.project-install-result.v1"
UNIVERSE_ATTACHED = "UNIVERSE_ATTACHED"
PROJECT_STANDALONE = "PROJECT_STANDALONE"
INSTALL_MODES = frozenset({UNIVERSE_ATTACHED, PROJECT_STANDALONE})
PREFER_BOOT_VALUES = frozenset({"HOST", "STANDALONE"})
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
INSTALLATION_MANIFEST = Path(
    ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
)
REQUIRED_INSTALL_ARTIFACTS = (
    INSTALLATION_MANIFEST,
    Path(".ai/runtime/project_instance/VERSION_MANIFEST.md"),
    Path(".ai/runtime/project_instance/project_anchor.md"),
    Path(".ai/runtime/project_instance/validation/latest.md"),
)
PLAN_FIELDS = (
    "schema",
    "project_id",
    "target_root",
    "install_mode",
    "prefer_boot",
    "source",
    "operation",
    "installed_runtime",
    "preservation",
    "read_only_preflight",
    "candidate_execution",
)


class LifecycleAdapter(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ProjectInstallFlowError(ValueError):
    """A fail-closed plan, adapter, or postcondition error."""

    def __init__(self, code: str, message: str, *, state: str = "BLOCKED") -> None:
        super().__init__(message)
        self.code = code
        self.state = state


def plan_project_install_flow(
    *,
    project_root: Path,
    project_id: str,
    install_mode: str = UNIVERSE_ATTACHED,
    source_commit: str,
    prefer_boot: str = "HOST",
) -> dict[str, Any]:
    """Build a read-only plan for a fresh or existing project."""

    root = _project_root(project_root)
    normalized_project_id = _project_id(project_id)
    normalized_mode = _install_mode(install_mode)
    normalized_prefer_boot = _prefer_boot(prefer_boot)
    normalized_commit = _commit(source_commit)
    installed = _inspect_existing_install(root, normalized_project_id)
    preservation = _preservation_snapshot(root)
    blocked_reason = installed.get("blocked_reason")
    state = "BLOCKED" if blocked_reason else "PLAN_READY"
    state_history = ["PREFLIGHT", state]

    material = {
        "schema": INSTALL_PLAN_SCHEMA,
        "project_id": normalized_project_id,
        "target_root": str(root),
        "install_mode": normalized_mode,
        "prefer_boot": normalized_prefer_boot,
        "source": {
            "kind": "ai-career",
            "binding": "immutable-commit",
            "commit": normalized_commit,
        },
        "operation": installed["operation"],
        "installed_runtime": installed,
        "preservation": preservation,
        "read_only_preflight": True,
        "candidate_execution": "FORBIDDEN",
    }
    result = {
        **material,
        "plan_digest": _digest(material),
        "state": state,
        "state_history": state_history,
        "status": (
            "PROJECT_INSTALL_PLAN_BLOCKED"
            if blocked_reason
            else "PROJECT_INSTALL_PLAN_READY"
        ),
    }
    if blocked_reason:
        result["blocked_reason"] = blocked_reason
    return result


def preflight_project_install_flow(**kwargs: Any) -> dict[str, Any]:
    """Explicit read-only alias used by fresh-clone callers."""

    return plan_project_install_flow(**kwargs)


def apply_project_install_flow(
    *,
    plan: Mapping[str, Any],
    approved_plan_digest: str,
    lifecycle_adapter: LifecycleAdapter
    | Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run the adapter and verify its installation postconditions.

    This function only reads the project. It never creates ``.ai`` or any
    other Runtime file.
    """

    normalized_plan = _validate_plan(plan)
    if _digest_text(approved_plan_digest, "approved_plan_digest") != normalized_plan["plan_digest"]:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PLAN_APPROVAL_STALE",
            "approved plan digest does not match the plan",
        )
    if normalized_plan["state"] != "PLAN_READY":
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PLAN_BLOCKED",
            "blocked plan cannot be applied",
        )

    root = Path(normalized_plan["target_root"])
    current_plan = plan_project_install_flow(
        project_root=root,
        project_id=normalized_plan["project_id"],
        install_mode=normalized_plan["install_mode"],
        source_commit=normalized_plan["source"]["commit"],
        prefer_boot=normalized_plan["prefer_boot"],
    )
    if current_plan["plan_digest"] != normalized_plan["plan_digest"]:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PLAN_STALE",
            "project state changed after the plan was created",
        )

    request = _adapter_request(normalized_plan)
    response = _invoke_adapter(lifecycle_adapter, request)
    _validate_adapter_result(response, normalized_plan)
    artifacts = verify_installation_artifacts(
        root,
        project_id=normalized_plan["project_id"],
        source_commit=normalized_plan["source"]["commit"],
    )
    managed_paths = _managed_paths(response, root)
    changed_local = _changed_preserved_files(
        root,
        normalized_plan["preservation"]["files"],
        managed_paths,
    )
    if changed_local:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_LOCAL_FILES_CHANGED",
            "lifecycle adapter changed pre-existing unmanaged files: "
            + ", ".join(changed_local),
        )
    return {
        "schema": INSTALL_FLOW_SCHEMA,
        "status": "PROJECT_INSTALL_READY_FOR_BOOT",
        "state": "READY_FOR_BOOT",
        "state_history": [
            "PREFLIGHT",
            "PLAN_READY",
            "APPLYING",
            "ARTIFACTS_VERIFIED",
            "READY_FOR_BOOT",
        ],
        "project_id": normalized_plan["project_id"],
        "target_root": normalized_plan["target_root"],
        "install_mode": normalized_plan["install_mode"],
        "prefer_boot": normalized_plan["prefer_boot"],
        "operation": normalized_plan["operation"],
        "source_commit": normalized_plan["source"]["commit"],
        "plan_digest": normalized_plan["plan_digest"],
        "artifacts": artifacts,
        "preserved_file_count": len(normalized_plan["preservation"]["files"]),
        "managed_path_count": len(managed_paths),
        "adapter_status": "PASS",
        "repository_runtime": "VERIFIED",
        "boot_handoff": "READY_FOR_BOOT",
    }


def verify_installation_artifacts(
    project_root: Path,
    *,
    project_id: str,
    source_commit: str,
) -> dict[str, Any]:
    """Verify installed artifacts and the live source commit."""

    root = _project_root(project_root)
    expected_project = _project_id(project_id)
    expected_commit = _commit(source_commit)
    missing: list[str] = []
    artifact_rows: list[dict[str, Any]] = []
    for relative in REQUIRED_INSTALL_ARTIFACTS:
        target = root / relative
        if not target.is_file() or target.is_symlink():
            missing.append(relative.as_posix())
            continue
        content = target.read_bytes()
        artifact_rows.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    if missing:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_ARTIFACTS_MISSING",
            "required installation artifacts are missing: " + ", ".join(missing),
        )

    manifest_path = root / INSTALLATION_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_MANIFEST_INVALID",
            str(error),
        ) from error
    if not isinstance(manifest, Mapping):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_MANIFEST_INVALID",
            "installation manifest must be an object",
        )
    if manifest.get("schema") != "ai-career.project-runtime-installation.v1":
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_MANIFEST_INVALID",
            "installation manifest schema is unsupported",
        )
    installation = manifest.get("installation")
    installed_project = (
        installation.get("project")
        if isinstance(installation, Mapping)
        else manifest.get("project")
    )
    if installed_project != expected_project:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PROJECT_MISMATCH",
            "installation manifest project does not match the plan",
        )
    source = manifest.get("source")
    installed_commit = (
        source.get("commit")
        if isinstance(source, Mapping)
        else manifest.get("source_commit")
    )
    if installed_commit != expected_commit:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_SOURCE_COMMIT_MISMATCH",
            "installation manifest source commit does not match ai-career source",
        )
    return {
        "manifest": INSTALLATION_MANIFEST.as_posix(),
        "project_id": expected_project,
        "source_commit": expected_commit,
        "required_count": len(REQUIRED_INSTALL_ARTIFACTS),
        "artifacts": artifact_rows,
    }


def _validate_adapter_result(response: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    if response.get("schema") not in {None, INSTALL_RESULT_SCHEMA}:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_ADAPTER_RESULT_INVALID",
            "lifecycle adapter result schema is unsupported",
        )
    if response.get("result") != "PASS":
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_ADAPTER_FAILED",
            "lifecycle adapter did not return PASS",
        )
    if response.get("repository_runtime") != "VERIFIED":
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_RUNTIME_UNVERIFIED",
            "lifecycle adapter did not verify the repository Runtime",
        )
    if response.get("target") != plan["target_root"]:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_TARGET_MISMATCH",
            "lifecycle adapter target does not match the plan",
        )
    if response.get("operation") != plan["operation"]:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_OPERATION_MISMATCH",
            "lifecycle adapter operation does not match the plan",
        )
    if response.get("install_mode") != plan["install_mode"]:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_MODE_MISMATCH",
            "lifecycle adapter install mode does not match the plan",
        )
    source = response.get("source")
    live_commit = source.get("commit") if isinstance(source, Mapping) else None
    if live_commit != plan["source"]["commit"]:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_LIVE_SOURCE_MISMATCH",
            "lifecycle adapter live source commit does not match the plan",
        )
    for field in ("source_commit",):
        if field in response and response[field] != plan["source"]["commit"]:
            raise ProjectInstallFlowError(
                "PROJECT_INSTALL_LIVE_SOURCE_MISMATCH",
                f"lifecycle adapter {field} does not match the plan",
            )
    validation = response.get("validate")
    if (
        isinstance(validation, Mapping)
        and "source_commit" in validation
        and validation["source_commit"] != plan["source"]["commit"]
    ):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_LIVE_SOURCE_MISMATCH",
            "lifecycle validation source commit does not match the plan",
        )
    boot_handoff = response.get("boot_handoff")
    if (
        not isinstance(boot_handoff, Mapping)
        or boot_handoff.get("status") != "READY_FOR_BOOT"
    ):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_FALSE_READY",
            "lifecycle adapter did not produce READY_FOR_BOOT",
        )


def _adapter_request(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": INSTALL_REQUEST_SCHEMA,
        "state": "APPLYING",
        "project_id": plan["project_id"],
        "target": plan["target_root"],
        "install_mode": plan["install_mode"],
        "prefer_boot": plan["prefer_boot"],
        "operation": plan["operation"],
        "source": {
            "kind": "ai-career",
            "binding": "immutable-commit",
            "commit": plan["source"]["commit"],
        },
        "preservation": plan["preservation"],
        "runtime_file_owner": "LIFECYCLE_ADAPTER",
        "universe_flow": "MUST_NOT_CREATE_RUNTIME_FILES",
    }


def _invoke_adapter(
    adapter: LifecycleAdapter | Callable[[Mapping[str, Any]], Mapping[str, Any]],
    request: Mapping[str, Any],
) -> Mapping[str, Any]:
    if callable(adapter):
        response = adapter(request)
    else:
        method = getattr(adapter, "apply", None)
        if not callable(method):
            raise ProjectInstallFlowError(
                "PROJECT_INSTALL_ADAPTER_INVALID",
                "lifecycle adapter must be callable or expose apply()",
            )
        response = method(request)
    if not isinstance(response, Mapping):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_ADAPTER_RESULT_INVALID",
            "lifecycle adapter must return a mapping",
        )
    return response


def _validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ProjectInstallFlowError("PROJECT_INSTALL_PLAN_INVALID", "plan must be an object")
    required = set(PLAN_FIELDS) | {"plan_digest", "state", "state_history", "status"}
    if not required.issubset(plan):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PLAN_INVALID",
            "plan is missing required fields",
        )
    material = {field: plan[field] for field in PLAN_FIELDS}
    if plan.get("schema") != INSTALL_PLAN_SCHEMA:
        raise ProjectInstallFlowError("PROJECT_INSTALL_PLAN_INVALID", "plan schema is unsupported")
    if plan.get("plan_digest") != _digest(material):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PLAN_DIGEST_MISMATCH",
            "plan digest is invalid",
        )
    if plan.get("read_only_preflight") is not True:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_PLAN_INVALID",
            "plan must originate from a read-only preflight",
        )
    return dict(plan)


def _inspect_existing_install(root: Path, project_id: str) -> dict[str, Any]:
    ai_root = root / ".ai"
    if not ai_root.exists():
        return {
            "state": "ABSENT",
            "operation": "OS_INSTALL",
            "source_commit": "NONE",
            "manifest_sha256": "NONE",
        }
    if ai_root.is_symlink() or not ai_root.is_dir():
        return {
            "state": "INVALID",
            "operation": "OS_UPDATE",
            "source_commit": "UNKNOWN",
            "manifest_sha256": "NONE",
            "blocked_reason": "PROJECT_AI_ROOT_INVALID",
        }
    manifest_path = root / INSTALLATION_MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return {
            "state": "PARTIAL",
            "operation": "OS_UPDATE",
            "source_commit": "UNKNOWN",
            "manifest_sha256": "NONE",
            "blocked_reason": "PROJECT_RUNTIME_INSTALLATION_INCOMPLETE",
        }
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "state": "INVALID",
            "operation": "OS_UPDATE",
            "source_commit": "UNKNOWN",
            "manifest_sha256": "NONE",
            "blocked_reason": "PROJECT_RUNTIME_MANIFEST_INVALID",
        }
    if not isinstance(value, Mapping):
        return {
            "state": "INVALID",
            "operation": "OS_UPDATE",
            "source_commit": "UNKNOWN",
            "manifest_sha256": "NONE",
            "blocked_reason": "PROJECT_RUNTIME_MANIFEST_INVALID",
        }
    installation = value.get("installation")
    installed_project = (
        installation.get("project")
        if isinstance(installation, Mapping)
        else value.get("project")
    )
    if installed_project not in {None, project_id}:
        return {
            "state": "INVALID",
            "operation": "OS_UPDATE",
            "source_commit": "UNKNOWN",
            "manifest_sha256": "NONE",
            "blocked_reason": "PROJECT_RUNTIME_IDENTITY_MISMATCH",
        }
    source = value.get("source")
    installed_commit = (
        source.get("commit")
        if isinstance(source, Mapping)
        else value.get("source_commit", "UNKNOWN")
    )
    return {
        "state": "MANAGED",
        "operation": "OS_UPDATE",
        "source_commit": installed_commit,
        "manifest_sha256": _sha256_file(manifest_path),
    }


def _preservation_snapshot(root: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if name != ".git" and not (current_path / name).is_symlink()
        ]
        for name in names:
            path = current_path / name
            files[path.relative_to(root).as_posix()] = _fingerprint(path)
    return {
        "file_count": len(files),
        "files": dict(sorted(files.items())),
        "snapshot_digest": _digest(files),
    }


def _changed_preserved_files(
    root: Path,
    files: Mapping[str, Any],
    managed_paths: set[str],
) -> list[str]:
    changed: list[str] = []
    for relative, expected in files.items():
        if relative in managed_paths:
            continue
        path = root / Path(relative)
        actual = _fingerprint(path) if path.exists() or path.is_symlink() else "ABSENT"
        if actual != expected:
            changed.append(relative)
    return sorted(changed)


def _managed_paths(response: Mapping[str, Any], root: Path) -> set[str]:
    raw = response.get("managed_paths", [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_MANAGED_PATHS_INVALID",
            "lifecycle adapter managed_paths must be a list of strings",
        )
    result: set[str] = set()
    for item in raw:
        if "\\" in item or ":" in item:
            raise ProjectInstallFlowError(
                "PROJECT_INSTALL_MANAGED_PATHS_INVALID",
                "managed path must use a relative POSIX path",
            )
        path = Path(item)
        if path.is_absolute():
            try:
                path = path.resolve(strict=False).relative_to(root.resolve(strict=False))
            except ValueError as error:
                raise ProjectInstallFlowError(
                    "PROJECT_INSTALL_MANAGED_PATHS_INVALID",
                    "managed path is outside the project root",
                ) from error
        normalized = path.as_posix()
        pure = PurePosixPath(normalized)
        if (
            not normalized
            or normalized == "."
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or not normalized.startswith(".ai/")
        ):
            raise ProjectInstallFlowError(
                "PROJECT_INSTALL_MANAGED_PATHS_INVALID",
                "managed path must be a normalized path under .ai",
            )
        result.add(normalized)
    return result


def _project_root(value: Path) -> Path:
    candidate = value.expanduser()
    if candidate.is_symlink():
        raise ProjectInstallFlowError(
            "PROJECT_ROOT_INVALID",
            "project root must not be a symlink",
        )
    root = candidate.resolve(strict=True)
    if not root.is_dir():
        raise ProjectInstallFlowError(
            "PROJECT_ROOT_INVALID",
            "project root must be an existing real directory",
        )
    return root


def _project_id(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID_PATTERN.fullmatch(value.strip()):
        raise ProjectInstallFlowError(
            "PROJECT_ID_INVALID",
            "project_id must use letters, digits, dot, underscore, or hyphen",
        )
    return value.strip()


def _install_mode(value: Any) -> str:
    normalized = _required_text(value, "install_mode").upper()
    if normalized not in INSTALL_MODES:
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_MODE_INVALID",
            "install_mode must be UNIVERSE_ATTACHED or PROJECT_STANDALONE",
        )
    return normalized


def _prefer_boot(value: Any) -> str:
    normalized = _required_text(value, "prefer_boot").upper()
    if normalized not in PREFER_BOOT_VALUES:
        raise ProjectInstallFlowError(
            "PROJECT_PREFER_BOOT_INVALID",
            "prefer_boot must be HOST or STANDALONE",
        )
    return normalized


def _commit(value: Any) -> str:
    normalized = _required_text(value, "source_commit").lower()
    if not COMMIT_PATTERN.fullmatch(normalized):
        raise ProjectInstallFlowError(
            "PROJECT_SOURCE_COMMIT_INVALID",
            "source_commit must be a full 40-character immutable Git object ID",
        )
    return normalized


def _digest_text(value: Any, field: str) -> str:
    normalized = _required_text(value, field).lower()
    if not DIGEST_PATTERN.fullmatch(normalized):
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_DIGEST_INVALID",
            f"{field} must be a 64-character SHA-256 digest",
        )
    return normalized


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectInstallFlowError(
            "PROJECT_INSTALL_FIELD_INVALID",
            f"{field} must be non-empty text",
        )
    return value.strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> str:
    if path.is_symlink():
        return "SYMLINK:" + os.readlink(path)
    if path.is_file():
        return "FILE:" + _sha256_file(path)
    return "OTHER"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Plan a Universe project install")
    commands = cli.add_subparsers(dest="command", required=True)
    for command in ("plan", "preflight"):
        sub = commands.add_parser(command)
        sub.add_argument("--project-root", type=Path, required=True)
        sub.add_argument("--project-id", required=True)
        sub.add_argument("--source-commit", required=True)
        sub.add_argument(
            "--install-mode",
            default=UNIVERSE_ATTACHED,
            choices=sorted(INSTALL_MODES),
        )
        sub.add_argument(
            "--prefer-boot",
            default="HOST",
            choices=sorted(PREFER_BOOT_VALUES),
        )
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = plan_project_install_flow(
            project_root=args.project_root,
            project_id=args.project_id,
            install_mode=args.install_mode,
            source_commit=args.source_commit,
            prefer_boot=args.prefer_boot,
        )
    except ProjectInstallFlowError as error:
        result = {
            "schema": INSTALL_FLOW_SCHEMA,
            "status": "PROJECT_INSTALL_PLAN_FAILED",
            "state": error.state,
            "error": {"code": error.code, "message": str(error)},
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["state"] == "PLAN_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
