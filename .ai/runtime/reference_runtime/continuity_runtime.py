"""Deterministic preparation, persistence, and continuity commands.

The runtime packages caller-selected evidence and can persist passive
Checkpoint or Resume records. It never selects or activates a prior Anchor.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .continuity_store_runtime import (
    DATABASE_REF,
    ContinuityStore,
    ContinuityStoreError,
)


PROFILE_SCHEMA = "ai-career.continuity-command-profile.v1"
INSTALLATION_MANIFEST_SCHEMA = "ai-career.project-runtime-installation.v1"
INSTALLATION_MANIFEST_PATH = ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
IMMUTABLE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SUPPORTED_OPERATIONS = {
    "checkpoint.prepare",
    "checkpoint.save",
    "checkpoint.list",
    "checkpoint.load",
    "memory-sync.prepare",
    "handoff-append.attest",
    "resume-save.prepare",
    "resume-save.save",
    "resume-restore.discover",
    "resume-restore.load",
    "conversation-recall.query",
    "anchor-currentness.evaluate",
}


@dataclass(frozen=True)
class ContinuityCommandError(Exception):
    error_code: str
    detail: str


@dataclass(frozen=True)
class ContinuityCommandProfile:
    profile_id: str
    source_repository: str
    source_commit: str
    contract_refs: tuple[str, ...]
    command_policy: Mapping[str, Mapping[str, Any]]
    profile_path: str
    profile_sha256: str


def load_continuity_profile(
    repo_root: Path, profile_path: Path
) -> ContinuityCommandProfile:
    """Load one installed-distribution-bound continuity profile."""

    raw = _read_bytes(profile_path, "profile")
    payload = _json_object(raw, "profile")
    if payload.get("schema") != PROFILE_SCHEMA:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", f"profile schema must be {PROFILE_SCHEMA}"
        )
    profile_id = _required_text(
        payload.get("profile_id"),
        "profile_id",
        error_code="CONTINUITY_PROFILE_INVALID",
    )
    source = _required_mapping(
        payload.get("source"),
        "source",
        error_code="CONTINUITY_PROFILE_INVALID",
    )
    if source.get("binding") != "installed-distribution":
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID",
            "source.binding must be installed-distribution",
        )
    manifest_ref = _safe_repo_path(
        source.get("installation_manifest", INSTALLATION_MANIFEST_PATH)
    )
    if manifest_ref != INSTALLATION_MANIFEST_PATH:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID",
            f"source.installation_manifest must be {INSTALLATION_MANIFEST_PATH}",
        )

    manifest_path = repo_root / PurePosixPath(manifest_ref)
    manifest = _json_object(_read_bytes(manifest_path, "installation manifest"), "installation manifest")
    if manifest.get("schema") != INSTALLATION_MANIFEST_SCHEMA:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", "installed distribution manifest schema is invalid"
        )
    installed_source = _required_mapping(
        manifest.get("source"),
        "installation.source",
        error_code="CONTINUITY_PROFILE_INVALID",
    )
    source_repository = _required_text(
        installed_source.get("repository"),
        "installation.source.repository",
        error_code="CONTINUITY_PROFILE_INVALID",
    )
    source_commit = _required_text(
        installed_source.get("commit"),
        "installation.source.commit",
        error_code="CONTINUITY_PROFILE_INVALID",
    )
    if IMMUTABLE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID",
            "installation.source.commit must be an immutable Git commit",
        )

    managed_paths = _managed_path_index(manifest.get("managed_paths"))
    try:
        profile_ref = profile_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError) as error:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", "profile must be inside repo_root"
        ) from error
    _verify_managed_bytes(managed_paths, profile_ref, raw, "profile")

    refs = payload.get("contract_refs")
    if not isinstance(refs, list) or not refs:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", "contract_refs must be a non-empty array"
        )
    contract_refs: list[str] = []
    for index, value in enumerate(refs):
        ref = _safe_repo_path(value)
        contract_path = repo_root / PurePosixPath(ref)
        _verify_managed_bytes(
            managed_paths,
            ref,
            _read_bytes(contract_path, f"contract_refs[{index}]"),
            f"contract_refs[{index}]",
        )
        contract_refs.append(ref)

    command_policy = _required_mapping(
        payload.get("command_policy"),
        "command_policy",
        error_code="CONTINUITY_PROFILE_INVALID",
    )
    if set(command_policy) != SUPPORTED_OPERATIONS:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID",
            "command_policy must declare the exact supported operation set",
        )
    _validate_policy(command_policy)
    return ContinuityCommandProfile(
        profile_id=profile_id,
        source_repository=source_repository,
        source_commit=source_commit,
        contract_refs=tuple(contract_refs),
        command_policy={
            key: dict(
                _required_mapping(
                    value,
                    key,
                    error_code="CONTINUITY_PROFILE_INVALID",
                )
            )
            for key, value in command_policy.items()
        },
        profile_path=str(profile_path),
        profile_sha256=_sha256(raw),
    )


def run_continuity_command(
    *,
    profile: ContinuityCommandProfile,
    operation: str,
    request: Mapping[str, Any],
    store: ContinuityStore | None = None,
) -> dict[str, Any]:
    if operation not in profile.command_policy:
        raise ContinuityCommandError(
            "CONTINUITY_OPERATION_UNSUPPORTED", f"unsupported operation: {operation}"
        )
    try:
        if operation == "checkpoint.prepare":
            result = _prepare_checkpoint(request)
        elif operation == "checkpoint.save":
            result = _save_checkpoint(profile, request, _required_store(store))
        elif operation == "checkpoint.list":
            result = _list_checkpoint(request, store)
        elif operation == "checkpoint.load":
            result = _load_checkpoint(request, store)
        elif operation == "memory-sync.prepare":
            result = _prepare_memory_sync(request)
        elif operation == "handoff-append.attest":
            result = _attest_handoff_append(request)
        elif operation == "resume-save.prepare":
            result = _prepare_resume(request)
        elif operation == "resume-save.save":
            result = _save_resume(profile, request, _required_store(store))
        elif operation == "resume-restore.discover":
            result = _discover_resume(request, store)
        elif operation == "resume-restore.load":
            result = _load_resume(request, store)
        elif operation == "conversation-recall.query":
            result = _query_conversation_recall(request)
        else:
            result = _evaluate_anchor_currentness(request)
    except ContinuityStoreError as error:
        raise ContinuityCommandError(error.error_code, error.detail) from error
    return {
        "schema": "ai-career.continuity-command-result.v1",
        "operation": operation,
        "profile": {
            "profile_id": profile.profile_id,
            "source_repository": profile.source_repository,
            "source_commit": profile.source_commit,
            "contract_refs": list(profile.contract_refs),
            "profile_path": profile.profile_path,
            "profile_sha256": profile.profile_sha256,
        },
        "repository_write": False,
        "runtime_state_write": bool(result.get("runtime_state_write", False)),
        "authority_created": False,
        "activation_performed": False,
        "result": result,
    }


def _prepare_checkpoint(request: Mapping[str, Any]) -> dict[str, Any]:
    payload = _normalize_checkpoint_candidate(request)
    return {
        "status": "PREPARED",
        "command": "SNAPSHOT_SAVE",
        "candidate_id": _stable_id("checkpoint", payload),
        "durability": "UNKNOWN",
        "persistence_state": "PASSIVE",
        "runtime_state_write": False,
        "candidate": payload,
    }


def _normalize_checkpoint_candidate(request: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        request,
        {
            "session_id",
            "frame_id",
            "anchor_id",
            "snapshot",
            "summary",
            "source_refs",
            "observed_at",
            "target_ref",
        },
        "checkpoint request",
    )
    snapshot = dict(_required_mapping(request.get("snapshot"), "snapshot"))
    _ensure_json(snapshot, "snapshot")
    return {
        "session_id": _required_text(request.get("session_id"), "session_id"),
        "frame_id": _required_text(request.get("frame_id"), "frame_id"),
        "anchor_id": _required_text(request.get("anchor_id"), "anchor_id"),
        "snapshot": snapshot,
        "summary": _optional_text(request.get("summary", ""), "summary"),
        "source_refs": _source_refs(request.get("source_refs")),
        "observed_at": _timestamp(request.get("observed_at"), "observed_at"),
        "target_ref": _optional_text(request.get("target_ref", DATABASE_REF), "target_ref"),
    }


def _save_checkpoint(
    profile: ContinuityCommandProfile,
    request: Mapping[str, Any],
    store: ContinuityStore,
) -> dict[str, Any]:
    _exact_fields(request, {"candidate_id", "candidate"}, "checkpoint save request")
    candidate = _normalize_checkpoint_candidate(
        _required_mapping(request.get("candidate"), "candidate")
    )
    candidate_id = _required_text(request.get("candidate_id"), "candidate_id")
    if candidate_id != _stable_id("checkpoint", candidate):
        raise ContinuityCommandError(
            "CONTINUITY_CANDIDATE_ID_MISMATCH",
            "checkpoint candidate_id does not match its immutable payload",
        )
    _require_continuity_store_target(candidate["target_ref"])
    snapshot = _required_mapping(candidate["snapshot"], "candidate.snapshot")
    node = _required_text(snapshot.get("node"), "candidate.snapshot.node")
    mode = _required_text(snapshot.get("mode"), "candidate.snapshot.mode").upper()
    saved = store.save(
        record_type="CHECKPOINT",
        record_id=candidate_id,
        node=node,
        mode=mode,
        candidate=candidate,
        profile_sha256=profile.profile_sha256,
        source_commit=profile.source_commit,
    )
    return {"command": "SNAPSHOT_SAVE", **saved}


def _list_checkpoint(
    request: Mapping[str, Any], store: ContinuityStore | None
) -> dict[str, Any]:
    _exact_fields(request, {"node", "mode", "limit"}, "checkpoint list request")
    node = _required_text(request.get("node"), "node")
    mode = _required_text(request.get("mode"), "mode").upper()
    limit = _limit(request.get("limit", 20))
    records = (
        []
        if store is None
        else store.list_records(
            record_type="CHECKPOINT", node=node, mode=mode, limit=limit
        )
    )
    return {
        "status": "CHECKPOINTS_LISTED",
        "command": "SNAPSHOT_SAVE",
        "node": node,
        "mode": mode,
        "records": records,
        "store_status": "AVAILABLE" if store is not None else "ABSENT",
        "runtime_state_write": False,
    }


def _load_checkpoint(
    request: Mapping[str, Any], store: ContinuityStore | None
) -> dict[str, Any]:
    _exact_fields(request, {"checkpoint_id"}, "checkpoint load request")
    checkpoint_id = _required_text(request.get("checkpoint_id"), "checkpoint_id")
    record = (
        None
        if store is None
        else store.load(record_type="CHECKPOINT", record_id=checkpoint_id)
    )
    return {
        "status": "CHECKPOINT_LOADED" if record is not None else "CHECKPOINT_NOT_FOUND",
        "command": "SNAPSHOT_SAVE",
        "checkpoint_id": checkpoint_id,
        "record": record,
        "persistence_state": "PASSIVE",
        "activation_performed": False,
        "runtime_state_write": False,
    }


def _prepare_memory_sync(request: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        request,
        {
            "session_id",
            "frame_id",
            "selection_ref",
            "selected_items",
            "observed_at",
            "target_ref",
        },
        "memory-sync request",
    )
    raw_items = request.get("selected_items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", "selected_items must be a non-empty array"
        )
    items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        item = _required_mapping(raw_item, f"selected_items[{index}]")
        _exact_fields(item, {"memory_id", "content", "source_refs"}, f"selected_items[{index}]")
        items.append(
            {
                "memory_id": _required_text(item.get("memory_id"), f"selected_items[{index}].memory_id"),
                "content": _required_text(item.get("content"), f"selected_items[{index}].content"),
                "source_refs": _source_refs(item.get("source_refs")),
            }
        )
    payload = {
        "session_id": _required_text(request.get("session_id"), "session_id"),
        "frame_id": _required_text(request.get("frame_id"), "frame_id"),
        "selection_ref": _required_text(request.get("selection_ref"), "selection_ref"),
        "selected_items": items,
        "observed_at": _timestamp(request.get("observed_at"), "observed_at"),
        "target_ref": _optional_text(request.get("target_ref", "UNKNOWN"), "target_ref"),
    }
    return {
        "status": "PREPARED",
        "command": "MEMORY_SYNC",
        "candidate_id": _stable_id("memory", payload),
        "durability": "UNKNOWN",
        "persistence_state": "PASSIVE",
        "candidate": payload,
    }


def _attest_handoff_append(request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate provider evidence after a Host performs an append-only handoff."""

    _exact_fields(
        request,
        {
            "provider",
            "provider_write_capability",
            "target_path",
            "selection_ref",
            "base_source_ref",
            "result_ref",
            "provider_receipt_ref",
        },
        "handoff-append request",
    )
    if request.get("provider_write_capability") != "AVAILABLE":
        raise ContinuityCommandError(
            "HANDOFF_APPEND_UNAVAILABLE", "provider_write_capability must be AVAILABLE"
        )
    try:
        target_path = _safe_repo_path(request.get("target_path"))
    except ContinuityCommandError as error:
        raise ContinuityCommandError(
            "HANDOFF_APPEND_PATH_FORBIDDEN",
            f"target_path is not a safe Runtime-owned path: {error.detail}",
        ) from error
    allowed_prefixes = (".ai/memory/inbox/", ".ai/queue/", ".ai/archive/")
    if not target_path.startswith(allowed_prefixes):
        raise ContinuityCommandError(
            "HANDOFF_APPEND_PATH_FORBIDDEN",
            "target_path is not a Runtime-owned append-only path",
        )
    evidence = {
        "schema": "ai-career.handoff-append-evidence.v1",
        "operation_class": "HANDOFF_APPEND",
        "provider": _required_text(request.get("provider"), "provider"),
        "target_path": target_path,
        "selection_ref": _required_text(request.get("selection_ref"), "selection_ref"),
        "base_source_ref": _required_text(
            request.get("base_source_ref"), "base_source_ref"
        ),
        "result_ref": _required_text(request.get("result_ref"), "result_ref"),
        "provider_receipt_ref": _required_text(
            request.get("provider_receipt_ref"), "provider_receipt_ref"
        ),
    }
    return {
        "status": "HANDOFF_APPEND_RECORDED",
        "durability": "PROVIDER_ATTESTED",
        "repository_write": False,
        "execution_host_required": False,
        "evidence": evidence,
    }


