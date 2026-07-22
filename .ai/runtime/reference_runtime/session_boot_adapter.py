"""Host transport for the deterministic Session Boot runtime."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .anchor_session_memory_adapter import (
    AnchorSessionMemoryHostServer,
    call_host_adapter,
)
from .session_boot_runtime import (
    INSTALLATION_MANIFEST_REF,
    SessionBootCoordinates,
    SessionBootError,
    build_session_boot_artifacts,
)


INSTALLER_REF = ".ai/runtime/tools/project_runtime_installer.py"
VALIDATION_REF = ".ai/runtime/project_instance/validation/latest.md"
SESSION_STATE_REF = ".ai/runtime/state/session.md"
RUNTIME_FRAME_REF = ".ai/runtime/state/current_anchor_frame.md"
RUNTIME_CLI_REF = ".ai/runtime/reference_runtime/cli.py"


@dataclass(frozen=True)
class PreparedSessionBoot:
    """A running loopback server and its raw boot result."""

    server: AnchorSessionMemoryHostServer
    result: dict[str, Any]


def read_project_runtime_status(
    repo_root: Path, *, timeout_seconds: int = 60
) -> dict[str, Any]:
    """Run the installed distribution status command without repository writes."""

    root = repo_root.resolve()
    installer = root / Path(INSTALLER_REF)
    if not installer.is_file():
        raise SessionBootError(
            "SESSION_BOOT_EXECUTOR_UNAVAILABLE",
            f"installed project runtime status tool is missing: {INSTALLER_REF}",
        )
    try:
        completed = subprocess.run(
            [sys.executable, str(installer), "status", "--target", str(root)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SessionBootError(
            "PROJECT_RUNTIME_STATUS_UNAVAILABLE", str(error)
        ) from error
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SessionBootError(
            "PROJECT_RUNTIME_STATUS_INVALID",
            "installed status command did not return one JSON object",
        ) from error
    if not isinstance(payload, dict):
        raise SessionBootError(
            "PROJECT_RUNTIME_STATUS_INVALID",
            "installed status command result must be an object",
        )
    if completed.returncode != 0:
        error_code = payload.get("error_code", "PROJECT_RUNTIME_STATUS_FAILED")
        raise SessionBootError(str(error_code), json.dumps(payload, sort_keys=True))
    return payload


def _markdown_field(text: str, label: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(label)}:\s*(.*?)\s*$", text)
    return match.group(1) if match is not None else None


def _read_markdown(path: Path, *, error_code: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SessionBootError(error_code, str(error)) from error


def read_project_runtime_boot_evidence(repo_root: Path) -> dict[str, Any]:
    """Read installed boot evidence without invoking full Runtime validation."""
    root = repo_root.resolve()
    manifest = read_installation_manifest(root)
    source = manifest.get("source")
    installation = manifest.get("installation")
    if not isinstance(source, Mapping) or not isinstance(installation, Mapping):
        raise SessionBootError(
            "SESSION_BOOT_MANIFEST_INVALID",
            "installation manifest source or installation coordinate is invalid",
        )
    required_paths = (INSTALLER_REF, RUNTIME_CLI_REF, SESSION_STATE_REF, RUNTIME_FRAME_REF)
    missing = [path for path in required_paths if not (root / path).is_file()]
    if missing:
        raise SessionBootError(
            "SESSION_BOOT_SURFACE_MISSING",
            json.dumps({"paths": missing}, sort_keys=True),
        )

    validation_text = _read_markdown(
        root / VALIDATION_REF,
        error_code="SESSION_BOOT_VALIDATION_UNAVAILABLE",
    )
    validation_result = _markdown_field(validation_text, "Result") or "UNKNOWN"
    repository_runtime = (
        _markdown_field(validation_text, "Repository Runtime") or "UNKNOWN"
    )
    validation_id = _markdown_field(validation_text, "Validation ID") or "UNKNOWN"
    validation_commit = _markdown_field(validation_text, "Source Commit") or "UNKNOWN"
    source_commit = source.get("commit")
    if (
        validation_result != "PASS"
        or repository_runtime != "VERIFIED"
        or not isinstance(source_commit, str)
        or validation_commit != source_commit
    ):
        raise SessionBootError(
            "SESSION_BOOT_VALIDATION_REQUIRED",
            json.dumps(
                {
                    "result": validation_result,
                    "repository_runtime": repository_runtime,
                    "validation_commit": validation_commit,
                    "manifest_commit": source_commit,
                },
                sort_keys=True,
            ),
        )

    session_text = _read_markdown(
        root / SESSION_STATE_REF,
        error_code="SESSION_BOOT_STATE_UNAVAILABLE",
    )
    return {
        "project": installation.get("project", "UNKNOWN"),
        "node": installation.get("node", "UNKNOWN"),
        "mode": installation.get("mode", "UNKNOWN"),
        "role": installation.get("role", "UNKNOWN"),
        "result": "PASS",
        "repository_runtime": "VERIFIED",
        "session_runtime": _markdown_field(session_text, "Session Runtime")
        or "UNKNOWN",
        "session_initialization": _markdown_field(
            session_text, "Session Initialization"
        )
        or "UNKNOWN",
        "session_preparation_state": (
            _markdown_field(session_text, "Session Preparation State")
            or "UNKNOWN"
        ),
        "executable_runtime_currentness": (
            _markdown_field(session_text, "Executable Runtime Currentness")
            or _markdown_field(session_text, "Currentness")
            or "UNKNOWN"
        ),
        "authority": _markdown_field(session_text, "Authority") or "UNKNOWN",
        "authority_ref": _markdown_field(session_text, "Authority Ref") or "UNKNOWN",
        "execution_assignment": _markdown_field(
            session_text, "Execution Assignment"
        )
        or "UNKNOWN",
        "assignment_ref": _markdown_field(session_text, "Assignment Ref")
        or "UNKNOWN",
        "source_repository": source.get("repository", "UNKNOWN"),
        "source_commit": source_commit,
        "source_provider": source.get("provider", "UNKNOWN"),
        "source_binding": source.get("binding", "UNKNOWN"),
        "live_validation": {
            "result": validation_result,
            "repository_runtime": repository_runtime,
            "validation_id": validation_id,
        },
    }


def read_installation_manifest(repo_root: Path) -> dict[str, Any]:
    path = repo_root.resolve() / Path(INSTALLATION_MANIFEST_REF)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SessionBootError(
            "SESSION_BOOT_MANIFEST_UNAVAILABLE", str(error)
        ) from error
    if not isinstance(payload, dict):
        raise SessionBootError(
            "SESSION_BOOT_MANIFEST_INVALID",
            "installation manifest root must be an object",
        )
    return payload


def prepare_session_boot_server(
    *,
    repo_root: Path,
    coordinates: SessionBootCoordinates,
    port: int = 0,
    token: str = "",
    observed_at: str = "",
    project_status: Mapping[str, Any] | None = None,
    installation_manifest: Mapping[str, Any] | None = None,
) -> PreparedSessionBoot:
    """Validate, assemble, activate, and expose one process-local boot image."""

    root = repo_root.resolve()
    if not root.is_dir():
        raise SessionBootError(
            "SESSION_BOOT_REPOSITORY_UNAVAILABLE",
            f"repository root is not a directory: {root}",
        )
    status_payload = dict(
        project_status
        if project_status is not None
        else read_project_runtime_boot_evidence(root)
    )
    manifest_payload = dict(
        installation_manifest
        if installation_manifest is not None
        else read_installation_manifest(root)
    )
    checked_at = observed_at or datetime.now(timezone.utc).isoformat()
    artifacts = build_session_boot_artifacts(
        project_status=status_payload,
        installation_manifest=manifest_payload,
        coordinates=coordinates,
        observed_at=checked_at,
    )

    server = AnchorSessionMemoryHostServer(
        port=port,
        token=token,
        repository_root=root,
        protect_lifecycle=True,
    )
    server.start()
    try:
        http_status, activation = call_host_adapter(
            endpoint=server.endpoint,
            token=server.lifecycle_token,
            method="POST",
            path="/v1/anchor-session-memory/activate",
            payload=artifacts["activation_payload"],
        )
        if http_status != 200 or activation.get("status") != "HOST_SESSION_MEMORY_ACTIVATED":
            raise SessionBootError(
                "SESSION_BOOT_ACTIVATION_FAILED",
                json.dumps(activation, sort_keys=True),
            )
        metadata = server.metadata()
        runtime_state = dict(artifacts["runtime_state"])
        runtime_state["runtime_image"] = {
            **runtime_state["runtime_image"],
            "endpoint": metadata["endpoint"],
        }
        result = {
            "schema": artifacts["schema"],
            "status": "SESSION_BOOT_IMAGE_CREATED",
            "command": "BOOT",
            "capability": "session_boot_executor",
            "repository_write": False,
            "boot_repository_write": False,
            "execution_permission": "UNASSIGNED",
            "mutation_enforcement": "HOST_DEPENDENT",
            "session_runtime": "READY",
            "session_initialization": "INITIALIZED",
            "repository_runtime": runtime_state["repository_runtime"],
            "session_preparation_state": runtime_state["session_preparation_state"],
            "executable_runtime_currentness": runtime_state[
                "executable_runtime_currentness"
            ],
            "authority": runtime_state["authority"],
            "execution_assignment": runtime_state["execution_assignment"],
            "boot_evidence_bundle": artifacts["boot_evidence_bundle"],
            "anchor_derivation": artifacts["anchor_derivation"],
            "runtime_state": runtime_state,
            "activation_evidence": activation,
            "host_adapter": metadata,
        }
        return PreparedSessionBoot(server=server, result=result)
    except Exception:
        server.stop()
        raise
