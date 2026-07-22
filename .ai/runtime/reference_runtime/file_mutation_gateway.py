"""Receipt-aware file mutation gateway for one installed project root."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .execution_guard_runtime import ExecutionGuardError, ExecutionGuardRuntime


class FileMutationGateway:
    """Apply bounded file mutations only after one matching receipt is consumed."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve(strict=True)

    def apply(
        self,
        *,
        guard: ExecutionGuardRuntime,
        snapshot: Mapping[str, Any],
        payload: Mapping[str, Any],
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = payload.get("request")
        if not isinstance(request, Mapping):
            return _blocked("MUTATION_REQUEST_REQUIRED")
        operation = str(request.get("operation", "")).strip().upper()
        if operation not in {"CREATE", "MODIFY", "DELETE"}:
            return _blocked("FILE_MUTATION_OPERATION_UNSUPPORTED")
        create_parents = _may_create_work_receipt_parents(
            snapshot=snapshot, request=request, operation=operation
        )
        try:
            target = self._target(
                str(request.get("target", "")), allow_missing_parents=create_parents
            )
        except (OSError, ValueError):
            return _blocked("TARGET_BOUNDARY_VIOLATION")

        declared_preimage = request.get("target_preimage")
        if not isinstance(declared_preimage, Mapping):
            return _blocked("TARGET_PREIMAGE_REQUIRED")
        actual_preimage = _preimage(target)
        if dict(declared_preimage) != actual_preimage:
            return _blocked("TARGET_PREIMAGE_CHANGED")

        content_result = _content(payload, operation)
        if isinstance(content_result, str):
            return _blocked(content_result)
        content = content_result
        declared_payload_sha256 = str(request.get("payload_sha256", ""))
        actual_payload_sha256 = (
            "NONE" if content is None else hashlib.sha256(content).hexdigest()
        )
        if declared_payload_sha256 != actual_payload_sha256:
            return _blocked("PAYLOAD_MISMATCH")
        if operation == "CREATE" and actual_preimage["status"] != "ABSENT":
            return _blocked("CREATE_TARGET_ALREADY_EXISTS")
        if operation in {"MODIFY", "DELETE"} and actual_preimage["status"] != "PRESENT":
            return _blocked("MUTATION_TARGET_NOT_FOUND")

        try:
            consumed = guard.consume(
                receipt_id=str(payload.get("receipt_id", "")),
                snapshot=snapshot,
                request=request,
                observed_at=str(payload.get("observed_at", "")),
                task_frame_lineage_verification=task_frame_lineage_verification,
            )
        except ExecutionGuardError as error:
            return {
                "status": "FILE_MUTATION_BLOCKED",
                "decision": "BLOCKED",
                "repository_write": False,
                "reasons": [error.error_code],
                "detail": error.detail,
            }
        if consumed.get("status") != "PERMIT_RECEIPT_CONSUMED":
            return {
                "status": "FILE_MUTATION_BLOCKED",
                "decision": "BLOCKED",
                "repository_write": False,
                "reasons": list(consumed.get("reasons", ["PERMIT_RECEIPT_REJECTED"])),
                "receipt_result": consumed,
            }

        try:
            if operation == "CREATE":
                if create_parents and not target.parent.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target = self._target(str(target), allow_missing_parents=False)
                _create(target, content or b"")
            elif operation == "MODIFY":
                _replace(target, content or b"")
            else:
                target.unlink()
        except OSError as error:
            return {
                "status": "FILE_MUTATION_FAILED",
                "decision": "BLOCKED",
                "repository_write": False,
                "reasons": ["HOST_FILE_MUTATION_FAILED"],
                "detail": str(error),
                "receipt_result": consumed,
            }

        return {
            "status": "FILE_MUTATION_APPLIED",
            "decision": "APPLIED",
            "operation": operation,
            "target": str(target),
            "repository_write": True,
            "receipt_id": consumed["receipt_id"],
            "postimage": _preimage(target),
        }

    def _target(self, value: str, *, allow_missing_parents: bool) -> Path:
        target = Path(os.path.normpath(value))
        if not target.is_absolute():
            raise ValueError("target must be absolute")
        if target.is_symlink():
            raise ValueError("symlink target is forbidden")
        parent = target.parent
        if not parent.is_dir():
            if not allow_missing_parents:
                raise ValueError("target parent must exist")
            parent = _nearest_existing_parent(parent)
        parent = parent.resolve(strict=True)
        if os.path.commonpath(
            [os.path.normcase(str(parent)), os.path.normcase(str(self.repository_root))]
        ) != os.path.normcase(str(self.repository_root)):
            raise ValueError("target escapes repository root")
        current = parent
        while current != self.repository_root:
            if current.is_symlink():
                raise ValueError("symlink parent is forbidden")
            current = current.parent
        if allow_missing_parents:
            return target
        return parent / target.name


def _content(payload: Mapping[str, Any], operation: str) -> bytes | None | str:
    encoded = payload.get("content_base64")
    if operation == "DELETE":
        if encoded not in {None, ""}:
            return "DELETE_PAYLOAD_FORBIDDEN"
        return None
    if not isinstance(encoded, str):
        return "MUTATION_PAYLOAD_REQUIRED"
    try:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return "MUTATION_PAYLOAD_INVALID"


def _preimage(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"status": "ABSENT", "sha256": "NONE"}
    if not path.is_file() or path.is_symlink():
        return {"status": "UNSUPPORTED", "sha256": "NONE"}
    return {
        "status": "PRESENT",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _create(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _replace(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            raise ValueError("target has no existing parent")
        current = current.parent
    if not current.is_dir() or current.is_symlink():
        raise ValueError("target parent is not a real directory")
    return current


def _may_create_work_receipt_parents(
    *, snapshot: Mapping[str, Any], request: Mapping[str, Any], operation: str
) -> bool:
    if operation != "CREATE":
        return False
    assignment = snapshot.get("execution_assignment")
    if not isinstance(assignment, Mapping):
        return False
    if assignment.get("status") != "ASSIGNED":
        return False
    if assignment.get("assignment_kind") != "PROJECT_SOURCE_WORK":
        return False
    operations = assignment.get("write_operations")
    roots = assignment.get("write_roots")
    if not isinstance(operations, list) or "CREATE" not in operations:
        return False
    if not isinstance(roots, list):
        return False
    target = Path(str(request.get("target", "")))
    if not target.is_absolute():
        return False
    try:
        normalized_target = os.path.normcase(os.path.abspath(str(target)))
        return any(
            isinstance(root, str)
            and os.path.commonpath(
                [normalized_target, os.path.normcase(os.path.abspath(root))]
            )
            == os.path.normcase(os.path.abspath(root))
            for root in roots
        )
    except (OSError, ValueError):
        return False


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "FILE_MUTATION_BLOCKED",
        "decision": "BLOCKED",
        "repository_write": False,
        "reasons": [reason],
    }
