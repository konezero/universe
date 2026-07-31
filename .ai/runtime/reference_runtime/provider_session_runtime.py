"""Deterministic Provider Session connection evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


REQUEST_SCHEMA = "ai-career.provider-session-evaluation.v1"
RESULT_SCHEMA = "ai-career.provider-session-evaluation-result.v1"
EXECUTION_KINDS = frozenset(
    {"MODE_SESSION", "TASK_FRAME_BOSS", "TASK_FRAME_WORKER"}
)


@dataclass(frozen=True)
class ProviderSessionError(Exception):
    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


def _required_token(value: Any, field: str) -> str:
    token = str(value or "").strip()
    if not token or token.upper() == "UNKNOWN":
        raise ProviderSessionError(
            "PROVIDER_SESSION_REQUEST_INVALID",
            f"{field} must be a concrete value",
        )
    return token


def _optional_token(value: Any) -> str:
    token = str(value or "").strip()
    return token if token and token.upper() != "UNKNOWN" else "UNKNOWN"


def _coordinate(value: Any, field: str, *, required: bool) -> dict[str, str]:
    if value is None and not required:
        value = {}
    if not isinstance(value, Mapping):
        raise ProviderSessionError(
            "PROVIDER_SESSION_REQUEST_INVALID",
            f"{field} must be an object",
        )
    provider = (
        _required_token(value.get("provider"), f"{field}.provider")
        if required
        else _optional_token(value.get("provider"))
    )
    session_ref = (
        _required_token(value.get("session_ref"), f"{field}.session_ref")
        if required
        else _optional_token(value.get("session_ref"))
    )
    if not required and (provider == "UNKNOWN") != (session_ref == "UNKNOWN"):
        raise ProviderSessionError(
            "PROVIDER_SESSION_REQUEST_INVALID",
            f"{field} must contain both provider and session_ref or neither",
        )
    return {"provider": provider, "session_ref": session_ref}


def evaluate_provider_session_connection(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate connection replacement without claiming session currentness."""

    if request.get("schema") != REQUEST_SCHEMA:
        raise ProviderSessionError(
            "PROVIDER_SESSION_SCHEMA_INVALID",
            f"schema must be {REQUEST_SCHEMA}",
        )
    target_ref = _required_token(request.get("target_ref"), "target_ref")
    requested_mode = _required_token(request.get("requested_mode"), "requested_mode")
    execution_kind = _required_token(
        request.get("execution_kind"), "execution_kind"
    ).upper()
    if execution_kind not in EXECUTION_KINDS:
        raise ProviderSessionError(
            "PROVIDER_SESSION_EXECUTION_KIND_INVALID",
            f"unsupported execution_kind: {execution_kind}",
        )

    opened = _coordinate(request.get("opened"), "opened", required=True)
    if execution_kind != "MODE_SESSION":
        return {
            "schema": RESULT_SCHEMA,
            "status": "PROVIDER_SESSION_CONNECTION_EVALUATED",
            "target_ref": target_ref,
            "requested_mode": requested_mode,
            "execution_kind": execution_kind,
            "connection_state": "EPHEMERAL",
            "greeting_required": False,
            "persistence": "EPHEMERAL",
            "opened": opened,
            "coordinate_update": None,
            "currentness": "UNKNOWN",
            "authority": False,
        }

    stored = _coordinate(request.get("stored"), "stored", required=False)
    if stored["provider"] == "UNKNOWN":
        connection_state = "NEW"
    elif stored == opened:
        connection_state = "REUSED"
    else:
        connection_state = "REPLACED"

    return {
        "schema": RESULT_SCHEMA,
        "status": "PROVIDER_SESSION_CONNECTION_EVALUATED",
        "target_ref": target_ref,
        "requested_mode": requested_mode,
        "execution_kind": execution_kind,
        "connection_state": connection_state,
        "greeting_required": connection_state != "REUSED",
        "persistence": "LAST_COORDINATE",
        "opened": opened,
        "coordinate_update": opened,
        "currentness": "UNKNOWN",
        "authority": False,
    }
