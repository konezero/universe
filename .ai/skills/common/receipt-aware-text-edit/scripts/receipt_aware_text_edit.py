#!/usr/bin/env python3
"""Apply one exact, bounded repository text replacement through Guard + apply-file.

This helper does not create Authority, Assignment, approval, Host capability,
currentness, or permission. It only:

1. reads the target as exact bytes;
2. applies one exact old_text -> new_text replacement (fail closed otherwise);
3. calculates target preimage and payload SHA-256;
4. invokes execution-guard check once;
5. immediately consumes a permitted one-time receipt through mutation-gateway
   apply-file;
6. fails closed without retry after any blocked or failed result.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess  # nosec B404 - intentional CLI transport to Runtime surfaces
import sys
import tempfile
from collections.abc import Callable, Mapping
from typing import Any


REQUEST_SCHEMA = "receipt-aware-text-edit.request.v1"
RESULT_SCHEMA = "receipt-aware-text-edit.result.v1"
UTF8_BOM = b"\xef\xbb\xbf"
SECRET_FIELDS = frozenset({"token", "endpoint_token", "authorization"})

InvokeFn = Callable[[str, str, Mapping[str, Any]], tuple[int, dict[str, Any]]]


class EditError(Exception):
    """A local text-edit preparation failure that must not mutate."""

    def __init__(self, error_code: str, detail: str = "") -> None:
        self.error_code = error_code
        self.detail = detail or error_code
        super().__init__(self.detail)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def target_preimage(raw: bytes | None) -> dict[str, str]:
    if raw is None:
        return {"status": "ABSENT", "sha256": "NONE"}
    return {"status": "PRESENT", "sha256": sha256_hex(raw)}


def read_target_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise EditError("TARGET_SYMLINK_FORBIDDEN", "symlink targets are forbidden")
    if not path.exists():
        raise EditError("TARGET_NOT_FOUND", f"target does not exist: {path}")
    if not path.is_file():
        raise EditError("TARGET_NOT_FILE", f"target is not a regular file: {path}")
    return path.read_bytes()


def apply_exact_replacement(raw: bytes, old_text: str, new_text: str) -> bytes:
    """Replace exactly one occurrence while preserving BOM and surrounding bytes."""

    if not isinstance(old_text, str) or old_text == "":
        raise EditError("OLD_TEXT_REQUIRED", "old_text must be a non-empty string")
    if not isinstance(new_text, str):
        raise EditError("NEW_TEXT_REQUIRED", "new_text must be a string")
    if "\x00" in old_text or "\x00" in new_text:
        raise EditError("TEXT_NUL_FORBIDDEN", "replacement text must not contain NUL")

    has_bom = raw.startswith(UTF8_BOM)
    body = raw[len(UTF8_BOM) :] if has_bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EditError(
            "TARGET_NOT_UTF8",
            "target body must be UTF-8 text",
        ) from error

    matches = text.count(old_text)
    if matches == 0:
        raise EditError("ZERO_MATCH", "old_text was not found exactly once")
    if matches > 1:
        raise EditError(
            "AMBIGUOUS_MATCH",
            f"old_text matched {matches} times; exact single match required",
        )

    updated = text.replace(old_text, new_text, 1)
    encoded = updated.encode("utf-8")
    return (UTF8_BOM + encoded) if has_bom else encoded


def _require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise EditError("REQUEST_INVALID", f"{field} must be a non-empty string")
    if "\x00" in value:
        raise EditError("REQUEST_INVALID", f"{field} must not contain NUL")
    return value


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EditError("REQUEST_INVALID", f"{field} must be an object")
    return dict(value)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_FIELDS:
                redacted[str(key)] = "REDACTED"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return value
    return value


def _base_result(status: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": status,
        "authority_created": False,
        "repository_write": False,
        "retry": False,
    }
    payload.update(fields)
    return _redact(payload)


def _result_reasons(
    payload: Mapping[str, Any],
    *,
    fallback: str,
) -> list[str]:
    raw = payload.get("reasons")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return raw or [fallback]
    return [fallback]


def _resolve_cli_path(request: Mapping[str, Any], repo_root: Path | None) -> Path:
    raw = request.get("cli_path")
    if isinstance(raw, str) and raw.strip():
        path = Path(raw)
        if not path.is_file():
            raise EditError("CLI_UNAVAILABLE", f"cli_path is not a file: {path}")
        return path.resolve()
    if repo_root is not None:
        candidate = repo_root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        if candidate.is_file():
            return candidate.resolve()
    raise EditError(
        "CLI_UNAVAILABLE",
        "cli_path is required when repository reference_runtime/cli.py is absent",
    )


def _transport_dir(request: Mapping[str, Any]) -> tuple[Path, bool]:
    raw = request.get("runtime_tmp")
    if isinstance(raw, str) and raw.strip():
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve(), False
    return Path(tempfile.mkdtemp(prefix="receipt-aware-text-edit-")), True


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_bytes(
        json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _default_cli_invoke(
    *,
    python_executable: str,
    cli_path: Path,
    operation: str,
    endpoint: str,
    token: str,
    request_path: Path,
) -> tuple[int, dict[str, Any]]:
    if operation == "check":
        args = [
            python_executable,
            str(cli_path),
            "execution-guard",
            "check",
            "--endpoint",
            endpoint,
            "--token",
            token,
            "--request",
            str(request_path),
        ]
    elif operation == "apply-file":
        args = [
            python_executable,
            str(cli_path),
            "mutation-gateway",
            "apply-file",
            "--endpoint",
            endpoint,
            "--token",
            token,
            "--request",
            str(request_path),
        ]
    else:
        raise EditError("TRANSPORT_UNSUPPORTED", f"unsupported operation: {operation}")

    completed = subprocess.run(  # nosec B603 - fixed Runtime CLI argv, no shell
        args,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        raise EditError(
            "TRANSPORT_EMPTY_RESPONSE",
            completed.stderr.decode("utf-8", errors="replace")[:500],
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise EditError(
            "TRANSPORT_JSON_INVALID",
            f"CLI stdout was not JSON: {stdout[:200]}",
        ) from error
    if not isinstance(payload, dict):
        raise EditError("TRANSPORT_SHAPE_INVALID", "CLI payload root must be an object")
    return completed.returncode, payload


def build_mutation_request(
    *,
    session_id: str,
    frame_id: str,
    anchor_id: str,
    target: str,
    boundary: str,
    source_commit: str,
    validation_ref: str,
    payload_sha256: str,
    preimage: Mapping[str, str],
    host_capability: Mapping[str, Any],
    approval: Mapping[str, Any],
    task_frame_lineage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "session_id": session_id,
        "frame_id": frame_id,
        "anchor_id": anchor_id,
        "operation": "MODIFY",
        "target": target,
        "boundary": boundary,
        "source_commit": source_commit,
        "validation_ref": validation_ref,
        "payload_sha256": payload_sha256,
        "target_preimage": dict(preimage),
        "host_capability": dict(host_capability),
        "approval": dict(approval),
    }
    if task_frame_lineage is not None:
        request["task_frame_lineage"] = dict(task_frame_lineage)
    return request


def run_text_edit(
    request: Mapping[str, Any],
    *,
    invoke_check: InvokeFn | None = None,
    invoke_apply: InvokeFn | None = None,
    repo_root: Path | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Prepare one exact replacement, check once, and apply once when permitted."""

    try:
        schema = _require_string(request.get("schema"), "schema")
        if schema != REQUEST_SCHEMA:
            raise EditError(
                "REQUEST_SCHEMA_UNSUPPORTED",
                f"schema must be {REQUEST_SCHEMA}",
            )
        endpoint = _require_string(request.get("endpoint"), "endpoint")
        token = _require_string(request.get("token"), "token")
        session_id = _require_string(request.get("session_id"), "session_id")
        frame_id = _require_string(request.get("frame_id"), "frame_id")
        anchor_id = _require_string(request.get("anchor_id"), "anchor_id")
        target_raw = _require_string(request.get("target"), "target")
        boundary = _require_string(request.get("boundary"), "boundary")
        source_commit = _require_string(request.get("source_commit"), "source_commit")
        validation_ref = _require_string(
            request.get("validation_ref"), "validation_ref"
        )
        old_text = request.get("old_text")
        new_text = request.get("new_text")
        if not isinstance(old_text, str) or old_text == "":
            raise EditError("REQUEST_INVALID", "old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise EditError("REQUEST_INVALID", "new_text must be a string")
        host_capability = _require_mapping(
            request.get("host_capability"), "host_capability"
        )
        approval = _require_mapping(request.get("approval"), "approval")
        lineage_raw = request.get("task_frame_lineage")
        task_frame_lineage = (
            _require_mapping(lineage_raw, "task_frame_lineage")
            if lineage_raw is not None
            else None
        )

        target = Path(os.path.normpath(target_raw))
        if not target.is_absolute():
            raise EditError("TARGET_NOT_ABSOLUTE", "target must be an absolute path")

        original = read_target_bytes(target)
        preimage = target_preimage(original)
        payload_bytes = apply_exact_replacement(original, old_text, new_text)
        payload_sha256 = sha256_hex(payload_bytes)
        mutation_request = build_mutation_request(
            session_id=session_id,
            frame_id=frame_id,
            anchor_id=anchor_id,
            target=str(target),
            boundary=boundary,
            source_commit=source_commit,
            validation_ref=validation_ref,
            payload_sha256=payload_sha256,
            preimage=preimage,
            host_capability=host_capability,
            approval=approval,
            task_frame_lineage=task_frame_lineage,
        )
        check_payload = {
            "session_id": session_id,
            "request": mutation_request,
        }
        if isinstance(request.get("observed_at"), str) and request["observed_at"]:
            check_payload["observed_at"] = request["observed_at"]
    except EditError as error:
        return _base_result(
            "TEXT_EDIT_BLOCKED",
            decision="BLOCKED",
            reasons=[error.error_code],
            detail=error.detail,
        )

    transport: Path | None = None
    remove_transport = False
    check_path: Path | None = None
    apply_path: Path | None = None
    try:
        transport, remove_transport = _transport_dir(request)
        check_path = transport / "text-edit-check-request.json"
        apply_path = transport / "text-edit-apply-request.json"
        _write_json(check_path, check_payload)

        if invoke_check is None or invoke_apply is None:
            cli_path = _resolve_cli_path(request, repo_root)
            python_bin = python_executable or sys.executable

            def _cli_check(
                ep: str, tok: str, payload: Mapping[str, Any]
            ) -> tuple[int, dict[str, Any]]:
                _write_json(check_path, payload)
                return _default_cli_invoke(
                    python_executable=python_bin,
                    cli_path=cli_path,
                    operation="check",
                    endpoint=ep,
                    token=tok,
                    request_path=check_path,
                )

            def _cli_apply(
                ep: str, tok: str, payload: Mapping[str, Any]
            ) -> tuple[int, dict[str, Any]]:
                _write_json(apply_path, payload)
                return _default_cli_invoke(
                    python_executable=python_bin,
                    cli_path=cli_path,
                    operation="apply-file",
                    endpoint=ep,
                    token=tok,
                    request_path=apply_path,
                )

            if invoke_check is None:
                invoke_check = _cli_check
            if invoke_apply is None:
                invoke_apply = _cli_apply

        check_code, check_result = invoke_check(endpoint, token, check_payload)
        check_status = str(check_result.get("status", "UNKNOWN"))
        if check_code != 0 or check_status != "EXECUTION_GUARD_PERMITTED":
            fallback = (
                "GUARD_TRANSPORT_EXIT_NONZERO"
                if check_code != 0 and check_status == "EXECUTION_GUARD_PERMITTED"
                else check_status or "GUARD_NOT_PERMITTED"
            )
            return _base_result(
                "TEXT_EDIT_BLOCKED",
                decision="BLOCKED",
                reasons=_result_reasons(check_result, fallback=fallback),
                guard_status=check_status,
                guard_exit_code=check_code,
                guard_result=check_result,
                target_preimage=preimage,
                payload_sha256=payload_sha256,
            )

        permit = check_result.get("permit_receipt")
        if not isinstance(permit, Mapping):
            return _base_result(
                "TEXT_EDIT_BLOCKED",
                decision="BLOCKED",
                reasons=["PERMIT_RECEIPT_MISSING"],
                guard_status=check_status,
                guard_result=check_result,
            )
        receipt_id = permit.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            return _base_result(
                "TEXT_EDIT_BLOCKED",
                decision="BLOCKED",
                reasons=["PERMIT_RECEIPT_ID_MISSING"],
                guard_status=check_status,
                guard_result=check_result,
            )

        apply_payload = {
            "session_id": session_id,
            "receipt_id": receipt_id,
            "request": mutation_request,
            "content_base64": base64.b64encode(payload_bytes).decode("ascii"),
        }
        if isinstance(request.get("observed_at"), str) and request["observed_at"]:
            apply_payload["observed_at"] = request["observed_at"]
        _write_json(apply_path, apply_payload)

        apply_code, apply_result = invoke_apply(endpoint, token, apply_payload)
        apply_status = str(apply_result.get("status", "UNKNOWN"))
        if apply_code == 0 and apply_status == "FILE_MUTATION_APPLIED":
            return _base_result(
                "TEXT_EDIT_APPLIED",
                decision="APPLIED",
                repository_write=True,
                operation="MODIFY",
                target=str(target),
                receipt_id=receipt_id,
                target_preimage=preimage,
                payload_sha256=payload_sha256,
                postimage=apply_result.get("postimage"),
                guard_status=check_status,
                apply_status=apply_status,
                apply_exit_code=apply_code,
                apply_result=apply_result,
            )

        fallback = (
            "APPLY_TRANSPORT_EXIT_NONZERO"
            if apply_code != 0 and apply_status == "FILE_MUTATION_APPLIED"
            else apply_status or "APPLY_NOT_APPLIED"
        )
        return _base_result(
            "TEXT_EDIT_BLOCKED",
            decision="BLOCKED",
            reasons=_result_reasons(apply_result, fallback=fallback),
            guard_status=check_status,
            apply_status=apply_status,
            apply_exit_code=apply_code,
            apply_result=apply_result,
            receipt_id=receipt_id,
            target_preimage=preimage,
            payload_sha256=payload_sha256,
        )
    except (EditError, OSError) as error:
        if isinstance(error, EditError):
            reason = error.error_code
            detail = error.detail
        else:
            reason = "TRANSPORT_UNAVAILABLE"
            detail = str(error)
        return _base_result(
            "TEXT_EDIT_BLOCKED",
            decision="BLOCKED",
            reasons=[reason],
            detail=detail,
            target_preimage=preimage,
            payload_sha256=payload_sha256,
        )
    finally:
        for path in (check_path, apply_path):
            try:
                if path is not None and path.exists():
                    path.unlink()
            except OSError:
                pass
        if remove_transport and transport is not None:
            try:
                transport.rmdir()
            except OSError:
                pass


def _load_request(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(UTF8_BOM):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise EditError("REQUEST_INVALID", "request root must be an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply one exact repository text replacement through "
            "execution-guard check and mutation-gateway apply-file."
        )
    )
    parser.add_argument("--request", required=True, help="UTF-8 JSON request path")
    parser.add_argument("--result", required=True, help="UTF-8 JSON result path")
    parser.add_argument(
        "--repo-root",
        default="",
        help="Optional repository root used to locate reference_runtime/cli.py",
    )
    args = parser.parse_args(argv)

    result_path = Path(args.result)
    try:
        request = _load_request(Path(args.request))
        # CLI override keeps secrets out of optional durable request copies when
        # callers prefer environment-local argv transport.
        if "token" not in request and os.environ.get("AI_CAREER_SESSION_BOOT_TOKEN"):
            request["token"] = os.environ["AI_CAREER_SESSION_BOOT_TOKEN"]
        if "endpoint" not in request and os.environ.get(
            "AI_CAREER_SESSION_BOOT_ENDPOINT"
        ):
            request["endpoint"] = os.environ["AI_CAREER_SESSION_BOOT_ENDPOINT"]
        repo_root = Path(args.repo_root).resolve() if args.repo_root else None
        result = run_text_edit(request, repo_root=repo_root)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, EditError) as error:
        if isinstance(error, EditError):
            result = _base_result(
                "TEXT_EDIT_BLOCKED",
                decision="BLOCKED",
                reasons=[error.error_code],
                detail=error.detail,
            )
        else:
            result = _base_result(
                "TEXT_EDIT_BLOCKED",
                decision="BLOCKED",
                reasons=["REQUEST_UNAVAILABLE"],
                detail=str(error),
            )

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if result.get("status") == "TEXT_EDIT_APPLIED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
