"""Deterministic OS_STATUS result for a source-only repository attachment."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


SOURCE_ONLY_OS_STATUS_REQUEST_SCHEMA = (
    "ai-career.source-only-os-status-request.v1"
)
SOURCE_ONLY_OS_STATUS_RESULT_SCHEMA = "ai-career.os-status-result.v1"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class OsStatusError(ValueError):
    """Raised when a source-only OS_STATUS request is invalid."""


def evaluate_source_only_os_status(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the strongest status supported by source observation alone."""

    if not isinstance(request, Mapping):
        raise OsStatusError("request must be an object")
    _exact_fields(
        request,
        {"schema", "source", "observed_references"},
        "request",
    )
    if request.get("schema") != SOURCE_ONLY_OS_STATUS_REQUEST_SCHEMA:
        raise OsStatusError("request schema is unsupported")

    source = _source(request.get("source"))
    observed = _observed_references(request.get("observed_references"))
    checkpoint_status = (
        "OBSERVED_REFERENCE" if observed["checkpoints"] else "NOT_OBSERVED"
    )

    return {
        "schema": SOURCE_ONLY_OS_STATUS_RESULT_SCHEMA,
        "command": "OS_STATUS",
        "status": "SOURCE_READY",
        "source": {
            **source,
            "state": "SOURCE_READY",
        },
        "checkpoint": {
            "status": checkpoint_status,
            "references": observed["checkpoints"],
        },
        "resume_restore": {
            "status": "NOT_PERFORMED",
            "observed_references": observed["resume_archives"],
        },
        "validation": {
            "status": "NOT_RUN",
            "observed_references": observed["validations"],
        },
        "runtime_image": {
            "status": "UNKNOWN",
            "observed_references": observed["runtime_images"],
        },
        "session_preparation_state": "UNKNOWN",
        "mode_current_anchor": "UNKNOWN",
        "session_runtime": "UNKNOWN",
        "repository_runtime": "UNKNOWN",
        "executable_runtime_currentness": "UNKNOWN",
        "authority": "UNASSIGNED",
        "execution_assignment": "UNASSIGNED",
        "repository_write": False,
    }


def _source(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise OsStatusError("source must be an object")
    _exact_fields(
        value,
        {"repository", "ref", "commit", "provider", "evidence_ref"},
        "source",
    )
    source = {
        field: _required_text(value.get(field), f"source.{field}")
        for field in value
    }
    commit = source["commit"].lower()
    if not _COMMIT_PATTERN.fullmatch(commit):
        raise OsStatusError("source.commit must be a full immutable Git SHA")
    source["commit"] = commit
    if source["evidence_ref"].upper() == "UNKNOWN":
        raise OsStatusError("source.evidence_ref must be provider-observed")
    return source


def _observed_references(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise OsStatusError("observed_references must be an object")
    fields = {
        "checkpoints",
        "resume_archives",
        "validations",
        "runtime_images",
    }
    _exact_fields(value, fields, "observed_references")
    return {
        field: _reference_list(
            value.get(field), f"observed_references.{field}"
        )
        for field in sorted(fields)
    }


def _reference_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise OsStatusError(f"{label} must be an array")
    references: list[str] = []
    for index, item in enumerate(value):
        reference = _required_text(item, f"{label}[{index}]")
        if reference.upper() == "UNKNOWN":
            raise OsStatusError(f"{label}[{index}] must be observed")
        if reference not in references:
            references.append(reference)
    return references


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OsStatusError(f"{label} must be a non-empty string")
    return value.strip()


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise OsStatusError(f"{label} has an invalid shape")
