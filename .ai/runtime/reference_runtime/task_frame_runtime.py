"""Generic Task Frame ledger with optional file-backed journal storage.

The runtime validates declared task-turn mechanics and emits evidence. It does
not choose a model, invoke a vendor worker, mutate a Parent Queue, restore an
anchor, or create authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


PROFILE_SCHEMA = "ai-career.task-frame-proof-profile.v0"
INSTALLED_PROFILE_SCHEMA = "ai-career.task-frame-profile.v1"
INSTALLATION_MANIFEST_SCHEMA = "ai-career.project-runtime-installation.v1"
INSTALLATION_MANIFEST_PATH = ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
GENERIC_WRITE_PROOF = "GENERIC_WRITE_PROOF"
IMMUTABLE_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TASK_FRAME_EXECUTION_PROPOSAL_SCHEMA = "ai-career.task-frame-execution-proposal.v2"
TASK_FRAME_EXECUTION_SHAPES = {"DEBATE"}
TASK_FRAME_MODEL_MODES = {"AUTO", "EXPLICIT"}
TASK_FRAME_TRANSCRIPT_POLICY = "BOUNDED_RETURNED_MESSAGES_ONLY"
PARENT_OBSERVATION_STATUSES = {"MATCHED", "MISSING", "MISMATCHED", "UNKNOWN"}
HOST_CAPABILITY_STATUSES = {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
REPOSITORY_WRITE_SCOPES = {"NONE", "BOUNDED"}
REPOSITORY_MUTATION_OPERATIONS = {"CREATE", "MODIFY", "DELETE"}
REVIEW_DECISIONS = {"ACCEPT", "RETURN", "BLOCKED", "UNKNOWN"}
PROHIBITED_TURN_RESULT_CLAIMS = frozenset(
    {
        "authority",
        "currentness",
        "execution_assignment",
        "execution_permission",
        "repository_write_scope",
        "parent_adoption",
        "parent_decision",
        "next_task_id",
    }
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS task_frame_context (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    profile_id TEXT NOT NULL,
    profile_path TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    frame_id TEXT NOT NULL,
    origin_anchor_ref TEXT NOT NULL,
    origin_session_id TEXT NOT NULL,
    origin_frame_id TEXT NOT NULL,
    origin_governance_session_ref TEXT NOT NULL,
    task_summary_ref TEXT NOT NULL,
    execution_assignment_ref TEXT NOT NULL,
    task_state TEXT NOT NULL,
    attachment_state TEXT NOT NULL,
    adoption_state TEXT NOT NULL,
    current_parent_status TEXT NOT NULL,
    current_parent_evidence_ref TEXT NOT NULL,
    dispatch_topology_json TEXT NOT NULL DEFAULT '{}',
    execution_gate_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parent_observations (
    observation_ordinal INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_instructions (
    instruction_ordinal INTEGER PRIMARY KEY,
    instruction_id TEXT NOT NULL UNIQUE,
    parent_session_id TEXT NOT NULL,
    parent_frame_id TEXT NOT NULL,
    parent_anchor_ref TEXT NOT NULL,
    user_instruction_raw TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    expected_output_json TEXT NOT NULL,
    repository_write_scope TEXT NOT NULL,
    mutation_scope_json TEXT NOT NULL,
    instruction_digest TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL DEFAULT '',
    acknowledged_at TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boss_allocations (
    allocation_ordinal INTEGER PRIMARY KEY,
    instruction_digest TEXT NOT NULL,
    boss_turn_id TEXT NOT NULL,
    boss_worker_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    worker_slot_ref TEXT NOT NULL,
    worker_path TEXT NOT NULL UNIQUE,
    task_text TEXT NOT NULL,
    expected_output_json TEXT NOT NULL,
    mutation_scope_json TEXT NOT NULL,
    allocation_digest TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_turns (
    turn_ordinal INTEGER PRIMARY KEY,
    turn_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    input_turn_ids_json TEXT NOT NULL,
    accept_turn_id TEXT NOT NULL DEFAULT '',
    return_turn_id TEXT NOT NULL DEFAULT '',
    terminal_on_accept INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    claimed_by TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    review_decision TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_journal (
    event_ordinal INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_execution_state (
    turn_id TEXT PRIMARY KEY,
    worker_slot_ref TEXT NOT NULL DEFAULT '',
    capability_evidence_ref TEXT NOT NULL DEFAULT '',
    invoker_actor_ref TEXT NOT NULL DEFAULT '',
    worker_path TEXT NOT NULL DEFAULT '',
    worker_actor_ref TEXT NOT NULL DEFAULT '',
    host_invocation_receipt_ref TEXT NOT NULL DEFAULT '',
    worker_result_digest TEXT NOT NULL DEFAULT '',
    host_result_evidence_ref TEXT NOT NULL DEFAULT '',
    worker_result_envelope_json TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS task_turns_state_lookup
ON task_turns(state, turn_ordinal);

CREATE UNIQUE INDEX IF NOT EXISTS worker_invocation_receipt_unique
ON worker_execution_state(host_invocation_receipt_ref)
WHERE host_invocation_receipt_ref <> '';
"""


class TaskFrameProfileError(ValueError):
    """The supplied profile cannot safely drive the Task Frame ledger."""


@dataclass(frozen=True)
class TaskFrameProofProfile:
    profile_id: str
    source_repository: str
    source_commit: str
    contract_refs: tuple[str, ...]
    max_active_turns: int
    profile_path: str
    profile_sha256: str
    execution_approval_required: bool
    parent_participation_forbidden: bool
    worker_invocation_evidence_required: bool


TaskFrameProfile = TaskFrameProofProfile


@dataclass(frozen=True)
class ParentObservation:
    """Caller-observed Parent context; it is not an authority assertion."""

    status: str
    evidence_ref: str


@dataclass(frozen=True)
class TaskTurn:
    """A declared turn. Role is input data, not a runtime authority class."""

    turn_id: str
    role: str
    input_turn_ids: tuple[str, ...] = ()
    accept_turn_id: str = ""
    return_turn_id: str = ""
    terminal_on_accept: bool = False


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execution_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value.strip()


