"""Deterministic trust gate for reviewing candidate repository sources."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


SOURCE_REVIEW_SCHEMA = "ai-career.source-review-request.v1"
SOURCE_REVIEW_RESULT_SCHEMA = "ai-career.source-review-result.v1"
REVIEW_MODES = {"STATIC_REVIEW", "SANDBOXED_EXECUTION_REVIEW"}
POLICY_SOURCE_KINDS = {"TRUSTED_BASE", "INSTALLED_DISTRIBUTION"}
BLOCKED_SANDBOX_VALUES = {
    "disposable": True,
    "host_filesystem": "BLOCKED",
    "credentials": "ABSENT",
    "network": "BLOCKED",
}
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class SourceReviewError(ValueError):
    """Raised when a source-review request is structurally invalid."""


def evaluate_source_review(request: Mapping[str, Any]) -> dict[str, Any]:
    """Separate trusted reviewer policy from an untrusted candidate source."""

    if not isinstance(request, Mapping):
        raise SourceReviewError("request must be an object")
    expected = {
        "schema",
        "policy_source",
        "candidate_source",
        "review_mode",
        "sandbox",
    }
    if set(request) != expected:
        raise SourceReviewError("request has an invalid shape")
    if request.get("schema") != SOURCE_REVIEW_SCHEMA:
        raise SourceReviewError("request schema is unsupported")

    policy = _source(
        request.get("policy_source"),
        label="policy_source",
        expected_fields={"ref", "commit", "kind", "evidence_ref"},
    )
    kind = _text(policy.get("kind"), "policy_source.kind").upper()
    if kind not in POLICY_SOURCE_KINDS:
        raise SourceReviewError("policy_source.kind is unsupported")

    candidate = _source(
        request.get("candidate_source"),
        label="candidate_source",
        expected_fields={"ref", "commit"},
    )
    if (
        policy["commit"] == candidate["commit"]
        or policy["ref"] == candidate["ref"]
    ):
        raise SourceReviewError(
            "policy_source must be independent from candidate_source"
        )

    review_mode = _text(request.get("review_mode"), "review_mode").upper()
    if review_mode not in REVIEW_MODES:
        raise SourceReviewError("review_mode is unsupported")

    sandbox = request.get("sandbox")
    if not isinstance(sandbox, Mapping):
        raise SourceReviewError("sandbox must be an object")
    expected_sandbox = {
        "disposable",
        "host_filesystem",
        "credentials",
        "network",
        "evidence_ref",
    }
    if set(sandbox) != expected_sandbox:
        raise SourceReviewError("sandbox has an invalid shape")

    result: dict[str, Any] = {
        "schema": SOURCE_REVIEW_RESULT_SCHEMA,
        "policy_source": {
            **policy,
            "kind": kind,
            "use": "REVIEWER_POLICY",
        },
        "candidate_source": {
            **candidate,
            "policy_activation": "FORBIDDEN",
            "classification": "DATA_ONLY",
        },
        "review_mode": review_mode,
        "repository_write": False,
        "authority_created": False,
        "execution_assignment_created": False,
    }

    if review_mode == "STATIC_REVIEW":
        result.update(
            {
                "status": "SOURCE_REVIEW_PERMITTED",
                "candidate_execution": "FORBIDDEN",
                "execution_environment": "NOT_APPLICABLE",
                "test_status": "NOT_RUN_UNTRUSTED",
                "reasons": [],
            }
        )
        return result

    reasons = []
    for field, required in BLOCKED_SANDBOX_VALUES.items():
        value = sandbox.get(field)
        normalized = value.upper() if isinstance(value, str) else value
        if normalized != required:
            reasons.append(f"SANDBOX_{field.upper()}_REQUIRED")
    evidence_ref = sandbox.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        reasons.append("SANDBOX_EVIDENCE_REQUIRED")

    if reasons:
        result.update(
            {
                "status": "SOURCE_REVIEW_BLOCKED",
                "candidate_execution": "BLOCKED",
                "execution_environment": "UNVERIFIED",
                "test_status": "NOT_RUN_UNTRUSTED",
                "reasons": reasons,
            }
        )
        return result

    result.update(
        {
            "status": "SOURCE_REVIEW_PERMITTED",
            "candidate_execution": "SANDBOX_ONLY",
            "execution_environment": "VERIFIED_DISPOSABLE_SANDBOX",
            "test_status": "READY_TO_RUN_SANDBOXED",
            "sandbox_evidence_ref": evidence_ref.strip(),
            "reasons": [],
        }
    )
    return result


def _source(
    value: Any, *, label: str, expected_fields: set[str]
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise SourceReviewError(f"{label} has an invalid shape")
    normalized = {field: _text(value.get(field), f"{label}.{field}") for field in value}
    commit = normalized["commit"].lower()
    if not _COMMIT_PATTERN.fullmatch(commit):
        raise SourceReviewError(f"{label}.commit must be a full immutable Git SHA")
    normalized["commit"] = commit
    if "evidence_ref" in normalized and normalized["evidence_ref"].upper() == "UNKNOWN":
        raise SourceReviewError(f"{label}.evidence_ref must be Host-observed")
    return normalized


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceReviewError(f"{label} must be a non-empty string")
    return value.strip()