def _prepare_resume(request: Mapping[str, Any]) -> dict[str, Any]:
    payload = _normalize_resume_candidate(request)
    return {
        "status": "PREPARED",
        "command": "RESUME_SAVE",
        "candidate_id": _stable_id("resume", payload),
        "durability": "UNKNOWN",
        "persistence_state": "PASSIVE",
        "runtime_state_write": False,
        "candidate": payload,
    }


def _normalize_resume_candidate(request: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        request,
        {
            "node",
            "mode",
            "session_id",
            "frame_id",
            "anchor_id",
            "checkpoint_ref",
            "snapshot",
            "summary",
            "source_refs",
            "source_ref",
            "observed_at",
            "target_ref",
        },
        "resume-save request",
    )
    snapshot = dict(_required_mapping(request.get("snapshot"), "snapshot"))
    _ensure_json(snapshot, "snapshot")
    source_refs = _source_refs(request.get("source_refs"))
    source_ref = _optional_text(
        request.get("source_ref", source_refs[0]), "source_ref"
    )
    if source_ref != source_refs[0]:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID",
            "source_ref must match the first source_refs entry",
        )
    return {
        "node": _required_text(request.get("node"), "node"),
        "mode": _required_text(request.get("mode"), "mode").upper(),
        "session_id": _required_text(request.get("session_id"), "session_id"),
        "frame_id": _required_text(request.get("frame_id"), "frame_id"),
        "anchor_id": _required_text(request.get("anchor_id"), "anchor_id"),
        "checkpoint_ref": _required_text(
            request.get("checkpoint_ref"), "checkpoint_ref"
        ),
        "snapshot": snapshot,
        "summary": _optional_text(request.get("summary", ""), "summary"),
        "source_refs": source_refs,
        "source_ref": source_ref,
        "observed_at": _timestamp(request.get("observed_at"), "observed_at"),
        "target_ref": _optional_text(request.get("target_ref", DATABASE_REF), "target_ref"),
    }


