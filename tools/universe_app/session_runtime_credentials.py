"""Resolve opaque references to existing Host-owned session attachments only."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from .connection import UniverseError


def runtime_credential_ref(binding: Mapping[str, Any]) -> str:
    material = {
        key: binding.get(key)
        for key in (
            "session_id",
            "origin_anchor_ref",
            "binding_evidence_ref",
            "endpoint",
            "token",
        )
    }
    return (
        "session-runtime-credential_"
        + hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def resolve_session_runtime_connection(
    *,
    project_id: str,
    session: Mapping[str, Any],
    binding: Mapping[str, Any] | None,
    session_anchor_ref: str,
    credential_ref: str,
) -> dict[str, str]:
    """No attach, resume, token registration, or fallback to another Mode."""
    if (session.get("current_project_id") or session.get("node")) != project_id:
        raise UniverseError(
            "RUNTIME_CREDENTIAL_PROJECT_MISMATCH",
            "session belongs to another project",
            409,
        )
    if session.get("state") != "LIVE" or session.get("currentness") != "CURRENT":
        raise UniverseError(
            "RUNTIME_CREDENTIAL_SESSION_NOT_CURRENT",
            "current live session required",
            409,
        )
    if not binding:
        raise UniverseError(
            "RUNTIME_CREDENTIAL_UNAVAILABLE",
            "session has no Host-owned Runtime attachment",
            409,
        )
    if (
        session.get("session_anchor_ref") != session_anchor_ref
        or binding.get("origin_anchor_ref") != session_anchor_ref
        or binding.get("session_id") != session.get("session_id")
        or binding.get("runtime_currentness_observation") != "CURRENT"
    ):
        raise UniverseError(
            "RUNTIME_CREDENTIAL_ATTACHMENT_STALE",
            "Runtime attachment coordinates are not current",
            409,
        )
    if not isinstance(credential_ref, str) or not hmac.compare_digest(
        credential_ref.encode("utf-8"), runtime_credential_ref(binding).encode("ascii")
    ):
        raise UniverseError(
            "RUNTIME_CREDENTIAL_REF_INVALID",
            "credential reference does not identify this attachment",
            409,
        )
    if not all(
        isinstance(binding.get(key), str) and binding[key]
        for key in ("endpoint", "token")
    ):
        raise UniverseError(
            "RUNTIME_CREDENTIAL_UNAVAILABLE", "Runtime transport is unavailable", 409
        )
    return {key: binding[key] for key in ("endpoint", "token")}
