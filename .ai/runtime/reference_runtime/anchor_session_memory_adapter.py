"""Loopback-only Host Adapter for explicit Anchor session memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

if __package__:
    from .anchor_session_memory_runtime import AnchorSessionMemoryRuntime
    from .execution_binding_runtime import (
        ExecutionBindingError,
        apply_approved_git_proposal,
        apply_execution_binding,
        begin_project_source_work,
        build_assignment_proposal,
    )
    from .execution_guard_runtime import ExecutionGuardError, ExecutionGuardRuntime
    from .git_command_gateway import (
        GIT_MUTATION_TIMEOUT_SECONDS,
        LOCAL_GIT_TIMEOUT_SECONDS,
    )
    from .git_proposal_runtime import GitProposalError, GitProposalJournal
    from .mode_registry_runtime import (
        ModeRegistryError,
        load_mode_registry,
        mode_definition_digest,
        mode_registry_digest,
        normalize_mode_id,
    )
    from .receipt_verifying_write_gateway import ReceiptVerifyingWriteGateway
    from .task_frame_runtime import ParentObservation, TaskFrameRuntime, load_profile
else:  # Direct module execution remains available for the loopback server.
    from anchor_session_memory_runtime import AnchorSessionMemoryRuntime
    from execution_binding_runtime import (
        ExecutionBindingError,
        apply_approved_git_proposal,
        apply_execution_binding,
        begin_project_source_work,
        build_assignment_proposal,
    )
    from execution_guard_runtime import ExecutionGuardError, ExecutionGuardRuntime
    from git_command_gateway import (
        GIT_MUTATION_TIMEOUT_SECONDS,
        LOCAL_GIT_TIMEOUT_SECONDS,
    )
    from git_proposal_runtime import GitProposalError, GitProposalJournal
    from mode_registry_runtime import (
        ModeRegistryError,
        load_mode_registry,
        mode_definition_digest,
        mode_registry_digest,
        normalize_mode_id,
    )
    from receipt_verifying_write_gateway import ReceiptVerifyingWriteGateway
    from task_frame_runtime import ParentObservation, TaskFrameRuntime, load_profile


LOOPBACK_HOST = "127.0.0.1"
TOKEN_HEADER = "X-Anchor-Session-Memory-Token"
DEFAULT_HTTP_TIMEOUT_SECONDS = 2
GIT_HTTP_TIMEOUT_SECONDS = (
    GIT_MUTATION_TIMEOUT_SECONDS + (3 * LOCAL_GIT_TIMEOUT_SECONDS) + 10
)
GIT_HTTP_PATHS = {
    "/v1/execution-binding/import-git-proposal",
    "/v1/mutation-gateway/apply-git",
}
MEMORY_STORAGE_SCOPE = "process-local sqlite :memory:"
FILE_STORAGE_SCOPE = "project-local anchor SQLite file"
ANCHOR_STORE_RELATIVE_PATH = Path(".ai/runtime/anchor_store")
TASK_FRAME_STORE_RELATIVE_PATH = Path(".ai/runtime/task_frames")


class AnchorSessionMemoryHostAdapter:
    """Own per-session runtimes without interpreting their snapshots."""

    def __init__(self, *, repository_root: Path | None = None) -> None:
        self._repository_root = (
            None if repository_root is None else repository_root.resolve()
        )
        self._session_runtimes: dict[str, AnchorSessionMemoryRuntime] = {}
        self._mode_runtimes: dict[str, AnchorSessionMemoryRuntime] = {}
        self._session_modes: dict[str, str] = {}
        self._execution_guards: dict[str, ExecutionGuardRuntime] = {}
        self._task_frames: dict[tuple[str, str], TaskFrameRuntime] = {}
        self._write_gateway = (
            None
            if repository_root is None
            else ReceiptVerifyingWriteGateway(repository_root)
        )
        self._git_proposals: GitProposalJournal | None = None

    @property
    def storage_scope(self) -> str:
        return FILE_STORAGE_SCOPE if self._repository_root is not None else MEMORY_STORAGE_SCOPE

    def activate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        source_ref = self._required_text(payload, "source_ref", "SOURCE_REF_REQUIRED")
        if isinstance(source_ref, dict):
            return source_ref
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            return {"status": "SNAPSHOT_REQUIRED", "session_id": session_id}
        anchor_mode = self._activation_anchor_mode(payload, snapshot)
        if isinstance(anchor_mode, dict):
            return {**anchor_mode, "session_id": session_id}

        created = session_id not in self._session_runtimes
        runtime = self._runtime_for(
            session_id,
            create=True,
            anchor_mode=anchor_mode,
        )
        assert runtime is not None
        if self._repository_root is not None:
            self._session_modes[session_id] = anchor_mode
        stored_snapshot = dict(snapshot)
        if self._repository_root is not None:
            stored_snapshot["session_id"] = session_id
            registry_binding = self._mode_registry_binding(anchor_mode)
            if isinstance(registry_binding, dict) and "status" in registry_binding:
                if created:
                    runtime.close()
                    self._session_runtimes.pop(session_id, None)
                return {**registry_binding, "session_id": session_id}
            stored_snapshot.update(registry_binding)
        outcome = runtime.record_snapshot(
            snapshot=stored_snapshot,
            source_ref=source_ref,
        )
        if outcome["status"] not in {"SNAPSHOT_RECORDED", "SNAPSHOT_UPDATED"}:
            if created:
                runtime.close()
                self._session_runtimes.pop(session_id, None)
            return {"status": outcome["status"], "session_id": session_id}

        if session_id not in self._execution_guards:
            self._execution_guards[session_id] = ExecutionGuardRuntime()

        result = {
            "status": "HOST_SESSION_MEMORY_ACTIVATED" if created else "HOST_SESSION_MEMORY_UPDATED",
            "session_id": session_id,
            "storage_scope": MEMORY_STORAGE_SCOPE,
            "snapshot": outcome["snapshot"],
            "event": outcome["event"],
        }
        if self._repository_root is not None:
            result["anchor_mode"] = anchor_mode
        if "beyond_footprint" in outcome:
            result["beyond_footprint"] = outcome["beyond_footprint"]
        return result

    def prepare_mode_current_anchor(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve the selected Mode's durable Current Anchor without a Runtime boot."""

        if self._repository_root is None:
            return {"status": "MODE_ANCHOR_STORE_UNBOUND"}
        mode = self._required_text(payload, "mode", "ANCHOR_MODE_REQUIRED")
        if isinstance(mode, dict):
            return mode
        source_ref = self._required_text(payload, "source_ref", "SOURCE_REF_REQUIRED")
        if isinstance(source_ref, dict):
            return source_ref
        normalized_mode = self._normalize_anchor_mode(mode)
        if normalized_mode is None:
            return {"status": "ANCHOR_MODE_INVALID"}
        try:
            registry = load_mode_registry(self._repository_root)
            definition = registry.resolve(normalized_mode)
        except ModeRegistryError as error:
            return {"status": error.error_code, "detail": error.detail}
        normalized_mode = definition.mode
        registry_revision = registry.revision
        definition_digest = mode_definition_digest(definition)
        registry_digest = mode_registry_digest(registry)
        runtime = self._mode_runtime(normalized_mode)
        stored = runtime.stored_snapshot()
        observed_at = _physical_time()
        observer_ref = str(payload.get("host_session_ref", "UNKNOWN")).strip() or "UNKNOWN"
        stored_payload = (
            stored.get("snapshot")
            if isinstance(stored, Mapping) and isinstance(stored.get("snapshot"), Mapping)
            else {}
        )
        registry_binding_changed = stored is not None and (
            stored_payload.get("mode_registry_revision") != registry_revision
            or stored_payload.get("mode_definition_digest") != definition_digest
            or stored_payload.get("mode_registry_digest") != registry_digest
        )
        source_changed = stored is not None and stored.get("source_ref") != source_ref
        if stored is None or registry_binding_changed or source_changed:
            snapshot = {
                "frame_id": "current",
                "anchor_id": f"{normalized_mode}-CURRENT-{secrets.token_hex(8).upper()}",
                "state": "CURRENT",
                "observed_at": observed_at,
                "coordinates": {"mode": normalized_mode},
                "observer_session_ref": observer_ref,
                "mode_registry_revision": registry_revision,
                "mode_definition_digest": definition_digest,
                "mode_registry_digest": registry_digest,
            }
            outcome = runtime.record_snapshot(snapshot=snapshot, source_ref=source_ref)
            status = "MODE_CURRENT_ANCHOR_CREATED"
        else:
            outcome = runtime.observe_current_anchor(
                frame_id=str(stored["frame_id"]),
                anchor_id=str(stored["anchor_id"]),
                observed_at=observed_at,
            )
            status = "MODE_CURRENT_ANCHOR_OBSERVED"
        if outcome["status"] not in {
            "SNAPSHOT_RECORDED",
            "SNAPSHOT_UPDATED",
            "CURRENT_ANCHOR_OBSERVED",
        }:
            return {"status": outcome["status"], "anchor_mode": normalized_mode}
        return {
            "status": status,
            "anchor_mode": normalized_mode,
            "storage_scope": FILE_STORAGE_SCOPE,
            "snapshot": outcome["snapshot"],
            "beyond_footprint": outcome.get("beyond_footprint"),
            "mode_registry": {
                "revision": registry_revision,
                "definition_digest": definition_digest,
                "registry_digest": registry_digest,
            },
        }

    def record_observation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        outcome = runtime.record_observation(
            frame_id=str(payload.get("frame_id", "")),
            event_id=str(payload.get("event_id", "")),
            action=str(payload.get("action", "")),
            details=payload.get("details"),
            source_ref=str(payload.get("source_ref", "")),
            observed_at=str(payload.get("observed_at", "")),
        )
        return {"status": outcome["status"], "session_id": session_id, **self._optional_outcome(outcome)}

    def observe_current_input(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Advance the same Current Anchor using Host physical time."""

        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        outcome = runtime.observe_current_anchor(
            frame_id=str(payload.get("frame_id", "")),
            anchor_id=str(payload.get("anchor_id", "")),
            observed_at=_physical_time(),
        )
        return {"session_id": session_id, **outcome}

    def status(self, *, session_id: str, mode: str = "") -> dict[str, Any]:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            return {"status": "SESSION_ID_REQUIRED"}
        runtime = self._runtime_for(
            normalized_session_id,
            create=False,
            anchor_mode=mode,
        )
        if runtime is None:
            return self._unknown_session_status(normalized_session_id, mode)
        result = {
            "status": "HOST_SESSION_MEMORY_ACTIVE",
            "session_id": normalized_session_id,
            "storage_scope": MEMORY_STORAGE_SCOPE,
            "snapshot": runtime.stored_snapshot(),
            "beyond_footprints": runtime.beyond_footprints(),
        }
        if self._repository_root is not None:
            result["anchor_mode"] = self._resolved_anchor_mode(normalized_session_id, mode)
        return result

    def evidence(self, *, session_id: str, mode: str = "") -> dict[str, Any]:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            return {"status": "SESSION_ID_REQUIRED"}
        runtime = self._runtime_for(
            normalized_session_id,
            create=False,
            anchor_mode=mode,
        )
        if runtime is None:
            return self._unknown_session_status(normalized_session_id, mode)
        snapshot = runtime.stored_snapshot()
        if snapshot is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": normalized_session_id}
        result = {
            "status": "HOST_SESSION_MEMORY_EVIDENCE_AVAILABLE",
            "session_id": normalized_session_id,
            "storage_scope": MEMORY_STORAGE_SCOPE,
            "snapshot": snapshot,
            "events": runtime.event_history(),
            "beyond_footprints": runtime.beyond_footprints(),
        }
        if self._repository_root is not None:
            result["anchor_mode"] = self._resolved_anchor_mode(normalized_session_id, mode)
        return result

    def propose_execution(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        request = payload.get("request")
        if not isinstance(request, Mapping):
            return {"status": "ASSIGNMENT_PROPOSAL_REQUEST_REQUIRED", "session_id": session_id}
        try:
            return build_assignment_proposal(
                snapshot=stored["snapshot"],
                request=request,
                observed_at=_physical_time(),
            )
        except ExecutionBindingError as error:
            return {
                "status": "UNKNOWN",
                "error_code": error.error_code,
                "detail": error.detail,
                "session_id": session_id,
            }

    def bind_execution(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        proposal = payload.get("proposal")
        approval = payload.get("approval")
        if not isinstance(proposal, Mapping) or not isinstance(approval, Mapping):
            return {"status": "EXECUTION_BINDING_REQUEST_REQUIRED", "session_id": session_id}
        try:
            result = apply_execution_binding(
                snapshot=stored["snapshot"],
                proposal=proposal,
                approval=approval,
                observed_at=_physical_time(),
            )
        except ExecutionBindingError as error:
            return {
                "status": "UNKNOWN",
                "error_code": error.error_code,
                "detail": error.detail,
                "session_id": session_id,
            }
        outcome = runtime.record_snapshot(
            snapshot=result["snapshot"], source_ref=stored["source_ref"]
        )
        if outcome.get("status") != "SNAPSHOT_UPDATED":
            return {
                "status": "UNKNOWN",
                "error_code": "EXECUTION_BINDING_SNAPSHOT_UPDATE_FAILED",
                "detail": str(outcome.get("status", "UNKNOWN")),
                "session_id": session_id,
            }
        return {
            **result,
            "session_id": session_id,
            "snapshot": outcome["snapshot"],
        }

    def bind_git_proposal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._repository_root is None:
            return {
                "status": "GIT_PROPOSAL_JOURNAL_UNAVAILABLE",
                "repository_write": False,
            }
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        coordinates = stored["snapshot"].get("coordinates")
        coordinate_mode = (
            coordinates.get("mode", "") if isinstance(coordinates, Mapping) else ""
        )
        mode = self._resolved_anchor_mode(session_id, str(coordinate_mode))
        if mode is None:
            return {"status": "ANCHOR_MODE_REQUIRED", "session_id": session_id}
        try:
            approved_action = self._git_proposal_journal().approved_scoped_action(
                session_id=session_id,
                mode=mode,
                proposal_id=str(payload.get("proposal_id", "")),
                action=str(payload.get("action", "")),
            )
            result = apply_approved_git_proposal(
                snapshot=stored["snapshot"],
                approved_action=approved_action,
                observed_at=_physical_time(),
            )
        except (ExecutionBindingError, GitProposalError) as error:
            return {
                "status": "UNKNOWN",
                "error_code": error.error_code,
                "detail": error.detail,
                "session_id": session_id,
            }
        outcome = runtime.record_snapshot(
            snapshot=result["snapshot"], source_ref=stored["source_ref"]
        )
        if outcome.get("status") != "SNAPSHOT_UPDATED":
            return {
                "status": "UNKNOWN",
                "error_code": "EXECUTION_BINDING_SNAPSHOT_UPDATE_FAILED",
                "detail": str(outcome.get("status", "UNKNOWN")),
                "session_id": session_id,
            }
        return {
            **result,
            "session_id": session_id,
            "snapshot": outcome["snapshot"],
        }

    def begin_project_source_work(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Activate a bounded user-instruction work receipt in this session."""

        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        work = payload.get("work")
        if not isinstance(work, Mapping):
            return {"status": "WORK_RECEIPT_REQUEST_REQUIRED", "session_id": session_id}
        try:
            result = begin_project_source_work(
                snapshot=stored["snapshot"],
                work=work,
                observed_at=_physical_time(),
                repository_root=self._repository_root,
            )
        except ExecutionBindingError as error:
            return {
                "status": "UNKNOWN",
                "error_code": error.error_code,
                "detail": error.detail,
                "session_id": session_id,
            }
        outcome = runtime.record_snapshot(
            snapshot=result["snapshot"], source_ref=stored["source_ref"]
        )
        if outcome.get("status") != "SNAPSHOT_UPDATED":
            return {
                "status": "UNKNOWN",
                "error_code": "WORK_RECEIPT_SNAPSHOT_UPDATE_FAILED",
                "detail": str(outcome.get("status", "UNKNOWN")),
                "session_id": session_id,
            }
        return {**result, "session_id": session_id, "snapshot": outcome["snapshot"]}

    def check_execution(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        guard = self._execution_guards.get(session_id)
        if runtime is None or guard is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        request = payload.get("request")
        if not isinstance(request, Mapping):
            return {"status": "EXECUTION_GUARD_REQUEST_REQUIRED", "session_id": session_id}
        lineage_verification = self._task_frame_lineage_verification(
            session_id=session_id,
            request=request,
        )
        try:
            return guard.check(
                snapshot=stored["snapshot"],
                request=request,
                observed_at=_physical_time(),
                task_frame_lineage_verification=lineage_verification,
            )
        except ExecutionGuardError as error:
            return {
                "status": "UNKNOWN",
                "error_code": error.error_code,
                "detail": error.detail,
                "session_id": session_id,
            }

    def consume_execution_receipt(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        guard = self._execution_guards.get(session_id)
        if runtime is None or guard is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        request = payload.get("request")
        if not isinstance(request, Mapping):
            return {"status": "EXECUTION_GUARD_REQUEST_REQUIRED", "session_id": session_id}
        lineage_verification = self._task_frame_lineage_verification(
            session_id=session_id,
            request=request,
        )
        try:
            return guard.consume(
                receipt_id=str(payload.get("receipt_id", "")),
                snapshot=stored["snapshot"],
                request=request,
                observed_at=_physical_time(),
                task_frame_lineage_verification=lineage_verification,
            )
        except ExecutionGuardError as error:
            return {
                "status": "UNKNOWN",
                "error_code": error.error_code,
                "detail": error.detail,
                "session_id": session_id,
            }

    def apply_file_mutation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._write_gateway is None:
            return {
                "status": "FILE_MUTATION_GATEWAY_UNAVAILABLE",
                "decision": "BLOCKED",
                "repository_write": False,
            }
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        guard = self._execution_guards.get(session_id)
        if runtime is None or guard is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        physical_payload = dict(payload)
        physical_payload["observed_at"] = _physical_time()
        request = payload.get("request")
        lineage_verification = (
            self._task_frame_lineage_verification(
                session_id=session_id,
                request=request,
            )
            if isinstance(request, Mapping)
            else None
        )
        return self._write_gateway.apply_file(
            guard=guard,
            snapshot=stored["snapshot"],
            payload=physical_payload,
            task_frame_lineage_verification=lineage_verification,
        )

    def apply_git_command(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._write_gateway is None:
            return {
                "status": "MUTATION_GATEWAY_UNAVAILABLE",
                "decision": "BLOCKED",
                "repository_write": False,
            }
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        runtime = self._runtime_for(session_id, create=False)
        guard = self._execution_guards.get(session_id)
        if runtime is None or guard is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        stored = runtime.stored_snapshot()
        if stored is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        request = payload.get("request")
        physical_payload = dict(payload)
        physical_payload["observed_at"] = _physical_time()
        lineage_verification = (
            self._task_frame_lineage_verification(
                session_id=session_id,
                request=request,
            )
            if isinstance(request, Mapping)
            else None
        )
        result = self._write_gateway.apply_git(
            guard=guard,
            snapshot=stored["snapshot"],
            payload=physical_payload,
            task_frame_lineage_verification=lineage_verification,
        )
        assignment = stored["snapshot"].get("execution_assignment")
        if (
            self._repository_root is not None
            and isinstance(assignment, Mapping)
            and assignment.get("assignment_kind") == "DURABLE_GIT_PROPOSAL"
            and assignment.get("git_proposal_kind") == "PUSH"
            and assignment.get("git_action") == "PUSH"
        ):
            try:
                journal_result = self._git_proposal_journal().record_result(
                    proposal_id=str(assignment.get("durable_proposal_id", "")),
                    action=str(assignment.get("git_action", "")),
                    result=result,
                    observed_at=_physical_time(),
                )
            except GitProposalError as error:
                return {
                    **result,
                    "status": "GIT_COMMAND_RESULT_JOURNAL_FAILED",
                    "journal_error_code": error.error_code,
                    "journal_detail": error.detail,
                }
            result = {**result, "git_proposal_journal": journal_result}
        return result

    def _git_proposal_journal(self) -> GitProposalJournal:
        if self._repository_root is None:
            raise GitProposalError(
                "GIT_PROPOSAL_JOURNAL_UNAVAILABLE",
                "repository-bound Git proposal journal is unavailable",
            )
        if self._git_proposals is None:
            self._git_proposals = GitProposalJournal(self._repository_root)
        return self._git_proposals

    def _task_frame_lineage_verification(
        self,
        *,
        session_id: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        candidate = request.get("task_frame_lineage")
        if candidate is None:
            return None
        if not isinstance(candidate, Mapping):
            return {"status": "TASK_FRAME_MUTATION_LINEAGE_INVALID"}
        task_frame_id = str(candidate.get("task_frame_id", "")).strip()
        runtime = self._task_frames.get((session_id, task_frame_id))
        if runtime is None:
            return {"status": "TASK_FRAME_MUTATION_FRAME_NOT_FOUND"}
        return runtime.verify_sub_mutation_lineage(
            lineage=candidate,
            operation=str(request.get("operation", "")),
            target=str(request.get("target", "")),
        )

    def create_task_frame(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create or reopen one project-local Task Frame journal for Host Workers."""

        if self._repository_root is None:
            return {
                "status": "TASK_FRAME_HOST_UNAVAILABLE",
                "persistent_task_frame_host": "UNKNOWN",
            }
        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        if self._runtime_for(session_id, create=False) is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        session_runtime = self._runtime_for(session_id, create=False)
        assert session_runtime is not None
        active_snapshot = session_runtime.stored_snapshot()
        if active_snapshot is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}
        profile_path = self._required_text(payload, "profile", "TASK_FRAME_PROFILE_REQUIRED")
        if isinstance(profile_path, dict):
            return profile_path
        frame = payload.get("frame")
        if not isinstance(frame, Mapping):
            return {"status": "TASK_FRAME_REQUEST_REQUIRED", "session_id": session_id}
        frame_id = self._required_text(frame, "frame_id", "TASK_FRAME_ID_REQUIRED")
        if isinstance(frame_id, dict):
            return frame_id
        key = (session_id, frame_id)
        if key in self._task_frames:
            return {
                "status": "TASK_FRAME_ALREADY_ACTIVE",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        origin_mismatches = {
            "origin_anchor_ref": {
                "active": active_snapshot["anchor_id"],
                "requested": frame.get("origin_anchor_ref"),
            },
            "origin_session_id": {
                "active": session_id,
                "requested": frame.get("origin_session_id"),
            },
            "origin_frame_id": {
                "active": active_snapshot["frame_id"],
                "requested": frame.get("origin_frame_id"),
            },
        }
        origin_mismatches = {
            key_name: values
            for key_name, values in origin_mismatches.items()
            if values["requested"] != values["active"]
        }
        if origin_mismatches:
            return {
                "status": "TASK_FRAME_ORIGIN_MISMATCH",
                "session_id": session_id,
                "frame_id": frame_id,
                "coordinates": origin_mismatches,
            }
        parent = frame.get("parent_observation")
        if not isinstance(parent, Mapping):
            return {
                "status": "TASK_FRAME_PARENT_OBSERVATION_REQUIRED",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        parent_instruction = frame.get("parent_instruction")
        if not isinstance(parent_instruction, Mapping):
            return {
                "status": "TASK_FRAME_PARENT_INSTRUCTION_REQUIRED",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        try:
            selected_profile = Path(profile_path)
            if not selected_profile.is_absolute():
                selected_profile = self._repository_root / selected_profile
            database_path = self._task_frame_store_path(session_id, frame_id)
            if database_path.is_file():
                try:
                    persisted_profile = TaskFrameRuntime.persisted_profile_path(
                        database_path
                    )
                except ValueError:
                    persisted_profile = str(selected_profile)
                persisted_profile_path = Path(persisted_profile)
                if not persisted_profile_path.is_absolute():
                    persisted_profile_path = self._repository_root / persisted_profile_path
                runtime = TaskFrameRuntime.open_existing(
                    profile=load_profile(self._repository_root, persisted_profile_path),
                    database_path=database_path,
                )
            else:
                runtime = TaskFrameRuntime(
                profile=load_profile(self._repository_root, selected_profile),
                frame_id=frame_id,
                origin_anchor_ref=str(active_snapshot["anchor_id"]),
                origin_session_id=session_id,
                origin_frame_id=str(active_snapshot["frame_id"]),
                task_summary_ref=str(frame.get("task_summary_ref", "")),
                source_ref=str(frame.get("source_ref", "")),
                execution_assignment_ref=str(
                    frame.get("execution_assignment_ref", "UNASSIGNED")
                ),
                task_frame_execution_proposal=(
                    frame.get("task_frame_execution_proposal")
                    if isinstance(frame.get("task_frame_execution_proposal"), Mapping)
                    else None
                ),
                task_frame_execution_approval=(
                    frame.get("task_frame_execution_approval")
                    if isinstance(frame.get("task_frame_execution_approval"), Mapping)
                    else None
                ),
                parent_instruction=parent_instruction,
                parent_observation=ParentObservation(
                    status=str(parent.get("status", "")),
                    evidence_ref=str(parent.get("evidence_ref", "")),
                ),
                observed_at=str(frame.get("observed_at", "")),
                database_path=database_path,
            )
        except Exception as error:
            return {
                "status": "UNKNOWN",
                "error_code": "TASK_FRAME_INITIALIZATION_FAILED",
                "detail": str(error),
                "session_id": session_id,
                "frame_id": frame_id,
            }
        self._task_frames[key] = runtime
        return {
            "status": "TASK_FRAME_HOST_ACTIVE",
            "persistent_task_frame_host": "AVAILABLE",
            "session_id": session_id,
            "frame_id": frame_id,
            "storage_scope": self.storage_scope,
            "runtime_state": runtime.runtime_state(),
        }

    def apply_task_frame_operation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one turn operation without closing the process-local ledger."""

        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        frame_id = self._required_text(payload, "frame_id", "TASK_FRAME_ID_REQUIRED")
        if isinstance(frame_id, dict):
            return frame_id
        runtime = self._task_frames.get((session_id, frame_id))
        if runtime is None:
            return {
                "status": "TASK_FRAME_HOST_UNKNOWN",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        operation = payload.get("operation")
        if not isinstance(operation, Mapping):
            return {
                "status": "TASK_FRAME_OPERATION_REQUIRED",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        try:
            # Import lazily to avoid changing the established CLI/Host import boundary.
            if __package__:
                from .cli import _task_operation
            else:
                from cli import _task_operation

            output = _task_operation(runtime, operation, 0)
        except Exception as error:
            return {
                "status": "UNKNOWN",
                "error_code": "TASK_FRAME_OPERATION_FAILED",
                "detail": str(error),
                "session_id": session_id,
                "frame_id": frame_id,
            }
        return {
            "status": "TASK_FRAME_OPERATION_APPLIED",
            "session_id": session_id,
            "frame_id": frame_id,
            "output": output,
            "runtime_state": runtime.runtime_state(),
        }

    def accept_task_frame_worker_result(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Accept one Host-captured Worker envelope without Parent rewriting."""

        session_id = self._required_text(payload, "session_id", "SESSION_ID_REQUIRED")
        if isinstance(session_id, dict):
            return session_id
        frame_id = self._required_text(payload, "frame_id", "TASK_FRAME_ID_REQUIRED")
        if isinstance(frame_id, dict):
            return frame_id
        runtime = self._task_frames.get((session_id, frame_id))
        if runtime is None:
            return {
                "status": "TASK_FRAME_HOST_UNKNOWN",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        envelope = payload.get("envelope")
        if not isinstance(envelope, Mapping):
            return {
                "status": "WORKER_RESULT_ENVELOPE_REQUIRED",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        return runtime.submit_worker_envelope(
            envelope=envelope,
            host_result_evidence_ref=str(
                payload.get("host_result_evidence_ref", "")
            ),
            observed_at=str(payload.get("observed_at", "")),
        )

    def task_frame_status(self, *, session_id: str, frame_id: str) -> dict[str, Any]:
        runtime = self._task_frames.get((session_id.strip(), frame_id.strip()))
        if runtime is None:
            return {
                "status": "TASK_FRAME_HOST_UNKNOWN",
                "session_id": session_id.strip(),
                "frame_id": frame_id.strip(),
            }
        return {
            "status": "TASK_FRAME_HOST_ACTIVE",
            "persistent_task_frame_host": "AVAILABLE",
            "session_id": session_id.strip(),
            "frame_id": frame_id.strip(),
            "runtime_state": runtime.runtime_state(),
            "execution_evidence": runtime.execution_evidence(),
        }

    def close_task_frame(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id", "")).strip()
        frame_id = str(payload.get("frame_id", "")).strip()
        runtime = self._task_frames.pop((session_id, frame_id), None)
        if runtime is None:
            return {
                "status": "TASK_FRAME_HOST_UNKNOWN",
                "session_id": session_id,
                "frame_id": frame_id,
            }
        runtime.close()
        return {
            "status": "TASK_FRAME_HOST_CLOSED",
            "session_id": session_id,
            "frame_id": frame_id,
        }

    def stop(self, *, session_id: str) -> dict[str, Any]:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            return {"status": "SESSION_ID_REQUIRED"}
        runtime = self._session_runtimes.pop(normalized_session_id, None)
        if runtime is None:
            return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": normalized_session_id}
        runtime.close()
        self._session_modes.pop(normalized_session_id, None)
        guard = self._execution_guards.pop(normalized_session_id, None)
        if guard is not None:
            guard.close()
        for key in [key for key in self._task_frames if key[0] == normalized_session_id]:
            self._task_frames.pop(key).close()
        return {"status": "HOST_SESSION_MEMORY_STOPPED", "session_id": normalized_session_id}

    def close(self) -> None:
        for runtime in self._session_runtimes.values():
            runtime.close()
        self._session_runtimes.clear()
        for runtime in self._mode_runtimes.values():
            runtime.close()
        self._mode_runtimes.clear()
        self._session_modes.clear()
        for guard in self._execution_guards.values():
            guard.close()
        self._execution_guards.clear()
        for runtime in self._task_frames.values():
            runtime.close()
        self._task_frames.clear()
        if self._git_proposals is not None:
            self._git_proposals.close()
            self._git_proposals = None

    def _runtime_for(
        self,
        session_id: str,
        *,
        create: bool,
        anchor_mode: str = "",
    ) -> AnchorSessionMemoryRuntime | None:
        runtime = self._session_runtimes.get(session_id)
        if runtime is not None:
            return runtime

        if not create:
            return None
        runtime = AnchorSessionMemoryRuntime()
        self._session_runtimes[session_id] = runtime
        if self._repository_root is not None:
            normalized_mode = self._normalize_anchor_mode(anchor_mode)
            if normalized_mode is not None:
                self._session_modes[session_id] = normalized_mode
        return runtime

    def _mode_runtime(self, anchor_mode: str) -> AnchorSessionMemoryRuntime:
        """Open one Mode store without treating an observer session as its identity."""

        assert self._repository_root is not None
        normalized_mode = anchor_mode.upper()
        runtime = self._mode_runtimes.get(normalized_mode)
        if runtime is None:
            runtime = AnchorSessionMemoryRuntime(
                database_path=self._anchor_store_path(normalized_mode)
            )
            self._mode_runtimes[normalized_mode] = runtime
        return runtime

    def _resolved_anchor_mode(self, session_id: str, anchor_mode: str = "") -> str | None:
        if self._repository_root is None:
            return None
        candidate = anchor_mode.strip() or self._session_modes.get(session_id, "")
        if not candidate or candidate == "UNKNOWN":
            return None
        return self._normalize_anchor_mode(candidate)

    def _activation_anchor_mode(
        self,
        payload: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> str | dict[str, str]:
        if self._repository_root is None:
            return ""
        coordinates = snapshot.get("coordinates")
        coordinate_mode = (
            coordinates.get("mode", "") if isinstance(coordinates, Mapping) else ""
        )
        candidate = payload.get("anchor_mode", payload.get("mode", coordinate_mode))
        if not isinstance(candidate, str) or not candidate.strip() or candidate == "UNKNOWN":
            return {"status": "ANCHOR_MODE_REQUIRED"}
        normalized_mode = self._normalize_anchor_mode(candidate)
        if normalized_mode is None:
            return {"status": "ANCHOR_MODE_INVALID"}
        return self._registered_anchor_mode(normalized_mode)

    def _anchor_store_path(self, anchor_mode: str) -> Path:
        assert self._repository_root is not None
        mode_digest = hashlib.sha256(anchor_mode.encode("utf-8")).hexdigest()[:12]
        filename = f"mode-{mode_digest}.sqlite3"
        path = (
            self._repository_root
            / ANCHOR_STORE_RELATIVE_PATH
            / filename
        )
        self._assert_runtime_path_contained(path)
        return path

    def _task_frame_store_path(self, session_id: str, frame_id: str) -> Path:
        assert self._repository_root is not None
        digest = hashlib.sha256(
            f"{session_id}\0{frame_id}".encode("utf-8")
        ).hexdigest()[:24]
        path = self._repository_root / TASK_FRAME_STORE_RELATIVE_PATH / f"{digest}.sqlite3"
        self._assert_runtime_path_contained(path)
        return path

    def _assert_runtime_path_contained(self, path: Path) -> None:
        assert self._repository_root is not None
        root = self._repository_root.resolve()
        candidate = path.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("runtime state path escapes the repository") from error
        current = root
        for part in path.relative_to(root).parts[:-1]:
            current /= part
            if not current.exists():
                continue
            stat_result = current.lstat()
            reparse_flag = getattr(stat_result, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            )
            if current.is_symlink() or reparse_flag:
                raise ValueError("runtime state path traverses a reparse point")

    @staticmethod
    def _normalize_anchor_mode(anchor_mode: str) -> str | None:
        try:
            return normalize_mode_id(anchor_mode)
        except ModeRegistryError:
            return None

    def _registered_anchor_mode(self, anchor_mode: str) -> str | dict[str, str]:
        assert self._repository_root is not None
        try:
            definition = load_mode_registry(self._repository_root).resolve(anchor_mode)
        except ModeRegistryError as error:
            return {"status": error.error_code, "detail": error.detail}
        return definition.mode

    def _mode_registry_binding(
        self, anchor_mode: str
    ) -> dict[str, int | str]:
        assert self._repository_root is not None
        try:
            registry = load_mode_registry(self._repository_root)
            definition = registry.resolve(anchor_mode)
        except ModeRegistryError as error:
            return {"status": error.error_code, "detail": error.detail}
        return {
            "mode_registry_revision": registry.revision,
            "mode_definition_digest": mode_definition_digest(definition),
            "mode_registry_digest": mode_registry_digest(registry),
        }

    def _unknown_session_status(self, session_id: str, mode: str) -> dict[str, str]:
        return {"status": "HOST_SESSION_MEMORY_UNKNOWN", "session_id": session_id}

    @staticmethod
    def _required_text(payload: Mapping[str, Any], key: str, status: str) -> str | dict[str, str]:
        value = payload.get(key, "")
        if not isinstance(value, str) or not value.strip():
            return {"status": status}
        return value.strip()

    @staticmethod
    def _optional_outcome(outcome: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("snapshot", "event"):
            if key in outcome:
                result[key] = outcome[key]
        return result


class AnchorSessionMemoryHostServer:
    """One loopback owner for mode-scoped Current and Beyond Anchor stores."""

    def __init__(
        self,
        *,
        host: str = LOOPBACK_HOST,
        port: int = 0,
        token: str = "",
        repository_root: Path | None = None,
        protect_lifecycle: bool = False,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("host adapter must bind to 127.0.0.1 only")
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        self.adapter = AnchorSessionMemoryHostAdapter(repository_root=repository_root)
        self._write_gateway_available = repository_root is not None
        self.token = token.strip() or secrets.token_urlsafe(24)
        self._protect_lifecycle = protect_lifecycle
        self._lifecycle_token = (
            secrets.token_urlsafe(24) if protect_lifecycle else self.token
        )
        # Memory-mode SQLite connections stay on this single HTTP owner thread.
        self._server = HTTPServer((host, port), self._handler_type())
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def lifecycle_token(self) -> str:
        """Return the private token for Boot-owned activate and stop routes."""

        return self._lifecycle_token

    def metadata(self) -> dict[str, str]:
        return {
            "status": "HOST_SESSION_MEMORY_LISTENING",
            "endpoint": self.endpoint,
            "token": self.token,
            "transport": "loopback-http",
            "storage_scope": MEMORY_STORAGE_SCOPE,
            "mode_anchor_storage_scope": (
                FILE_STORAGE_SCOPE if self._write_gateway_available else "UNKNOWN"
            ),
            "task_frame_storage_scope": (
                "project-local Task Frame SQLite file"
                if self._write_gateway_available
                else "UNKNOWN"
            ),
            "file_mutation_gateway": (
                "AVAILABLE" if self._write_gateway_available else "UNAVAILABLE"
            ),
            "mutation_gateway": (
                "AVAILABLE" if self._write_gateway_available else "UNAVAILABLE"
            ),
            "pre_write_hook": (
                "AVAILABLE" if self._write_gateway_available else "UNAVAILABLE"
            ),
            "persistent_task_frame_host": (
                "AVAILABLE" if self._write_gateway_available else "UNKNOWN"
            ),
            "lifecycle_routes": (
                "PROTECTED" if self._protect_lifecycle else "SHARED_TOKEN"
            ),
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()
            self.adapter.close()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        adapter = self.adapter
        token = self.token
        lifecycle_token = self._lifecycle_token

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized(token):
                    return
                parsed = urlparse(self.path)
                session_id = parse_qs(parsed.query).get("session_id", [""])[0]
                mode = parse_qs(parsed.query).get("mode", [""])[0]
                if parsed.path == "/v1/anchor-session-memory/status":
                    self._send(200, adapter.status(session_id=session_id, mode=mode))
                    return
                if parsed.path == "/v1/anchor-session-memory/evidence":
                    self._send(200, adapter.evidence(session_id=session_id, mode=mode))
                    return
                if parsed.path == "/v1/task-frame/status":
                    frame_id = parse_qs(parsed.query).get("frame_id", [""])[0]
                    self._send(
                        200,
                        adapter.task_frame_status(
                            session_id=session_id, frame_id=frame_id
                        ),
                    )
                    return
                self._send(404, {"status": "HOST_ADAPTER_ROUTE_NOT_FOUND"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                expected_token = (
                    lifecycle_token
                    if parsed.path
                    in {
                        "/v1/anchor-session-memory/activate",
                        "/v1/anchor-session-memory/stop",
                    }
                    else token
                )
                if not self._authorized(expected_token):
                    return
                payload = self._json_payload()
                if payload is None:
                    return
                if parsed.path == "/v1/anchor-session-memory/activate":
                    self._send(200, adapter.activate(payload))
                    return
                if parsed.path == "/v1/mode-anchor/prepare":
                    self._send(200, adapter.prepare_mode_current_anchor(payload))
                    return
                if parsed.path == "/v1/anchor-session-memory/observe":
                    self._send(200, adapter.record_observation(payload))
                    return
                if parsed.path == "/v1/anchor-session-memory/current-input":
                    self._send(200, adapter.observe_current_input(payload))
                    return
                if parsed.path == "/v1/anchor-session-memory/stop":
                    self._send(200, adapter.stop(session_id=str(payload.get("session_id", ""))))
                    return
                if parsed.path == "/v1/execution-binding/propose":
                    self._send(200, adapter.propose_execution(payload))
                    return
                if parsed.path == "/v1/execution-binding/apply":
                    self._send(200, adapter.bind_execution(payload))
                    return
                if parsed.path == "/v1/execution-binding/begin-work":
                    self._send(200, adapter.begin_project_source_work(payload))
                    return
                if parsed.path == "/v1/execution-binding/import-git-proposal":
                    self._send(200, adapter.bind_git_proposal(payload))
                    return
                if parsed.path == "/v1/execution-guard/check":
                    self._send(200, adapter.check_execution(payload))
                    return
                if parsed.path == "/v1/execution-guard/consume":
                    self._send(200, adapter.consume_execution_receipt(payload))
                    return
                if parsed.path == "/v1/mutation-gateway/apply-file":
                    self._send(200, adapter.apply_file_mutation(payload))
                    return
                if parsed.path == "/v1/mutation-gateway/apply-git":
                    self._send(200, adapter.apply_git_command(payload))
                    return
                if parsed.path == "/v1/task-frame/create":
                    self._send(200, adapter.create_task_frame(payload))
                    return
                if parsed.path == "/v1/task-frame/operation":
                    self._send(200, adapter.apply_task_frame_operation(payload))
                    return
                if parsed.path == "/v1/task-frame/worker-result":
                    self._send(200, adapter.accept_task_frame_worker_result(payload))
                    return
                if parsed.path == "/v1/task-frame/close":
                    self._send(200, adapter.close_task_frame(payload))
                    return
                self._send(404, {"status": "HOST_ADAPTER_ROUTE_NOT_FOUND"})

            def _authorized(self, expected_token: str) -> bool:
                if secrets.compare_digest(
                    self.headers.get(TOKEN_HEADER, ""), expected_token
                ):
                    return True
                self._send(403, {"status": "HOST_ADAPTER_TOKEN_INVALID"})
                return False

            def _json_payload(self) -> Mapping[str, Any] | None:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"status": "HOST_ADAPTER_REQUEST_INVALID"})
                    return None
                try:
                    payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(400, {"status": "HOST_ADAPTER_REQUEST_INVALID"})
                    return None
                if not isinstance(payload, Mapping):
                    self._send(400, {"status": "HOST_ADAPTER_REQUEST_INVALID"})
                    return None
                return payload

            def _send(self, http_status: int, payload: Mapping[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
                self.send_response(http_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return Handler


def call_host_adapter(
    *,
    endpoint: str,
    token: str,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    headers = {TOKEN_HEADER: token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{endpoint}{path}", data=data, headers=headers, method=method)
    timeout = (
        GIT_HTTP_TIMEOUT_SECONDS
        if path in GIT_HTTP_PATHS
        else DEFAULT_HTTP_TIMEOUT_SECONDS
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        finally:
            error.close()


def _physical_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Anchor Session Memory Host Adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Run a loopback-only process-local session memory server.")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--token", default="")
    args = parser.parse_args(argv)

    server = AnchorSessionMemoryHostServer(port=args.port, token=args.token)
    print(json.dumps(server.metadata(), ensure_ascii=True, separators=(",", ":"), sort_keys=True), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