def _save_resume(
    profile: ContinuityCommandProfile,
    request: Mapping[str, Any],
    store: ContinuityStore,
) -> dict[str, Any]:
    _exact_fields(request, {"candidate_id", "candidate"}, "resume save request")
    candidate = _normalize_resume_candidate(
        _required_mapping(request.get("candidate"), "candidate")
    )
    candidate_id = _required_text(request.get("candidate_id"), "candidate_id")
    if candidate_id != _stable_id("resume", candidate):
        raise ContinuityCommandError(
            "CONTINUITY_CANDIDATE_ID_MISMATCH",
            "resume candidate_id does not match its immutable payload",
        )
    _require_continuity_store_target(candidate["target_ref"])
    saved = store.save(
        record_type="RESUME",
        record_id=candidate_id,
        node=candidate["node"],
        mode=candidate["mode"],
        candidate=candidate,
        profile_sha256=profile.profile_sha256,
        source_commit=profile.source_commit,
    )
    return {"command": "RESUME_SAVE", **saved}


def _discover_resume(
    request: Mapping[str, Any], store: ContinuityStore | None = None
) -> dict[str, Any]:
    _exact_fields(request, {"current", "candidates", "limit"}, "resume request")
    current = _required_mapping(request.get("current"), "current")
    _exact_fields(current, {"node", "mode", "session_id", "frame_id"}, "current")
    current_node = _required_text(current.get("node"), "current.node")
    current_mode = _required_text(current.get("mode"), "current.mode").upper()
    current_coordinate = {
        "node": current_node,
        "mode": current_mode,
        "session_id": _required_text(current.get("session_id"), "current.session_id"),
        "frame_id": _required_text(current.get("frame_id"), "current.frame_id"),
    }
    limit = _limit(request.get("limit", 20))
    raw_candidates = request.get("candidates")
    if raw_candidates is None:
        raw_candidates = []
        if store is not None:
            for summary in store.list_records(
                record_type="RESUME",
                node=current_node,
                mode=current_mode,
                limit=limit,
            ):
                loaded = store.load(
                    record_type="RESUME", record_id=summary["record_id"]
                )
                if loaded is None:
                    continue
                candidate = loaded["candidate"]
                raw_candidates.append(
                    {
                        "candidate_id": loaded["record_id"],
                        "node": candidate["node"],
                        "mode": candidate["mode"],
                        "session_id": candidate["session_id"],
                        "frame_id": candidate["frame_id"],
                        "anchor_id": candidate["anchor_id"],
                        "checkpoint_ref": candidate["checkpoint_ref"],
                        "updated_at": loaded["saved_at"],
                        "source_ref": candidate["source_ref"],
                        "summary": candidate["summary"],
                    }
                )
    if not isinstance(raw_candidates, list):
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", "candidates must be an array"
        )
    eligible: list[dict[str, Any]] = []
    for index, raw_candidate in enumerate(raw_candidates):
        candidate = _required_mapping(raw_candidate, f"candidates[{index}]")
        _exact_fields(
            candidate,
            {
                "candidate_id",
                "node",
                "mode",
                "session_id",
                "frame_id",
                "anchor_id",
                "checkpoint_ref",
                "updated_at",
                "source_ref",
                "summary",
            },
            f"candidates[{index}]",
        )
        normalized = {
            "candidate_id": _required_text(candidate.get("candidate_id"), f"candidates[{index}].candidate_id"),
            "node": _required_text(candidate.get("node"), f"candidates[{index}].node"),
            "mode": _required_text(candidate.get("mode"), f"candidates[{index}].mode"),
            "session_id": _required_text(candidate.get("session_id"), f"candidates[{index}].session_id"),
            "frame_id": _required_text(candidate.get("frame_id"), f"candidates[{index}].frame_id"),
            "anchor_id": _required_text(candidate.get("anchor_id"), f"candidates[{index}].anchor_id"),
            "checkpoint_ref": _required_text(candidate.get("checkpoint_ref"), f"candidates[{index}].checkpoint_ref"),
            "updated_at": _timestamp(candidate.get("updated_at"), f"candidates[{index}].updated_at"),
            "source_ref": _required_text(candidate.get("source_ref"), f"candidates[{index}].source_ref"),
            "summary": _optional_text(candidate.get("summary", ""), f"candidates[{index}].summary"),
        }
        if normalized["node"] == current_node and normalized["mode"] == current_mode:
            eligible.append(normalized)
    eligible.sort(key=lambda item: (item["updated_at"], item["candidate_id"]), reverse=True)
    return {
        "status": "RESUME_CANDIDATE_AVAILABLE" if eligible else "RESUME_CANDIDATE_NOT_FOUND",
        "command": "RESUME",
        "current": current_coordinate,
        "candidates": eligible,
        "selected_candidate": None,
        "requires_commander_selection": bool(eligible),
        "requires_status_or_validate": bool(eligible),
        "adoption_state": "CANDIDATE",
        "source": "LOCAL_SQLITE" if store is not None and "candidates" not in request else "CALLER_SUPPLIED",
        "runtime_state_write": False,
    }


