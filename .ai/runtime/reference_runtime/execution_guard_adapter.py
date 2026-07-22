"""Thin Host transport for the process-local Execution Guard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .anchor_session_memory_adapter import call_host_adapter


ROUTES = {
    "check": "/v1/execution-guard/check",
    "consume": "/v1/execution-guard/consume",
    "apply-file": "/v1/mutation-gateway/apply-file",
    "apply-git": "/v1/mutation-gateway/apply-git",
}


def invoke_execution_guard(
    *,
    endpoint: str,
    token: str,
    operation: str,
    payload: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Forward one request without remapping Guard output."""

    route = ROUTES.get(operation)
    if route is None:
        return 2, {
            "status": "UNKNOWN",
            "error_code": "EXECUTION_GUARD_OPERATION_UNSUPPORTED",
            "operation": operation,
        }
    http_status, result = call_host_adapter(
        endpoint=endpoint,
        token=token,
        method="POST",
        path=route,
        payload=payload,
    )
    if http_status != 200:
        return 3, result
    if result.get("status") in {
        "EXECUTION_GUARD_PERMITTED",
        "PERMIT_RECEIPT_CONSUMED",
        "FILE_MUTATION_APPLIED",
        "GIT_COMMAND_APPLIED",
    }:
        return 0, result
    if result.get("status") in {
        "EXECUTION_GUARD_BLOCKED",
        "BLOCKED_EXECUTION_HOST_REQUIRED",
        "PERMIT_RECEIPT_REJECTED",
        "FILE_MUTATION_BLOCKED",
        "GIT_COMMAND_BLOCKED",
        "GIT_COMMAND_FAILED",
    }:
        return 4, result
    return 3, result