def _canonical_role_token(value: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if token in {"BOSS", "BOSS_OPEN", "BOSS_SYNTHESIS"}:
        return "BOSS"
    if token in {"SUB", "SUB_REVIEWER", "REVIEWER"}:
        return "SUB_REVIEWER"
    if re.fullmatch(r"(?:SUB|SUB_REVIEWER|REVIEWER)_\d+", token):
        return "SUB_REVIEWER"
    return ""


def _canonical_execution_role(value: Any, context: str) -> str:
    raw = _execution_text(value, context)
    canonical = _canonical_role_token(raw)
    if not canonical:
        raise ValueError(f"{context} is unsupported")
    return canonical


def _execution_exact_fields(
    value: Mapping[str, Any], expected: set[str], context: str
) -> None:
    extra = sorted(set(value) - expected)
    if extra:
        raise ValueError(f"{context} contains unsupported field: {extra[0]}")
    missing = sorted(expected - set(value))
    if missing:
        raise ValueError(f"{context} is missing required field: {missing[0]}")


def _normalize_mutation_scope(value: Any, context: str) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != {"operations", "targets"}:
        raise ValueError(f"{context} must contain operations and targets")
    operations = value.get("operations")
    targets = value.get("targets")
    if (
        not isinstance(operations, list)
        or not isinstance(targets, list)
        or any(not isinstance(item, str) for item in operations)
        or any(not isinstance(item, str) for item in targets)
    ):
        raise ValueError(f"{context} operations and targets must be arrays of strings")
    normalized_operations = list(
        dict.fromkeys(item.strip().upper() for item in operations if item.strip())
    )
    if any(
        item not in REPOSITORY_MUTATION_OPERATIONS
        for item in normalized_operations
    ):
        raise ValueError(f"{context}.operations contains an unsupported operation")
    normalized_targets: list[str] = []
    for item in targets:
        if not item.strip() or not Path(item).is_absolute():
            raise ValueError(f"{context}.targets must contain absolute paths")
        normalized_target = os.path.normpath(item.strip())
        if normalized_target not in normalized_targets:
            normalized_targets.append(normalized_target)
    if bool(normalized_operations) != bool(normalized_targets):
        raise ValueError(f"{context} operations and targets must both be empty or non-empty")
    return {
        "operations": normalized_operations,
        "targets": normalized_targets,
    }


def _normalize_repository_boundary(
    *,
    repository_write_scope: Any,
    mutation_scope: Any,
    context: str,
) -> tuple[str, dict[str, list[str]]]:
    write_scope = _execution_text(
        repository_write_scope,
        f"{context}.repository_write_scope",
    ).upper()
    if write_scope not in REPOSITORY_WRITE_SCOPES:
        raise ValueError(
            f"{context}.repository_write_scope must be NONE or BOUNDED"
        )
    normalized_mutation_scope = _normalize_mutation_scope(
        mutation_scope,
        f"{context}.mutation_scope",
    )
    has_mutation = bool(normalized_mutation_scope["operations"])
    if write_scope == "NONE" and has_mutation:
        raise ValueError(f"{context} NONE scope requires an empty mutation_scope")
    if write_scope == "BOUNDED" and not has_mutation:
        raise ValueError(f"{context} BOUNDED scope requires a non-empty mutation_scope")
    return write_scope, normalized_mutation_scope


def _normalize_execution_turns(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("execution_plan.turns must be a non-empty array")
    turns: list[dict[str, str]] = []
    turn_ids: set[str] = set()
    expected = {
        "turn_id",
        "role",
        "worker_slot_ref",
        "provider",
        "model",
        "reasoning_effort",
    }
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"execution_plan.turns[{index}] must be an object")
        _execution_exact_fields(item, expected, f"execution_plan.turns[{index}]")
        turn = {
            field: (
                _canonical_execution_role(
                    item.get(field), f"execution_plan.turns[{index}].{field}"
                )
                if field == "role"
                else _execution_text(
                    item.get(field), f"execution_plan.turns[{index}].{field}"
                )
            )
            for field in expected
        }
        if turn["turn_id"] in turn_ids:
            raise ValueError("execution_plan.turn_id values must be unique")
        turn_ids.add(turn["turn_id"])
        turns.append(turn)
    return turns


def _normalize_execution_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(value)
    value.setdefault("transcript_policy", TASK_FRAME_TRANSCRIPT_POLICY)
    value.setdefault("candidate_source_ref", "NONE")
    value.setdefault("source_review_result", None)
    expected = {
        "profile_id",
        "requested_shape",
        "resolved_shape",
        "model_mode",
        "frame_id",
        "origin_anchor_ref",
        "origin_session_id",
        "origin_frame_id",
        "task_summary_ref",
        "source_ref",
        "candidate_source_ref",
        "source_review_result",
        "parent_actor_ref",
        "commander_surface",
        "execution_assignment_ref",
        "host_worker_capability",
        "repository_write_scope",
        "mutation_scope",
        "fallback_reason",
        "transcript_policy",
        "turns",
    }
    _execution_exact_fields(value, expected, "execution_plan")
    requested_shape = _execution_text(
        value.get("requested_shape"), "execution_plan.requested_shape"
    ).upper()
    resolved_shape = _execution_text(
        value.get("resolved_shape"), "execution_plan.resolved_shape"
    ).upper()
    model_mode = _execution_text(
        value.get("model_mode"), "execution_plan.model_mode"
    ).upper()
    host_capability = _execution_text(
        value.get("host_worker_capability"),
        "execution_plan.host_worker_capability",
    ).upper()
    if requested_shape not in TASK_FRAME_EXECUTION_SHAPES:
        raise ValueError("execution_plan.requested_shape is unsupported")
    if resolved_shape not in TASK_FRAME_EXECUTION_SHAPES:
        raise ValueError("execution_plan.resolved_shape is unsupported")
    if model_mode not in TASK_FRAME_MODEL_MODES:
        raise ValueError("execution_plan.model_mode must be AUTO or EXPLICIT")
    if host_capability != "AVAILABLE":
        raise ValueError(
            "execution_plan.host_worker_capability must be AVAILABLE for Task Frame execution"
        )
    repository_write_scope, mutation_scope = _normalize_repository_boundary(
        repository_write_scope=value.get("repository_write_scope"),
        mutation_scope=value.get("mutation_scope"),
        context="execution_plan",
    )
    transcript_policy = _execution_text(
        value.get("transcript_policy"), "execution_plan.transcript_policy"
    )
    if transcript_policy != TASK_FRAME_TRANSCRIPT_POLICY:
        raise ValueError(
            "execution_plan.transcript_policy must be "
            f"{TASK_FRAME_TRANSCRIPT_POLICY}"
        )
    parent_actor_ref = _execution_text(
        value.get("parent_actor_ref"), "execution_plan.parent_actor_ref"
    )
    source_ref = _execution_text(value.get("source_ref"), "execution_plan.source_ref")
    candidate_source_ref = _execution_text(
        value.get("candidate_source_ref"),
        "execution_plan.candidate_source_ref",
    )
    source_review_result = _normalize_source_review_result(
        value.get("source_review_result"),
        policy_source_ref=source_ref,
        candidate_source_ref=candidate_source_ref,
    )
    turns = _normalize_execution_turns(value.get("turns"))
    return {
        "profile_id": _execution_text(
            value.get("profile_id"), "execution_plan.profile_id"
        ),
        "requested_shape": requested_shape,
        "resolved_shape": resolved_shape,
        "model_mode": model_mode,
        "frame_id": _execution_text(
            value.get("frame_id"), "execution_plan.frame_id"
        ),
        "origin_anchor_ref": _execution_text(
            value.get("origin_anchor_ref"), "execution_plan.origin_anchor_ref"
        ),
        "origin_session_id": _execution_text(
            value.get("origin_session_id"), "execution_plan.origin_session_id"
        ),
        "origin_frame_id": _execution_text(
            value.get("origin_frame_id"), "execution_plan.origin_frame_id"
        ),
        "task_summary_ref": _execution_text(
            value.get("task_summary_ref"), "execution_plan.task_summary_ref"
        ),
        "source_ref": source_ref,
        "candidate_source_ref": candidate_source_ref,
        "source_review_result": source_review_result,
        "parent_actor_ref": parent_actor_ref,
        "commander_surface": _execution_text(
            value.get("commander_surface"), "execution_plan.commander_surface"
        ),
        "execution_assignment_ref": _execution_text(
            value.get("execution_assignment_ref"),
            "execution_plan.execution_assignment_ref",
        ),
        "host_worker_capability": host_capability,
        "repository_write_scope": repository_write_scope,
        "mutation_scope": mutation_scope,
        "fallback_reason": _execution_text(
            value.get("fallback_reason"), "execution_plan.fallback_reason"
        ),
        "transcript_policy": transcript_policy,
        "turns": turns,
    }


def _normalize_source_review_result(
    value: Any, *, policy_source_ref: str, candidate_source_ref: str
) -> dict[str, Any] | None:
    if candidate_source_ref == "NONE":
        if value is not None:
            raise ValueError(
                "execution_plan.source_review_result requires candidate_source_ref"
            )
        return None
    if not isinstance(value, Mapping):
        raise ValueError(
            "execution_plan.source_review_result is required for Candidate review"
        )
    if value.get("schema") != "ai-career.source-review-result.v1":
        raise ValueError("execution_plan.source_review_result schema is unsupported")
    if value.get("status") != "SOURCE_REVIEW_PERMITTED":
        raise ValueError("execution_plan.source_review_result must be permitted")

    policy = value.get("policy_source")
    candidate = value.get("candidate_source")
    if not isinstance(policy, Mapping) or policy.get("ref") != policy_source_ref:
        raise ValueError(
            "execution_plan.source_review_result policy source does not match source_ref"
        )
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("ref") != candidate_source_ref
        or candidate.get("classification") != "DATA_ONLY"
        or candidate.get("policy_activation") != "FORBIDDEN"
    ):
        raise ValueError(
            "execution_plan.source_review_result Candidate boundary is invalid"
        )

    review_mode = value.get("review_mode")
    candidate_execution = value.get("candidate_execution")
    if review_mode == "STATIC_REVIEW" and candidate_execution != "FORBIDDEN":
        raise ValueError(
            "STATIC_REVIEW source_review_result must forbid Candidate execution"
        )
    if (
        review_mode == "SANDBOXED_EXECUTION_REVIEW"
        and candidate_execution != "SANDBOX_ONLY"
    ):
        raise ValueError(
            "SANDBOXED_EXECUTION_REVIEW source_review_result must be sandbox-only"
        )
    if review_mode not in {"STATIC_REVIEW", "SANDBOXED_EXECUTION_REVIEW"}:
        raise ValueError("execution_plan.source_review_result review mode is invalid")

    try:
        return json.loads(
            json.dumps(
                dict(value),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "execution_plan.source_review_result is not serializable"
        ) from error


def build_task_frame_execution_proposal(
    execution_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one exact, user-reviewable Task Frame execution proposal."""

    normalized = _normalize_execution_plan(execution_plan)
    plan_digest = _digest(normalized)
    proposal_id = "task_frame_proposal_" + plan_digest[:24]
    return {
        "schema": TASK_FRAME_EXECUTION_PROPOSAL_SCHEMA,
        "status": "TASK_FRAME_EXECUTION_PROPOSED",
        "proposal_id": proposal_id,
        "plan_digest": plan_digest,
        "approval_required": True,
        "execution_plan": normalized,
        "authority_created": False,
        "task_frame_started": False,
    }


def _validate_task_frame_execution_approval(
    *,
    profile: TaskFrameProofProfile,
    proposal: Mapping[str, Any] | None,
    approval: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise ValueError("task_frame_execution_proposal is required")
    _execution_exact_fields(
        proposal,
        {
            "schema",
            "status",
            "proposal_id",
            "plan_digest",
            "approval_required",
            "execution_plan",
            "authority_created",
            "task_frame_started",
        },
        "task_frame_execution_proposal",
    )
    if proposal.get("schema") != TASK_FRAME_EXECUTION_PROPOSAL_SCHEMA:
        raise ValueError("task_frame_execution_proposal schema is invalid")
    if proposal.get("status") != "TASK_FRAME_EXECUTION_PROPOSED":
        raise ValueError("task_frame_execution_proposal status is invalid")
    if proposal.get("approval_required") is not True:
        raise ValueError("task_frame_execution_proposal approval flag is invalid")
    if proposal.get("authority_created") is not False:
        raise ValueError("task_frame_execution_proposal cannot create authority")
    if proposal.get("task_frame_started") is not False:
        raise ValueError("task_frame_execution_proposal cannot start a Task Frame")
    plan_value = proposal.get("execution_plan")
    if not isinstance(plan_value, Mapping):
        raise ValueError("task_frame_execution_proposal.execution_plan is invalid")
    plan = _normalize_execution_plan(plan_value)
    expected_digest = _digest(plan)
    expected_id = "task_frame_proposal_" + expected_digest[:24]
    if (
        proposal.get("plan_digest") != expected_digest
        or proposal.get("proposal_id") != expected_id
    ):
        raise ValueError("task_frame_execution_proposal content hash is invalid")
    if plan["profile_id"] != profile.profile_id:
        raise ValueError(
            "task_frame_execution_proposal profile does not match Runtime profile"
        )
    if not isinstance(approval, Mapping):
        raise ValueError("task_frame_execution_approval is required")
    _execution_exact_fields(
        approval,
        {"status", "proposal_id", "plan_digest", "commander_surface", "evidence_ref"},
        "task_frame_execution_approval",
    )
    if (
        _execution_text(
            approval.get("status"), "task_frame_execution_approval.status"
        ).upper()
        != "APPROVED"
    ):
        raise ValueError("task_frame_execution_approval.status must be APPROVED")
    if approval.get("proposal_id") != expected_id:
        raise ValueError("task_frame_execution_approval does not match the current proposal")
    if approval.get("plan_digest") != expected_digest:
        raise ValueError("task_frame_execution_approval plan digest is stale")
    if approval.get("commander_surface") != plan["commander_surface"]:
        raise ValueError("task_frame_execution_approval commander surface is stale")
    return {
        "proposal_id": expected_id,
        "plan_digest": expected_digest,
        "approval_ref": _execution_text(
            approval.get("evidence_ref"), "task_frame_execution_approval.evidence_ref"
        ),
        "execution_plan": plan,
    }


def git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )


def git_text(repo_root: Path, *args: str) -> str | None:
    result = git(repo_root, *args)
    if result.returncode != 0:
        return None
    value = result.stdout.decode("utf-8", errors="replace").strip()
    return value or None


def safe_repo_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskFrameProfileError("contract reference must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value or path.as_posix() == ".":
        raise TaskFrameProfileError(f"unsafe contract reference: {value!r}")
    return path.as_posix()


def resolve_commit(repo_root: Path, value: Any) -> str | None:
    if not isinstance(value, str) or not IMMUTABLE_COMMIT_PATTERN.fullmatch(value):
        return None
    return git_text(repo_root, "rev-parse", "--verify", f"{value}^{{commit}}")


def git_path_exists(repo_root: Path, source_commit: str, path: str) -> bool:
    return git(repo_root, "cat-file", "-e", f"{source_commit}:{path}").returncode == 0


def require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskFrameProfileError(f"{context} must be a non-empty string")
    return value.strip()


def load_profile(repo_root: Path, profile_path: Path) -> TaskFrameProofProfile:
    """Load one immutable-commit-bound Task Frame profile."""

    try:
        raw = profile_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaskFrameProfileError(f"profile cannot be read as UTF-8 JSON: {error}") from error

    if not isinstance(payload, dict):
        raise TaskFrameProfileError("profile root must be an object")
    schema = payload.get("schema")
    if schema not in {PROFILE_SCHEMA, INSTALLED_PROFILE_SCHEMA}:
        raise TaskFrameProfileError(
            f"schema must be {PROFILE_SCHEMA} or {INSTALLED_PROFILE_SCHEMA}"
        )
    if payload.get("proof_scope") != GENERIC_WRITE_PROOF:
        raise TaskFrameProfileError(f"proof_scope must be {GENERIC_WRITE_PROOF}")
    profile_id = require_string(payload.get("profile_id"), "profile_id")

    source = payload.get("source")
    if not isinstance(source, dict):
        raise TaskFrameProfileError("source must be an object")
    binding = source.get("binding", "git-commit")
    if binding == "git-commit":
        source_repository = require_string(source.get("repository"), "source.repository")
        source_commit = resolve_commit(repo_root, source.get("commit"))
        if source_commit is None:
            raise TaskFrameProfileError(
                "source.commit must resolve to an immutable Git commit"
            )
        managed_paths = None
    elif binding == "installed-distribution":
        manifest_ref = safe_repo_path(
            source.get("installation_manifest", INSTALLATION_MANIFEST_PATH)
        )
        if manifest_ref != INSTALLATION_MANIFEST_PATH:
            raise TaskFrameProfileError(
                f"source.installation_manifest must be {INSTALLATION_MANIFEST_PATH}"
            )
        manifest_path = repo_root / PurePosixPath(manifest_ref)
        try:
            installation_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TaskFrameProfileError(
                f"installed distribution manifest cannot be read: {error}"
            ) from error
        if (
            not isinstance(installation_manifest, dict)
            or installation_manifest.get("schema") != INSTALLATION_MANIFEST_SCHEMA
        ):
            raise TaskFrameProfileError("installed distribution manifest schema is invalid")
        installed_source = installation_manifest.get("source")
        if not isinstance(installed_source, dict):
            raise TaskFrameProfileError("installed distribution source is invalid")
        source_repository = require_string(
            installed_source.get("repository"), "installation.source.repository"
        )
        source_commit = require_string(
            installed_source.get("commit"), "installation.source.commit"
        )
        if IMMUTABLE_COMMIT_PATTERN.fullmatch(source_commit) is None:
            raise TaskFrameProfileError(
                "installation.source.commit must be an immutable Git commit"
            )
        rows = installation_manifest.get("managed_paths")
        if not isinstance(rows, list):
            raise TaskFrameProfileError("installation.managed_paths must be an array")
        managed_paths = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise TaskFrameProfileError(
                    f"installation.managed_paths[{index}] must be an object"
                )
            target_path = safe_repo_path(row.get("target_path"))
            local_sha256 = row.get("local_sha256")
            if not isinstance(local_sha256, str) or SHA256_PATTERN.fullmatch(local_sha256) is None:
                raise TaskFrameProfileError(
                    f"installation.managed_paths[{index}].local_sha256 is invalid"
                )
            if target_path in managed_paths:
                raise TaskFrameProfileError(
                    f"installation.managed_paths contains duplicate target: {target_path}"
                )
            managed_paths[target_path] = row
        try:
            profile_ref = profile_path.resolve().relative_to(repo_root.resolve()).as_posix()
        except (OSError, ValueError) as error:
            raise TaskFrameProfileError(
                "installed distribution profile must be inside repo_root"
            ) from error
        profile_row = managed_paths.get(profile_ref)
        if not isinstance(profile_row, dict):
            raise TaskFrameProfileError(
                f"installed distribution profile is absent from manifest: {profile_ref}"
            )
        if profile_row.get("local_sha256") != sha256(raw):
            raise TaskFrameProfileError(
                f"installed distribution profile does not match manifest: {profile_ref}"
            )
    else:
        raise TaskFrameProfileError(f"unsupported source.binding: {binding}")

    refs_value = payload.get("contract_refs")
    if not isinstance(refs_value, list) or not refs_value:
        raise TaskFrameProfileError("contract_refs must be a non-empty list")
    contract_refs: list[str] = []
    for index, value in enumerate(refs_value):
        path = safe_repo_path(value)
        if managed_paths is None:
            if not git_path_exists(repo_root, source_commit, path):
                raise TaskFrameProfileError(
                    f"contract_refs[{index}] is absent at source commit: {path}"
                )
        else:
            row = managed_paths.get(path)
            if not isinstance(row, dict):
                raise TaskFrameProfileError(
                    f"contract_refs[{index}] is absent from installed manifest: {path}"
                )
            expected_sha256 = row.get("local_sha256")
            local_path = repo_root / PurePosixPath(path)
            try:
                actual_sha256 = sha256(local_path.read_bytes())
            except OSError as error:
                raise TaskFrameProfileError(
                    f"contract_refs[{index}] cannot be read: {path}"
                ) from error
            if expected_sha256 != actual_sha256:
                raise TaskFrameProfileError(
                    f"contract_refs[{index}] does not match installed manifest: {path}"
                )
        contract_refs.append(path)

    policy = payload.get("frame_policy")
    if not isinstance(policy, dict):
        raise TaskFrameProfileError("frame_policy must be an object")
    required_policy = {
        "storage_scope": "TASK_FRAME_ONLY",
        "worker_authority": "NONE",
        "parent_queue_mutation": "FORBIDDEN",
        "vendor_worker_spawn": "HOST_ONLY",
        "origin_anchor_restore": "FORBIDDEN",
        "result_adoption": "PARENT_ONLY",
    }
    for field, expected in required_policy.items():
        if policy.get(field) != expected:
            raise TaskFrameProfileError(f"frame_policy.{field} must be {expected}")
    if schema == INSTALLED_PROFILE_SCHEMA:
        execution_policy = {
            "execution_approval": "REQUIRED",
            "parent_participation": "FORBIDDEN",
            "nested_worker_invoker": "BOSS_ONLY",
            "parent_instruction_repository_boundary": "REQUIRED",
            "sub_mutation_lineage": "REQUIRED_FOR_MUTATION",
            "worker_invocation_evidence": "REQUIRED",
            "worker_actor_binding": "REQUIRED",
            "parent_visibility": TASK_FRAME_TRANSCRIPT_POLICY,
            "hidden_reasoning": "FORBIDDEN",
        }
        for field, expected in execution_policy.items():
            if policy.get(field) != expected:
                raise TaskFrameProfileError(
                    f"frame_policy.{field} must be {expected}"
                )
        execution_approval_required = True
        parent_participation_forbidden = True
        worker_invocation_evidence_required = True
    else:
        execution_approval_required = False
        parent_participation_forbidden = False
        worker_invocation_evidence_required = False
    max_active_turns = policy.get("max_active_turns")
    if (
        not isinstance(max_active_turns, int)
        or isinstance(max_active_turns, bool)
        or max_active_turns < 1
    ):
        raise TaskFrameProfileError("frame_policy.max_active_turns must be a positive integer")

    return TaskFrameProofProfile(
        profile_id=profile_id,
        source_repository=source_repository,
        source_commit=source_commit,
        contract_refs=tuple(contract_refs),
        max_active_turns=max_active_turns,
        profile_path=str(profile_path),
        profile_sha256=sha256(raw),
        execution_approval_required=execution_approval_required,
        parent_participation_forbidden=parent_participation_forbidden,
        worker_invocation_evidence_required=worker_invocation_evidence_required,
    )


class TaskFrameRuntime:
    """Deterministic Task Frame interpreter with optional file-backed journal."""

    def __init__(
        self,
        *,
        profile: TaskFrameProofProfile,
        frame_id: str,
        origin_anchor_ref: str,
        origin_session_id: str,
        origin_frame_id: str,
        task_summary_ref: str,
        source_ref: str,
        origin_governance_session_ref: str = "UNKNOWN",
        execution_assignment_ref: str = "UNASSIGNED",
        task_frame_execution_proposal: Mapping[str, Any] | None = None,
        task_frame_execution_approval: Mapping[str, Any] | None = None,
        parent_instruction: Mapping[str, Any] | None = None,
        dispatch_topology: Mapping[str, Any] | None = None,
        database_path: str | Path | None = None,
        parent_observation: ParentObservation,
        observed_at: str,
    ) -> None:
        self.profile = profile
        self.run_id = str(uuid.uuid4())
        self.database_path, self.storage_kind = self._database_location(database_path)
        self.conn = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.storage_kind == "FILE":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._ensure_profile_path_column(profile.profile_path)
        self._ensure_instruction_boundary_columns()

        timestamp = self._normalize_timestamp(observed_at)
        if not timestamp:
            self.close()
            raise ValueError("observed_at must be an ISO-8601 timestamp with timezone")

        self.frame_id = self._required_value(frame_id, "frame_id")
        self.origin_anchor_ref = self._required_value(origin_anchor_ref, "origin_anchor_ref")
        self.origin_session_id = self._required_value(origin_session_id, "origin_session_id")
        self.origin_frame_id = self._required_value(origin_frame_id, "origin_frame_id")
        self.origin_governance_session_ref = self._required_value(
            origin_governance_session_ref, "origin_governance_session_ref"
        )
        self.task_summary_ref = self._required_value(task_summary_ref, "task_summary_ref")
        self.source_ref = self._required_value(source_ref, "source_ref")
        self.execution_assignment_ref = self._required_value(
            execution_assignment_ref, "execution_assignment_ref"
        )
        self.execution_gate: dict[str, Any] | None = None
        self.execution_turns: dict[str, dict[str, str]] = {}
        self.worker_invocation_plans: dict[str, dict[str, str]] = {}
        self.worker_claim_evidence: dict[str, dict[str, str]] = {}
        self.worker_result_envelopes: dict[str, dict[str, Any]] = {}
        self.worker_slot_actors: dict[str, str] = {}
        self.worker_invocation_receipts: set[str] = set()

        existing_context = self._context_or_none()
        if existing_context is not None:
            self._restore_existing_context(
                context=existing_context,
                task_frame_execution_proposal=task_frame_execution_proposal,
                task_frame_execution_approval=task_frame_execution_approval,
                dispatch_topology=dispatch_topology,
            )
            return

        self.dispatch_topology = self._normalize_dispatch_topology(dispatch_topology)
        if profile.execution_approval_required:
            try:
                self.execution_gate = _validate_task_frame_execution_approval(
                    profile=profile,
                    proposal=task_frame_execution_proposal,
                    approval=task_frame_execution_approval,
                )
            except Exception:
                self.close()
                raise
            execution_plan = self.execution_gate["execution_plan"]
            expected_coordinates = {
                "frame_id": self.frame_id,
                "origin_anchor_ref": self.origin_anchor_ref,
                "origin_session_id": self.origin_session_id,
                "origin_frame_id": self.origin_frame_id,
                "task_summary_ref": self.task_summary_ref,
                "source_ref": self.source_ref,
                "execution_assignment_ref": self.execution_assignment_ref,
            }
            stale_coordinate = next(
                (
                    field
                    for field, expected in expected_coordinates.items()
                    if execution_plan[field] != expected
                ),
                None,
            )
            if stale_coordinate is not None:
                self.close()
                raise ValueError(
                    f"{stale_coordinate} does not match the approved Task Frame plan"
                )
            self.execution_turns = {
                turn["turn_id"]: dict(turn) for turn in execution_plan["turns"]
            }
        normalized_parent = self._normalize_parent_observation(parent_observation)
        attachment_state = self._attachment_for_parent_status(normalized_parent.status)

        self.conn.execute(
            """
            INSERT INTO task_frame_context(
                singleton, profile_id, profile_path, source_commit, source_ref, frame_id,
                origin_anchor_ref, origin_session_id, origin_frame_id,
                origin_governance_session_ref, task_summary_ref,
                execution_assignment_ref, task_state,
                attachment_state, adoption_state, current_parent_status,
                current_parent_evidence_ref, dispatch_topology_json,
                execution_gate_json, created_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DECLARATION_REQUIRED', ?, 'CANDIDATE', ?, ?, ?, ?, ?)
            """,
            (
                profile.profile_id,
                profile.profile_path,
                profile.source_commit,
                self.source_ref,
                self.frame_id,
                self.origin_anchor_ref,
                self.origin_session_id,
                self.origin_frame_id,
                self.origin_governance_session_ref,
                self.task_summary_ref,
                self.execution_assignment_ref,
                attachment_state,
                normalized_parent.status,
                normalized_parent.evidence_ref,
                json.dumps(
                    self.dispatch_topology,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                json.dumps(
                    self.execution_gate or {},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if self.execution_gate is not None
                else "",
                timestamp,
            ),
        )
        self._record_parent_observation(normalized_parent, timestamp)
        frame_created_details = {
            "frame_id": self.frame_id,
            "origin_anchor_ref": self.origin_anchor_ref,
            "origin_governance_session_ref": self.origin_governance_session_ref,
            "task_summary_ref": self.task_summary_ref,
            "source_ref": self.source_ref,
        }
        if self.storage_kind == "FILE":
            frame_created_details["journal_storage"] = self.storage_kind
        if self.execution_gate is not None:
            frame_created_details["execution_proposal_id"] = self.execution_gate[
                "proposal_id"
            ]
        self._append_journal(
            "FRAME_CREATED",
            "",
            frame_created_details,
            timestamp,
        )
        if self.execution_gate is not None and parent_instruction is None:
            self.close()
            raise ValueError("parent_instruction is required for an approved Task Frame")
        if parent_instruction is not None:
            instruction_result = self.record_parent_instruction(
                instruction=parent_instruction,
                observed_at=timestamp,
            )
            if instruction_result["status"] != "PARENT_INSTRUCTION_RECORDED":
                self.close()
                raise ValueError(
                    f"parent_instruction invalid: {instruction_result['status']}"
                )

    def close(self) -> None:
        self.conn.close()

    @classmethod
    def open_existing(
        cls,
        *,
        profile: TaskFrameProofProfile,
        database_path: str | Path,
    ) -> "TaskFrameRuntime":
        """Open a persisted Task Frame without inventing Parent input or coordinates."""

        instance = cls.__new__(cls)
        instance.profile = profile
        instance.run_id = str(uuid.uuid4())
        instance.database_path, instance.storage_kind = instance._database_location(
            database_path
        )
        if instance.storage_kind != "FILE" or not Path(instance.database_path).is_file():
            raise ValueError("task frame database is unavailable")
        instance.conn = sqlite3.connect(
            instance.database_path, timeout=5, isolation_level=None
        )
        instance.conn.row_factory = sqlite3.Row
        instance.conn.execute("PRAGMA journal_mode=WAL")
        instance.conn.executescript(SCHEMA)
        instance._ensure_profile_path_column(profile.profile_path)
        instance._ensure_instruction_boundary_columns()
        context = instance._context_or_none()
        if context is None:
            instance.close()
            raise ValueError("task frame database has no context")
        instance._set_context_coordinates(context)
        instance._load_context_coordinates(context)
        instance.dispatch_topology = instance._dispatch_topology_from_context(context)
        instance.execution_gate = instance._execution_gate_from_context(context)
        instance.execution_turns = (
            {
                turn["turn_id"]: dict(turn)
                for turn in instance.execution_gate["execution_plan"]["turns"]
            }
            if instance.execution_gate is not None
            else {}
        )
        instance.worker_invocation_plans = {}
        instance.worker_claim_evidence = {}
        instance.worker_result_envelopes = {}
        instance.worker_slot_actors = {}
        instance.worker_invocation_receipts = set()
        instance._hydrate_worker_execution_state()
        return instance

    @staticmethod
    def persisted_profile_path(database_path: str | Path) -> str:
        path = Path(database_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("task frame database is unavailable")
        connection = sqlite3.connect(path, timeout=5)
        try:
            try:
                row = connection.execute(
                    "SELECT profile_path FROM task_frame_context WHERE singleton = 1"
                ).fetchone()
            except sqlite3.OperationalError as error:
                raise ValueError(
                    "persisted Task Frame profile path is unavailable"
                ) from error
        finally:
            connection.close()
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            raise ValueError("persisted Task Frame profile path is unavailable")
        return row[0].strip()

    @staticmethod
    def _database_location(database_path: str | Path | None) -> tuple[str, str]:
        if database_path is None or str(database_path).strip() in {"", ":memory:"}:
            return ":memory:", "MEMORY"
        path = Path(database_path).expanduser().resolve()
        if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("task frame database path must use a SQLite file suffix")
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path), "FILE"

    def _context_or_none(self) -> sqlite3.Row | None:
        try:
            return self.conn.execute(
                "SELECT * FROM task_frame_context WHERE singleton = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None

    def _ensure_profile_path_column(self, profile_path: str) -> None:
        columns = {
            str(row["name"])
            for row in self.conn.execute(
                "PRAGMA table_info(task_frame_context)"
            ).fetchall()
        }
        if "profile_path" not in columns:
            self.conn.execute(
                "ALTER TABLE task_frame_context "
                "ADD COLUMN profile_path TEXT NOT NULL DEFAULT 'UNKNOWN'"
            )
        self.conn.execute(
            """
            UPDATE task_frame_context
            SET profile_path = ?
            WHERE singleton = 1 AND profile_path = 'UNKNOWN'
            """,
            (profile_path,),
        )

    def _ensure_instruction_boundary_columns(self) -> None:
        columns = {
            str(row["name"])
            for row in self.conn.execute(
                "PRAGMA table_info(task_instructions)"
            ).fetchall()
        }
        if "repository_write_scope" not in columns:
            self.conn.execute(
                "ALTER TABLE task_instructions "
                "ADD COLUMN repository_write_scope TEXT NOT NULL DEFAULT 'UNKNOWN'"
            )
        if "mutation_scope_json" not in columns:
            self.conn.execute(
                "ALTER TABLE task_instructions "
                "ADD COLUMN mutation_scope_json TEXT NOT NULL "
                "DEFAULT '{\"operations\":[],\"targets\":[]}'"
            )

    def _load_context_coordinates(self, context: sqlite3.Row) -> None:
        expected = {
            "profile_id": self.profile.profile_id,
            "source_commit": self.profile.source_commit,
            "source_ref": self.source_ref,
            "frame_id": self.frame_id,
            "origin_anchor_ref": self.origin_anchor_ref,
            "origin_session_id": self.origin_session_id,
            "origin_frame_id": self.origin_frame_id,
            "origin_governance_session_ref": self.origin_governance_session_ref,
            "task_summary_ref": self.task_summary_ref,
            "execution_assignment_ref": self.execution_assignment_ref,
        }
        mismatch = next(
            (
                key
                for key, value in expected.items()
                if str(context[key]) != str(value)
            ),
            None,
        )
        if mismatch is not None:
            self.close()
            raise ValueError(f"persisted Task Frame {mismatch} does not match the request")

    def _set_context_coordinates(self, context: sqlite3.Row) -> None:
        self.frame_id = str(context["frame_id"])
        self.origin_anchor_ref = str(context["origin_anchor_ref"])
        self.origin_session_id = str(context["origin_session_id"])
        self.origin_frame_id = str(context["origin_frame_id"])
        self.origin_governance_session_ref = str(
            context["origin_governance_session_ref"]
        )
        self.task_summary_ref = str(context["task_summary_ref"])
        self.source_ref = str(context["source_ref"])
        self.execution_assignment_ref = str(context["execution_assignment_ref"])

    def _restore_existing_context(
        self,
        *,
        context: sqlite3.Row,
        task_frame_execution_proposal: Mapping[str, Any] | None,
        task_frame_execution_approval: Mapping[str, Any] | None,
        dispatch_topology: Mapping[str, Any] | None,
    ) -> None:
        self._load_context_coordinates(context)
        self.dispatch_topology = self._dispatch_topology_from_context(context)
        if dispatch_topology is not None and self._normalize_dispatch_topology(
            dispatch_topology
        ) != self.dispatch_topology:
            self.close()
            raise ValueError("persisted Task Frame dispatch topology does not match the request")
        self.execution_gate = self._execution_gate_from_context(context)
        if self.profile.execution_approval_required and self.execution_gate is None:
            self.close()
            raise ValueError("persisted Task Frame execution gate is missing")
        if task_frame_execution_proposal is not None or task_frame_execution_approval is not None:
            try:
                requested_gate = _validate_task_frame_execution_approval(
                    profile=self.profile,
                    proposal=task_frame_execution_proposal,
                    approval=task_frame_execution_approval,
                )
            except Exception:
                self.close()
                raise
            if self.execution_gate is None or (
                requested_gate["plan_digest"] != self.execution_gate["plan_digest"]
            ):
                self.close()
                raise ValueError("persisted Task Frame execution plan does not match the request")
        self.execution_turns = (
            {
                turn["turn_id"]: dict(turn)
                for turn in self.execution_gate["execution_plan"]["turns"]
            }
            if self.execution_gate is not None
            else {}
        )
        self._hydrate_worker_execution_state()

    def _dispatch_topology_from_context(self, context: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(str(context["dispatch_topology_json"]))
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            self.close()
            raise ValueError("persisted Task Frame dispatch topology is invalid") from error
        return self._normalize_dispatch_topology(value)

    def _execution_gate_from_context(self, context: sqlite3.Row) -> dict[str, Any] | None:
        raw = str(context["execution_gate_json"])
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            self.close()
            raise ValueError("persisted Task Frame execution gate is invalid") from error
        if not isinstance(value, dict):
            self.close()
            raise ValueError("persisted Task Frame execution gate is invalid")
        return value

    def _normalize_dispatch_topology(
        self, value: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        default = {
            "interaction_carrier": "UNKNOWN",
            "execution_host": {
                "ref": "UNKNOWN",
                "capability": "UNKNOWN",
                "binding_evidence_ref": "UNKNOWN",
            },
            "write_target": {
                "ref": "UNKNOWN",
                "capability": "UNKNOWN",
                "binding_evidence_ref": "UNKNOWN",
            },
        }
        if value is None:
            return default
        if not isinstance(value, Mapping) or set(value) != set(default):
            raise ValueError("dispatch_topology has an invalid shape")

        normalized: dict[str, Any] = {
            "interaction_carrier": self._required_value(
                value.get("interaction_carrier"), "dispatch_topology.interaction_carrier"
            )
        }
        for key in ("execution_host", "write_target"):
            candidate = value.get(key)
            if not isinstance(candidate, Mapping) or set(candidate) != {
                "ref",
                "capability",
                "binding_evidence_ref",
            }:
                raise ValueError(f"dispatch_topology.{key} has an invalid shape")
            capability = self._required_value(
                candidate.get("capability"), f"dispatch_topology.{key}.capability"
            ).upper()
            if capability not in HOST_CAPABILITY_STATUSES:
                raise ValueError(f"dispatch_topology.{key}.capability is invalid")
            normalized[key] = {
                "ref": self._required_value(candidate.get("ref"), f"dispatch_topology.{key}.ref"),
                "capability": capability,
                "binding_evidence_ref": self._required_value(
                    candidate.get("binding_evidence_ref"),
                    f"dispatch_topology.{key}.binding_evidence_ref",
                ),
            }
        return normalized

    def _hydrate_worker_execution_state(self) -> None:
        rows = self.conn.execute(
            """
            SELECT turn_id, worker_slot_ref, capability_evidence_ref,
                   invoker_actor_ref, worker_path, worker_actor_ref,
                   host_invocation_receipt_ref, worker_result_digest,
                   host_result_evidence_ref, worker_result_envelope_json
            FROM worker_execution_state
            """
        ).fetchall()
        for row in rows:
            turn_id = str(row["turn_id"])
            if str(row["capability_evidence_ref"]):
                self.worker_invocation_plans[turn_id] = {
                    "capability_evidence_ref": str(row["capability_evidence_ref"]),
                    "worker_slot_ref": str(row["worker_slot_ref"]),
                    "invoker_actor_ref": str(row["invoker_actor_ref"]),
                    "worker_path": str(row["worker_path"]),
                }
            if str(row["worker_actor_ref"]):
                claim = {
                    "worker_actor_ref": str(row["worker_actor_ref"]),
                    "worker_slot_ref": str(row["worker_slot_ref"]),
                    "host_invocation_receipt_ref": str(row["host_invocation_receipt_ref"]),
                    "capability_evidence_ref": str(row["capability_evidence_ref"]),
                    "invoker_actor_ref": str(row["invoker_actor_ref"]),
                    "worker_path": str(row["worker_path"]),
                }
                if str(row["worker_result_digest"]):
                    claim["worker_result_digest"] = str(row["worker_result_digest"])
                    claim["host_result_evidence_ref"] = str(row["host_result_evidence_ref"])
                self.worker_claim_evidence[turn_id] = claim
                self.worker_slot_actors[claim["worker_slot_ref"]] = claim["worker_actor_ref"]
                self.worker_invocation_receipts.add(claim["host_invocation_receipt_ref"])
            if str(row["worker_result_envelope_json"]):
                self.worker_result_envelopes[turn_id] = {
                    "worker_result_digest": str(row["worker_result_digest"]),
                    "host_result_evidence_ref": str(row["host_result_evidence_ref"]),
                    "envelope": json.loads(str(row["worker_result_envelope_json"])),
                }

    def _persist_worker_execution_state(self, turn_id: str) -> None:
        plan = self.worker_invocation_plans.get(turn_id, {})
        claim = self.worker_claim_evidence.get(turn_id, {})
        envelope = self.worker_result_envelopes.get(turn_id, {})
        self.conn.execute(
            """
            INSERT INTO worker_execution_state(
                turn_id, worker_slot_ref, capability_evidence_ref,
                invoker_actor_ref, worker_path, worker_actor_ref,
                host_invocation_receipt_ref, worker_result_digest,
                host_result_evidence_ref, worker_result_envelope_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                worker_slot_ref = excluded.worker_slot_ref,
                capability_evidence_ref = excluded.capability_evidence_ref,
                invoker_actor_ref = excluded.invoker_actor_ref,
                worker_path = excluded.worker_path,
                worker_actor_ref = excluded.worker_actor_ref,
                host_invocation_receipt_ref = excluded.host_invocation_receipt_ref,
                worker_result_digest = excluded.worker_result_digest,
                host_result_evidence_ref = excluded.host_result_evidence_ref,
                worker_result_envelope_json = excluded.worker_result_envelope_json
            """,
            (
                turn_id,
                str(claim.get("worker_slot_ref", plan.get("worker_slot_ref", ""))),
                str(claim.get("capability_evidence_ref", plan.get("capability_evidence_ref", ""))),
                str(claim.get("invoker_actor_ref", plan.get("invoker_actor_ref", ""))),
                str(claim.get("worker_path", plan.get("worker_path", ""))),
                str(claim.get("worker_actor_ref", "")),
                str(claim.get("host_invocation_receipt_ref", "")),
                str(claim.get("worker_result_digest", envelope.get("worker_result_digest", ""))),
                str(claim.get("host_result_evidence_ref", envelope.get("host_result_evidence_ref", ""))),
                json.dumps(
                    envelope.get("envelope"),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if envelope else "",
            ),
        )

    def observe_parent(
        self, *, parent_observation: ParentObservation, observed_at: str
    ) -> dict[str, Any]:
        """Record caller-provided Parent evidence without inferring currentness."""

        timestamp = self._normalize_timestamp(observed_at)
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        normalized = self._normalize_parent_observation(parent_observation)
        self._record_parent_observation(normalized, timestamp)
        return {
            "status": "PARENT_OBSERVATION_RECORDED",
            "parent_observation": self.parent_observation_snapshot(),
        }

    def record_parent_instruction(
        self, *, instruction: Mapping[str, Any], observed_at: str
    ) -> dict[str, Any]:
        """Append a Parent instruction without decomposing or rewording it."""

        timestamp = self._normalize_timestamp(observed_at)
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        if not isinstance(instruction, Mapping) or set(instruction) != {
            "instruction_id",
            "user_instruction_raw",
            "constraints",
            "expected_output",
            "repository_write_scope",
            "mutation_scope",
        }:
            return {"status": "PARENT_INSTRUCTION_INVALID"}
        instruction_id = str(instruction.get("instruction_id", "")).strip()
        raw_instruction = instruction.get("user_instruction_raw")
        constraints = instruction.get("constraints")
        expected_output = instruction.get("expected_output")
        if not instruction_id:
            return {"status": "PARENT_INSTRUCTION_ID_REQUIRED"}
        if not isinstance(raw_instruction, str) or not raw_instruction.strip():
            return {"status": "PARENT_INSTRUCTION_TEXT_REQUIRED"}
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) and item.strip() for item in constraints
        ):
            return {"status": "PARENT_INSTRUCTION_CONSTRAINTS_INVALID"}
        try:
            repository_write_scope, mutation_scope = _normalize_repository_boundary(
                repository_write_scope=instruction.get("repository_write_scope"),
                mutation_scope=instruction.get("mutation_scope"),
                context="parent_instruction",
            )
        except ValueError:
            return {"status": "PARENT_INSTRUCTION_REPOSITORY_BOUNDARY_INVALID"}
        if self.execution_gate is not None:
            execution_plan = self.execution_gate["execution_plan"]
            if (
                repository_write_scope
                != execution_plan.get("repository_write_scope")
                or mutation_scope != execution_plan.get("mutation_scope")
            ):
                return {"status": "PARENT_INSTRUCTION_EXECUTION_PLAN_MISMATCH"}
        existing_instructions = self.instructions()
        if existing_instructions:
            first = existing_instructions[0]
            if (
                repository_write_scope != first["repository_write_scope"]
                or mutation_scope != first["mutation_scope"]
            ):
                return {
                    "status": "PARENT_INSTRUCTION_REPOSITORY_BOUNDARY_CHANGED"
                }
        try:
            constraints_json = json.dumps(
                constraints, ensure_ascii=True, separators=(",", ":")
            )
            expected_output_json = json.dumps(
                expected_output,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            mutation_scope_json = json.dumps(
                mutation_scope,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return {"status": "PARENT_INSTRUCTION_NOT_SERIALIZABLE"}

        canonical = {
            "instruction_id": instruction_id,
            "parent_session_id": self.origin_session_id,
            "parent_frame_id": self.origin_frame_id,
            "parent_anchor_ref": self.origin_anchor_ref,
            "user_instruction_raw": raw_instruction,
            "constraints": constraints,
            "expected_output": expected_output,
            "repository_write_scope": repository_write_scope,
            "mutation_scope": mutation_scope,
        }
        instruction_digest = _digest(canonical)
        try:
            self.conn.execute(
                """
                INSERT INTO task_instructions(
                    instruction_id, parent_session_id, parent_frame_id,
                    parent_anchor_ref, user_instruction_raw, constraints_json,
                    expected_output_json, repository_write_scope,
                    mutation_scope_json, instruction_digest, state, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECORDED', ?)
                """,
                (
                    instruction_id,
                    self.origin_session_id,
                    self.origin_frame_id,
                    self.origin_anchor_ref,
                    raw_instruction,
                    constraints_json,
                    expected_output_json,
                    repository_write_scope,
                    mutation_scope_json,
                    instruction_digest,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError:
            return {"status": "PARENT_INSTRUCTION_DUPLICATED"}
        self._append_journal(
            "PARENT_INSTRUCTION_RECORDED",
            "",
            {
                "instruction_id": instruction_id,
                "instruction_digest": instruction_digest,
                "parent_session_id": self.origin_session_id,
                "parent_frame_id": self.origin_frame_id,
                "parent_anchor_ref": self.origin_anchor_ref,
                "repository_write_scope": repository_write_scope,
                "mutation_scope": mutation_scope,
            },
            timestamp,
        )
        return {
            "status": "PARENT_INSTRUCTION_RECORDED",
            "instruction": self.instruction_snapshot(instruction_id),
        }

    def declare_turns(
        self, *, turns: Iterable[TaskTurn], observed_at: str
    ) -> dict[str, Any]:
        if self._current_parent_status() != "MATCHED":
            return self._parent_not_matched_result()
        if self._task_state() != "DECLARATION_REQUIRED":
            return {"status": "TASK_TURNS_ALREADY_DECLARED"}
        timestamp = self._normalize_timestamp(observed_at)
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        normalized = self._normalize_turns(turns)
        if isinstance(normalized, str):
            return {"status": normalized}
        if self.execution_gate is not None:
            declared = [(turn.turn_id, turn.role) for turn in normalized]
            approved = [
                (turn["turn_id"], turn["role"])
                for turn in self.execution_gate["execution_plan"]["turns"]
            ]
            if declared != approved:
                return {
                    "status": "TASK_FRAME_EXECUTION_PLAN_MISMATCH",
                    "approved_turns": approved,
                    "declared_turns": declared,
                }

        roots = [turn for turn in normalized if not turn.input_turn_ids]
        if len(roots) != 1:
            return {"status": "TASK_ROOT_TURN_REQUIRED"}

        self.conn.executemany(
            """
            INSERT INTO task_turns(
                turn_id, role, input_turn_ids_json, accept_turn_id, return_turn_id,
                terminal_on_accept, state, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'DECLARED', ?)
            """,
            [
                (
                    turn.turn_id,
                    turn.role,
                    json.dumps(turn.input_turn_ids, ensure_ascii=True, separators=(",", ":")),
                    turn.accept_turn_id,
                    turn.return_turn_id,
                    int(turn.terminal_on_accept),
                    timestamp,
                )
                for turn in normalized
            ],
        )
        self.conn.execute(
            "UPDATE task_turns SET state = 'READY' WHERE turn_id = ?", (roots[0].turn_id,)
        )
        self._set_task_state("ACTIVE")
        self._append_journal(
            "TURNS_DECLARED",
            roots[0].turn_id,
            {"turn_ids": [turn.turn_id for turn in normalized]},
            timestamp,
        )
        return {
            "status": "TASK_TURNS_DECLARED",
            "root_turn_id": roots[0].turn_id,
            "task_frame": self.task_frame_snapshot(),
        }

    def worker_invocation_plan(
        self,
        *,
        turn_id: str,
        host_capability_status: str,
        capability_evidence_ref: str = "",
        invoker_actor_ref: str = "",
        observed_at: str,
    ) -> dict[str, Any]:
        """Return a plan only. This runtime never spawns a vendor Worker."""

        timestamp = self._normalize_timestamp(observed_at)
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        capability = host_capability_status.strip().upper()
        if capability not in HOST_CAPABILITY_STATUSES:
            return {"status": "HOST_CAPABILITY_STATUS_INVALID"}
        if self._current_parent_status() != "MATCHED":
            return self._parent_not_matched_result()

        turn = self.turn_snapshot(turn_id)
        if turn is None:
            return {"status": "TURN_NOT_FOUND"}
        if turn["state"] != "READY":
            return {"status": "TURN_NOT_READY", "turn": turn}
        invoker_binding = self._nested_invoker_binding(turn)
        normalized_invoker_ref = invoker_actor_ref.strip()
        if invoker_binding is not None:
            expected_invoker_ref, worker_path = invoker_binding
            if normalized_invoker_ref != expected_invoker_ref:
                return {
                    "status": (
                        "BOSS_SUB_INVOCATION_REQUIRED"
                        if turn["role"] == "SUB_REVIEWER"
                        else "PARENT_BOSS_INVOCATION_REQUIRED"
                    ),
                    "expected_invoker_actor_ref": expected_invoker_ref,
                    "worker_path": worker_path,
                }
        bundle = self.input_bundle(turn_id=turn["turn_id"])
        if bundle["status"] != "TURN_INPUTS_READY":
            return bundle

        if capability != "AVAILABLE":
            self._append_journal(
                "WORKER_INVOCATION_UNKNOWN",
                turn["turn_id"],
                {"host_capability_status": capability},
                timestamp,
            )
            return {
                "status": "WORKER_INVOCATION_UNKNOWN",
                "worker_invocation": {
                    "status": "UNKNOWN",
                    "turn_id": turn["turn_id"],
                    "reason": f"HOST_CAPABILITY_{capability}",
                },
            }

        normalized_capability_ref = capability_evidence_ref.strip()
        if (
            self.profile.worker_invocation_evidence_required
            and (
                not normalized_capability_ref
                or normalized_capability_ref.upper() == "UNKNOWN"
            )
        ):
            return {
                "status": "WORKER_INVOCATION_EVIDENCE_REQUIRED",
                "worker_invocation": {
                    "status": "UNKNOWN",
                    "turn_id": turn["turn_id"],
                    "reason": "CAPABILITY_EVIDENCE_REQUIRED",
                },
            }

        planned_turn = self.execution_turns.get(turn["turn_id"], {})
        invocation_details = {"role": turn["role"]}
        if self.execution_gate is not None:
            self.worker_invocation_plans[turn["turn_id"]] = {
                "capability_evidence_ref": normalized_capability_ref,
                "worker_slot_ref": str(planned_turn.get("worker_slot_ref", "")),
                "invoker_actor_ref": normalized_invoker_ref,
                "worker_path": (
                    invoker_binding[1] if invoker_binding is not None else ""
                ),
            }
            self._persist_worker_execution_state(turn["turn_id"])
            invocation_details.update(
                {
                    "capability_evidence_ref": normalized_capability_ref,
                    "worker_slot_ref": planned_turn["worker_slot_ref"],
                    "invoker_actor_ref": normalized_invoker_ref,
                    "worker_path": (
                        invoker_binding[1] if invoker_binding is not None else ""
                    ),
                }
            )

        self._append_journal(
            "WORKER_INVOCATION_PLANNED",
            turn["turn_id"],
            invocation_details,
            timestamp,
        )
        execution_details = (
            {
                "worker_slot_ref": planned_turn["worker_slot_ref"],
                "provider": planned_turn["provider"],
                "model": planned_turn["model"],
                "reasoning_effort": planned_turn["reasoning_effort"],
                "capability_evidence_ref": normalized_capability_ref,
                "invoker_actor_ref": normalized_invoker_ref,
                "worker_path": (
                    invoker_binding[1] if invoker_binding is not None else ""
                ),
            }
            if planned_turn
            else {}
        )
        return {
            "status": "WORKER_INVOCATION_READY",
            "worker_invocation": {
                "status": "READY",
                "turn_id": turn["turn_id"],
                "role": turn["role"],
                "input_bundle": bundle,
                **execution_details,
            },
        }

    def claim_turn(
        self,
        *,
        turn_id: str,
        worker_id: str,
        host_invocation_receipt_ref: str = "",
        capability_evidence_ref: str = "",
        invoker_actor_ref: str = "",
        observed_at: str,
    ) -> dict[str, Any]:
        if self._current_parent_status() != "MATCHED":
            return self._parent_not_matched_result()
        if self._task_state() != "ACTIVE":
            return {"status": "TASK_NOT_ACTIVE", "task_frame": self.task_frame_snapshot()}

        timestamp = self._normalize_timestamp(observed_at)
        normalized_turn_id = turn_id.strip()
        normalized_worker_id = worker_id.strip()
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        if not normalized_turn_id:
            return {"status": "TURN_ID_REQUIRED"}
        if not normalized_worker_id:
            return {"status": "WORKER_ID_REQUIRED"}
        if self.execution_gate is not None:
            parent_actor_ref = self.execution_gate["execution_plan"]["parent_actor_ref"]
            if normalized_worker_id == parent_actor_ref:
                return {
                    "status": "PARENT_SELF_SUBSTITUTION_BLOCKED",
                    "worker_invocation": {
                        "status": "UNKNOWN",
                        "turn_id": normalized_turn_id,
                        "reason": "PARENT_ACTOR_CANNOT_CLAIM_WORKER_TURN",
                    },
                }
            planned = self.worker_invocation_plans.get(normalized_turn_id)
            if planned is None:
                return {"status": "WORKER_INVOCATION_PLAN_REQUIRED"}
            normalized_receipt_ref = host_invocation_receipt_ref.strip()
            normalized_capability_ref = capability_evidence_ref.strip()
            normalized_invoker_ref = invoker_actor_ref.strip()
            if (
                not normalized_receipt_ref
                or normalized_receipt_ref.upper() == "UNKNOWN"
            ):
                return {"status": "WORKER_INVOCATION_RECEIPT_REQUIRED"}
            if normalized_capability_ref != planned["capability_evidence_ref"]:
                return {"status": "WORKER_CAPABILITY_EVIDENCE_MISMATCH"}
            if normalized_invoker_ref != planned["invoker_actor_ref"]:
                return {"status": "WORKER_INVOKER_ACTOR_MISMATCH"}
            if normalized_receipt_ref in self.worker_invocation_receipts:
                return {"status": "WORKER_INVOCATION_RECEIPT_REUSED"}
            worker_slot_ref = planned["worker_slot_ref"]
            bound_actor = self.worker_slot_actors.get(worker_slot_ref)
            if bound_actor is not None and bound_actor != normalized_worker_id:
                return {"status": "WORKER_SLOT_ACTOR_MISMATCH"}

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            active_count = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM task_turns WHERE state = 'CLAIMED'"
                ).fetchone()[0]
            )
            if self._boss_allocation_required_for_turn(normalized_turn_id):
                active_count -= int(
                    self.conn.execute(
                        """
                        SELECT COUNT(*) FROM task_turns
                        WHERE state = 'CLAIMED' AND role = 'BOSS'
                          AND input_turn_ids_json = '[]'
                        """
                    ).fetchone()[0]
                )
            if active_count >= self.profile.max_active_turns:
                self.conn.execute("ROLLBACK")
                return {
                    "status": "MAX_ACTIVE_TURNS_REACHED",
                    "max_active_turns": self.profile.max_active_turns,
                }

            cursor = self.conn.execute(
                """
                UPDATE task_turns
                SET state = 'CLAIMED', claimed_by = ?, claimed_at = ?
                WHERE turn_id = ? AND state = 'READY'
                """,
                (normalized_worker_id, timestamp, normalized_turn_id),
            )
            if cursor.rowcount != 1:
                turn = self.turn_snapshot(normalized_turn_id)
                self.conn.execute("ROLLBACK")
                return {
                    "status": "TURN_NOT_READY" if turn is not None else "TURN_NOT_FOUND",
                    "turn": turn,
                }

            claim_details = {"worker_id": normalized_worker_id}
            if self.execution_gate is not None:
                claim_details.update(
                    {
                        "host_invocation_receipt_ref": host_invocation_receipt_ref.strip(),
                        "capability_evidence_ref": capability_evidence_ref.strip(),
                        "invoker_actor_ref": invoker_actor_ref.strip(),
                        "worker_path": planned["worker_path"],
                    }
                )
            self._append_journal(
                "TURN_CLAIMED",
                normalized_turn_id,
                claim_details,
                timestamp,
            )
            if self.execution_gate is not None:
                worker_slot_ref = self.worker_invocation_plans[normalized_turn_id][
                    "worker_slot_ref"
                ]
                self.worker_slot_actors.setdefault(worker_slot_ref, normalized_worker_id)
                self.worker_invocation_receipts.add(host_invocation_receipt_ref.strip())
                self.worker_claim_evidence[normalized_turn_id] = {
                    "worker_actor_ref": normalized_worker_id,
                    "worker_slot_ref": worker_slot_ref,
                    "host_invocation_receipt_ref": host_invocation_receipt_ref.strip(),
                    "capability_evidence_ref": capability_evidence_ref.strip(),
                    "invoker_actor_ref": invoker_actor_ref.strip(),
                    "worker_path": self.worker_invocation_plans[normalized_turn_id][
                        "worker_path"
                    ],
                }
                self._persist_worker_execution_state(normalized_turn_id)
            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            self._hydrate_worker_execution_state()
            raise
        return {"status": "TURN_CLAIMED", "turn": self.turn_snapshot(normalized_turn_id)}

    def complete_turn(
        self,
        *,
        turn_id: str,
        worker_id: str,
        result: Mapping[str, Any],
        evidence_refs: Iterable[str],
        observed_at: str,
        review_decision: str = "",
        host_invocation_receipt_ref: str = "",
        _worker_envelope_digest: str = "",
    ) -> dict[str, Any]:
        timestamp = self._normalize_timestamp(observed_at)
        normalized_turn_id = turn_id.strip()
        normalized_worker_id = worker_id.strip()
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        if not normalized_turn_id:
            return {"status": "TURN_ID_REQUIRED"}
        if not normalized_worker_id:
            return {"status": "WORKER_ID_REQUIRED"}
        if self.execution_gate is not None:
            if not _worker_envelope_digest:
                return {"status": "WORKER_RESULT_ENVELOPE_REQUIRED"}
            claim_evidence = self.worker_claim_evidence.get(normalized_turn_id)
            if claim_evidence is None:
                return {"status": "WORKER_INVOCATION_RECEIPT_REQUIRED"}
            if claim_evidence["worker_actor_ref"] != normalized_worker_id:
                return {"status": "WORKER_ACTOR_MISMATCH"}
            if (
                claim_evidence["host_invocation_receipt_ref"]
                != host_invocation_receipt_ref.strip()
            ):
                return {"status": "WORKER_INVOCATION_RECEIPT_MISMATCH"}
            envelope = self.worker_result_envelopes.get(normalized_turn_id)
            if envelope is None:
                return {"status": "WORKER_RESULT_ENVELOPE_REQUIRED"}
            if envelope["worker_result_digest"] != _worker_envelope_digest:
                return {"status": "WORKER_RESULT_DIGEST_MISMATCH"}
        if not isinstance(result, Mapping):
            return {"status": "TURN_RESULT_INVALID"}
        if PROHIBITED_TURN_RESULT_CLAIMS.intersection(result):
            return {"status": "PROHIBITED_TURN_RESULT_CLAIM"}
        normalized_evidence = self._normalize_evidence_refs(evidence_refs)
        if isinstance(normalized_evidence, str):
            return {"status": normalized_evidence}
        try:
            result_json = json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            return {"status": "TURN_RESULT_NOT_SERIALIZABLE"}

        turn = self.turn_snapshot(normalized_turn_id)
        if turn is None:
            return {"status": "TURN_NOT_FOUND"}
        if turn["state"] != "CLAIMED" or turn["claimed_by"] != normalized_worker_id:
            return {"status": "TURN_NOT_CLAIMED_BY_WORKER", "turn": turn}

        if self._current_parent_status() != "MATCHED":
            self._store_final(
                turn_id=normalized_turn_id,
                state="UNATTACHED",
                result_json=result_json,
                evidence_refs=normalized_evidence,
                review_decision="",
                observed_at=timestamp,
            )
            self._set_task_state("RESULT_UNATTACHED", attachment_state="UNATTACHED")
            self._append_journal(
                "TURN_COMPLETED_UNATTACHED",
                normalized_turn_id,
                {"parent_status": self._current_parent_status()},
                timestamp,
            )
            return {
                "status": "TURN_COMPLETED_UNATTACHED",
                "parent_status": self._current_parent_status(),
                "turn": self.turn_snapshot(normalized_turn_id),
            }

        decision = review_decision.strip().upper()
        has_routes = bool(turn["accept_turn_id"] or turn["return_turn_id"] or turn["terminal_on_accept"])
        if has_routes and decision not in REVIEW_DECISIONS:
            return {"status": "REVIEW_DECISION_REQUIRED"}
        if not has_routes and decision:
            return {"status": "TURN_DECISION_NOT_ALLOWED"}
        if decision == "RETURN" and not turn["return_turn_id"]:
            return {"status": "REVIEW_RETURN_ROUTE_REQUIRED"}
        if decision == "ACCEPT" and not (turn["accept_turn_id"] or turn["terminal_on_accept"]):
            return {"status": "REVIEW_ACCEPT_ROUTE_REQUIRED"}

        self._store_final(
            turn_id=normalized_turn_id,
            state="COMPLETED",
            result_json=result_json,
            evidence_refs=normalized_evidence,
            review_decision=decision,
            observed_at=timestamp,
        )
        self._append_journal(
            "TURN_COMPLETED",
            normalized_turn_id,
            {"review_decision": decision or None},
            timestamp,
        )

        if not has_routes:
            activation = self._activate_dependency_ready_turns(timestamp)
            if activation["status"] != "DEPENDENCY_TURNS_EVALUATED":
                self._set_task_state("BLOCKED")
                return {"status": "TASK_TURN_ORDER_BLOCKED", "route": activation}
            self._finalize_if_complete(timestamp)
            return {
                "status": "TASK_COMPLETED" if self._task_state() == "COMPLETED" else "TURN_COMPLETED",
                "ready_turn_ids": activation["turn_ids"],
                "turn": self.turn_snapshot(normalized_turn_id),
            }

        if decision in {"BLOCKED", "UNKNOWN"}:
            self._set_task_state("BLOCKED" if decision == "BLOCKED" else "REVIEW_UNKNOWN")
            return {
                "status": "TASK_BLOCKED" if decision == "BLOCKED" else "TASK_REVIEW_UNKNOWN",
                "turn": self.turn_snapshot(normalized_turn_id),
            }

        selected_turn_id = turn["accept_turn_id"] if decision == "ACCEPT" else turn["return_turn_id"]
        skipped_turn_id = turn["return_turn_id"] if decision == "ACCEPT" else turn["accept_turn_id"]
        if skipped_turn_id:
            self._mark_branch_skipped(skipped_turn_id, timestamp)

        if decision == "ACCEPT" and turn["terminal_on_accept"]:
            self._set_task_state("COMPLETED")
            self._append_journal("TASK_COMPLETED", normalized_turn_id, {"terminal": True}, timestamp)
            return {"status": "TASK_COMPLETED", "turn": self.turn_snapshot(normalized_turn_id)}

        activation = self._activate_route(selected_turn_id, timestamp)
        if activation["status"] != "TURN_READY":
            self._set_task_state("BLOCKED")
            return {"status": "TASK_ROUTE_BLOCKED", "route": activation}
        return {
            "status": "TURN_ACCEPTED" if decision == "ACCEPT" else "TURN_RETURNED",
            "next_turn_id": selected_turn_id,
            "turn": self.turn_snapshot(normalized_turn_id),
        }

    def submit_boss_allocations(
        self,
        *,
        boss_turn_id: str,
        boss_worker_id: str,
        host_invocation_receipt_ref: str,
        instruction_digests: Iterable[str],
        worker_allocations: Iterable[Mapping[str, Any]],
        observed_at: str,
    ) -> dict[str, Any]:
        """Let the active Boss allocate declared child turns without completing."""

        timestamp = self._normalize_timestamp(observed_at)
        normalized_turn_id = boss_turn_id.strip()
        normalized_worker_id = boss_worker_id.strip()
        normalized_receipt_ref = host_invocation_receipt_ref.strip()
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        turn = self.turn_snapshot(normalized_turn_id)
        if turn is None:
            return {"status": "TURN_NOT_FOUND"}
        if turn["role"] != "BOSS" or turn["input_turn_ids"]:
            return {"status": "ROOT_BOSS_TURN_REQUIRED"}
        if turn["state"] != "CLAIMED" or turn["claimed_by"] != normalized_worker_id:
            return {"status": "BOSS_TURN_NOT_CLAIMED", "turn": turn}
        claim_evidence = self.worker_claim_evidence.get(normalized_turn_id)
        if claim_evidence is None:
            return {"status": "WORKER_INVOCATION_RECEIPT_REQUIRED"}
        if claim_evidence["host_invocation_receipt_ref"] != normalized_receipt_ref:
            return {"status": "WORKER_INVOCATION_RECEIPT_MISMATCH"}
        normalized_digests = [str(item).strip() for item in instruction_digests]
        normalized_allocations = list(worker_allocations)
        return self._record_boss_allocations(
            turn=turn,
            worker_id=normalized_worker_id,
            instruction_digests=normalized_digests,
            allocations=normalized_allocations,
            observed_at=timestamp,
        )

    def boss_result_bundle(
        self,
        *,
        boss_turn_id: str,
        boss_worker_id: str,
        host_invocation_receipt_ref: str,
    ) -> dict[str, Any]:
        """Return recorded child results only to the still-claimed Boss."""

        turn = self.turn_snapshot(boss_turn_id.strip())
        if turn is None:
            return {"status": "TURN_NOT_FOUND"}
        if (
            turn["role"] != "BOSS"
            or turn["input_turn_ids"]
            or turn["state"] != "CLAIMED"
            or turn["claimed_by"] != boss_worker_id.strip()
        ):
            return {"status": "BOSS_TURN_NOT_CLAIMED", "turn": turn}
        claim_evidence = self.worker_claim_evidence.get(turn["turn_id"])
        if (
            claim_evidence is None
            or claim_evidence["host_invocation_receipt_ref"]
            != host_invocation_receipt_ref.strip()
        ):
            return {"status": "WORKER_INVOCATION_RECEIPT_MISMATCH"}
        completion = self._boss_completion_status(turn["turn_id"])
        if completion["status"] != "BOSS_COMPLETION_READY":
            return completion
        child_results = []
        for allocation in self.allocations():
            child_turn = self.turn_snapshot(allocation["turn_id"])
            child_results.append(
                {
                    "allocation": allocation,
                    "turn": child_turn,
                    "worker_result_envelope": self.worker_result_envelopes.get(
                        allocation["turn_id"], {}
                    ).get("envelope"),
                }
            )
        return {
            "status": "BOSS_RESULT_BUNDLE_READY",
            "boss_turn_id": turn["turn_id"],
            "parent_instruction_bundle": self.instruction_bundle(),
            "child_results": child_results,
        }

    def submit_worker_envelope(
        self,
        *,
        envelope: Mapping[str, Any],
        host_result_evidence_ref: str,
        observed_at: str,
    ) -> dict[str, Any]:
        """Bind and preserve one Host-captured Worker envelope unchanged."""

        if self.execution_gate is None:
            return {"status": "WORKER_RESULT_ENVELOPE_NOT_REQUIRED"}
        timestamp = self._normalize_timestamp(observed_at)
        if not timestamp:
            return {"status": "OBSERVED_AT_INVALID"}
        expected = {
            "turn_id",
            "worker_id",
            "host_invocation_receipt_ref",
            "status",
            "evidence_refs",
            "result",
            "review_decision",
        }
        if set(envelope) != expected:
            return {"status": "WORKER_RESULT_ENVELOPE_INVALID"}
        turn_id = str(envelope.get("turn_id", "")).strip()
        worker_id = str(envelope.get("worker_id", "")).strip()
        receipt_ref = str(envelope.get("host_invocation_receipt_ref", "")).strip()
        result_status = str(envelope.get("status", "")).strip().upper()
        evidence_refs = envelope.get("evidence_refs")
        result = envelope.get("result")
        raw_review_decision = envelope.get("review_decision", "")
        if not isinstance(raw_review_decision, str):
            return {"status": "WORKER_RESULT_ENVELOPE_INVALID"}
        review_decision = raw_review_decision
        normalized_host_evidence = host_result_evidence_ref.strip()
        if not turn_id or not worker_id or not receipt_ref:
            return {"status": "WORKER_RESULT_ENVELOPE_INVALID"}
        if result_status not in {"COMPLETED", "FAILED", "UNKNOWN"}:
            return {"status": "WORKER_RESULT_STATUS_INVALID"}
        if not isinstance(evidence_refs, list) or not all(
            isinstance(item, str) for item in evidence_refs
        ):
            return {"status": "WORKER_RESULT_EVIDENCE_INVALID"}
        if not isinstance(result, Mapping):
            return {"status": "TURN_RESULT_INVALID"}
        if PROHIBITED_TURN_RESULT_CLAIMS.intersection(result):
            return {"status": "PROHIBITED_TURN_RESULT_CLAIM"}
        normalized_evidence = self._normalize_evidence_refs(evidence_refs)
        if isinstance(normalized_evidence, str):
            return {"status": normalized_evidence}
        if (
            not normalized_host_evidence
            or normalized_host_evidence.upper() == "UNKNOWN"
        ):
            return {"status": "HOST_WORKER_RESULT_EVIDENCE_REQUIRED"}
        if turn_id in self.worker_result_envelopes:
            previous = self.worker_result_envelopes[turn_id]
            try:
                candidate_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "turn_id": turn_id,
                            "worker_id": worker_id,
                            "host_invocation_receipt_ref": receipt_ref,
                            "status": result_status,
                            "evidence_refs": list(evidence_refs),
                            "result": dict(result),
                            "review_decision": review_decision,
                        },
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
            except (TypeError, ValueError):
                return {"status": "WORKER_RESULT_NOT_SERIALIZABLE"}
            turn = self.turn_snapshot(turn_id)
            if (
                previous["worker_result_digest"] == candidate_digest
                and turn is not None
                and turn["state"] in {"COMPLETED", "UNATTACHED"}
            ):
                return {
                    "status": "WORKER_RESULT_ENVELOPE_REPLAYED",
                    "worker_result_digest": candidate_digest,
                    "turn": turn,
                }
            return {"status": "WORKER_RESULT_ENVELOPE_ALREADY_RECORDED"}
        claim_evidence = self.worker_claim_evidence.get(turn_id)
        if claim_evidence is None:
            return {"status": "WORKER_INVOCATION_RECEIPT_REQUIRED"}
        if claim_evidence["worker_actor_ref"] != worker_id:
            return {"status": "WORKER_ACTOR_MISMATCH"}
        if claim_evidence["host_invocation_receipt_ref"] != receipt_ref:
            return {"status": "WORKER_INVOCATION_RECEIPT_MISMATCH"}
        turn = self.turn_snapshot(turn_id)
        if turn is None:
            return {"status": "TURN_NOT_FOUND"}
        if turn["state"] != "CLAIMED" or turn["claimed_by"] != worker_id:
            return {"status": "TURN_NOT_CLAIMED_BY_WORKER", "turn": turn}
        decision = review_decision.strip().upper()
        has_routes = bool(
            turn["accept_turn_id"]
            or turn["return_turn_id"]
            or turn["terminal_on_accept"]
        )
        if has_routes and decision not in REVIEW_DECISIONS:
            return {"status": "REVIEW_DECISION_REQUIRED"}
        if not has_routes and decision:
            return {"status": "TURN_DECISION_NOT_ALLOWED"}
        if decision == "RETURN" and not turn["return_turn_id"]:
            return {"status": "REVIEW_RETURN_ROUTE_REQUIRED"}
        if decision == "ACCEPT" and not (
            turn["accept_turn_id"] or turn["terminal_on_accept"]
        ):
            return {"status": "REVIEW_ACCEPT_ROUTE_REQUIRED"}
        if turn["role"] == "BOSS" and not turn["input_turn_ids"]:
            boss_completion = self._boss_completion_status(turn["turn_id"])
            if boss_completion["status"] != "BOSS_COMPLETION_READY":
                return boss_completion

        normalized_envelope = {
            "turn_id": turn_id,
            "worker_id": worker_id,
            "host_invocation_receipt_ref": receipt_ref,
            "status": result_status,
            "evidence_refs": list(evidence_refs),
            "result": dict(result),
            "review_decision": review_decision,
        }
        try:
            encoded = json.dumps(
                normalized_envelope,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            return {"status": "WORKER_RESULT_NOT_SERIALIZABLE"}
        digest = hashlib.sha256(encoded).hexdigest()
        preserved = json.loads(encoded.decode("utf-8"))
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.worker_result_envelopes[turn_id] = {
                "worker_result_digest": digest,
                "host_result_evidence_ref": normalized_host_evidence,
                "envelope": preserved,
            }
            self.worker_claim_evidence.setdefault(turn_id, {}).update(
                {
                    "worker_result_digest": digest,
                    "host_result_evidence_ref": normalized_host_evidence,
                }
            )
            self._persist_worker_execution_state(turn_id)
            self._append_journal(
                "WORKER_RESULT_ENVELOPE_RECORDED",
                turn_id,
                {
                    "worker_result_digest": digest,
                    "host_result_evidence_ref": normalized_host_evidence,
                },
                timestamp,
            )
            completed = self.complete_turn(
                turn_id=turn_id,
                worker_id=worker_id,
                result=preserved["result"],
                evidence_refs=preserved["evidence_refs"],
                observed_at=timestamp,
                review_decision=preserved["review_decision"],
                host_invocation_receipt_ref=receipt_ref,
                _worker_envelope_digest=digest,
            )
            if not (
                str(completed.get("status", "")).startswith("TURN_COMPLETED")
                or completed.get("status") == "TASK_COMPLETED"
            ):
                raise ValueError(
                    f"worker envelope completion failed: {completed.get('status', 'UNKNOWN')}"
                )
            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            self.worker_result_envelopes.pop(turn_id, None)
            self._hydrate_worker_execution_state()
            raise
        return {
            **completed,
            "worker_result_digest": digest,
            "host_result_evidence_ref": normalized_host_evidence,
        }

    def input_bundle(self, *, turn_id: str) -> dict[str, Any]:
        turn = self.turn_snapshot(turn_id)
        if turn is None:
            return {"status": "TURN_NOT_FOUND"}

        allocation = (
            self.allocation_snapshot(turn["turn_id"])
            if turn["role"] == "SUB_REVIEWER"
            else None
        )
        inputs: list[dict[str, Any]] = []
        for input_turn_id in turn["input_turn_ids"]:
            input_turn = self.turn_snapshot(input_turn_id)
            if (
                allocation is not None
                and input_turn is not None
                and input_turn["role"] == "BOSS"
                and not input_turn["input_turn_ids"]
            ):
                continue
            if input_turn is None or input_turn["state"] != "COMPLETED":
                return {"status": "TURN_INPUTS_NOT_READY", "input_turn_id": input_turn_id}
            inputs.append(
                {
                    "turn_id": input_turn["turn_id"],
                    "role": input_turn["role"],
                    "result": input_turn["result"],
                    "evidence_refs": input_turn["evidence_refs"],
                    "review_decision": input_turn["review_decision"],
                }
            )
        bundle: dict[str, Any] = {
            "status": "TURN_INPUTS_READY",
            "task_summary_ref": self.task_summary_ref,
            "parent_context": {
                "session_id": self.origin_session_id,
                "frame_id": self.origin_frame_id,
                "anchor_ref": self.origin_anchor_ref,
                "task_frame_id": self.frame_id,
            },
            "inputs": inputs,
        }
        if self.execution_gate is not None:
            source_review_result = self.execution_gate["execution_plan"].get(
                "source_review_result"
            )
            if source_review_result is not None:
                bundle["source_review_result"] = source_review_result
        if self.instructions() and (
            turn["role"] == "BOSS"
            or (
                not turn["input_turn_ids"]
                and not any(
                    candidate["role"] == "BOSS"
                    and not candidate["input_turn_ids"]
                    for candidate in self.turns()
                )
            )
        ):
            instruction_bundle = self.instruction_bundle()
            if instruction_bundle["status"] != "PARENT_INSTRUCTION_BUNDLE_READY":
                return instruction_bundle
            bundle["parent_instruction_bundle"] = instruction_bundle
            bundle["dispatch_context"] = self.dispatch_snapshot()
        elif turn["role"] == "SUB_REVIEWER":
            if allocation is None and self._boss_allocation_required_for_turn(
                turn["turn_id"]
            ):
                return {
                    "status": "BOSS_ALLOCATION_REQUIRED",
                    "turn_id": turn["turn_id"],
                }
            if allocation is not None:
                bundle["boss_allocation"] = allocation
        return bundle

    def build_result_packet(self) -> dict[str, Any]:
        final_turns = [
            turn for turn in self.turns() if turn["state"] in {"COMPLETED", "UNATTACHED"}
        ]
        if not final_turns:
            return {"status": "RESULT_PACKET_NOT_READY"}

        if self._task_state() == "RESULT_UNATTACHED" or any(
            turn["state"] == "UNATTACHED" for turn in final_turns
        ):
            attachment_state = "UNATTACHED"
        else:
            attachment_state = self._attachment_for_parent_status(self._current_parent_status())
        packet = {
            "frame_id": self.frame_id,
            "origin_anchor_ref": self.origin_anchor_ref,
            "turn_refs": [turn["turn_id"] for turn in final_turns],
            "evidence_refs": self._unique_refs(
                ref for turn in final_turns for ref in turn["evidence_refs"]
            ),
            "attachment_state": attachment_state,
            "adoption_state": "CANDIDATE",
        }
        if self.execution_gate is not None:
            packet["worker_result_envelopes"] = [
                self.worker_result_envelopes[turn["turn_id"]]["envelope"]
                for turn in final_turns
                if turn["turn_id"] in self.worker_result_envelopes
            ]
        return {"status": "RESULT_PACKET_BUILT", "result_packet": packet}

    def execution_evidence(self) -> dict[str, Any]:
        """Return raw ledger observations. No derived authority/state claims."""

        evidence: dict[str, Any] = {
            "schema": "ai-career.task-frame-execution-evidence.v0",
            "run_id": self.run_id,
            "profile": {
                "profile_id": self.profile.profile_id,
                "source_commit": self.profile.source_commit,
                "profile_sha256": self.profile.profile_sha256,
                "contract_refs": list(self.profile.contract_refs),
            },
            "frame": {
                "frame_id": self.frame_id,
                "origin_anchor_ref": self.origin_anchor_ref,
                "origin_session_id": self.origin_session_id,
                "origin_frame_id": self.origin_frame_id,
                "origin_governance_session_ref": self.origin_governance_session_ref,
                "task_summary_ref": self.task_summary_ref,
                "source_ref": self.source_ref,
            },
            "parent_observations": self.parent_observations(),
            "turns": self.turns(),
            "journal": self.journal(),
        }
        if self.instructions():
            evidence["parent_instructions"] = self.instructions()
            evidence["boss_allocations"] = self.allocations()
        if self.storage_kind == "FILE":
            evidence["journal_storage"] = {
                "kind": self.storage_kind,
                "database_path": self.database_path,
            }
        if self.execution_gate is not None:
            worker_invocations = []
            for turn in self.execution_gate["execution_plan"]["turns"]:
                turn_id = turn["turn_id"]
                planned = self.worker_invocation_plans.get(turn_id, {})
                claimed = self.worker_claim_evidence.get(turn_id, {})
                worker_invocations.append(
                    {
                        "turn_id": turn_id,
                        "worker_slot_ref": turn["worker_slot_ref"],
                        "capability_evidence_ref": planned.get(
                            "capability_evidence_ref"
                        ),
                        "invoker_actor_ref": planned.get("invoker_actor_ref"),
                        "worker_path": planned.get("worker_path"),
                        "worker_actor_ref": claimed.get("worker_actor_ref"),
                        "host_invocation_receipt_ref": claimed.get(
                            "host_invocation_receipt_ref"
                        ),
                        "worker_result_digest": claimed.get(
                            "worker_result_digest"
                        ),
                        "host_result_evidence_ref": claimed.get(
                            "host_result_evidence_ref"
                        ),
                        "worker_result_envelope": self.worker_result_envelopes.get(
                            turn_id, {}
                        ).get("envelope"),
                    }
                )
            evidence["execution_gate"] = {
                "proposal_id": self.execution_gate["proposal_id"],
                "plan_digest": self.execution_gate["plan_digest"],
                "approval_ref": self.execution_gate["approval_ref"],
                "execution_plan": self.execution_gate["execution_plan"],
                "worker_invocations": worker_invocations,
            }
        return evidence

    def runtime_state(self) -> dict[str, Any]:
        """Return a disposable Task Frame-derived view, not an authority object."""

        context = self._context()
        task_frame_state: dict[str, Any] = {
            "frame_id": self.frame_id,
            "task_state": str(context["task_state"]),
            "attachment_state": str(context["attachment_state"]),
            "adoption_state": str(context["adoption_state"]),
            "turn_counts": self._turn_counts(),
            "evidence_bundle_ref": "task_frame_execution_evidence",
        }
        if self.instructions():
            task_frame_state["instruction_counts"] = self._instruction_counts()
        state: dict[str, Any] = {
            "schema": "ai-career.task-frame-runtime-state.v0",
            "run_id": self.run_id,
            "profile_id": self.profile.profile_id,
            "source_commit": self.profile.source_commit,
            "task_frame": task_frame_state,
        }
        if self.storage_kind == "FILE":
            state["journal_storage"] = {
                "kind": self.storage_kind,
                "database_path": self.database_path,
            }
        if self.execution_gate is not None:
            state["task_frame"]["execution_gate"] = {
                "status": "APPROVED",
                "proposal_id": self.execution_gate["proposal_id"],
                "evidence_ref": "task_frame_execution_evidence.execution_gate",
            }
        return state

    def task_frame_snapshot(self) -> dict[str, Any]:
        context = self._context()
        snapshot = {
            "frame_id": self.frame_id,
            "origin_anchor_ref": self.origin_anchor_ref,
            "origin_governance_session_ref": self.origin_governance_session_ref,
            "task_summary_ref": self.task_summary_ref,
            "task_state": str(context["task_state"]),
            "attachment_state": str(context["attachment_state"]),
            "adoption_state": str(context["adoption_state"]),
            "turn_counts": self._turn_counts(),
        }
        if self.instructions():
            snapshot["instruction_counts"] = self._instruction_counts()
        return snapshot

    def dispatch_snapshot(self) -> dict[str, Any]:
        """Return the immutable Parent-to-Boss dispatch record.

        This is a copied origin coordinate and task purpose. It never observes,
        refreshes, or mutates the Mode Current Anchor.
        """

        return {
            "frame_id": self.frame_id,
            "origin_anchor_snapshot": {
                "anchor_ref": self.origin_anchor_ref,
                "session_id": self.origin_session_id,
                "frame_id": self.origin_frame_id,
                "governance_session_ref": self.origin_governance_session_ref,
            },
            "purpose_ref": self.task_summary_ref,
            "source_ref": self.source_ref,
            "execution_assignment_ref": self.execution_assignment_ref,
            "topology": self.dispatch_topology,
        }

    def instruction_snapshot(self, instruction_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT instruction_id, parent_session_id, parent_frame_id,
                   parent_anchor_ref, user_instruction_raw, constraints_json,
                   expected_output_json, repository_write_scope,
                   mutation_scope_json, instruction_digest, state,
                   acknowledged_by, acknowledged_at, recorded_at
            FROM task_instructions WHERE instruction_id = ?
            """,
            (instruction_id.strip(),),
        ).fetchone()
        return self._instruction_from_row(row) if row is not None else None

    def instructions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT instruction_id, parent_session_id, parent_frame_id,
                   parent_anchor_ref, user_instruction_raw, constraints_json,
                   expected_output_json, repository_write_scope,
                   mutation_scope_json, instruction_digest, state,
                   acknowledged_by, acknowledged_at, recorded_at
            FROM task_instructions ORDER BY instruction_ordinal
            """
        ).fetchall()
        return [self._instruction_from_row(row) for row in rows]

    def instruction_bundle(self) -> dict[str, Any]:
        instructions = self.instructions()
        if not instructions:
            return {"status": "PARENT_INSTRUCTION_REQUIRED"}
        return {
            "status": "PARENT_INSTRUCTION_BUNDLE_READY",
            "parent_context": {
                "session_id": self.origin_session_id,
                "frame_id": self.origin_frame_id,
                "anchor_ref": self.origin_anchor_ref,
                "governance_session_ref": self.origin_governance_session_ref,
                "task_frame_id": self.frame_id,
            },
            "instruction_digests": [
                instruction["instruction_digest"] for instruction in instructions
            ],
            "instructions": instructions,
        }

    def allocation_snapshot(self, turn_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT instruction_digest, boss_turn_id, boss_worker_id, turn_id,
                   worker_slot_ref, worker_path, task_text, expected_output_json,
                   mutation_scope_json, allocation_digest, recorded_at
            FROM boss_allocations WHERE turn_id = ?
            """,
            (turn_id.strip(),),
        ).fetchone()
        return self._allocation_from_row(row) if row is not None else None

    def allocations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT instruction_digest, boss_turn_id, boss_worker_id, turn_id,
                   worker_slot_ref, worker_path, task_text, expected_output_json,
                   mutation_scope_json, allocation_digest, recorded_at
            FROM boss_allocations ORDER BY allocation_ordinal
            """
        ).fetchall()
        return [self._allocation_from_row(row) for row in rows]

    def verify_sub_mutation_lineage(
        self,
        *,
        lineage: Mapping[str, Any],
        operation: str,
        target: str,
    ) -> dict[str, Any]:
        """Verify one claimed Sub mutation against its Boss allocation."""

        required_fields = {
            "task_frame_id",
            "parent_assignment_id",
            "boss_allocation_id",
            "sub_turn_id",
            "sub_worker_id",
            "worker_path",
        }
        if not isinstance(lineage, Mapping) or set(lineage) != required_fields:
            return {"status": "TASK_FRAME_MUTATION_LINEAGE_INVALID"}
        normalized = {
            field: str(lineage.get(field, "")).strip() for field in required_fields
        }
        if not all(normalized.values()):
            return {"status": "TASK_FRAME_MUTATION_LINEAGE_INVALID"}
        if normalized["task_frame_id"] != self.frame_id:
            return {"status": "TASK_FRAME_MUTATION_FRAME_MISMATCH"}
        if normalized["parent_assignment_id"] != self.execution_assignment_ref:
            return {"status": "TASK_FRAME_MUTATION_ASSIGNMENT_MISMATCH"}

        allocation = self.allocation_snapshot(normalized["sub_turn_id"])
        if allocation is None:
            return {"status": "TASK_FRAME_MUTATION_ALLOCATION_NOT_FOUND"}
        if normalized["boss_allocation_id"] != allocation["allocation_id"]:
            return {"status": "TASK_FRAME_MUTATION_ALLOCATION_MISMATCH"}
        if normalized["worker_path"] != allocation["worker_path"]:
            return {"status": "TASK_FRAME_MUTATION_WORKER_PATH_MISMATCH"}

        turn = self.turn_snapshot(normalized["sub_turn_id"])
        if turn is None or turn["state"] != "CLAIMED":
            return {"status": "TASK_FRAME_MUTATION_SUB_NOT_CLAIMED"}
        if turn["claimed_by"] != normalized["sub_worker_id"]:
            return {"status": "TASK_FRAME_MUTATION_SUB_WORKER_MISMATCH"}
        claim = self.worker_claim_evidence.get(normalized["sub_turn_id"])
        if claim is None:
            return {"status": "TASK_FRAME_MUTATION_CLAIM_EVIDENCE_REQUIRED"}
        if claim.get("worker_path") != allocation["worker_path"]:
            return {"status": "TASK_FRAME_MUTATION_WORKER_PATH_MISMATCH"}
        if claim.get("invoker_actor_ref") != allocation["boss_worker_id"]:
            return {"status": "TASK_FRAME_MUTATION_BOSS_INVOKER_MISMATCH"}

        normalized_operation = str(operation).strip().upper()
        normalized_target = os.path.normpath(str(target).strip())
        mutation_scope = allocation["mutation_scope"]
        if normalized_operation not in mutation_scope["operations"]:
            return {"status": "TASK_FRAME_MUTATION_OPERATION_NOT_ALLOCATED"}
        if normalized_target not in mutation_scope["targets"]:
            return {"status": "TASK_FRAME_MUTATION_TARGET_NOT_ALLOCATED"}

        verified = {
            "status": "VERIFIED",
            "task_frame_id": self.frame_id,
            "parent_assignment_id": self.execution_assignment_ref,
            "boss_allocation_id": allocation["allocation_id"],
            "boss_allocation_digest": allocation["allocation_digest"],
            "boss_turn_id": allocation["boss_turn_id"],
            "boss_worker_id": allocation["boss_worker_id"],
            "sub_turn_id": normalized["sub_turn_id"],
            "sub_worker_id": normalized["sub_worker_id"],
            "worker_path": allocation["worker_path"],
            "operation": normalized_operation,
            "target": normalized_target,
        }
        verified["lineage_digest"] = _digest(verified)
        return verified

    def parent_observation_snapshot(self) -> dict[str, str]:
        context = self._context()
        return {
            "status": str(context["current_parent_status"]),
            "evidence_ref": str(context["current_parent_evidence_ref"]),
        }

    def parent_observations(self) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT status, evidence_ref, observed_at FROM parent_observations ORDER BY observation_ordinal"
        ).fetchall()
        return [
            {
                "status": str(row["status"]),
                "evidence_ref": str(row["evidence_ref"]),
                "observed_at": str(row["observed_at"]),
            }
            for row in rows
        ]

    def turn_snapshot(self, turn_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT turn_id, role, input_turn_ids_json, accept_turn_id, return_turn_id,
                   terminal_on_accept, state, claimed_by, result_json,
                   evidence_refs_json, review_decision, created_at, claimed_at,
                   completed_at
            FROM task_turns WHERE turn_id = ?
            """,
            (turn_id.strip(),),
        ).fetchone()
        return self._turn_from_row(row) if row is not None else None

    def turns(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT turn_id, role, input_turn_ids_json, accept_turn_id, return_turn_id,
                   terminal_on_accept, state, claimed_by, result_json,
                   evidence_refs_json, review_decision, created_at, claimed_at,
                   completed_at
            FROM task_turns ORDER BY turn_ordinal
            """
        ).fetchall()
        return [self._turn_from_row(row) for row in rows]

    def journal(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT event_type, turn_id, details_json, observed_at FROM task_journal ORDER BY event_ordinal"
        ).fetchall()
        return [
            {
                "event_type": str(row["event_type"]),
                "turn_id": str(row["turn_id"]),
                "details": json.loads(str(row["details_json"])),
                "observed_at": str(row["observed_at"]),
            }
            for row in rows
        ]

    def database_paths(self) -> list[str]:
        return [str(row[2]) for row in self.conn.execute("PRAGMA database_list").fetchall()]

    def _normalize_turns(self, turns: Iterable[TaskTurn]) -> list[TaskTurn] | str:
        normalized: list[TaskTurn] = []
        seen_ids: set[str] = set()
        for turn in turns:
            if not isinstance(turn, TaskTurn):
                return "TURN_DECLARATION_INVALID"
            turn_id = turn.turn_id.strip()
            raw_role = turn.role.strip()
            role = (
                _canonical_role_token(raw_role)
                if self.execution_gate is not None
                else raw_role.upper()
            )
            inputs = tuple(str(value).strip() for value in turn.input_turn_ids)
            accept_turn_id = turn.accept_turn_id.strip()
            return_turn_id = turn.return_turn_id.strip()
            if not turn_id:
                return "TURN_ID_REQUIRED"
            if not raw_role:
                return "TURN_ROLE_REQUIRED"
            if not role:
                return "TURN_ROLE_UNSUPPORTED"
            if not inputs or any(not value for value in inputs):
                inputs = tuple(value for value in inputs if value)
            if len(set(inputs)) != len(inputs):
                return "TURN_INPUT_DUPLICATED"
            if turn_id in seen_ids:
                return "TURN_ID_DUPLICATED"
            if turn_id in inputs or turn_id in {accept_turn_id, return_turn_id}:
                return "TURN_SELF_REFERENCE"
            seen_ids.add(turn_id)
            normalized.append(
                TaskTurn(
                    turn_id=turn_id,
                    role=role,
                    input_turn_ids=inputs,
                    accept_turn_id=accept_turn_id,
                    return_turn_id=return_turn_id,
                    terminal_on_accept=bool(turn.terminal_on_accept),
                )
            )

        if not normalized:
            return "TASK_TURNS_REQUIRED"
        turn_ids = {turn.turn_id for turn in normalized}
        for turn in normalized:
            for target in (*turn.input_turn_ids, turn.accept_turn_id, turn.return_turn_id):
                if target and target not in turn_ids:
                    return "TURN_REFERENCE_NOT_DECLARED"
        return normalized

    def _activate_dependency_ready_turns(self, observed_at: str) -> dict[str, Any]:
        routed_targets = {
            target
            for turn in self.turns()
            for target in (turn["accept_turn_id"], turn["return_turn_id"])
            if target
        }
        ready_turn_ids = [
            turn["turn_id"]
            for turn in self.turns()
            if turn["state"] == "DECLARED"
            and turn["turn_id"] not in routed_targets
            and self._inputs_completed(turn["input_turn_ids"])
        ]
        if len(ready_turn_ids) > 1:
            return {"status": "DEPENDENCY_ORDER_AMBIGUOUS", "turn_ids": ready_turn_ids}
        if ready_turn_ids:
            ready_turn = self.turn_snapshot(ready_turn_ids[0])
            if (
                ready_turn is not None
                and ready_turn["role"] == "SUB_REVIEWER"
                and self._boss_allocation_required_for_turn(ready_turn_ids[0])
                and self.allocation_snapshot(ready_turn_ids[0]) is None
            ):
                return {
                    "status": "BOSS_ALLOCATION_REQUIRED",
                    "turn_ids": ready_turn_ids,
                }
            self.conn.execute(
                "UPDATE task_turns SET state = 'READY' WHERE turn_id = ?", (ready_turn_ids[0],)
            )
            self._append_journal("TURN_READY", ready_turn_ids[0], {"reason": "DEPENDENCIES_COMPLETED"}, observed_at)
        return {"status": "DEPENDENCY_TURNS_EVALUATED", "turn_ids": ready_turn_ids}

    def _activate_route(self, turn_id: str, observed_at: str) -> dict[str, str]:
        target = self.turn_snapshot(turn_id)
        if target is None:
            return {"status": "ROUTE_TURN_NOT_FOUND"}
        if target["state"] != "DECLARED":
            return {"status": "ROUTE_TURN_NOT_DECLARED"}
        if not self._inputs_completed(target["input_turn_ids"]):
            return {"status": "ROUTE_INPUTS_NOT_READY"}
        self.conn.execute("UPDATE task_turns SET state = 'READY' WHERE turn_id = ?", (turn_id,))
        self._append_journal("TURN_READY", turn_id, {"reason": "DECLARED_REVIEW_ROUTE"}, observed_at)
        return {"status": "TURN_READY"}

    def _mark_branch_skipped(self, turn_id: str, observed_at: str) -> None:
        target = self.turn_snapshot(turn_id)
        if target is None or target["state"] != "DECLARED":
            return
        self.conn.execute("UPDATE task_turns SET state = 'SKIPPED' WHERE turn_id = ?", (turn_id,))
        self._append_journal("TURN_SKIPPED", turn_id, {"reason": "UNSELECTED_REVIEW_ROUTE"}, observed_at)
        for turn in self.turns():
            if turn["state"] == "DECLARED" and turn_id in turn["input_turn_ids"]:
                self._mark_branch_skipped(turn["turn_id"], observed_at)

    def _finalize_if_complete(self, observed_at: str) -> None:
        active = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM task_turns WHERE state IN ('DECLARED', 'READY', 'CLAIMED')"
            ).fetchone()[0]
        )
        if active == 0:
            self._set_task_state("COMPLETED")
            self._append_journal("TASK_COMPLETED", "", {"terminal": True}, observed_at)

    def _inputs_completed(self, input_turn_ids: Iterable[str]) -> bool:
        return all(
            (turn := self.turn_snapshot(turn_id)) is not None and turn["state"] == "COMPLETED"
            for turn_id in input_turn_ids
        )

    def _store_final(
        self,
        *,
        turn_id: str,
        state: str,
        result_json: str,
        evidence_refs: tuple[str, ...],
        review_decision: str,
        observed_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE task_turns
            SET state = ?, result_json = ?, evidence_refs_json = ?, review_decision = ?, completed_at = ?
            WHERE turn_id = ?
            """,
            (
                state,
                result_json,
                json.dumps(evidence_refs, ensure_ascii=True, separators=(",", ":")),
                review_decision,
                observed_at,
                turn_id,
            ),
        )

    def _record_parent_observation(self, observation: ParentObservation, observed_at: str) -> None:
        self.conn.execute(
            "INSERT INTO parent_observations(status, evidence_ref, observed_at) VALUES (?, ?, ?)",
            (observation.status, observation.evidence_ref, observed_at),
        )
        attachment_state = self._attachment_for_parent_status(observation.status)
        if self._task_state() == "RESULT_UNATTACHED":
            attachment_state = "UNATTACHED"
        self.conn.execute(
            """
            UPDATE task_frame_context
            SET current_parent_status = ?, current_parent_evidence_ref = ?, attachment_state = ?
            WHERE singleton = 1
            """,
            (observation.status, observation.evidence_ref, attachment_state),
        )
        self._append_journal(
            "PARENT_OBSERVED",
            "",
            {"status": observation.status, "evidence_ref": observation.evidence_ref},
            observed_at,
        )

    def _append_journal(
        self, event_type: str, turn_id: str, details: Mapping[str, Any], observed_at: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO task_journal(event_type, turn_id, details_json, observed_at) VALUES (?, ?, ?, ?)",
            (
                event_type,
                turn_id,
                json.dumps(details, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                observed_at,
            ),
        )

    def _set_task_state(self, task_state: str, attachment_state: str | None = None) -> None:
        if attachment_state is None:
            self.conn.execute(
                "UPDATE task_frame_context SET task_state = ? WHERE singleton = 1", (task_state,)
            )
            return
        self.conn.execute(
            "UPDATE task_frame_context SET task_state = ?, attachment_state = ? WHERE singleton = 1",
            (task_state, attachment_state),
        )

    def _task_state(self) -> str:
        return str(self._context()["task_state"])

    def _current_parent_status(self) -> str:
        return str(self._context()["current_parent_status"])

    def _parent_not_matched_result(self) -> dict[str, Any]:
        return {"status": "PARENT_CONTEXT_NOT_MATCHED", "parent_status": self._current_parent_status()}

    def _context(self) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM task_frame_context WHERE singleton = 1").fetchone()
        if row is None:
            raise RuntimeError("task frame context is missing")
        return row

    def _turn_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS count FROM task_turns GROUP BY state ORDER BY state"
        ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def _instruction_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS count FROM task_instructions GROUP BY state ORDER BY state"
        ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def _boss_allocation_required_for_turn(self, turn_id: str) -> bool:
        target = self.turn_snapshot(turn_id)
        if target is None or target["role"] != "SUB_REVIEWER":
            return False
        return any(
            turn["role"] == "BOSS" and not turn["input_turn_ids"]
            for turn in self.turns()
        )

    def _record_boss_allocations(
        self,
        *,
        turn: Mapping[str, Any],
        worker_id: str,
        instruction_digests: list[str],
        allocations: list[Mapping[str, Any]],
        observed_at: str,
    ) -> dict[str, Any]:
        reviewer_turns = [
            candidate
            for candidate in self.turns()
            if candidate["role"] == "SUB_REVIEWER"
        ]
        parent_instructions = self.instructions()
        if not parent_instructions:
            return {"status": "PARENT_INSTRUCTION_REQUIRED"}
        parent_boundary = parent_instructions[-1]
        if parent_boundary["repository_write_scope"] not in REPOSITORY_WRITE_SCOPES:
            return {
                "status": "PARENT_INSTRUCTION_REPOSITORY_BOUNDARY_REQUIRED"
            }
        expected_digests = [
            instruction["instruction_digest"] for instruction in parent_instructions
        ]
        if not reviewer_turns:
            return {"status": "BOSS_ALLOCATION_NOT_REQUIRED"}
        if instruction_digests != expected_digests:
            return {"status": "BOSS_INSTRUCTION_ACKNOWLEDGEMENT_REQUIRED"}
        if self.allocations():
            return {"status": "BOSS_ALLOCATIONS_ALREADY_RECORDED"}
        expected_turn_ids = {candidate["turn_id"] for candidate in reviewer_turns}
        normalized: list[dict[str, Any]] = []
        seen_turn_ids: set[str] = set()
        seen_worker_paths: set[str] = set()
        for allocation in allocations:
            required_fields = {
                "turn_id",
                "worker_slot_ref",
                "worker_path",
                "task",
                "expected_output",
            }
            if (
                not isinstance(allocation, Mapping)
                or not required_fields.issubset(allocation)
                or not set(allocation).issubset(required_fields | {"mutation_scope"})
            ):
                return {"status": "BOSS_ALLOCATION_INVALID"}
            target_turn_id = str(allocation.get("turn_id", "")).strip()
            worker_slot_ref = str(allocation.get("worker_slot_ref", "")).strip()
            worker_path = str(allocation.get("worker_path", "")).strip()
            task_text = allocation.get("task")
            expected_output = allocation.get("expected_output")
            if (
                target_turn_id not in expected_turn_ids
                or target_turn_id in seen_turn_ids
                or not worker_slot_ref
                or not re.fullmatch(r"/root/boss/[a-zA-Z0-9._-]+", worker_path)
                or worker_path in seen_worker_paths
                or not isinstance(task_text, str)
                or not task_text.strip()
            ):
                return {"status": "BOSS_ALLOCATION_INVALID"}
            planned = self.execution_turns.get(target_turn_id, {})
            if worker_slot_ref != str(planned.get("worker_slot_ref", "")):
                return {"status": "BOSS_ALLOCATION_WORKER_SLOT_MISMATCH"}
            try:
                expected_output_json = json.dumps(
                    expected_output,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (TypeError, ValueError):
                return {"status": "BOSS_ALLOCATION_NOT_SERIALIZABLE"}
            mutation_scope = self._normalize_allocation_mutation_scope(
                allocation.get("mutation_scope")
            )
            if mutation_scope is None:
                return {"status": "BOSS_ALLOCATION_MUTATION_SCOPE_INVALID"}
            if (
                parent_boundary["repository_write_scope"] == "NONE"
                and mutation_scope["operations"]
            ):
                return {
                    "status": "TASK_FRAME_READ_ONLY_MUTATION_BLOCKED",
                    "turn_id": target_turn_id,
                }
            if parent_boundary["repository_write_scope"] == "BOUNDED":
                parent_mutation_scope = parent_boundary["mutation_scope"]
                if (
                    not set(mutation_scope["operations"]).issubset(
                        parent_mutation_scope["operations"]
                    )
                    or not set(mutation_scope["targets"]).issubset(
                        parent_mutation_scope["targets"]
                    )
                ):
                    return {
                        "status": "BOSS_ALLOCATION_PARENT_SCOPE_EXCEEDED",
                        "turn_id": target_turn_id,
                    }
            normalized.append(
                {
                    "turn_id": target_turn_id,
                    "worker_slot_ref": worker_slot_ref,
                    "worker_path": worker_path,
                    "task": task_text,
                    "expected_output": expected_output,
                    "expected_output_json": expected_output_json,
                    "mutation_scope": mutation_scope,
                    "mutation_scope_json": json.dumps(
                        mutation_scope,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                }
            )
            seen_turn_ids.add(target_turn_id)
            seen_worker_paths.add(worker_path)
        if seen_turn_ids != expected_turn_ids:
            return {"status": "BOSS_ALLOCATION_INCOMPLETE"}

        latest_digest = expected_digests[-1]
        for allocation in normalized:
            canonical = {
                "instruction_digest": latest_digest,
                "boss_turn_id": turn["turn_id"],
                "boss_worker_id": worker_id,
                "turn_id": allocation["turn_id"],
                "worker_slot_ref": allocation["worker_slot_ref"],
                "worker_path": allocation["worker_path"],
                "task": allocation["task"],
                "expected_output": allocation["expected_output"],
                "mutation_scope": allocation["mutation_scope"],
            }
            self.conn.execute(
                """
                INSERT INTO boss_allocations(
                    instruction_digest, boss_turn_id, boss_worker_id, turn_id,
                    worker_slot_ref, worker_path, task_text, expected_output_json,
                    mutation_scope_json, allocation_digest, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    latest_digest,
                    turn["turn_id"],
                    worker_id,
                    allocation["turn_id"],
                    allocation["worker_slot_ref"],
                    allocation["worker_path"],
                    allocation["task"],
                    allocation["expected_output_json"],
                    allocation["mutation_scope_json"],
                    _digest(canonical),
                    observed_at,
                ),
            )
        self._acknowledge_instructions(
            worker_id=worker_id,
            instruction_digests=expected_digests,
            turn_id=str(turn["turn_id"]),
            observed_at=observed_at,
        )
        self._append_journal(
            "BOSS_ALLOCATIONS_RECORDED",
            str(turn["turn_id"]),
            {
                "boss_worker_id": worker_id,
                "instruction_digests": expected_digests,
                "turn_ids": sorted(seen_turn_ids),
            },
            observed_at,
        )
        first_reviewer = reviewer_turns[0]
        if first_reviewer["state"] != "DECLARED":
            return {"status": "BOSS_ALLOCATION_TURN_STATE_INVALID"}
        self.conn.execute(
            "UPDATE task_turns SET state = 'READY' WHERE turn_id = ?",
            (first_reviewer["turn_id"],),
        )
        self._append_journal(
            "TURN_READY",
            first_reviewer["turn_id"],
            {
                "reason": "BOSS_ALLOCATION_RECORDED",
                "boss_turn_id": turn["turn_id"],
                "boss_worker_id": worker_id,
            },
            observed_at,
        )
        return {"status": "BOSS_ALLOCATIONS_RECORDED"}

    def _boss_completion_status(self, boss_turn_id: str) -> dict[str, Any]:
        reviewer_turns = [
            turn for turn in self.turns() if turn["role"] == "SUB_REVIEWER"
        ]
        if not reviewer_turns:
            return {"status": "BOSS_COMPLETION_READY"}
        if len(self.allocations()) != len(reviewer_turns):
            return {"status": "BOSS_ALLOCATION_REQUIRED"}
        pending_turn_ids = [
            turn["turn_id"]
            for turn in reviewer_turns
            if turn["state"] != "COMPLETED"
        ]
        if pending_turn_ids:
            return {
                "status": "BOSS_SUB_RESULTS_PENDING",
                "boss_turn_id": boss_turn_id,
                "pending_turn_ids": pending_turn_ids,
            }
        return {"status": "BOSS_COMPLETION_READY"}

    def _nested_invoker_binding(
        self, turn: Mapping[str, Any]
    ) -> tuple[str, str] | None:
        root_boss = next(
            (
                candidate
                for candidate in self.turns()
                if candidate["role"] == "BOSS" and not candidate["input_turn_ids"]
            ),
            None,
        )
        if root_boss is None or self.execution_gate is None:
            return None
        if turn["turn_id"] == root_boss["turn_id"]:
            return (
                self.execution_gate["execution_plan"]["parent_actor_ref"],
                "/root/boss",
            )
        if turn["role"] == "SUB_REVIEWER":
            allocation = self.allocation_snapshot(str(turn["turn_id"]))
            if allocation is None:
                return None
            return (
                str(allocation["boss_worker_id"]),
                str(allocation["worker_path"]),
            )
        return None

    def _acknowledge_instructions(
        self,
        *,
        worker_id: str,
        instruction_digests: list[str],
        turn_id: str,
        observed_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE task_instructions
            SET state = 'ACKNOWLEDGED', acknowledged_by = ?, acknowledged_at = ?
            WHERE state = 'RECORDED'
            """,
            (worker_id, observed_at),
        )
        self._append_journal(
            "BOSS_INSTRUCTIONS_ACKNOWLEDGED",
            turn_id,
            {
                "boss_worker_id": worker_id,
                "instruction_digests": instruction_digests,
            },
            observed_at,
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "turn_id": str(row["turn_id"]),
            "role": str(row["role"]),
            "input_turn_ids": tuple(json.loads(str(row["input_turn_ids_json"]))),
            "accept_turn_id": str(row["accept_turn_id"]),
            "return_turn_id": str(row["return_turn_id"]),
            "terminal_on_accept": bool(row["terminal_on_accept"]),
            "state": str(row["state"]),
            "claimed_by": str(row["claimed_by"]),
            "result": json.loads(str(row["result_json"])),
            "evidence_refs": tuple(json.loads(str(row["evidence_refs_json"]))),
            "review_decision": str(row["review_decision"]),
            "created_at": str(row["created_at"]),
            "claimed_at": str(row["claimed_at"]),
            "completed_at": str(row["completed_at"]),
        }

    @staticmethod
    def _instruction_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "instruction_id": str(row["instruction_id"]),
            "parent_session_id": str(row["parent_session_id"]),
            "parent_frame_id": str(row["parent_frame_id"]),
            "parent_anchor_ref": str(row["parent_anchor_ref"]),
            "user_instruction_raw": str(row["user_instruction_raw"]),
            "constraints": json.loads(str(row["constraints_json"])),
            "expected_output": json.loads(str(row["expected_output_json"])),
            "repository_write_scope": str(row["repository_write_scope"]),
            "mutation_scope": json.loads(str(row["mutation_scope_json"])),
            "instruction_digest": str(row["instruction_digest"]),
            "state": str(row["state"]),
            "acknowledged_by": str(row["acknowledged_by"]),
            "acknowledged_at": str(row["acknowledged_at"]),
            "recorded_at": str(row["recorded_at"]),
        }

    @staticmethod
    def _allocation_from_row(row: sqlite3.Row) -> dict[str, Any]:
        allocation_digest = str(row["allocation_digest"])
        return {
            "instruction_digest": str(row["instruction_digest"]),
            "boss_turn_id": str(row["boss_turn_id"]),
            "boss_worker_id": str(row["boss_worker_id"]),
            "turn_id": str(row["turn_id"]),
            "worker_slot_ref": str(row["worker_slot_ref"]),
            "worker_path": str(row["worker_path"]),
            "task": str(row["task_text"]),
            "expected_output": json.loads(str(row["expected_output_json"])),
            "mutation_scope": json.loads(str(row["mutation_scope_json"])),
            "allocation_id": "allocation_" + allocation_digest[:24],
            "allocation_digest": allocation_digest,
            "recorded_at": str(row["recorded_at"]),
        }

    @staticmethod
    def _normalize_allocation_mutation_scope(value: Any) -> dict[str, list[str]] | None:
        if value is None:
            return {"operations": [], "targets": []}
        try:
            return _normalize_mutation_scope(
                value,
                "boss_allocation.mutation_scope",
            )
        except ValueError:
            return None

    @staticmethod
    def _normalize_timestamp(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return ""
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _required_value(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _normalize_parent_observation(value: ParentObservation) -> ParentObservation:
        if not isinstance(value, ParentObservation):
            raise ValueError("parent_observation must be ParentObservation")
        status = value.status.strip().upper()
        evidence_ref = value.evidence_ref.strip()
        if status not in PARENT_OBSERVATION_STATUSES:
            raise ValueError("parent_observation.status is invalid")
        if not evidence_ref:
            raise ValueError("parent_observation.evidence_ref is required")
        return ParentObservation(status=status, evidence_ref=evidence_ref)

    @staticmethod
    def _normalize_evidence_refs(value: Iterable[str]) -> tuple[str, ...] | str:
        try:
            refs = tuple(str(item).strip() for item in value)
        except TypeError:
            return "EVIDENCE_REFS_INVALID"
        if not refs:
            return "EVIDENCE_REFS_REQUIRED"
        if any(not item for item in refs):
            return "EVIDENCE_REF_INVALID"
        if len(set(refs)) != len(refs):
            return "EVIDENCE_REF_DUPLICATED"
        return refs

    @staticmethod
    def _attachment_for_parent_status(status: str) -> str:
        if status == "MATCHED":
            return "ATTACHED"
        if status == "UNKNOWN":
            return "UNKNOWN"
        return "UNATTACHED"

    @staticmethod
    def _unique_refs(refs: Iterable[str]) -> list[str]:
        ordered: list[str] = []
        for ref in refs:
            if ref not in ordered:
                ordered.append(ref)
        return ordered