def _load_resume(
    request: Mapping[str, Any], store: ContinuityStore | None
) -> dict[str, Any]:
    _exact_fields(request, {"resume_id"}, "resume load request")
    resume_id = _required_text(request.get("resume_id"), "resume_id")
    record = (
        None if store is None else store.load(record_type="RESUME", record_id=resume_id)
    )
    return {
        "status": (
            "REHYDRATION_CANDIDATE_LOADED"
            if record is not None
            else "RESUME_CANDIDATE_NOT_FOUND"
        ),
        "command": "RESUME",
        "resume_id": resume_id,
        "record": record,
        "adoption_state": "CANDIDATE",
        "selection_state": "CALLER_SELECTED" if record is not None else "UNKNOWN",
        "requires_commander_selection": False,
        "requires_adoption_decision": record is not None,
        "activation_performed": False,
        "authority_created": False,
        "runtime_state_write": False,
    }


def _query_conversation_recall(request: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(request, {"query", "records", "limit"}, "recall request")
    query = _required_text(request.get("query"), "query")
    limit = request.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", "limit must be an integer from 1 to 100"
        )
    raw_records = request.get("records")
    if not isinstance(raw_records, list):
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", "records must be an array"
        )
    matches: list[dict[str, Any]] = []
    folded_query = query.casefold()
    for index, raw_record in enumerate(raw_records):
        record = _required_mapping(raw_record, f"records[{index}]")
        _exact_fields(record, {"record_id", "text", "source_ref", "observed_at"}, f"records[{index}]")
        text = _required_text(record.get("text"), f"records[{index}].text")
        if folded_query not in text.casefold():
            continue
        matches.append(
            {
                "record_id": _required_text(record.get("record_id"), f"records[{index}].record_id"),
                "text": text,
                "source_ref": _required_text(record.get("source_ref"), f"records[{index}].source_ref"),
                "observed_at": _timestamp(record.get("observed_at"), f"records[{index}].observed_at"),
            }
        )
        if len(matches) == limit:
            break
    return {
        "status": "RECALL_CANDIDATES_AVAILABLE" if matches else "RECALL_CANDIDATE_NOT_FOUND",
        "command": "ARCHIVE_RECALL",
        "query": query,
        "match_mode": "CASEFOLD_SUBSTRING",
        "matches": matches,
        "adoption_state": "CANDIDATE",
        "semantic_summary": None,
    }


def _evaluate_anchor_currentness(request: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        request,
        {"current_session_id", "current_frame_id", "snapshot", "checked_at"},
        "currentness request",
    )
    snapshot = _required_mapping(request.get("snapshot"), "snapshot")
    _exact_fields(
        snapshot,
        {
            "session_id",
            "frame_id",
            "state_origin",
            "state_freshness",
            "observed_at",
            "stale_after",
            "source_ref",
        },
        "snapshot",
    )
    current_session_id = _required_text(request.get("current_session_id"), "current_session_id")
    current_frame_id = _required_text(request.get("current_frame_id"), "current_frame_id")
    checked_at = _parsed_timestamp(request.get("checked_at"), "checked_at")
    observed_at = _parsed_timestamp(snapshot.get("observed_at"), "snapshot.observed_at")
    source_ref = _required_text(snapshot.get("source_ref"), "snapshot.source_ref")
    session_id = _required_text(snapshot.get("session_id"), "snapshot.session_id")
    frame_id = _required_text(snapshot.get("frame_id"), "snapshot.frame_id")
    state_origin = _required_text(snapshot.get("state_origin"), "snapshot.state_origin")
    state_freshness = _required_text(snapshot.get("state_freshness"), "snapshot.state_freshness")
    identity_match = session_id == current_session_id and frame_id == current_frame_id
    age_seconds = int((checked_at - observed_at).total_seconds())

    physical_freshness = "UNKNOWN"
    stale_after_text = snapshot.get("stale_after", "")
    if stale_after_text:
        stale_after = _parsed_timestamp(stale_after_text, "snapshot.stale_after")
        physical_freshness = (
            "RECHECK_REQUIRED" if checked_at > stale_after else "FRESH"
        )

    if age_seconds < 0:
        currentness = "UNKNOWN"
        physical_freshness = "UNKNOWN"
    elif not identity_match or state_origin != "current_session":
        currentness = "RECHECK_REQUIRED"
    elif state_freshness in {"stale", "superseded"}:
        currentness = "STALE"
    elif state_freshness == "expired" or physical_freshness == "RECHECK_REQUIRED":
        currentness = "RECHECK_REQUIRED"
    elif state_freshness in {"current", "fresh"}:
        currentness = "CURRENT"
    else:
        currentness = "UNKNOWN"

    return {
        "status": "CURRENTNESS_EVALUATED",
        "command": "ANCHOR_CURRENTNESS",
        "currentness": currentness,
        "currentness_key": f"{session_id}+{frame_id}",
        "identity_match": identity_match,
        "state_origin": state_origin,
        "state_freshness": state_freshness,
        "physical_freshness": physical_freshness,
        "observed_at": _iso(observed_at),
        "checked_at": _iso(checked_at),
        "age_seconds": age_seconds,
        "time_alone_changes_status": False,
        "stale_after": _optional_text(stale_after_text, "snapshot.stale_after"),
        "source_ref": source_ref,
        "recheck_required": currentness in {"STALE", "RECHECK_REQUIRED", "UNKNOWN"},
    }


def _validate_policy(policy: Mapping[str, Any]) -> None:
    required = {
        "checkpoint.prepare": {
            "canonical_command": "SNAPSHOT_SAVE",
            "content_selection": "CALLER_SELECTED",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "PREPARED",
        },
        "checkpoint.save": {
            "canonical_command": "SNAPSHOT_SAVE",
            "durable_write": "LOCAL_SQLITE_APPEND_ONLY",
            "activation": "FORBIDDEN",
            "result_status": "SAVED",
        },
        "checkpoint.list": {
            "canonical_command": "SNAPSHOT_SAVE",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "CHECKPOINTS_LISTED",
        },
        "checkpoint.load": {
            "canonical_command": "SNAPSHOT_SAVE",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "CHECKPOINT_LOADED_OR_NOT_FOUND",
        },
        "memory-sync.prepare": {
            "canonical_command": "MEMORY_SYNC",
            "content_selection": "USER_CONFIRMED",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "PREPARED",
        },
        "handoff-append.attest": {
            "canonical_command": "HANDOFF_APPEND",
            "content_selection": "USER_CONFIRMED",
            "durable_write": "HOST_PROVIDER_ONLY",
            "activation": "FORBIDDEN",
            "result_status": "HANDOFF_APPEND_RECORDED",
        },
        "resume-save.prepare": {
            "canonical_command": "RESUME_SAVE",
            "content_selection": "CALLER_SELECTED",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "PREPARED",
        },
        "resume-save.save": {
            "canonical_command": "RESUME_SAVE",
            "durable_write": "LOCAL_SQLITE_APPEND_ONLY",
            "activation": "FORBIDDEN",
            "result_status": "SAVED",
        },
        "resume-restore.discover": {
            "canonical_command": "RESUME",
            "same_node_mode_required": True,
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "CANDIDATE",
        },
        "resume-restore.load": {
            "canonical_command": "RESUME",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "REHYDRATION_CANDIDATE",
        },
        "conversation-recall.query": {
            "canonical_command": "ARCHIVE_RECALL",
            "corpus": "CALLER_SUPPLIED",
            "semantic_adoption": "PARENT_ONLY",
            "durable_write": "FORBIDDEN",
            "activation": "FORBIDDEN",
            "result_status": "CANDIDATE",
        },
        "anchor-currentness.evaluate": {
            "canonical_command": "ANCHOR_CURRENTNESS",
            "currentness_key": "SESSION_ID_PLUS_FRAME_ID",
            "physical_threshold": "SOURCE_SUPPLIED_RECHECK_ONLY",
            "authority_effect": "NONE",
            "durable_write": "FORBIDDEN",
            "result_status": "EVALUATED",
        },
    }
    for operation, expected in required.items():
        actual = _required_mapping(
            policy.get(operation),
            operation,
            error_code="CONTINUITY_PROFILE_INVALID",
        )
        if dict(actual) != expected:
            raise ContinuityCommandError(
                "CONTINUITY_PROFILE_INVALID", f"command_policy.{operation} is invalid"
            )


def _managed_path_index(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", "installation.managed_paths must be an array"
        )
    result: dict[str, Mapping[str, Any]] = {}
    for index, raw_row in enumerate(value):
        row = _required_mapping(
            raw_row,
            f"installation.managed_paths[{index}]",
            error_code="CONTINUITY_PROFILE_INVALID",
        )
        path = _safe_repo_path(row.get("target_path"))
        local_sha256 = row.get("local_sha256")
        if not isinstance(local_sha256, str) or SHA256_PATTERN.fullmatch(local_sha256) is None:
            raise ContinuityCommandError(
                "CONTINUITY_PROFILE_INVALID",
                f"installation.managed_paths[{index}].local_sha256 is invalid",
            )
        if path in result:
            raise ContinuityCommandError(
                "CONTINUITY_PROFILE_INVALID", f"duplicate managed path: {path}"
            )
        result[path] = row
    return result


def _verify_managed_bytes(
    managed_paths: Mapping[str, Mapping[str, Any]],
    path: str,
    raw: bytes,
    context: str,
) -> None:
    row = managed_paths.get(path)
    if row is None:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", f"{context} is absent from installed manifest: {path}"
        )
    if row.get("local_sha256") != _sha256(raw):
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", f"{context} does not match installed manifest: {path}"
        )


def _safe_repo_path(value: Any) -> str:
    path = _required_text(
        value,
        "path",
        error_code="CONTINUITY_PROFILE_INVALID",
    ).replace("\\", "/")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or path.startswith("./"):
        raise ContinuityCommandError("CONTINUITY_PROFILE_INVALID", f"unsafe repository path: {path}")
    return pure.as_posix()


def _read_bytes(path: Path, context: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_UNAVAILABLE", f"{context} cannot be read: {error}"
        ) from error


def _json_object(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContinuityCommandError(
            "CONTINUITY_PROFILE_INVALID", f"{context} must be UTF-8 JSON"
        ) from error
    return dict(
        _required_mapping(
            value,
            context,
            error_code="CONTINUITY_PROFILE_INVALID",
        )
    )


def _required_mapping(
    value: Any,
    context: str,
    *,
    error_code: str = "CONTINUITY_REQUEST_INVALID",
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContinuityCommandError(error_code, f"{context} must be an object")
    return value


def _required_text(
    value: Any,
    context: str,
    *,
    error_code: str = "CONTINUITY_REQUEST_INVALID",
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuityCommandError(error_code, f"{context} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", f"{context} must be a string"
        )
    return value.strip()


def _required_store(store: ContinuityStore | None) -> ContinuityStore:
    if store is None:
        raise ContinuityCommandError(
            "CONTINUITY_STORE_REQUIRED",
            "this continuity operation requires the project-local SQLite store",
        )
    return store


def _limit(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", "limit must be an integer from 1 to 100"
        )
    return value


def _source_refs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", "source_refs must be a non-empty array"
        )
    return [_required_text(item, f"source_refs[{index}]") for index, item in enumerate(value)]


def _require_continuity_store_target(value: Any) -> None:
    if _required_text(value, "target_ref") != DATABASE_REF:
        raise ContinuityCommandError(
            "CONTINUITY_STORE_TARGET_MISMATCH",
            f"target_ref must be {DATABASE_REF}",
        )


def _exact_fields(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extra = sorted(set(value).difference(allowed))
    if extra:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", f"{context} contains unsupported field: {extra[0]}"
        )


def _parsed_timestamp(value: Any, context: str) -> datetime:
    text = _required_text(value, context)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", f"{context} must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", f"{context} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: Any, context: str) -> str:
    return _iso(_parsed_timestamp(value, context))


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_json(value: Any, context: str) -> None:
    try:
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ContinuityCommandError(
            "CONTINUITY_REQUEST_INVALID", f"{context} must be JSON serializable"
        ) from error


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
