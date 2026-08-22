from __future__ import annotations

import argparse
import base64
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import queue
import re
import secrets
import sqlite3
import subprocess  # nosec B404
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from session_supervisor import (
    PROCESS_IDENTITY_FIELDS,
    SessionSupervisorError,
    SessionSupervisorStore,
)
from uuid import uuid4

from agent_session_gateway import (
    AgentSessionError,
    CodexAppServerSession,
    GrokAcpSession,
    UniverseAcpGateway,
)
from claude_permission_bridge import ClaudePermissionBridge
from claude_permission_broker import ClaudePermissionBroker
from claude_resident_session import ClaudeResidentError, ClaudeResidentSession
from host_profile import resolve_host_tool
from project_master_bridge import (
    ProjectMasterBridgeHost,
    ProjectMasterBridgeError,
    ProjectMasterBridgeHttpServer,
    normalize_bridge_envelope,
    post_agent_permission_request,
    post_master_reply,
    post_master_stream_event,
    utc_now,
)
from project_seed_apply import apply_project_seed_asset_proposal
from project_seed_assets import ProjectSeedAssetError
from project_integration_apply import (
    ProjectIntegrationApplyError,
    apply_project_integration_proposal,
)
from project_skill_binding import (
    ProjectSkillBindingError,
    build_project_skill_binding_proposal,
)
from project_skill_plan_apply import (
    ProjectSkillPlanApplyError,
    build_project_skill_plan_context,
    project_skill_plan_receipt,
)
from process_identity import WindowsKillOnCloseJob, launched_process_identity
from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli
from universe_runtime_worker_dispatch import (
    RuntimeWorkerDispatcher,
    WorkerDispatchError,
)
from universe_session_inject_hook import patch_mode_current_anchor
from worker_failure_evidence import WorkerFailureEvidenceStore


PROJECT_MASTER_HOST_SCHEMA = "universe.project-master-live-host.v1"
PROJECT_MASTER_SESSION_SCHEMA = "universe.project-master-session.v1"
PROVIDER_SESSION_CONNECTION_SCHEMA = "universe.provider-session-connection.v1"
SUPPORTED_PROVIDERS = frozenset({"GROK", "CODEX", "CLAUDE"})
TASK_PROPOSAL_DATABASE_RELATIVE_PATH = Path(
    ".ai/runtime/task_frames/task-proposals.sqlite3"
)
TASK_FRAME_PROFILE_RELATIVE_PATH = Path(
    ".ai/runtime/reference_runtime/profiles/task-frame-debate-v1.json"
)
TASK_FRAME_INSTRUCTION_PROFILE_RELATIVE_PATH = Path(
    ".ai/runtime/reference_runtime/profiles/task-frame-instruction-v2.json"
)
WRITE_ENABLED_WORKER_MAX_TURNS = 1
READ_ONLY_WORKER_MAX_TURNS = 16


class ProjectMasterHostError(RuntimeError):
    pass


def _task_frame_child_max_turns(*, write_enabled: bool) -> int:
    return (
        WRITE_ENABLED_WORKER_MAX_TURNS
        if write_enabled
        else READ_ONLY_WORKER_MAX_TURNS
    )


_WindowsKillOnCloseJob = WindowsKillOnCloseJob


class MasterProvider(Protocol):
    @property
    def session_ref(self) -> str: ...

    def reply(self, message: Mapping[str, Any]) -> str: ...

    def reply_stream(
        self,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> str: ...


class ContinuitySaver(Protocol):
    def save(
        self,
        *,
        project_root: Path,
        trigger: str,
        compressed_context: str,
        summary: str = "",
        source_refs: list[str] | None = None,
        runtime_coordinate: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def mark_dirty_end(
        self, project_root: Path, reason: str
    ) -> Mapping[str, Any]: ...


ReplyPoster = Callable[..., dict[str, Any]]
StreamPoster = Callable[..., dict[str, Any]]
PermissionPoster = Callable[..., dict[str, Any]]
NativeRunner = Callable[[NativeCliRequest], NativeCliResult]
BridgeRegistrar = Callable[[str, Mapping[str, Any]], tuple[dict[str, Any], bool]]
SourceBindingResolver = Callable[[Path], Mapping[str, Any]]
GovernanceContextResolver = Callable[[str], Mapping[str, Any]]
RetrievalContextResolver = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
NativeRoomObserver = Callable[[Mapping[str, Any]], None]
RoomPermissionObserver = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    None,
]


class CommanderSurfaceObserver(Protocol):
    def prepare(self) -> Mapping[str, Any]: ...

    def observe(self, message: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def observe_room_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ProjectModeCoordinator:
    """Invoke the installed project Runtime for Mode and surface ownership."""

    def __init__(
        self,
        project_root: Path,
        project_id: str,
        host_session_ref: str,
        *,
        session_node: str | None = None,
        requested_mode: str = "MASTER",
        native_runner: NativeRunner = run_native_cli,
        source_binding_resolver: SourceBindingResolver | None = None,
        session_supervisor: SessionSupervisorStore | None = None,
        worker_dispatcher: RuntimeWorkerDispatcher | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.session_node = _text(session_node or self.project_id, "session_node")
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self._mode_role: str | None = None
        self.host_session_ref = _text(host_session_ref, "host_session_ref")
        self.native_runner = native_runner
        self.source_binding_resolver = source_binding_resolver
        self.session_supervisor = session_supervisor
        self.runtime_cli = (
            self.project_root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        )
        if not self.runtime_cli.is_file():
            raise ProjectMasterHostError("PROJECT_RUNTIME_CLI_UNAVAILABLE")
        self._prepared: dict[str, Any] | None = None
        self._runtime_process: subprocess.Popen[str] | None = None
        self._runtime_job: _WindowsKillOnCloseJob | None = None
        self._runtime_binding: dict[str, str] | None = None
        self._runtime_stderr: deque[str] = deque(maxlen=40)
        self._runtime_lock = threading.RLock()
        self._supervisor_session_id: str | None = None
        self._lease_token: str | None = None
        self._lease_version: int | None = None
        self._process_identity: dict[str, Any] | None = None
        self._source_binding: dict[str, str] | None = None
        self.worker_dispatcher = worker_dispatcher or RuntimeWorkerDispatcher(
            self.project_root,
            failure_evidence_store=WorkerFailureEvidenceStore(
                _default_state_db(self.project_id)
            ),
        )

    def prepare(self) -> Mapping[str, Any]:
        """Prepare the live conversation coordinate without starting Runtime Boot.

        A resident provider is already observed by ``SessionSupervisorStore``
        before this method is called.  That Session Anchor is the coordinate a
        chat needs; launching the legacy ``prepare-session`` Runtime merely to
        open the chat made an otherwise healthy provider session depend on a
        second process, a Release selection, and a Mode Boot binding.

        """
        anchor_preparation = self._anchor_graph_preparation()
        if anchor_preparation is None:
            raise ProjectMasterHostError("PROJECT_MASTER_SESSION_ANCHOR_UNAVAILABLE")
        self._prepared = dict(anchor_preparation)
        return anchor_preparation

    def observe(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        message_id = _text(message.get("message_id"), "message.message_id")
        anchor_observation = self._anchor_graph_observation(
            evidence_ref=f"universe://project-room/messages/{message_id}"
        )
        if anchor_observation is None:
            raise ProjectMasterHostError("PROJECT_MASTER_SESSION_ANCHOR_UNAVAILABLE")
        return anchor_observation

    def observe_room_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        room_id = _text(event.get("room_id"), "event.room_id")
        room_event_id = _text(event.get("room_event_id"), "event.room_event_id")
        message = event.get("message")
        if not isinstance(message, Mapping) or message.get("author_role") != "USER":
            raise ProjectMasterHostError("PROJECT_COMMANDER_ROOM_EVENT_INVALID")
        anchor_observation = self._anchor_graph_observation(
            evidence_ref=f"universe://rooms/{room_id}/events/{room_event_id}"
        )
        if anchor_observation is None:
            raise ProjectMasterHostError("PROJECT_MASTER_SESSION_ANCHOR_UNAVAILABLE")
        return anchor_observation

    def _anchor_graph_preparation(self) -> dict[str, Any] | None:
        """Return the exact observed Session Anchor for a resident provider.

        ``None`` means no exact supervised Session Anchor owns this provider
        coordinate.  The caller must stop instead of manufacturing one through
        legacy Runtime Boot or a Mode Current Anchor.
        """
        session = self._anchor_graph_session()
        if session is None:
            return None
        anchor_ref = _text(session.get("session_anchor_ref"), "session_anchor_ref")
        mode = _text(session.get("mode"), "session.mode").upper()
        if mode != self.requested_mode:
            raise ProjectMasterHostError("PROJECT_MASTER_ANCHOR_MODE_MISMATCH")
        observed_at = utc_now()
        return {
            "schema": "universe.anchor-graph-session-preparation.v1",
            "status": "SESSION_PREPARED",
            "preparation_path": "ANCHOR_GRAPH",
            "project_id": self.project_id,
            "mode": mode,
            "session_id": _text(session.get("session_id"), "session_id"),
            "session_anchor_ref": anchor_ref,
            "mode_current_anchor": {
                "status": "MODE_CURRENT_ANCHOR_OBSERVED",
                "snapshot": {
                    "anchor_id": anchor_ref,
                    "observed_at": observed_at,
                    "snapshot": {
                        "anchor_id": anchor_ref,
                        "coordinates": {
                            "mode": mode,
                            "commander_surface": "UNIVERSE_UI",
                        },
                    },
                },
            },
        }

    def _anchor_graph_observation(
        self, *, evidence_ref: str
    ) -> dict[str, Any] | None:
        session = self._anchor_graph_session()
        if session is None:
            return None
        anchor_ref = _text(session.get("session_anchor_ref"), "session_anchor_ref")
        mode = _text(session.get("mode"), "session.mode").upper()
        if mode != self.requested_mode:
            raise ProjectMasterHostError("PROJECT_MASTER_ANCHOR_MODE_MISMATCH")
        observed_at = utc_now()
        return {
            "schema": "universe.anchor-graph-commander-observation.v1",
            "status": "COMMANDER_INPUT_OBSERVED",
            "anchor_mode": mode,
            "evidence_ref": _text(evidence_ref, "evidence_ref"),
            "snapshot": {
                "anchor_id": anchor_ref,
                "observed_at": observed_at,
                "snapshot": {
                    "anchor_id": anchor_ref,
                    "coordinates": {
                        "mode": mode,
                        "commander_surface": "UNIVERSE_UI",
                    },
                },
            },
        }

    def _anchor_graph_session(self) -> Mapping[str, Any] | None:
        if self.session_supervisor is None:
            return None
        candidates = [
            item
            for item in self.session_supervisor.list_sessions(
                node=self.session_node,
                mode=self.requested_mode,
                include_hidden=True,
            )
            if str(item.get("provider_session_ref") or "").strip()
            == self.host_session_ref
        ]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
        write_roots: tuple[Path, ...] | None = None,
        task_summary: str = "Apply one approved Universe Project Seed asset",
    ) -> Mapping[str, Any]:
        binding = self._ensure_runtime()
        normalized_operation = _text(operation, "operation").upper()
        normalized_target = target.expanduser().resolve(strict=target.exists())
        requested_roots = write_roots or (
            self.project_root / ".ai" / "universe",
        )
        normalized_roots: list[Path] = []
        for root in requested_roots:
            normalized_root = root.expanduser().resolve(strict=root.exists())
            try:
                normalized_root.relative_to(self.project_root)
            except ValueError as error:
                raise ProjectMasterHostError(
                    "PROJECT_MUTATION_ROOT_OUT_OF_SCOPE"
                ) from error
            normalized_roots.append(normalized_root)
        if not any(
            _path_is_within(normalized_target, normalized_root)
            for normalized_root in normalized_roots
        ):
            raise ProjectMasterHostError("PROJECT_MUTATION_TARGET_OUT_OF_SCOPE")
        normalized_summary = _text(task_summary, "task_summary")
        target_preimage = (
            {
                "status": "PRESENT",
                "sha256": hashlib.sha256(normalized_target.read_bytes()).hexdigest(),
            }
            if normalized_target.exists()
            else {"status": "ABSENT", "sha256": "NONE"}
        )
        proposal = self._invoke(
            (
                "execution-binding",
                "propose",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "request": {
                    "operation": normalized_operation,
                    "target": str(normalized_target),
                    "boundary": boundary,
                    "write_roots": [str(root) for root in normalized_roots],
                    "write_operations": ["CREATE", "MODIFY"],
                    "task_summary": normalized_summary,
                    "request_ref": request_ref,
                },
            },
        )
        if proposal.get("status") != "EXECUTION_ASSIGNMENT_PROPOSED":
            raise ProjectMasterHostError("PROJECT_SEED_ASSIGNMENT_PROPOSAL_FAILED")
        approval = {
            "status": "APPROVED",
            "proposal_id": proposal["proposal_id"],
            "commander_surface": "UNIVERSE_UI",
            "operation": normalized_operation,
            "target": str(normalized_target),
            "boundary": boundary,
            "evidence_ref": approval_evidence_ref,
            "authority_source_ref": approval_evidence_ref,
        }
        applied_binding = self._invoke(
            (
                "execution-binding",
                "apply",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "proposal": proposal,
                "approval": approval,
            },
        )
        if applied_binding.get("status") != "EXECUTION_BINDING_APPLIED":
            raise ProjectMasterHostError("PROJECT_SEED_EXECUTION_BINDING_FAILED")
        guard_request = {
            "session_id": binding["session_id"],
            "frame_id": binding["frame_id"],
            "anchor_id": binding["anchor_id"],
            "operation": normalized_operation,
            "target": str(normalized_target),
            "boundary": boundary,
            "source_commit": proposal["source_commit"],
            "validation_ref": proposal["validation_ref"],
            "payload_sha256": hashlib.sha256(content).hexdigest(),
            "target_preimage": target_preimage,
            "host_capability": {
                "filesystem_write": "AVAILABLE",
                "pre_write_hook": "AVAILABLE",
                "evidence_ref": (
                    "project-master://"
                    + self.project_id
                    + "/receipt-aware-mutation-gateway"
                ),
            },
            "approval": approval,
        }
        permit = self._invoke(
            (
                "execution-guard",
                "check",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "observed_at": utc_now(),
                "request": guard_request,
            },
        )
        if permit.get("status") != "EXECUTION_GUARD_PERMITTED":
            return permit
        result = self._invoke(
            (
                "mutation-gateway",
                "apply-file",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "observed_at": utc_now(),
                "request": guard_request,
                "receipt_id": permit["permit_receipt"]["receipt_id"],
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
        )
        return result

    def create_approved_descendant_task_frame(
        self,
        *,
        primary_proposal: Mapping[str, Any],
        governance_approval: Mapping[str, Any],
        source_work: Mapping[str, Any],
        task_frame: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Create one exact Task Frame from an already approved primary proposal.

        The primary proposal authorizes the bounded source roots.  It does not
        authorize the Host to guess which files a Worker may change, so the
        caller must provide an exact mutation target list for the descendant
        frame.  The frame approval inherits the same primary evidence only
        when every identity, boundary, root, and target check still matches.
        """

        primary_id = _text(primary_proposal.get("proposal_id"), "primary.proposal_id")
        primary_digest = _text(
            primary_proposal.get("proposal_digest"), "primary.proposal_digest"
        )
        if str(primary_proposal.get("state") or "").upper() != "APPROVED":
            raise ProjectMasterHostError("PRIMARY_TASK_PROPOSAL_NOT_APPROVED")
        primary_boundary = _text(primary_proposal.get("boundary"), "primary.boundary")
        _text(
            primary_proposal.get("task_summary"), "primary.task_summary"
        )
        primary_scope = primary_proposal.get("scope")
        if not isinstance(primary_scope, Mapping):
            raise ProjectMasterHostError("PRIMARY_TASK_PROPOSAL_SCOPE_INVALID")
        stored_approval = primary_proposal.get("approval")
        if not isinstance(stored_approval, Mapping):
            raise ProjectMasterHostError("PRIMARY_TASK_PROPOSAL_APPROVAL_UNAVAILABLE")
        stored_evidence_ref = _text(
            stored_approval.get("evidence_ref"), "primary.approval.evidence_ref"
        )

        approval_id = _text(
            governance_approval.get("proposal_id"), "governance_approval.proposal_id"
        )
        approval_digest = _text(
            governance_approval.get(
                "proposal_digest"
            ),
            "governance_approval.proposal_digest",
        )
        if (
            str(governance_approval.get("status") or "").upper() != "APPROVED"
            or approval_id != primary_id
            or approval_digest != primary_digest
            or str(governance_approval.get("commander_surface") or "").upper()
            != "UNIVERSE_UI"
        ):
            raise ProjectMasterHostError("PRIMARY_TASK_APPROVAL_MISMATCH")
        approval_evidence_ref = _text(
            governance_approval.get("evidence_ref"),
            "governance_approval.evidence_ref",
        )
        if approval_evidence_ref != stored_evidence_ref:
            raise ProjectMasterHostError("PRIMARY_TASK_APPROVAL_EVIDENCE_MISMATCH")
        active_work = governance_approval.get("active_work")
        if active_work is None:
            active_work = None
        elif not isinstance(active_work, Mapping):
            raise ProjectMasterHostError("PRIMARY_TASK_ACTIVE_WORK_INVALID")
        active_work_required = {
            "schema",
            "active_work_ref",
            "work_batch_id",
            "parent_instruction_ref",
            "proposal_id",
            "proposal_digest",
            "approval_evidence_ref",
            "commander_surface",
            "access_surface",
            "anchor",
            "recorded_at",
        }
        if active_work is not None and set(active_work) != active_work_required:
            raise ProjectMasterHostError("PRIMARY_TASK_ACTIVE_WORK_INVALID")
        active_anchor = active_work.get("anchor") if active_work is not None else None
        if active_work is not None and (
            active_work.get("schema") != "universe.active-work-reference.v1"
            or active_work.get("proposal_id") != primary_id
            or active_work.get("proposal_digest") != primary_digest
            or active_work.get("approval_evidence_ref") != approval_evidence_ref
            or active_work.get("commander_surface") != "UNIVERSE_UI"
            or not isinstance(active_anchor, Mapping)
            or set(active_anchor) != {"session_id", "anchor_ref", "provider", "currentness"}
            or active_anchor.get("currentness") != "CURRENT"
        ):
            raise ProjectMasterHostError("PRIMARY_TASK_ACTIVE_WORK_MISMATCH")

        normalized_work = self._approved_source_work(
            source_work=source_work,
            primary_scope=primary_scope,
            primary_boundary=primary_boundary,
            approval_evidence_ref=approval_evidence_ref,
        )
        normalized_frame = self._approved_task_frame_request(
            task_frame=task_frame,
            source_work=normalized_work,
        )
        binding = self._ensure_runtime()
        if active_anchor is not None and binding["anchor_id"] != active_anchor["anchor_ref"]:
            raise ProjectMasterHostError("PRIMARY_TASK_ACTIVE_WORK_ANCHOR_MISMATCH")
        origin_session_anchor_ref = self._origin_session_anchor_ref(binding)
        work_result = self._invoke(
            (
                "execution-binding",
                "begin-work",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "work": normalized_work,
            },
        )
        if work_result.get("status") != "WORK_RECEIPT_ACTIVATED":
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_ACTIVATION_FAILED")
        work_receipt = work_result.get("work_receipt")
        if not isinstance(work_receipt, Mapping):
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_RECEIPT_INVALID")
        work_receipt_id = _text(work_receipt.get("work_receipt_id"), "work_receipt_id")

        source_ref = normalized_frame["source_ref"]
        if source_ref == "NONE":
            source_ref = _text(
                primary_proposal.get("source_ref")
                or primary_proposal.get("request_ref"),
                "primary.source_ref",
            )
        task_summary_ref = _text(
            (
                active_work["active_work_ref"]
                if active_work is not None
                else primary_proposal.get("request_ref")
            ),
            "active_work.active_work_ref" if active_work is not None else "primary.request_ref",
        )
        execution_plan = {
            "profile_id": "task-frame-debate-v1",
            "requested_shape": "DEBATE",
            "resolved_shape": "DEBATE",
            "model_mode": "EXPLICIT",
            "frame_id": normalized_frame["frame_id"],
            "origin_anchor_ref": binding["anchor_id"],
            "origin_session_id": binding["session_id"],
            "origin_frame_id": binding["frame_id"],
            "task_summary_ref": task_summary_ref,
            "source_ref": source_ref,
            "candidate_source_ref": normalized_frame["candidate_source_ref"],
            "source_review_result": normalized_frame["source_review_result"],
            "parent_actor_ref": normalized_frame["parent_actor_ref"],
            "commander_surface": "UNIVERSE_UI",
            "execution_assignment_ref": work_receipt_id,
            "host_worker_capability": "AVAILABLE",
            "repository_write_scope": "BOUNDED",
            "mutation_scope": normalized_frame["mutation_scope"],
            "fallback_reason": "NONE",
            "transcript_policy": "BOUNDED_RETURNED_MESSAGES_ONLY",
            "turns": normalized_frame["turns"],
        }
        proposal_result = self._invoke(
            (
                "task-frame",
                "propose",
                "--repo-root",
                str(self.project_root),
                "--profile",
                str(TASK_FRAME_PROFILE_RELATIVE_PATH),
            ),
            {"execution_plan": execution_plan},
        )
        execution_proposal = proposal_result.get("execution_proposal")
        if not isinstance(execution_proposal, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_PROPOSAL_INVALID")
        execution_approval = {
            "status": "APPROVED",
            "proposal_id": _text(
                execution_proposal.get("proposal_id"),
                "task_frame_execution_proposal.proposal_id",
            ),
            "plan_digest": _text(
                execution_proposal.get("plan_digest"),
                "task_frame_execution_proposal.plan_digest",
            ),
            "commander_surface": "UNIVERSE_UI",
            "evidence_ref": approval_evidence_ref,
        }
        parent_instruction = {
            "instruction_id": normalized_frame["instruction_id"],
            "user_instruction_raw": normalized_frame["instruction_text"],
            "constraints": normalized_frame["constraints"],
            "expected_output": normalized_frame["expected_output"],
            "repository_write_scope": "BOUNDED",
            "mutation_scope": normalized_frame["mutation_scope"],
        }
        created = self._post_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/create",
            {
                "session_id": binding["session_id"],
                "profile": str(TASK_FRAME_PROFILE_RELATIVE_PATH),
                "frame": {
                    "frame_id": normalized_frame["frame_id"],
                    "origin_anchor_ref": binding["anchor_id"],
                    "origin_session_id": binding["session_id"],
                    "origin_frame_id": binding["frame_id"],
                    "origin_governance_session_ref": (
                        _text(
                            active_anchor["session_id"],
                            "active_work.anchor.session_id",
                        )
                        if active_anchor is not None
                        else "UNKNOWN"
                    ),
                    "task_summary_ref": task_summary_ref,
                    "source_ref": source_ref,
                    "execution_assignment_ref": work_receipt_id,
                    "task_frame_execution_proposal": dict(execution_proposal),
                    "task_frame_execution_approval": execution_approval,
                    "parent_instruction": parent_instruction,
                    "dispatch_topology": None,
                    "parent_observation": {
                        "status": "MATCHED",
                        "evidence_ref": approval_evidence_ref,
                    },
                    "observed_at": utc_now(),
                },
            },
        )
        if created.get("status") != "TASK_FRAME_HOST_ACTIVE":
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_CREATE_FAILED")
        self._record_task_frame_session_lineage(
            task_frame_id=normalized_frame["frame_id"],
            origin_anchor_ref=binding["anchor_id"],
            origin_session_anchor_ref=origin_session_anchor_ref,
            origin_session_id=binding["session_id"],
            origin_frame_id=binding["frame_id"],
        )
        declaration_turns = self._sequential_declared_turns(normalized_frame["turns"])
        declared = self._post_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/operation",
            {
                "session_id": binding["session_id"],
                "frame_id": normalized_frame["frame_id"],
                "operation": {
                    "operation": "declare_turns",
                    "turns": declaration_turns,
                    "observed_at": utc_now(),
                },
            },
        )
        output = declared.get("output")
        if (
            declared.get("status") != "TASK_FRAME_OPERATION_APPLIED"
            or not isinstance(output, Mapping)
            or output.get("status") not in {"TASK_TURNS_DECLARED", "TASK_TURNS_ALREADY_DECLARED"}
        ):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_TURN_DECLARATION_FAILED")
        return {
            "status": "APPROVED_DESCENDANT_TASK_FRAME_READY",
            "project_id": self.project_id,
            "primary_proposal_id": primary_id,
            "primary_proposal_digest": primary_digest,
            "approval_evidence_ref": approval_evidence_ref,
            "work_receipt_id": work_receipt_id,
            "execution_binding_id": _text(
                work_result.get("binding_id"), "work_result.binding_id"
            ),
            "task_frame_id": normalized_frame["frame_id"],
            "task_frame_proposal_id": execution_approval["proposal_id"],
            "task_frame_plan_digest": execution_approval["plan_digest"],
            "origin_session_anchor_ref": origin_session_anchor_ref,
            "turns": [
                {"turn_id": turn["turn_id"], "role": turn["role"]}
                for turn in normalized_frame["turns"]
            ],
            "repository_write": False,
        }

    def create_instruction_authorized_task_frame(
        self,
        *,
        proposal_reference: Mapping[str, Any],
        task_frame: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Create the v2 Task Frame from a direct Parent instruction.

        The proposal is an immutable planning reference only.  This seam does
        not read or create a governance approval artifact; the installed v2
        runtime profile remains responsible for its own instruction-scoped
        Guard when that profile is released.
        """

        if set(proposal_reference) != {
            "proposal_id",
            "proposal_digest",
            "request_ref",
        }:
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_REFERENCE_INVALID")
        proposal_id = _text(proposal_reference.get("proposal_id"), "proposal_reference.proposal_id")
        proposal_digest = _text(
            proposal_reference.get("proposal_digest"),
            "proposal_reference.proposal_digest",
        )
        if not re.fullmatch(r"[0-9a-f]{64}", proposal_digest.lower()):
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_REFERENCE_INVALID")
        instruction_ref = _text(
            proposal_reference.get("request_ref"), "proposal_reference.request_ref"
        )
        normalized_frame = self._approved_task_frame_request(
            task_frame=task_frame,
            source_work={
                "write_roots": [str(self.project_root)],
                "write_operations": ["CREATE", "MODIFY"],
            },
        )
        repository_write_scope = (
            "BOUNDED" if normalized_frame["mutation_scope"]["operations"] else "NONE"
        )
        binding = self._ensure_runtime()
        origin_session_anchor_ref = self._origin_session_anchor_ref(binding)
        instruction_assignment_ref = "instruction:" + normalized_frame["instruction_id"]
        execution_plan = {
            "profile_id": "task-frame-instruction-v2",
            # The Runtime worker topology remains Boss/Sub debate-shaped; v2
            # changes the Parent authority basis, not that execution shape.
            "requested_shape": "DEBATE",
            "resolved_shape": "DEBATE",
            "model_mode": "EXPLICIT",
            "frame_id": normalized_frame["frame_id"],
            # Runtime v1 keeps this legacy field; Session Anchor lineage is
            # persisted by the Host sidecar until v2 Runtime owns the field.
            "origin_anchor_ref": binding["anchor_id"],
            "origin_session_id": binding["session_id"],
            "origin_frame_id": binding["frame_id"],
            "task_summary_ref": instruction_ref,
            "source_ref": "NONE",
            "candidate_source_ref": normalized_frame["candidate_source_ref"],
            "source_review_result": normalized_frame["source_review_result"],
            "parent_actor_ref": normalized_frame["parent_actor_ref"],
            "commander_surface": "UNIVERSE_UI",
            "execution_assignment_ref": instruction_assignment_ref,
            "host_worker_capability": "AVAILABLE",
            "repository_write_scope": repository_write_scope,
            "mutation_scope": normalized_frame["mutation_scope"],
            "fallback_reason": "NONE",
            "transcript_policy": "BOUNDED_RETURNED_MESSAGES_ONLY",
            "turns": normalized_frame["turns"],
        }
        proposal_result = self._invoke(
            (
                "task-frame",
                "propose",
                "--repo-root",
                str(self.project_root),
                "--profile",
                str(TASK_FRAME_INSTRUCTION_PROFILE_RELATIVE_PATH),
            ),
            {"execution_plan": execution_plan},
        )
        execution_proposal = proposal_result.get("execution_proposal")
        if not isinstance(execution_proposal, Mapping):
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_PROPOSAL_INVALID")
        created = self._post_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/create",
            {
                "session_id": binding["session_id"],
                "profile": str(TASK_FRAME_INSTRUCTION_PROFILE_RELATIVE_PATH),
                "frame": {
                    "frame_id": normalized_frame["frame_id"],
                    "origin_anchor_ref": binding["anchor_id"],
                    "origin_session_id": binding["session_id"],
                    "origin_frame_id": binding["frame_id"],
                    "origin_governance_session_ref": "UNKNOWN",
                    "task_summary_ref": instruction_ref,
                    "source_ref": "NONE",
                    "execution_assignment_ref": instruction_assignment_ref,
                    "task_frame_execution_proposal": dict(execution_proposal),
                    # No approval artifact: task-frame-instruction-v2 derives
                    # authority from the Parent instruction_ref and Guard.
                    "task_frame_execution_approval": None,
                    "parent_instruction": {
                        "instruction_id": normalized_frame["instruction_id"],
                        "instruction_ref": instruction_ref,
                        "user_instruction_raw": normalized_frame["instruction_text"],
                        "constraints": normalized_frame["constraints"],
                        "expected_output": normalized_frame["expected_output"],
                        "repository_write_scope": repository_write_scope,
                        "mutation_scope": normalized_frame["mutation_scope"],
                    },
                    "parent_observation": {
                        "status": "MATCHED",
                        "evidence_ref": instruction_ref,
                    },
                    "observed_at": utc_now(),
                },
            },
        )
        if created.get("status") != "TASK_FRAME_HOST_ACTIVE":
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_CREATE_FAILED")
        declared = self._post_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/operation",
            {
                "session_id": binding["session_id"],
                "frame_id": normalized_frame["frame_id"],
                "operation": {
                    "operation": "declare_turns",
                    "turns": self._sequential_declared_turns(normalized_frame["turns"]),
                    "observed_at": utc_now(),
                },
            },
        )
        output = declared.get("output")
        if (
            declared.get("status") != "TASK_FRAME_OPERATION_APPLIED"
            or not isinstance(output, Mapping)
            or output.get("status")
            not in {"TASK_TURNS_DECLARED", "TASK_TURNS_ALREADY_DECLARED"}
        ):
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_TURN_DECLARATION_FAILED")
        self._record_task_frame_session_lineage(
            task_frame_id=normalized_frame["frame_id"],
            origin_anchor_ref=binding["anchor_id"],
            origin_session_anchor_ref=origin_session_anchor_ref,
            origin_session_id=binding["session_id"],
            origin_frame_id=binding["frame_id"],
        )
        return {
            "status": "INSTRUCTION_TASK_FRAME_READY",
            "project_id": self.project_id,
            "proposal_reference": {
                "proposal_id": proposal_id,
                "proposal_digest": proposal_digest,
                "request_ref": instruction_ref,
            },
            "task_frame_id": normalized_frame["frame_id"],
            "profile": str(TASK_FRAME_INSTRUCTION_PROFILE_RELATIVE_PATH),
            "origin_session_anchor_ref": origin_session_anchor_ref,
            "turns": [
                {"turn_id": turn["turn_id"], "role": turn["role"]}
                for turn in normalized_frame["turns"]
            ],
            "repository_write": False,
        }

    def run_approved_descendant_task_frame(
        self,
        *,
        task_frame_id: str,
        primary_proposal_id: str,
        primary_proposal_digest: str,
        approval_evidence_ref: str | None,
    ) -> Mapping[str, Any]:
        """Run one approved Frame through the Host-owned worker dispatcher.

        The Runtime owns turn state. This coordinator supplies the Parent-facing
        transport and forwards only the exact Runtime-approved mutation scope.
        """

        frame_id = _text(task_frame_id, "task_frame_id")
        primary_id = _text(primary_proposal_id, "primary_proposal_id")
        _text(primary_proposal_digest, "primary_proposal_digest")
        approval_ref = (
            _text(approval_evidence_ref, "approval_evidence_ref")
            if approval_evidence_ref is not None
            else None
        )
        binding = self._ensure_runtime(recover_task_frame_id=frame_id)
        self._validate_task_frame_session_lineage(
            task_frame_id=frame_id,
            binding=binding,
        )
        self._reopen_task_frame_from_runtime_store(
            binding=binding,
            task_frame_id=frame_id,
        )
        status = self._get_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/status",
            {"session_id": binding["session_id"], "frame_id": frame_id},
        )
        if status.get("status") != "TASK_FRAME_HOST_ACTIVE":
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_HOST_UNAVAILABLE")
        execution = status.get("execution_evidence")
        if not isinstance(execution, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_EVIDENCE_INVALID")
        gate = execution.get("execution_gate")
        if not isinstance(gate, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_GATE_UNAVAILABLE")
        if approval_ref is not None:
            if gate.get("approval_ref") != approval_ref:
                raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_LINEAGE_MISMATCH")
        elif gate.get("approval_ref") not in {None, "NONE"}:
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_LINEAGE_MISMATCH")
        plan = gate.get("execution_plan")
        if not isinstance(plan, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_PLAN_INVALID")
        if approval_ref is None and plan.get("profile_id") != "task-frame-instruction-v2":
            raise ProjectMasterHostError("INSTRUCTION_TASK_FRAME_PROFILE_MISMATCH")
        if _text(plan.get("frame_id"), "execution_plan.frame_id") != frame_id:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_PLAN_MISMATCH")
        parent_actor_ref = _text(
            plan.get("parent_actor_ref"), "execution_plan.parent_actor_ref"
        )
        turns = plan.get("turns")
        if not isinstance(turns, list) or not all(isinstance(turn, Mapping) for turn in turns):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_TURNS_INVALID")
        parent_mutation_scope = plan.get("mutation_scope")
        if not isinstance(parent_mutation_scope, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        boss = next(
            (
                dict(turn)
                for turn in turns
                if str(turn.get("role") or "").upper() == "BOSS"
            ),
            None,
        )
        if boss is None:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_MISSING")
        boss_turn_id = _text(boss.get("turn_id"), "execution_plan.boss.turn_id")
        boss_input = self._task_frame_operation(
            binding,
            frame_id,
            {"operation": "input_bundle", "turn_id": boss_turn_id},
        )
        parent_bundle = boss_input.get("parent_instruction_bundle")
        if not isinstance(parent_bundle, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_INSTRUCTION_BUNDLE_INVALID")
        instructions = parent_bundle.get("instructions")
        if not isinstance(instructions, list) or not instructions:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_INSTRUCTION_BUNDLE_INVALID")
        instruction_digests = [
            _text(item.get("instruction_digest"), "instruction.instruction_digest")
            for item in instructions
            if isinstance(item, Mapping)
        ]
        if len(instruction_digests) != len(instructions):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_INSTRUCTION_BUNDLE_INVALID")

        boss_request = {
            "schema": "universe.task-frame-worker-dispatch-request.v1",
            "provider": _text(boss.get("provider"), "execution_plan.boss.provider"),
            "endpoint": binding["endpoint"],
            "token": binding["token"],
            "session_id": binding["session_id"],
            "frame_id": frame_id,
            "turn_id": boss_turn_id,
            "invoker_actor_ref": parent_actor_ref,
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "context_pack": {
                "schema": "universe.task-frame-boss-context.v1",
                "frame_id": frame_id,
                "execution_plan": dict(plan),
                "input_bundle": boss_input,
            },
            "output_contract": self._boss_allocation_output_contract(
                turns, parent_mutation_scope=parent_mutation_scope
            ),
            "max_turns": 1,
            "result_mode": "STRUCTURED_JSON",
            "defer_terminal_result": True,
        }
        self._recover_stale_boss_claim(
            binding=binding,
            task_frame_id=frame_id,
            boss_request=boss_request,
        )
        try:
            boss_result = self.worker_dispatcher.dispatch(boss_request)
        except WorkerDispatchError as error:
            raise ProjectMasterHostError(error.code) from error
        if boss_result.get("status") != "WORKER_OUTPUT_CAPTURED":
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_CAPTURE_FAILED")
        captured_envelope = boss_result.get("worker_envelope")
        structured_result = boss_result.get("structured_result")
        if not isinstance(captured_envelope, Mapping) or not isinstance(
            structured_result, Mapping
        ):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_OUTPUT_INVALID")
        try:
            allocations = self._canonical_boss_allocations(
                structured_result.get("worker_allocations"),
                turns=turns,
                parent_mutation_scope=parent_mutation_scope,
            )
            allocation_result = self._task_frame_operation(
                binding,
                frame_id,
                {
                    "operation": "submit_boss_allocations",
                    "boss_turn_id": boss_turn_id,
                    "boss_worker_id": _text(boss_result.get("worker_id"), "boss.worker_id"),
                    "worker_run_ref": _text(
                        boss_result.get("worker_run_ref"), "boss.worker_run_ref"
                    ),
                    "instruction_digests": instruction_digests,
                    "worker_allocations": allocations,
                    "observed_at": utc_now(),
                },
            )
            if allocation_result.get("status") != "BOSS_ALLOCATIONS_RECORDED":
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_FAILED"
                )
        except ProjectMasterHostError as error:
            self._recover_captured_boss_claim(
                boss_request=boss_request,
                boss_result=boss_result,
                reason=str(error),
            )
            raise

        child_results: list[dict[str, str]] = []
        boss_actor_ref = _text(boss_result.get("worker_id"), "boss.worker_id")
        allocation_by_turn = {allocation["turn_id"]: allocation for allocation in allocations}
        try:
            for turn in turns:
                role = str(turn.get("role") or "").strip().upper()
                if role == "BOSS":
                    continue
                turn_id = _text(turn.get("turn_id"), "execution_plan.turn.turn_id")
                allocation = allocation_by_turn.get(turn_id)
                if allocation is None:
                    raise ProjectMasterHostError(
                        "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INCOMPLETE"
                    )
                input_bundle = self._task_frame_operation(
                    binding,
                    frame_id,
                    {"operation": "input_bundle", "turn_id": turn_id},
                )
                mutation_scope = dict(allocation["mutation_scope"])
                write_enabled = bool(mutation_scope["operations"])
                request = {
                    "schema": "universe.task-frame-worker-dispatch-request.v1",
                    "provider": _text(turn.get("provider"), "execution_plan.turn.provider"),
                    "endpoint": binding["endpoint"],
                    "token": binding["token"],
                    "session_id": binding["session_id"],
                    "frame_id": frame_id,
                    "turn_id": turn_id,
                    "invoker_actor_ref": boss_actor_ref,
                    "repository_write_scope": "BOUNDED" if write_enabled else "NONE",
                    "mutation_scope": mutation_scope,
                    "context_pack": {
                        "schema": "universe.task-frame-sub-worker-context.v1",
                        "frame_id": frame_id,
                        "semantic_role": role,
                        "allocation": allocation,
                        "input_bundle": input_bundle,
                    },
                    "output_contract": self._child_result_output_contract(
                        mutation_evidence_required=write_enabled
                    ),
                    "max_turns": _task_frame_child_max_turns(
                        write_enabled=write_enabled
                    ),
                    "result_mode": "STRUCTURED_JSON",
                }
                try:
                    child_result = self.worker_dispatcher.dispatch(request)
                except WorkerDispatchError as error:
                    raise ProjectMasterHostError(error.code) from error
                terminal_status = _text(child_result.get("status"), "child.status")
                if terminal_status not in {
                    "TASK_COMPLETED",
                    "TASK_FRAME_RESULT_RECORDED",
                    "TURN_COMPLETED",
                } and not terminal_status.startswith("TURN_COMPLETED"):
                    raise ProjectMasterHostError(
                        "DESCENDANT_TASK_FRAME_CHILD_RESULT_FAILED"
                    )
                child_payload = child_result.get("result")
                if not isinstance(child_payload, Mapping) or child_payload.get("outcome") != "SUCCEEDED":
                    raise ProjectMasterHostError(
                        "DESCENDANT_TASK_FRAME_CHILD_RESULT_INVALID"
                    )
                child_results.append({"turn_id": turn_id, "status": terminal_status})
        except ProjectMasterHostError as error:
            self._recover_captured_boss_claim(
                boss_request=boss_request,
                boss_result=boss_result,
                reason=str(error),
            )
            raise

        try:
            boss_completion = self.worker_dispatcher.record_captured_result(
                boss_request, captured_envelope
            )
        except WorkerDispatchError as error:
            raise ProjectMasterHostError(error.code) from error
        if boss_completion.get("status") != "TASK_COMPLETED":
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_COMPLETION_FAILED")
        return {
            "status": (
                "APPROVED_DESCENDANT_TASK_FRAME_COMPLETED"
                if approval_ref is not None
                else "INSTRUCTION_TASK_FRAME_COMPLETED"
            ),
            "project_id": self.project_id,
            "primary_proposal_id": primary_id,
            "task_frame_id": frame_id,
            "boss_turn_id": boss_turn_id,
            "child_results": child_results,
            "repository_write": False,
        }
    @staticmethod
    def _canonical_boss_allocations(
        raw_allocations: Any,
        *,
        turns: Sequence[Mapping[str, Any]],
        parent_mutation_scope: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        supported_roles = {
            "IMPLEMENTER",
            "SECURITY_REVIEWER",
            "QA_REVIEWER",
            "SUB_REVIEWER",
        }
        declared: dict[str, Mapping[str, Any]] = {}
        for turn in turns:
            role = str(turn.get("role") or "").strip().upper()
            if role == "BOSS":
                continue
            if role not in supported_roles:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_WORKER_ROLE_INVALID"
                )
            turn_id = _text(turn.get("turn_id"), "execution_plan.turn.turn_id")
            if turn_id in declared:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID"
                )
            declared[turn_id] = turn
        if not declared:
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INCOMPLETE"
            )
        if set(parent_mutation_scope) != {"operations", "targets"}:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        parent_operations = parent_mutation_scope.get("operations")
        parent_targets = parent_mutation_scope.get("targets")
        if not isinstance(parent_operations, list) or not isinstance(parent_targets, list):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        normalized_parent_operations = {
            _text(value, "execution_plan.mutation_scope.operations").upper()
            for value in parent_operations
        }
        normalized_parent_targets = {
            _text(value, "execution_plan.mutation_scope.targets")
            for value in parent_targets
        }
        if bool(normalized_parent_operations) != bool(normalized_parent_targets):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")

        if not isinstance(raw_allocations, list):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INVALID"
            )
        if len(raw_allocations) != len(declared):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INCOMPLETE"
            )
        proposals: dict[str, Mapping[str, Any]] = {}
        required_fields = {
            "turn_id",
            "worker_slot_ref",
            "worker_path",
            "task",
            "expected_output",
            "mutation_scope",
            "skill_bindings",
        }
        for item in raw_allocations:
            if not isinstance(item, Mapping) or set(item) != required_fields:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INVALID"
                )
            turn_id = _text(item.get("turn_id"), "boss_allocation.turn_id")
            if turn_id not in declared:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_UNDECLARED"
                )
            if turn_id in proposals:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_DUPLICATE"
                )
            proposals[turn_id] = item
        if set(proposals) != set(declared):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INCOMPLETE"
            )

        canonical: list[dict[str, Any]] = []
        for turn_id, turn in declared.items():
            proposal = proposals[turn_id]
            role = str(turn.get("role") or "").strip().upper()
            leaf = turn_id.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[a-zA-Z0-9._-]+", leaf):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID"
                )
            worker_slot_ref = _text(
                turn.get("worker_slot_ref"),
                "execution_plan.turn.worker_slot_ref",
            )
            worker_path = f"/root/boss/{leaf}"
            if (
                _text(proposal.get("worker_slot_ref"), "boss_allocation.worker_slot_ref")
                != worker_slot_ref
                or _text(proposal.get("worker_path"), "boss_allocation.worker_path")
                != worker_path
            ):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_IDENTITY_MISMATCH"
                )
            task = _text(proposal.get("task"), "boss_allocation.task")
            expected_output = proposal.get("expected_output")
            if not isinstance(expected_output, Mapping) or not expected_output:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INVALID"
                )
            mutation_scope = proposal.get("mutation_scope")
            if not isinstance(mutation_scope, Mapping) or set(mutation_scope) != {
                "operations",
                "targets",
            }:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_MUTATION_SCOPE_INVALID"
                )
            operations_value = mutation_scope.get("operations")
            targets_value = mutation_scope.get("targets")
            if not isinstance(operations_value, list) or not isinstance(targets_value, list):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_MUTATION_SCOPE_INVALID"
                )
            operations = [
                _text(value, "boss_allocation.mutation_scope.operations").upper()
                for value in operations_value
            ]
            targets = [
                _text(value, "boss_allocation.mutation_scope.targets")
                for value in targets_value
            ]
            if (
                len(set(operations)) != len(operations)
                or len(set(targets)) != len(targets)
                or bool(operations) != bool(targets)
                or not set(operations).issubset(normalized_parent_operations)
                or not set(targets).issubset(normalized_parent_targets)
            ):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_MUTATION_SCOPE_INVALID"
                )
            if role != "IMPLEMENTER" and (operations or targets):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_REVIEWER_MUTATION_SCOPE_FORBIDDEN"
                )
            skill_bindings = proposal.get("skill_bindings")
            if not isinstance(skill_bindings, list) or not all(
                isinstance(binding, Mapping) for binding in skill_bindings
            ):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_SKILL_BINDINGS_INVALID"
                )
            canonical.append(
                {
                    "turn_id": turn_id,
                    "worker_slot_ref": worker_slot_ref,
                    "worker_path": worker_path,
                    "task": task,
                    "expected_output": dict(expected_output),
                    "mutation_scope": {
                        "operations": operations,
                        "targets": targets,
                    },
                    "skill_bindings": [dict(binding) for binding in skill_bindings],
                }
            )
        return canonical
    def _recover_captured_boss_claim(
        self,
        *,
        boss_request: Mapping[str, Any],
        boss_result: Mapping[str, Any],
        reason: str,
    ) -> None:
        try:
            self.worker_dispatcher.recover_claimed_worker(
                boss_request,
                worker_id=_text(boss_result.get("worker_id"), "boss.worker_id"),
                worker_run_ref=_text(
                    boss_result.get("worker_run_ref"), "boss.worker_run_ref"
                ),
                failure_code="WORKER_PARENT_FINALIZATION_FAILED",
                failure_reason=_text(reason, "boss_finalization_reason"),
            )
        except WorkerDispatchError as error:
            raise ProjectMasterHostError(error.code) from error

    @staticmethod
    def _boss_allocation_output_contract(
        turns: Sequence[Mapping[str, Any]],
        *,
        parent_mutation_scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        parent_operations = [
            str(value).strip().upper()
            for value in parent_mutation_scope.get("operations", [])
        ]
        parent_targets = [
            str(value).strip()
            for value in parent_mutation_scope.get("targets", [])
        ]
        supported_roles = {
            "IMPLEMENTER",
            "SECURITY_REVIEWER",
            "QA_REVIEWER",
            "SUB_REVIEWER",
        }
        allocation_variants: list[dict[str, Any]] = []
        declared_turn_ids: set[str] = set()
        for turn in turns:
            role = str(turn.get("role") or "").strip().upper()
            if role == "BOSS":
                continue
            if role not in supported_roles:
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_WORKER_ROLE_INVALID"
                )
            turn_id = _text(turn.get("turn_id"), "execution_plan.turn.turn_id")
            leaf = turn_id.rsplit("/", 1)[-1]
            if turn_id in declared_turn_ids or not re.fullmatch(
                r"[a-zA-Z0-9._-]+", leaf
            ):
                raise ProjectMasterHostError(
                    "DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID"
                )
            declared_turn_ids.add(turn_id)
            worker_slot_ref = _text(
                turn.get("worker_slot_ref"),
                "execution_plan.turn.worker_slot_ref",
            )
            worker_path = f"/root/boss/{leaf}"
            if role == "IMPLEMENTER":
                mutation_scope = {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operations", "targets"],
                    "properties": {
                        "operations": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": parent_operations},
                        },
                        "targets": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": parent_targets},
                        },
                    },
                }
            else:
                mutation_scope = {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operations", "targets"],
                    "properties": {
                        "operations": {"type": "array", "maxItems": 0},
                        "targets": {"type": "array", "maxItems": 0},
                    },
                }
            allocation_variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "turn_id",
                        "worker_slot_ref",
                        "worker_path",
                        "task",
                        "expected_output",
                        "mutation_scope",
                        "skill_bindings",
                    ],
                    "properties": {
                        "turn_id": {"type": "string", "enum": [turn_id]},
                        "worker_slot_ref": {
                            "type": "string",
                            "enum": [worker_slot_ref],
                        },
                        "worker_path": {"type": "string", "enum": [worker_path]},
                        "task": {"type": "string"},
                        "expected_output": {"type": "object"},
                        "mutation_scope": mutation_scope,
                        "skill_bindings": {
                            "type": "array",
                            "maxItems": 0,
                            "items": {"type": "object"},
                        },
                    },
                }
            )
        if not allocation_variants:
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_BOSS_ALLOCATION_INCOMPLETE"
            )
        return {
            "schema": "universe.task-frame-boss-allocation.v1",
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "worker_allocations"],
                "properties": {
                    "summary": {"type": "string"},
                    "worker_allocations": {
                        "type": "array",
                        "minItems": len(allocation_variants),
                        "maxItems": len(allocation_variants),
                        "items": {"anyOf": allocation_variants},
                    },
                },
            },
        }

    @staticmethod
    def _child_result_output_contract(
        *, mutation_evidence_required: bool
    ) -> dict[str, Any]:
        evidence_refs = {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        }
        validation_entry = {
            "type": "object",
            "additionalProperties": False,
            "required": ["plane", "state", "evidence_refs"],
            "properties": {
                "plane": {"type": "string", "minLength": 1},
                "state": {
                    "type": "string",
                    "enum": ["PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"],
                },
                "evidence_refs": evidence_refs,
            },
        }
        required = ["outcome", "summary", "evidence_refs", "validation"]
        properties: dict[str, Any] = {
            "outcome": {"type": "string", "enum": ["SUCCEEDED"]},
            "summary": {"type": "string", "minLength": 1},
            "evidence_refs": evidence_refs,
            "validation": {
                "type": "array",
                "minItems": 1,
                "items": validation_entry,
            },
            "mutation_evidence_refs": evidence_refs,
        }
        if mutation_evidence_required:
            required.append("mutation_evidence_refs")
        return {
            "schema": "universe.task-frame-child-result.v1",
            "mutation_evidence_required": mutation_evidence_required,
            "instruction": (
                "Return only the structured result. outcome must be SUCCEEDED, "
                "include evidence references and at least one PASS validation."
            ),
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            },
        }
    def _task_frame_operation(
        self,
        binding: Mapping[str, str],
        frame_id: str,
        operation: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self._post_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/operation",
            {
                "session_id": binding["session_id"],
                "frame_id": frame_id,
                "operation": dict(operation),
            },
        )
        output = result.get("output")
        if result.get("status") != "TASK_FRAME_OPERATION_APPLIED" or not isinstance(
            output, Mapping
        ):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_OPERATION_FAILED")
        return dict(output)

    @staticmethod
    def _sequential_declared_turns(turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Project semantic Host roles into the Runtime's Boss/Sub topology."""

        worker_roles = {
            "IMPLEMENTER",
            "SECURITY_REVIEWER",
            "QA_REVIEWER",
            "SUB_REVIEWER",
        }
        declared: list[dict[str, Any]] = []
        previous_turn_id = ""
        root_seen = False
        for index, turn in enumerate(turns):
            turn_id = _text(turn.get("turn_id"), f"task_frame.turns[{index}].turn_id")
            role = _text(turn.get("role"), f"task_frame.turns[{index}].role").upper()
            if role == "BOSS":
                if root_seen or index != 0:
                    raise ProjectMasterHostError(
                        "DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID"
                    )
                root_seen = True
                runtime_role = "BOSS"
                inputs: list[str] = []
            else:
                if not root_seen or role not in worker_roles:
                    raise ProjectMasterHostError(
                        "DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID"
                    )
                runtime_role = role
                inputs = [previous_turn_id]
            declared.append(
                {
                    "turn_id": turn_id,
                    "role": runtime_role,
                    "input_turn_ids": inputs,
                }
            )
            previous_turn_id = turn_id
        if not root_seen:
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID"
            )
        return declared
    def _approved_source_work(
        self,
        *,
        source_work: Mapping[str, Any],
        primary_scope: Mapping[str, Any],
        primary_boundary: str,
        approval_evidence_ref: str,
    ) -> dict[str, Any]:
        required = {
            "scope_kind",
            "write_roots",
            "write_operations",
            "boundary",
            "task_summary",
            "instruction_ref",
        }
        if set(source_work) != required:
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_REQUEST_INVALID")
        if source_work.get("scope_kind") != "PROJECT_SOURCE_WORK":
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_SCOPE_INVALID")
        if _text(source_work.get("boundary"), "source_work.boundary") != primary_boundary:
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_BOUNDARY_MISMATCH")
        if (
            _text(source_work.get("instruction_ref"), "source_work.instruction_ref")
            != approval_evidence_ref
        ):
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_EVIDENCE_MISMATCH")
        roots_value = source_work.get("write_roots")
        if not isinstance(roots_value, list) or not roots_value:
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_ROOTS_INVALID")
        declared_paths = _absolute_paths_in_value(primary_scope)
        normalized_roots: list[str] = []
        for item in roots_value:
            root = _approved_source_path(item, "source_work.write_roots")
            if not _path_is_within(root, self.project_root):
                raise ProjectMasterHostError("APPROVED_SOURCE_WORK_ROOT_OUT_OF_SCOPE")
            if root not in declared_paths:
                raise ProjectMasterHostError("APPROVED_SOURCE_WORK_ROOT_NOT_PRIMARY")
            root_text = str(root)
            if root_text not in normalized_roots:
                normalized_roots.append(root_text)
        operations_value = source_work.get("write_operations")
        if not isinstance(operations_value, list) or not operations_value:
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_OPERATIONS_INVALID")
        operations = [
            _text(item, "source_work.write_operations").upper()
            for item in operations_value
        ]
        if len(set(operations)) != len(operations) or not set(operations).issubset(
            {"CREATE", "MODIFY"}
        ):
            raise ProjectMasterHostError("APPROVED_SOURCE_WORK_OPERATIONS_INVALID")
        return {
            "scope_kind": "PROJECT_SOURCE_WORK",
            "write_roots": normalized_roots,
            "write_operations": operations,
            "boundary": primary_boundary,
            "task_summary": _text(source_work.get("task_summary"), "source_work.task_summary"),
            "instruction_ref": approval_evidence_ref,
        }

    def _approved_task_frame_request(
        self,
        *,
        task_frame: Mapping[str, Any],
        source_work: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {
            "frame_id",
            "parent_actor_ref",
            "mutation_scope",
            "turns",
            "instruction_id",
            "instruction_text",
            "constraints",
            "expected_output",
        }
        candidate_fields = {"candidate_source_ref", "source_review_result"}
        actual_fields = set(task_frame)
        if actual_fields == required:
            source_ref = "NONE"
            candidate_source_ref = "NONE"
            source_review_result = None
        elif actual_fields == required | candidate_fields:
            (
                source_ref,
                candidate_source_ref,
                source_review_result,
            ) = self._approved_source_review(
                candidate_source_ref=task_frame.get("candidate_source_ref"),
                source_review_result=task_frame.get("source_review_result"),
            )
        else:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_REQUEST_INVALID")
        mutation_scope = task_frame.get("mutation_scope")
        if not isinstance(mutation_scope, Mapping) or set(mutation_scope) != {
            "operations",
            "targets",
        }:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        operations_value = mutation_scope.get("operations")
        targets_value = mutation_scope.get("targets")
        if not isinstance(operations_value, list) or not isinstance(targets_value, list):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        operations = [
            _text(item, "task_frame.mutation_scope.operations").upper()
            for item in operations_value
        ]
        if (
            len(set(operations)) != len(operations)
            or not set(operations).issubset(set(source_work["write_operations"]))
        ):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        normalized_targets: list[str] = []
        for item in targets_value:
            target = _approved_source_path(item, "task_frame.mutation_scope.targets")
            if not any(
                _path_is_within(target, Path(root))
                for root in source_work["write_roots"]
            ):
                raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_TARGET_OUT_OF_SCOPE")
            target_text = str(target)
            if target_text in normalized_targets:
                raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
            normalized_targets.append(target_text)
        if bool(operations) != bool(normalized_targets):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_SCOPE_INVALID")
        turns = task_frame.get("turns")
        if (
            not isinstance(turns, list)
            or not turns
            or not all(isinstance(turn, Mapping) for turn in turns)
        ):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_TURNS_INVALID")
        constraints = task_frame.get("constraints")
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) and item.strip() for item in constraints
        ):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_CONSTRAINTS_INVALID")
        expected_output = task_frame.get("expected_output")
        if not isinstance(expected_output, Mapping):
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_OUTPUT_INVALID")
        return {
            "frame_id": _text(task_frame.get("frame_id"), "task_frame.frame_id"),
            "parent_actor_ref": _text(
                task_frame.get("parent_actor_ref"), "task_frame.parent_actor_ref"
            ),
            "mutation_scope": {
                "operations": operations,
                "targets": normalized_targets,
            },
            "turns": [dict(turn) for turn in turns],
            "instruction_id": _text(
                task_frame.get("instruction_id"), "task_frame.instruction_id"
            ),
            "instruction_text": _text(
                task_frame.get("instruction_text"), "task_frame.instruction_text"
            ),
            "constraints": list(constraints),
            "expected_output": dict(expected_output),
            "source_ref": source_ref,
            "candidate_source_ref": candidate_source_ref,
            "source_review_result": source_review_result,
        }

    @staticmethod
    def _approved_source_review(
        *, candidate_source_ref: Any, source_review_result: Any
    ) -> tuple[str, str, dict[str, Any]]:
        candidate_ref = _text(
            candidate_source_ref, "task_frame.candidate_source_ref"
        )
        if candidate_ref.upper() in {"NONE", "UNKNOWN"}:
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_CANDIDATE_SOURCE_REQUIRED"
            )
        if not isinstance(source_review_result, Mapping):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_SOURCE_REVIEW_REQUIRED"
            )
        result = dict(source_review_result)
        if (
            result.get("schema") != "ai-career.source-review-result.v1"
            or result.get("status") != "SOURCE_REVIEW_PERMITTED"
            or result.get("review_mode") != "STATIC_REVIEW"
            or result.get("candidate_execution") != "FORBIDDEN"
            or result.get("repository_write") is not False
            or result.get("authority_created") is not False
            or result.get("execution_assignment_created") is not False
        ):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_SOURCE_REVIEW_INVALID"
            )
        policy = result.get("policy_source")
        candidate = result.get("candidate_source")
        if not isinstance(policy, Mapping) or not isinstance(candidate, Mapping):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_SOURCE_REVIEW_INVALID"
            )
        policy_ref = _text(policy.get("ref"), "source_review.policy_source.ref")
        policy_commit = _text(
            policy.get("commit"), "source_review.policy_source.commit"
        ).lower()
        candidate_commit = _text(
            candidate.get("commit"), "source_review.candidate_source.commit"
        ).lower()
        if (
            not re.fullmatch(r"[0-9a-f]{40}", policy_commit)
            or not re.fullmatch(r"[0-9a-f]{40}", candidate_commit)
            or policy_commit == candidate_commit
            or policy_ref == candidate_ref
            or not candidate_ref.endswith("@" + candidate_commit)
            or candidate.get("ref") != candidate_ref
            or candidate.get("classification") != "DATA_ONLY"
            or candidate.get("policy_activation") != "FORBIDDEN"
            or str(policy.get("kind") or "").upper()
            not in {"TRUSTED_BASE", "INSTALLED_DISTRIBUTION"}
            or policy.get("use") != "REVIEWER_POLICY"
            or str(policy.get("evidence_ref") or "").strip().upper()
            in {"", "UNKNOWN"}
        ):
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_SOURCE_REVIEW_MISMATCH"
            )
        try:
            normalized_result = json.loads(
                json.dumps(result, ensure_ascii=False, sort_keys=True)
            )
        except (TypeError, ValueError) as error:
            raise ProjectMasterHostError(
                "DESCENDANT_TASK_FRAME_SOURCE_REVIEW_INVALID"
            ) from error
        return policy_ref, candidate_ref, normalized_result

    @staticmethod
    def _post_runtime(
        endpoint: str,
        token: str,
        path: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        parsed = urlsplit(_text(endpoint, "runtime.endpoint"))
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.port
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_ENDPOINT_INVALID")
        request = Request(
            endpoint.rstrip("/") + path,
            data=json.dumps(
                dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Anchor-Session-Memory-Token": _text(token, "runtime.token"),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:  # nosec B310
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_UNAVAILABLE") from error
        if not isinstance(result, dict):
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_RESULT_INVALID")
        return result

    @staticmethod
    def _get_runtime(
        endpoint: str,
        token: str,
        path: str,
        query: Mapping[str, str],
    ) -> dict[str, Any]:
        parsed = urlsplit(_text(endpoint, "runtime.endpoint"))
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.port
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_ENDPOINT_INVALID")
        request = Request(
            endpoint.rstrip("/") + path + "?" + urlencode(dict(query)),
            headers={
                "X-Anchor-Session-Memory-Token": _text(token, "runtime.token"),
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=30) as response:  # nosec B310
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_UNAVAILABLE") from error
        if not isinstance(result, dict):
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_RESULT_INVALID")
        return result

    def close(self) -> None:
        with self._runtime_lock:
            process = self._runtime_process
            job = self._runtime_job
        if process is None or process.poll() is not None:
            with self._runtime_lock:
                self._runtime_process = None
                self._runtime_job = None
                self._runtime_binding = None
            if job is not None:
                job.close()
            self._mark_stale_if_owned("PROCESS_NOT_RUNNING_AT_STOP")
            return
        stop_receipt = self._authorize_supervised_stop()
        with self._runtime_lock:
            self._runtime_process = None
            self._runtime_job = None
            self._runtime_binding = None
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if job is not None:
            job.close()
        self._complete_supervised_stop(stop_receipt)

    def reconcile(self) -> str:
        with self._runtime_lock:
            process = self._runtime_process
            if process is None:
                return "NOT_STARTED"
            if process.poll() is None:
                return "LIVE"
            self._runtime_process = None
            job = self._runtime_job
            self._runtime_job = None
            self._runtime_binding = None
        if job is not None:
            job.close()
        self._mark_stale_if_owned("PROCESS_EXITED_UNEXPECTEDLY")
        return "EXITED"

    def continuity_coordinate(self) -> Mapping[str, str] | None:
        with self._runtime_lock:
            binding = (
                dict(self._runtime_binding)
                if self._runtime_binding is not None
                else None
            )
            process = self._runtime_process
        if binding is None or process is None or process.poll() is not None:
            return None
        return {
            "node": self.session_node,
            "mode": self.requested_mode,
            "session_id": binding["session_id"],
            "frame_id": binding["frame_id"],
            "anchor_id": binding["anchor_id"],
            "currentness": binding["runtime_currentness_observation"],
            "source_ref": (
                self._resolved_source_binding()["source_ref"]
            ),
        }

    def _resolved_source_binding(self) -> dict[str, str]:
        if self._source_binding is not None:
            return dict(self._source_binding)
        if self.source_binding_resolver is None:
            raise ProjectMasterHostError("PROJECT_RELEASE_SELECTION_REQUIRED")
        candidate = self.source_binding_resolver(self.project_root)
        if not isinstance(candidate, Mapping):
            raise ProjectMasterHostError("PROJECT_RELEASE_SELECTION_UNAVAILABLE")
        if str(candidate.get("status") or "").upper() != "SELECTED":
            raise ProjectMasterHostError("PROJECT_RELEASE_SELECTION_REQUIRED")
        release_id = _text(candidate.get("release_id"), "release_id")
        source_repository = _text(
            candidate.get("source_repository"), "source_repository"
        )
        source_commit = _text(candidate.get("source_commit"), "source_commit").lower()
        database_sha256 = _text(
            candidate.get("database_sha256"), "database_sha256"
        ).lower()
        if (
            len(source_commit) != 40
            or any(character not in "0123456789abcdef" for character in source_commit)
            or len(database_sha256) != 64
            or any(character not in "0123456789abcdef" for character in database_sha256)
        ):
            raise ProjectMasterHostError("PROJECT_RELEASE_SELECTION_INVALID")
        binding = {
            "source_ref": f"universe-release-db://{release_id}@{database_sha256}",
            "source_commit": source_commit,
            "source_repository": source_repository,
        }
        self._source_binding = binding
        return dict(binding)

    def _ensure_runtime(
        self, *, recover_task_frame_id: str | None = None
    ) -> dict[str, str]:
        with self._runtime_lock:
            if (
                self._runtime_binding is not None
                and self._runtime_process is not None
                and self._runtime_process.poll() is None
            ):
                return dict(self._runtime_binding)
            # A resident Host attaches directly to the exact Session Anchor
            # observed by the Supervisor.  Mode Boot bindings remain only for
            # already-running compatibility callers; creating one here would
            # reintroduce the legacy prepare-session gate.
            session = self._anchor_graph_session()
            if session is None:
                raise ProjectMasterHostError("PROJECT_MASTER_SESSION_ANCHOR_UNAVAILABLE")
            anchor_id = _text(session.get("session_anchor_ref"), "session_anchor_ref")
            session_id = _text(session.get("session_id"), "session_id")
            frame_id = _text(
                recover_task_frame_id or "current", "project_runtime.frame_id"
            )
            self._mode_role = "UNASSIGNED"
            token = secrets.token_urlsafe(32)
            python = _required_host_executable("python")
            command = [
                str(python),
                str(self.runtime_cli),
                "project-runtime",
                "serve",
                "--repo-root",
                str(self.project_root),
                "--session-id",
                session_id,
                "--frame-id",
                frame_id,
                "--anchor-id",
                anchor_id,
                "--mode",
                self.requested_mode,
                "--host-action",
                "PERSISTENT_SESSION_ATTACH",
                "--session-location",
                "PROJECT_MASTER_HOST",
                "--commander-surface",
                "UNIVERSE_UI",
                "--execution-surface",
                "LOCAL_RUNTIME",
                "--repository-location",
                str(self.project_root),
                "--port",
                "0",
                "--token",
                token,
            ]
            options: dict[str, Any] = {
                "cwd": str(self.project_root),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "shell": False,
            }
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                process = subprocess.Popen(command, **options)  # nosec B603
                runtime_job = _WindowsKillOnCloseJob(process)
                startup = self._read_runtime_startup(process)
                host_adapter = startup.get("host_adapter")
                runtime_state = startup.get("runtime_state")
                if (
                    startup.get("status") != "PERSISTENT_SESSION_ATTACHED"
                    or not isinstance(host_adapter, Mapping)
                    or not isinstance(runtime_state, Mapping)
                    or runtime_state.get("anchor_id") != anchor_id
                    or runtime_state.get("mode") != self.requested_mode
                    or runtime_state.get("role") != "UNASSIGNED"
                    or runtime_state.get("executable_runtime_currentness") != "CURRENT"
                    or startup.get("attachment_path") != "ANCHOR_GRAPH"
                    or "mode_boot_binding" in startup
                ):
                    raise ProjectMasterHostError(
                        "PROJECT_MASTER_RUNTIME_START_RESULT_INVALID"
                    )
                endpoint = _text(host_adapter.get("endpoint"), "host_adapter.endpoint")
                if _text(host_adapter.get("token"), "host_adapter.token") != token:
                    raise ProjectMasterHostError(
                        "PROJECT_MASTER_RUNTIME_TOKEN_MISMATCH"
                    )
            except Exception:
                if "process" in locals() and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                if "runtime_job" in locals():
                    runtime_job.close()
                raise
            self._runtime_process = process
            self._runtime_job = runtime_job
            self._runtime_binding = {
                "endpoint": endpoint,
                "token": token,
                "session_id": session_id,
                "frame_id": frame_id,
                "anchor_id": anchor_id,
                "attachment_path": "ANCHOR_GRAPH",
                "runtime_currentness_observation": str(
                    runtime_state["executable_runtime_currentness"]
                ),
            }
            try:
                self._register_process_lease(
                    process=process,
                    command=command,
                    endpoint=endpoint,
                    token=token,
                    runtime_session_id=session_id,
                    anchor_id=anchor_id,
                )
            except Exception:
                with self._runtime_lock:
                    self._runtime_process = None
                    self._runtime_job = None
                    self._runtime_binding = None
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                runtime_job.close()
                raise
            return dict(self._runtime_binding)

    def _runtime_session_id(
        self,
        *,
        anchor_id: str,
        frame_id: str,
        recover_task_frame_id: str | None,
    ) -> str:
        if recover_task_frame_id is not None:
            recovered = self._recover_task_frame_session_id(
                task_frame_id=_text(recover_task_frame_id, "task_frame_id"),
                anchor_id=anchor_id,
                frame_id=frame_id,
            )
            if recovered is not None:
                return recovered
        if self.session_node == self.project_id and self.requested_mode == "MASTER":
            return "project-master-" + self.project_id.lower() + "-master"
        return (
            "project-mode-"
            + self.session_node.lower()
            + "-"
            + self.project_id.lower()
            + "-"
            + self.requested_mode.lower()
        )

    @property
    def _task_frame_session_lineage_path(self) -> Path:
        return (
            self.project_root
            / ".ai"
            / "runtime"
            / "task_frames"
            / "task-frame-session-lineage.sqlite3"
        )

    def _origin_session_anchor_ref(self, binding: Mapping[str, str]) -> str:
        """Resolve the durable Session Anchor without changing Runtime v1 fields."""

        if self.session_supervisor is None:
            return "UNKNOWN"
        session_id = self._supervisor_session_id
        session: Mapping[str, Any] | None = None
        if session_id:
            try:
                session = self.session_supervisor.get_session(session_id)
            except SessionSupervisorError:
                session = None
        if session is None:
            candidates = [
                item
                for item in self.session_supervisor.list_sessions(
                    node=self.session_node,
                    mode=self.requested_mode,
                    include_hidden=True,
                )
                if str(item.get("provider_session_ref") or "")
                == str(binding.get("session_id") or "")
            ]
            if len(candidates) == 1:
                session = candidates[0]
        if session is None:
            return "UNKNOWN"
        return str(session.get("session_anchor_ref") or "UNKNOWN")

    def session_anchor_ref(self) -> str:
        """Return only the Supervisor-verified Session Anchor for this Host."""

        with self._runtime_lock:
            binding = (
                dict(self._runtime_binding)
                if self._runtime_binding is not None
                else None
            )
        if binding is None:
            return "UNKNOWN"
        return self._origin_session_anchor_ref(binding)

    def _task_frame_session_lineage(self, task_frame_id: str) -> dict[str, str] | None:
        path = self._task_frame_session_lineage_path
        if not path.is_file():
            return None
        try:
            connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1
            )
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    """
                    SELECT task_frame_id, origin_anchor_ref, origin_session_anchor_ref,
                           origin_session_id, origin_frame_id
                    FROM task_frame_session_lineage WHERE task_frame_id = ?
                    """,
                    (task_frame_id,),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise ProjectMasterHostError("TASK_FRAME_SESSION_LINEAGE_UNAVAILABLE") from error
        return None if row is None else {key: str(row[key]) for key in row.keys()}

    def _record_task_frame_session_lineage(
        self,
        *,
        task_frame_id: str,
        origin_anchor_ref: str,
        origin_session_anchor_ref: str,
        origin_session_id: str,
        origin_frame_id: str,
    ) -> None:
        path = self._task_frame_session_lineage_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(path, timeout=30)
            try:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS task_frame_session_lineage (
                            task_frame_id TEXT PRIMARY KEY,
                            origin_anchor_ref TEXT NOT NULL,
                            origin_session_anchor_ref TEXT NOT NULL,
                            origin_session_id TEXT NOT NULL,
                            origin_frame_id TEXT NOT NULL,
                            recorded_at TEXT NOT NULL
                        )
                        """
                    )
                    existing = connection.execute(
                        """
                        SELECT origin_anchor_ref, origin_session_anchor_ref,
                               origin_session_id, origin_frame_id
                        FROM task_frame_session_lineage WHERE task_frame_id = ?
                        """,
                        (task_frame_id,),
                    ).fetchone()
                    expected = (
                        origin_anchor_ref,
                        origin_session_anchor_ref,
                        origin_session_id,
                        origin_frame_id,
                    )
                    if existing is not None:
                        if tuple(str(item) for item in existing) != expected:
                            raise ProjectMasterHostError(
                                "TASK_FRAME_SESSION_LINEAGE_CONFLICT"
                            )
                        return
                    connection.execute(
                        """
                        INSERT INTO task_frame_session_lineage(
                            task_frame_id, origin_anchor_ref,
                            origin_session_anchor_ref, origin_session_id,
                            origin_frame_id, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (task_frame_id, *expected, utc_now()),
                    )
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise ProjectMasterHostError("TASK_FRAME_SESSION_LINEAGE_PERSIST_FAILED") from error

    def _validate_task_frame_session_lineage(
        self, *, task_frame_id: str, binding: Mapping[str, str]
    ) -> None:
        lineage = self._task_frame_session_lineage(task_frame_id)
        if lineage is None:
            # Legacy Frames carry only origin_anchor_ref in Runtime storage.
            return
        if (
            lineage["origin_anchor_ref"] != str(binding["anchor_id"])
            or lineage["origin_frame_id"] != str(binding["frame_id"])
        ):
            raise ProjectMasterHostError("TASK_FRAME_SESSION_LINEAGE_MISMATCH")
        current = self._origin_session_anchor_ref(binding)
        recorded = lineage["origin_session_anchor_ref"]
        if recorded != "UNKNOWN" and current != recorded:
            raise ProjectMasterHostError("TASK_FRAME_SESSION_ANCHOR_MISMATCH")

    def _recover_task_frame_session_id(
        self,
        *,
        task_frame_id: str,
        anchor_id: str,
        frame_id: str,
    ) -> str | None:
        """Recover only one exact, unfinished Frame from Runtime-owned storage."""

        frames_root = self.project_root / ".ai" / "runtime" / "task_frames"
        if not frames_root.is_dir():
            return None
        matches: set[str] = set()
        for database_path in frames_root.glob("*.sqlite3"):
            try:
                connection = sqlite3.connect(
                    f"file:{database_path.as_posix()}?mode=ro",
                    uri=True,
                    timeout=1,
                )
                try:
                    row = connection.execute(
                        """
                        SELECT origin_session_id, origin_anchor_ref, origin_frame_id,
                               task_state
                        FROM task_frame_context WHERE singleton = 1
                        """
                    ).fetchone()
                finally:
                    connection.close()
            except sqlite3.Error:
                continue
            if row is None:
                continue
            session_id, stored_anchor_id, stored_frame_id, task_state = row
            if (
                str(stored_anchor_id) == anchor_id
                and str(stored_frame_id) == frame_id
                and str(task_state) not in {"COMPLETED", "CLOSED"}
            ):
                # Frame id is a durable field in the file itself. It is read
                # separately because only the requested frame may be revived.
                try:
                    connection = sqlite3.connect(
                        f"file:{database_path.as_posix()}?mode=ro",
                        uri=True,
                        timeout=1,
                    )
                    try:
                        identity = connection.execute(
                            "SELECT frame_id FROM task_frame_context WHERE singleton = 1"
                        ).fetchone()
                    finally:
                        connection.close()
                except sqlite3.Error:
                    continue
                if identity is not None and str(identity[0]) == task_frame_id:
                    matches.add(_text(session_id, "task_frame.origin_session_id"))
        return next(iter(matches)) if len(matches) == 1 else None

    def _reopen_task_frame_from_runtime_store(
        self,
        *,
        binding: Mapping[str, str],
        task_frame_id: str,
    ) -> None:
        """Register one exact persisted Frame with a replacement Host process."""

        self._validate_task_frame_session_lineage(
            task_frame_id=task_frame_id,
            binding=binding,
        )
        frames_root = self.project_root / ".ai" / "runtime" / "task_frames"
        if not frames_root.is_dir():
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_RECOVERY_NOT_FOUND")
        candidates: list[dict[str, Any]] = []
        for database_path in frames_root.glob("*.sqlite3"):
            try:
                connection = sqlite3.connect(
                    f"file:{database_path.as_posix()}?mode=ro",
                    uri=True,
                    timeout=1,
                )
                connection.row_factory = sqlite3.Row
                try:
                    context = connection.execute(
                        "SELECT * FROM task_frame_context WHERE singleton = 1"
                    ).fetchone()
                    observation = connection.execute(
                        """
                        SELECT status, evidence_ref, observed_at
                        FROM parent_observations
                        ORDER BY observation_ordinal DESC LIMIT 1
                        """
                    ).fetchone()
                    instruction = connection.execute(
                        """
                        SELECT * FROM task_instructions
                        ORDER BY instruction_ordinal ASC LIMIT 1
                        """
                    ).fetchone()
                finally:
                    connection.close()
            except sqlite3.Error:
                continue
            if context is None or observation is None or instruction is None:
                continue
            if (
                str(context["frame_id"]) != task_frame_id
                or str(context["origin_session_id"]) != binding["session_id"]
                or str(context["origin_anchor_ref"]) != binding["anchor_id"]
                or str(context["origin_frame_id"]) != binding["frame_id"]
                or str(context["task_state"]) in {"COMPLETED", "CLOSED"}
            ):
                continue
            try:
                constraints = json.loads(str(instruction["constraints_json"]))
                expected_output = json.loads(str(instruction["expected_output_json"]))
                mutation_scope = json.loads(str(instruction["mutation_scope_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                not isinstance(constraints, list)
                or not isinstance(expected_output, Mapping)
                or not isinstance(mutation_scope, Mapping)
            ):
                continue
            candidates.append(
                {
                    "profile": str(context["profile_path"]),
                    "frame": {
                        "frame_id": str(context["frame_id"]),
                        "origin_anchor_ref": str(context["origin_anchor_ref"]),
                        "origin_session_id": str(context["origin_session_id"]),
                        "origin_frame_id": str(context["origin_frame_id"]),
                        "task_summary_ref": str(context["task_summary_ref"]),
                        "source_ref": str(context["source_ref"]),
                        "execution_assignment_ref": str(
                            context["execution_assignment_ref"]
                        ),
                        "parent_instruction": {
                            "instruction_id": str(instruction["instruction_id"]),
                            "user_instruction_raw": str(
                                instruction["user_instruction_raw"]
                            ),
                            "constraints": constraints,
                            "expected_output": dict(expected_output),
                            "repository_write_scope": str(
                                instruction["repository_write_scope"]
                            ),
                            "mutation_scope": dict(mutation_scope),
                        },
                        "parent_observation": {
                            "status": str(observation["status"]),
                            "evidence_ref": str(observation["evidence_ref"]),
                        },
                        "observed_at": str(observation["observed_at"]),
                    },
                }
            )
        if len(candidates) != 1:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_RECOVERY_AMBIGUOUS")
        reopened = self._post_runtime(
            binding["endpoint"],
            binding["token"],
            "/v1/task-frame/create",
            {
                "session_id": binding["session_id"],
                "profile": candidates[0]["profile"],
                "frame": candidates[0]["frame"],
            },
        )
        if reopened.get("status") not in {
            "TASK_FRAME_HOST_ACTIVE",
            "TASK_FRAME_ALREADY_ACTIVE",
        }:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_RECOVERY_FAILED")

    def _recover_stale_boss_claim(
        self,
        *,
        binding: Mapping[str, str],
        task_frame_id: str,
        boss_request: Mapping[str, Any],
    ) -> None:
        """Release a captured Boss claim left behind by a prior Host process."""

        frames_root = self.project_root / ".ai" / "runtime" / "task_frames"
        stale_claims: list[tuple[str, str]] = []
        if not frames_root.is_dir():
            return
        for database_path in frames_root.glob("*.sqlite3"):
            try:
                connection = sqlite3.connect(
                    f"file:{database_path.as_posix()}?mode=ro",
                    uri=True,
                    timeout=1,
                )
                connection.row_factory = sqlite3.Row
                try:
                    context = connection.execute(
                        "SELECT frame_id, origin_session_id, origin_anchor_ref, origin_frame_id FROM task_frame_context WHERE singleton = 1"
                    ).fetchone()
                    if (
                        context is None
                        or str(context["frame_id"]) != task_frame_id
                        or str(context["origin_session_id"]) != binding["session_id"]
                        or str(context["origin_anchor_ref"]) != binding["anchor_id"]
                        or str(context["origin_frame_id"]) != binding["frame_id"]
                    ):
                        continue
                    allocation_count = connection.execute(
                        "SELECT COUNT(*) FROM boss_allocations"
                    ).fetchone()[0]
                    boss = connection.execute(
                        """
                        SELECT turn_id, state, claimed_by
                        FROM task_turns
                        WHERE role = 'BOSS' AND input_turn_ids_json = '[]'
                        """
                    ).fetchone()
                    if (
                        boss is None
                        or str(boss["state"]) != "CLAIMED"
                        or allocation_count != 0
                    ):
                        continue
                    execution = connection.execute(
                        """
                        SELECT worker_actor_ref, worker_run_ref, worker_result_envelope_json
                        FROM worker_execution_state WHERE turn_id = ?
                        """,
                        (str(boss["turn_id"]),),
                    ).fetchone()
                finally:
                    connection.close()
            except sqlite3.Error:
                continue
            if (
                execution is not None
                and not str(execution["worker_result_envelope_json"] or "").strip()
                and str(execution["worker_actor_ref"] or "").strip()
                == str(boss["claimed_by"] or "").strip()
                and str(execution["worker_run_ref"] or "").strip()
            ):
                stale_claims.append(
                    (
                        str(execution["worker_actor_ref"]),
                        str(execution["worker_run_ref"]),
                    )
                )
        if not stale_claims:
            return
        if len(stale_claims) != 1:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_STALE_CLAIM_AMBIGUOUS")
        worker_id, worker_run_ref = stale_claims[0]
        try:
            self.worker_dispatcher.recover_claimed_worker(
                boss_request,
                worker_id=worker_id,
                worker_run_ref=worker_run_ref,
                failure_code="WORKER_CAPTURE_FINALIZATION_UNRECORDED",
                failure_reason="replacement Host found a claimed Boss without allocation or result evidence",
            )
        except WorkerDispatchError as error:
            raise ProjectMasterHostError(error.code) from error

    def _register_process_lease(
        self,
        *,
        process: subprocess.Popen[str],
        command: list[str],
        endpoint: str,
        token: str,
        runtime_session_id: str,
        anchor_id: str,
    ) -> None:
        if self.session_supervisor is None:
            return
        normalized_runtime_session_id = _text(
            runtime_session_id, "runtime_session_id"
        )
        normalized_anchor_id = _text(anchor_id, "anchor_id")
        self.session_supervisor.sweep_stale_live_sessions()
        session_material = json.dumps(
            {
                "node": self.session_node,
                "mode": self.requested_mode,
                "provider": "RUNTIME",
                "runtime_session_id": normalized_runtime_session_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        requested_supervisor_session_id = (
            "session_"
            + hashlib.sha256(session_material.encode("utf-8")).hexdigest()[:24]
        )
        session, _created = self.session_supervisor.register_session(
            {
                "session_id": requested_supervisor_session_id,
                "node": self.session_node,
                "project_id": self.project_id,
                "mode": self.requested_mode,
                "provider": "RUNTIME",
                "provider_session_ref": normalized_runtime_session_id,
                "anchor_ref": normalized_anchor_id,
                "state": "REGISTERED",
                "currentness": "CURRENT",
                "activity_state": "BOOTSTRAPPING",
                "location_evidence_ref": (
                    "project-master://"
                    + self.project_id
                    + "/task-frame-runtime/"
                    + normalized_runtime_session_id
                ),
                "alias": (
                    f"{self.project_id} {self.requested_mode} "
                    "Task Frame Runtime"
                ),
                "bounded_summary": (
                    "Project Master persistent Task Frame runtime host"
                ),
            }
        )
        supervisor_session_id = str(session["session_id"])
        identity = launched_process_identity(
            process,
            executable=Path(command[0]),
            command=command,
            endpoint=endpoint,
            handshake_token=token,
        )
        existing = session.get("process_lease")
        if (
            isinstance(existing, Mapping)
            and existing.get("lease_state") == "STOP_AUTHORIZED"
        ):
            try:
                recovered = self.session_supervisor.complete_managed_stop(
                    supervisor_session_id,
                    expected_lease_version=int(existing.get("lease_version", 0)),
                )
            except SessionSupervisorError as error:
                raise ProjectMasterHostError(error.code) from error
            recovered_lease = recovered.get("process_lease")
            if (
                not isinstance(recovered_lease, Mapping)
                or recovered_lease.get("lease_state") != "RELEASED"
            ):
                raise ProjectMasterHostError(
                    "RUNTIME_EXECUTOR_STOP_RECOVERY_RESULT_INVALID"
                )
            existing = recovered_lease
        expected_version = (
            0 if existing is None else int(existing.get("lease_version", 0))
        )
        acquired = self.session_supervisor.acquire_lease(
            supervisor_session_id,
            identity,
            expected_lease_version=expected_version,
            stop_capability=token,
        )
        self._supervisor_session_id = supervisor_session_id
        self._lease_token = str(acquired["lease_token"])
        self._lease_version = int(acquired["lease"]["lease_version"])
        self._process_identity = identity

    def _authorize_supervised_stop(self) -> Mapping[str, Any] | None:
        if (
            self.session_supervisor is None
            or self._supervisor_session_id is None
            or self._lease_token is None
            or self._lease_version is None
            or self._process_identity is None
        ):
            return None
        try:
            receipt = self.session_supervisor.authorize_stop(
                self._supervisor_session_id,
                self._process_identity,
                lease_token=self._lease_token,
                expected_lease_version=self._lease_version,
            )
        except SessionSupervisorError:
            current = self.session_supervisor.get_session(
                self._supervisor_session_id
            ).get("process_lease")
            if isinstance(current, Mapping):
                self._lease_version = int(current["lease_version"])
            raise
        self._lease_version = int(receipt["lease_version"])
        return receipt

    def _complete_supervised_stop(self, receipt: Mapping[str, Any] | None) -> None:
        if (
            receipt is None
            or self.session_supervisor is None
            or self._supervisor_session_id is None
            or self._lease_token is None
            or self._lease_version is None
        ):
            return
        self.session_supervisor.complete_stop(
            self._supervisor_session_id,
            lease_token=self._lease_token,
            expected_lease_version=self._lease_version,
        )
        self._supervisor_session_id = None
        self._lease_token = None
        self._lease_version = None
        self._process_identity = None

    def _mark_stale_if_owned(self, reason: str) -> None:
        if (
            self.session_supervisor is None
            or self._supervisor_session_id is None
            or self._lease_token is None
            or self._lease_version is None
            or self._process_identity is None
        ):
            return
        self.session_supervisor.mark_lease_stale(
            self._supervisor_session_id,
            self._process_identity,
            lease_token=self._lease_token,
            expected_lease_version=self._lease_version,
            reason=reason,
        )
        self._supervisor_session_id = None
        self._lease_token = None
        self._lease_version = None
        self._process_identity = None

    def _read_runtime_startup(
        self, process: subprocess.Popen[str]
    ) -> Mapping[str, Any]:
        if process.stdout is None:
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_STDOUT_UNAVAILABLE")
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                output.put(process.stdout.readline())
            except (OSError, UnicodeError):
                output.put("")

        threading.Thread(target=read_line, daemon=True).start()
        if process.stderr is not None:
            threading.Thread(
                target=self._drain_runtime_stderr,
                args=(process.stderr,),
                daemon=True,
            ).start()
        try:
            raw = output.get(timeout=30)
        except queue.Empty as error:
            raise ProjectMasterHostError(
                "PROJECT_MASTER_RUNTIME_START_TIMEOUT"
            ) from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError(
                "PROJECT_MASTER_RUNTIME_START_RESULT_INVALID"
            ) from error
        if not isinstance(payload, Mapping):
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_START_RESULT_INVALID")
        return payload

    def _drain_runtime_stderr(self, stream: Any) -> None:
        try:
            for line in stream:
                self._runtime_stderr.append(line.rstrip())
        except (OSError, UnicodeError):
            return

    def _mode_definition(self) -> Mapping[str, str]:
        registry_path = (
            self.project_root
            / ".ai"
            / "runtime"
            / "project_instance"
            / "mode_registry.json"
        )
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            definition = registry["modes"][self.requested_mode]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ProjectMasterHostError("PROJECT_MASTER_MODE_UNAVAILABLE") from error
        if not isinstance(definition, Mapping):
            raise ProjectMasterHostError("PROJECT_MASTER_MODE_UNAVAILABLE")
        role = _text(definition.get("role"), f"{self.requested_mode}.role").upper()
        return {
            "role": role,
            "scope": _text(definition.get("scope"), f"{self.requested_mode}.scope"),
            "mode_profile": _text(
                definition.get("mode_profile"), f"{self.requested_mode}.mode_profile"
            ),
        }

    # Compatibility for callers that still use the old private helper name.
    def _master_definition(self) -> Mapping[str, str]:
        return self._mode_definition()

    def _invoke(
        self,
        arguments: tuple[str, ...],
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        request_path = _runtime_tmp() / f"project-runtime-{uuid4().hex}.json"
        request_path.write_text(
            json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=_required_host_executable("python"),
                    arguments=(
                        str(self.runtime_cli),
                        *arguments,
                        "--request",
                        str(request_path),
                    ),
                    cwd=self.project_root,
                    timeout_seconds=30,
                )
            )
        finally:
            request_path.unlink(missing_ok=True)
        if result.status != "COMPLETED" or result.return_code != 0:
            runtime_code = "UNKNOWN"
            try:
                failure = json.loads(result.stdout)
            except json.JSONDecodeError:
                failure = None
            if isinstance(failure, Mapping):
                candidate = failure.get("error_code") or failure.get("status")
                if isinstance(candidate, str) and candidate.strip():
                    runtime_code = candidate.strip().upper()
            raise ProjectMasterHostError(
                "PROJECT_RUNTIME_COMMAND_FAILED:" + runtime_code
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError("PROJECT_RUNTIME_RESULT_INVALID") from error
        if not isinstance(payload, Mapping):
            raise ProjectMasterHostError("PROJECT_RUNTIME_RESULT_INVALID")
        return payload

class ProjectTaskProposalAdapter:
    """Read and decide the installed Runtime's durable Task Proposals."""

    def __init__(self, *, native_runner: NativeRunner = run_native_cli) -> None:
        self.native_runner = native_runner

    def list(
        self,
        project_root: Path,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        root = project_root.expanduser().resolve(strict=True)
        normalized_project_id = _text(project_id, "project_id")
        database_path = root / TASK_PROPOSAL_DATABASE_RELATIVE_PATH
        if not database_path.is_file():
            return []
        bounded_limit = max(1, min(int(limit), 500))
        try:
            connection = sqlite3.connect(
                f"{database_path.as_uri()}?mode=ro",
                uri=True,
                timeout=2,
            )
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT proposal_id, proposal_digest, proposal_json, state,
                           created_at, approved_at, approval_json, completed_at
                    FROM proposal
                    ORDER BY created_at DESC, proposal_id DESC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise ProjectMasterHostError(
                "PROJECT_TASK_PROPOSAL_JOURNAL_INVALID"
            ) from error
        return [
            self._proposal_row(normalized_project_id, row)
            for row in rows
        ]

    def approve(
        self,
        project_root: Path,
        *,
        proposal_id: str,
        proposal_digest: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        root = project_root.expanduser().resolve(strict=True)
        runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        if not runtime_cli.is_file():
            raise ProjectMasterHostError("PROJECT_RUNTIME_CLI_UNAVAILABLE")
        request_path = _runtime_tmp() / f"project-task-approval-{uuid4().hex}.json"
        request_path.write_text(
            json.dumps(
                {
                    "proposal_id": _text(proposal_id, "proposal_id"),
                    "proposal_digest": _text(
                        proposal_digest, "proposal_digest"
                    ),
                    "evidence_ref": _text(evidence_ref, "evidence_ref"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=_required_host_executable("python"),
                    arguments=(
                        str(runtime_cli),
                        "task-proposal",
                        "approve",
                        "--repo-root",
                        str(root),
                        "--request",
                        str(request_path),
                    ),
                    cwd=root,
                    timeout_seconds=30,
                )
            )
        finally:
            request_path.unlink(missing_ok=True)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError(
                "PROJECT_TASK_PROPOSAL_APPROVAL_RESULT_INVALID"
            ) from error
        if (
            result.status != "COMPLETED"
            or result.return_code != 0
            or not isinstance(payload, Mapping)
            or payload.get("status") != "TASK_PROPOSAL_APPROVED"
        ):
            error_code = (
                str(payload.get("error_code") or "").strip()
                if isinstance(payload, Mapping)
                else ""
            )
            raise ProjectMasterHostError(
                error_code or "PROJECT_TASK_PROPOSAL_APPROVAL_FAILED"
            )
        return dict(payload)

    def cancel(
        self,
        project_root: Path,
        *,
        proposal_id: str,
        proposal_digest: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        root = project_root.expanduser().resolve(strict=True)
        runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        if not runtime_cli.is_file():
            raise ProjectMasterHostError("PROJECT_RUNTIME_CLI_UNAVAILABLE")
        request_path = _runtime_tmp() / f"project-task-cancellation-{uuid4().hex}.json"
        request_path.write_text(
            json.dumps(
                {
                    "proposal_id": _text(proposal_id, "proposal_id"),
                    "proposal_digest": _text(
                        proposal_digest, "proposal_digest"
                    ),
                    "evidence_ref": _text(evidence_ref, "evidence_ref"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=_required_host_executable("python"),
                    arguments=(
                        str(runtime_cli),
                        "task-proposal",
                        "cancel",
                        "--repo-root",
                        str(root),
                        "--request",
                        str(request_path),
                    ),
                    cwd=root,
                    timeout_seconds=30,
                )
            )
        finally:
            request_path.unlink(missing_ok=True)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError(
                "PROJECT_TASK_PROPOSAL_CANCELLATION_RESULT_INVALID"
            ) from error
        if (
            result.status != "COMPLETED"
            or result.return_code != 0
            or not isinstance(payload, Mapping)
            or payload.get("status") != "TASK_PROPOSAL_CANCELLED"
        ):
            error_code = (
                str(payload.get("error_code") or "").strip()
                if isinstance(payload, Mapping)
                else ""
            )
            raise ProjectMasterHostError(
                error_code or "PROJECT_TASK_PROPOSAL_CANCELLATION_FAILED"
            )
        return dict(payload)

    @staticmethod
    def _proposal_row(project_id: str, row: sqlite3.Row) -> dict[str, Any]:
        try:
            proposal = json.loads(str(row["proposal_json"]))
            approval = (
                None
                if row["approval_json"] is None
                else json.loads(str(row["approval_json"]))
            )
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError(
                "PROJECT_TASK_PROPOSAL_JOURNAL_INVALID"
            ) from error
        if not isinstance(proposal, Mapping) or (
            approval is not None and not isinstance(approval, Mapping)
        ):
            raise ProjectMasterHostError(
                "PROJECT_TASK_PROPOSAL_JOURNAL_INVALID"
            )
        state = str(row["state"])
        return {
            "schema": "universe.governance-proposal.v1",
            "proposal_kind": "TASK_PROPOSAL",
            "project_id": project_id,
            "proposal_id": str(row["proposal_id"]),
            "proposal_digest": str(row["proposal_digest"]),
            "state": state,
            "approval_required": state == "PROPOSED",
            "platform_permission": False,
            "task_summary": str(proposal.get("task_summary") or ""),
            "boundary": str(proposal.get("boundary") or ""),
            "scope": (
                dict(proposal["scope"])
                if isinstance(proposal.get("scope"), Mapping)
                else {}
            ),
            "request_ref": str(proposal.get("request_ref") or "UNKNOWN"),
            "source_ref": str(proposal.get("source_ref") or "UNKNOWN"),
            "created_at": str(row["created_at"]),
            "approved_at": (
                None if row["approved_at"] is None else str(row["approved_at"])
            ),
            "completed_at": (
                None if row["completed_at"] is None else str(row["completed_at"])
            ),
            "approval": None if approval is None else dict(approval),
        }


class ProjectMasterSessionStore:
    def __init__(
        self,
        database_path: Path,
        project_id: str,
        *,
        session_node: str | None = None,
        session_supervisor: SessionSupervisorStore | None = None,
        requested_mode: str = "MASTER",
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.project_id = _text(project_id, "project_id")
        self.session_node = _text(session_node or self.project_id, "session_node")
        self.session_supervisor = session_supervisor
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()
        self._migrate_legacy_provider_sessions()
        self._migrate_supervisor_binding()

    def last_provider_session(self) -> dict[str, str] | None:
        if self.session_supervisor is not None:
            sessions = self.session_supervisor.list_sessions(
                node=self.session_node,
                mode=self.requested_mode,
            )
            selected = next(
                (session for session in sessions if session["is_default"]), None
            )
            if selected is not None and selected["provider_session_ref"]:
                return {
                    "provider": str(selected["provider"]),
                    "session_ref": str(selected["provider_session_ref"]),
                }
        return self._legacy_provider_session()

    def _legacy_provider_session(self) -> dict[str, str] | None:
        with self._connection() as connection:
            rows = {
                str(row["key"]): str(row["value"])
                for row in connection.execute(
                    """
                    SELECT key, value
                    FROM host_metadata
                    WHERE key IN ('last_provider', 'last_session_ref')
                    """
                ).fetchall()
            }
        provider = rows.get("last_provider")
        session_ref = rows.get("last_session_ref")
        if not provider or not session_ref:
            return None
        return {
            "provider": _provider(provider),
            "session_ref": _text(session_ref, "last_session_ref"),
        }

    def session_ref_for(self, provider: str) -> str | None:
        normalized_provider = _provider(provider)
        coordinate = self.last_provider_session()
        if coordinate is None or coordinate["provider"] != normalized_provider:
            return None
        return coordinate["session_ref"]

    def ensure_supervisor_session(
        self,
        provider: str,
        *,
        new_session: bool = False,
    ) -> dict[str, Any] | None:
        """Create the persistent Mode-session slot before its processes start."""
        if self.session_supervisor is None:
            return None
        normalized_provider = _provider(provider)
        if new_session:
            supervisor_session_id = "session_" + secrets.token_hex(12)
            session, _ = self.session_supervisor.register_session(
                {
                    "session_id": supervisor_session_id,
                    "node": self.session_node,
                    "mode": self.requested_mode,
                    "provider": normalized_provider,
                    "provider_session_ref": None,
                    "state": "REGISTERED",
                    "currentness": "UNKNOWN",
                    "activity_state": "BOOTSTRAPPING",
                    "location_evidence_ref": (
                        "universe://mode-session-new/"
                        f"{self.session_node}/{self.requested_mode}"
                    ),
                }
            )
            self.session_supervisor.set_default(
                supervisor_session_id,
                expected_pointer_version=session["default_pointer_version"],
            )
            return self.session_supervisor.get_session(supervisor_session_id)
        sessions = self.session_supervisor.list_sessions(
            node=self.session_node,
            mode=self.requested_mode,
        )
        selected = next(
            (session for session in sessions if session["is_default"]),
            None,
        )
        if selected is None and sessions:
            # A legacy pointer can retain the old node spelling after the
            # session itself has been moved. Prefer the observed current row
            # and repair the pointer before the provider process is leased.
            selected = next(
                (session for session in sessions if session["currentness"] == "CURRENT"),
                sessions[0],
            )
            self.session_supervisor.set_default(
                selected["session_id"],
                expected_pointer_version=selected["default_pointer_version"],
            )
            selected = next(
                item
                for item in self.session_supervisor.list_sessions(
                    node=self.session_node,
                    mode=self.requested_mode,
                )
                if item["session_id"] == selected["session_id"]
            )
        if selected is not None:
            return selected
        supervisor_session_id = self._supervisor_session_id(normalized_provider, "")
        session, _ = self.session_supervisor.register_session(
            {
                "session_id": supervisor_session_id,
                "node": self.session_node,
                "mode": self.requested_mode,
                "provider": normalized_provider,
                "provider_session_ref": None,
                "state": "REGISTERED",
                "currentness": "UNKNOWN",
                "activity_state": "BOOTSTRAPPING",
                "location_evidence_ref": (
                    "universe://mode-session-bootstrap/"
                    f"{self.session_node}/{self.requested_mode}"
                ),
            }
        )
        if not session["is_default"]:
            self.session_supervisor.set_default(
                supervisor_session_id,
                expected_pointer_version=session["default_pointer_version"],
            )
        return next(
            item
            for item in self.session_supervisor.list_sessions(
                node=self.session_node,
                mode=self.requested_mode,
            )
            if item["is_default"]
        )

    def observe_provider_session(self, provider: str, session_ref: str) -> str:
        normalized_provider = _provider(provider)
        normalized_session = _text(session_ref, "session_ref")
        previous = self.last_provider_session()
        if previous is None:
            state = "NEW"
        elif previous == {
            "provider": normalized_provider,
            "session_ref": normalized_session,
        }:
            state = "REUSED"
        else:
            state = "REPLACED"
        if self.session_supervisor is not None:
            sessions = self.session_supervisor.list_sessions(
                node=self.session_node,
                mode=self.requested_mode,
            )
            selected = next(
                (session for session in sessions if session["is_default"]),
                None,
            )
            if selected is not None:
                supervisor_session_id = str(selected["session_id"])
                if (
                    selected.get("provider") == normalized_provider
                    and selected.get("provider_session_ref") == normalized_session
                ):
                    candidate = selected
                else:
                    candidate = self.session_supervisor.bind_provider_session(
                        supervisor_session_id,
                        provider=normalized_provider,
                        provider_session_ref=normalized_session,
                        expected_version=selected["row_version"],
                    )
            else:
                supervisor_session_id = self._supervisor_session_id(
                    normalized_provider, normalized_session
                )
                candidate, _ = self.session_supervisor.register_session(
                    {
                        "session_id": supervisor_session_id,
                        "node": self.session_node,
                        "mode": self.requested_mode,
                        "provider": normalized_provider,
                        "provider_session_ref": normalized_session,
                        "state": "LIVE",
                        "currentness": "UNKNOWN",
                    }
                )
            supervisor_session_id = str(candidate["session_id"])
            if not candidate["is_default"]:
                self.session_supervisor.set_default(
                    supervisor_session_id,
                    expected_pointer_version=candidate["default_pointer_version"],
                )
            self.session_supervisor.observe_session_activity(
                supervisor_session_id,
                event_type="PROVIDER_SESSION_ATTACHED",
                activity_state="ATTACHED",
                evidence_ref=(
                    "universe://session-observer/"
                    f"{self.session_node}/{self.requested_mode}/provider-attach"
                ),
            )
        with self._connection() as connection:
            for key, value in (
                ("last_provider", normalized_provider),
                ("last_session_ref", normalized_session),
            ):
                connection.execute(
                    """
                    INSERT INTO host_metadata(key, value)
                    VALUES(?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
        return state

    def observe_session_activity(
        self,
        provider: str,
        session_ref: str,
        *,
        event_type: str,
        activity_state: str,
        evidence_ref: str,
    ) -> dict[str, Any] | None:
        if self.session_supervisor is None:
            return None
        normalized_provider = _provider(provider)
        normalized_session = _provider_session_identity(
            normalized_provider,
            _text(session_ref, "session_ref"),
        )
        candidate = self._find_supervisor_session(
            normalized_provider,
            normalized_session,
        )
        if candidate is None:
            self.observe_provider_session(normalized_provider, normalized_session)
            candidate = self._find_supervisor_session(
                normalized_provider,
                normalized_session,
            )
        if candidate is None:
            raise ProjectMasterHostError("MODE_SESSION_SUPERVISOR_SESSION_UNAVAILABLE")
        supervisor_session_id = str(candidate["session_id"])
        if not candidate["is_default"]:
            self.session_supervisor.set_default(
                supervisor_session_id,
                expected_pointer_version=candidate["default_pointer_version"],
            )
        return self.session_supervisor.observe_session_activity(
            supervisor_session_id,
            event_type=event_type,
            activity_state=activity_state,
            evidence_ref=evidence_ref,
        )

    def _find_supervisor_session(
        self,
        provider: str,
        session_ref: str,
    ) -> dict[str, Any] | None:
        if self.session_supervisor is None:
            return None
        normalized_provider = _provider(provider)
        return next(
            (
                session
                for session in self.session_supervisor.list_sessions(
                    node=self.session_node,
                    mode=self.requested_mode,
                )
                if str(session.get("provider") or "").upper() == normalized_provider
                and _same_provider_session_ref(
                    normalized_provider,
                    session.get("provider_session_ref"),
                    session_ref,
                )
            ),
            None,
        )

    def observe_current_anchor(self, anchor_ref: str) -> dict[str, Any] | None:
        if self.session_supervisor is None:
            return None
        selected = next(
            (
                session
                for session in self.session_supervisor.list_sessions(
                    node=self.session_node,
                    mode=self.requested_mode,
                )
                if session["is_default"]
            ),
            None,
        )
        if selected is None:
            raise ProjectMasterHostError("SUPERVISOR_PROJECT_SESSION_UNAVAILABLE")
        if selected.get("anchor_ref") == anchor_ref:
            return selected
        return self.session_supervisor.bind_current_anchor(
            selected["session_id"],
            anchor_ref=anchor_ref,
            expected_version=selected["row_version"],
        )

    def _migrate_legacy_provider_sessions(self) -> None:
        current = self._legacy_provider_session()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT key, value
                FROM host_metadata
                WHERE key = 'provider_session_id'
                   OR key LIKE 'provider_session_id:%'
                ORDER BY key
                """
            ).fetchall()
            candidates: dict[str, str] = {}
            for row in rows:
                key = str(row["key"])
                provider = key.partition(":")[2] or "GROK"
                candidates[_provider(provider)] = str(row["value"])
            if current is None and len(candidates) == 1:
                provider, session_ref = next(iter(candidates.items()))
                for key, value in (
                    ("last_provider", provider),
                    ("last_session_ref", session_ref),
                ):
                    connection.execute(
                        "INSERT OR REPLACE INTO host_metadata(key, value) VALUES(?, ?)",
                        (key, value),
                    )

    def _migrate_supervisor_binding(self) -> None:
        if self.session_supervisor is None:
            return
        if any(
            session["is_default"]
            for session in self.session_supervisor.list_sessions(
                node=self.session_node,
                mode=self.requested_mode,
            )
        ):
            return
        legacy = self._legacy_provider_session()
        if legacy is None:
            return
        supervisor_session_id = self._supervisor_session_id(
            legacy["provider"], legacy["session_ref"]
        )
        session, _ = self.session_supervisor.register_session(
            {
                "session_id": supervisor_session_id,
                "node": self.session_node,
                "mode": self.requested_mode,
                "provider": legacy["provider"],
                "provider_session_ref": legacy["session_ref"],
                "state": "DISCONNECTED",
                "currentness": "UNKNOWN",
            }
        )
        supervisor_session_id = str(session["session_id"])
        if not session["is_default"]:
            self.session_supervisor.set_default(
                supervisor_session_id,
                expected_pointer_version=session["default_pointer_version"],
            )

    def _supervisor_session_id(self, provider: str, session_ref: str) -> str:
        del provider, session_ref
        material = json.dumps(
            {
                "node": self.session_node,
                "mode": self.requested_mode,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "session_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def register(self, envelope: Mapping[str, Any]) -> bool:
        normalized = normalize_bridge_envelope(envelope)
        message_id = normalized["message"]["message_id"]
        envelope_json = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM inbox_message WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row is not None and row["state"] in {
                "PENDING",
                "PROCESSING",
                "COMPLETE",
            }:
                return False
            connection.execute(
                """
                INSERT INTO inbox_message(
                    message_id, envelope_json, state, attempts, last_error, updated_at
                ) VALUES(?, ?, 'PENDING', 0, '', ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    envelope_json = excluded.envelope_json,
                    state = 'PENDING',
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (message_id, envelope_json, utc_now()),
            )
        return True

    def recover(
        self,
        *,
        bridge_id: str | None = None,
        master_session_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        if (bridge_id is None) != (master_session_ref is None):
            raise ProjectMasterHostError(
                "MASTER_RECOVERY_TRANSPORT_INCOMPLETE"
            )
        normalized_bridge_id = (
            None if bridge_id is None else _text(bridge_id, "bridge_id")
        )
        normalized_session_ref = (
            None
            if master_session_ref is None
            else _text(master_session_ref, "master_session_ref")
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE inbox_message
                SET state = 'PENDING', updated_at = ?
                WHERE state = 'PROCESSING'
                   OR (
                        state = 'FAILED'
                    AND last_error = 'ProjectMasterBridgeError: UNIVERSE_REPLY_HTTP_409'
                   )
                """,
                (utc_now(),),
            )
            rows = connection.execute(
                """
                SELECT message_id, envelope_json
                FROM inbox_message
                WHERE state = 'PENDING'
                ORDER BY updated_at, message_id
                """
            ).fetchall()
            recovered = []
            for row in rows:
                envelope = json.loads(str(row["envelope_json"]))
                if (
                    normalized_bridge_id is not None
                    and normalized_session_ref is not None
                ):
                    envelope["bridge_id"] = normalized_bridge_id
                    envelope["master_session_ref"] = normalized_session_ref
                    envelope = normalize_bridge_envelope(envelope)
                    connection.execute(
                        """
                        UPDATE inbox_message
                        SET envelope_json = ?, updated_at = ?
                        WHERE message_id = ?
                        """,
                        (
                            json.dumps(
                                envelope,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            utc_now(),
                            str(row["message_id"]),
                        ),
                    )
                recovered.append(envelope)
        return recovered

    def claim(self, message_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE inbox_message
                SET state = 'PROCESSING', attempts = attempts + 1,
                    last_error = '', updated_at = ?
                WHERE message_id = ? AND state IN ('PENDING', 'FAILED')
                """,
                (utc_now(), message_id),
            )
        return cursor.rowcount == 1

    def complete(self, message_id: str) -> None:
        self._transition(message_id, "COMPLETE", "")

    def fail(self, message_id: str, error: str) -> None:
        self._transition(message_id, "FAILED", error[:1000])

    def cancel(self, message_id: str) -> bool:
        """Cancel only work that has not been claimed by the provider."""
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE inbox_message
                SET state = 'CANCELLED', last_error = '', updated_at = ?
                WHERE message_id = ? AND state IN ('PENDING', 'FAILED')
                """,
                (utc_now(), message_id),
            )
        return cursor.rowcount == 1

    def requeue_provider_start_timeouts(self) -> int:
        """Requeue only a prior provider startup timeout after an explicit switch."""
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE inbox_message
                SET state = 'PENDING', last_error = '', updated_at = ?
                WHERE state = 'FAILED'
                  AND last_error = 'ProjectMasterHostError: AGENT_RPC_TIMEOUT:session/prompt'
                """,
                (utc_now(),),
            )
        return int(cursor.rowcount)

    def state(self, message_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM inbox_message WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return str(row["state"]) if row is not None else "UNKNOWN"

    def apply_skill_plan_context(
        self,
        context: Mapping[str, Any],
        binding_proposal: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        handoff_id = _text(context.get("handoff_id"), "handoff_id")
        context_digest = _text(context.get("context_digest"), "context_digest")
        proposal_digest = _text(
            binding_proposal.get("proposal_digest"),
            "proposal_digest",
        )
        encoded = json.dumps(
            dict(context),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT context_json, context_digest, applied_at
                FROM skill_plan_context
                WHERE handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if existing is not None:
                if existing["context_digest"] != context_digest:
                    raise ProjectMasterHostError("PROJECT_SKILL_PLAN_CONTEXT_CONFLICT")
            proposal_row = connection.execute(
                """
                SELECT proposal_json, proposal_digest, resolved_at
                FROM skill_binding_proposal
                WHERE handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if (
                proposal_row is not None
                and proposal_row["proposal_digest"] != proposal_digest
            ):
                raise ProjectMasterHostError("PROJECT_SKILL_BINDING_PROPOSAL_CONFLICT")
            applied_at = (
                str(existing["applied_at"]) if existing is not None else utc_now()
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO skill_plan_context(
                        handoff_id, adoption_id, context_digest,
                        context_json, applied_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        handoff_id,
                        _text(context.get("adoption_id"), "adoption_id"),
                        context_digest,
                        encoded,
                        applied_at,
                    ),
                )
            if proposal_row is None:
                connection.execute(
                    """
                    INSERT INTO skill_binding_proposal(
                        handoff_id, proposal_id, proposal_digest,
                        proposal_json, resolved_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        handoff_id,
                        _text(binding_proposal.get("proposal_id"), "proposal_id"),
                        proposal_digest,
                        json.dumps(
                            dict(binding_proposal),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        applied_at,
                    ),
                )
            stored_context = (
                json.loads(str(existing["context_json"]))
                if existing is not None
                else dict(context)
            )
            stored_proposal = (
                json.loads(str(proposal_row["proposal_json"]))
                if proposal_row is not None
                else dict(binding_proposal)
            )
            resolved_at = (
                str(proposal_row["resolved_at"])
                if proposal_row is not None
                else applied_at
            )
        stored_context["applied_at"] = applied_at
        stored_proposal["resolved_at"] = resolved_at
        return (
            stored_context,
            stored_proposal,
            existing is None or proposal_row is None,
        )

    def skill_plan_contexts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT context_json, applied_at
                FROM skill_plan_context
                ORDER BY applied_at DESC, handoff_id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        contexts = []
        for row in reversed(rows):
            context = json.loads(str(row["context_json"]))
            context["applied_at"] = row["applied_at"]
            contexts.append(context)
        return contexts

    def skill_binding_proposals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT proposal_json, resolved_at
                FROM skill_binding_proposal
                ORDER BY resolved_at DESC, handoff_id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        proposals = []
        for row in reversed(rows):
            proposal = json.loads(str(row["proposal_json"]))
            proposal["resolved_at"] = row["resolved_at"]
            proposals.append(proposal)
        return proposals

    def _transition(self, message_id: str, state: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE inbox_message
                SET state = ?, last_error = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (state, error, utc_now(), message_id),
            )

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS host_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inbox_message (
                    message_id TEXT PRIMARY KEY,
                    envelope_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skill_plan_context (
                    handoff_id TEXT PRIMARY KEY,
                    adoption_id TEXT NOT NULL UNIQUE,
                    context_digest TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skill_binding_proposal (
                    handoff_id TEXT PRIMARY KEY
                        REFERENCES skill_plan_context(handoff_id)
                        ON DELETE CASCADE,
                    proposal_id TEXT NOT NULL UNIQUE,
                    proposal_digest TEXT NOT NULL UNIQUE,
                    proposal_json TEXT NOT NULL,
                    resolved_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def provider_session_connection(
    *,
    target_kind: str,
    target_id: str,
    requested_mode: str,
    store: ProjectMasterSessionStore | None,
    resident: bool,
    connection_state: str | None = None,
    model_ref: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    coordinate = store.last_provider_session() if store is not None else None
    state = str(connection_state or "").strip().upper()
    if not state:
        state = "STORED" if coordinate is not None else "NOT_OPENED"
    return {
        "schema": PROVIDER_SESSION_CONNECTION_SCHEMA,
        "target_kind": _text(target_kind, "target_kind").upper(),
        "target_id": _text(target_id, "target_id"),
        "requested_mode": _text(requested_mode, "requested_mode").upper(),
        "last_provider": (
            coordinate["provider"] if coordinate is not None else "UNKNOWN"
        ),
        "last_session_ref": (
            coordinate["session_ref"] if coordinate is not None else "UNKNOWN"
        ),
        "model_ref": _text(model_ref, "model_ref") if model_ref else "UNKNOWN",
        "effort": str(effort or "AUTO").strip().upper() or "AUTO",
        "connection_state": state,
        "session_persistence": "LAST_COORDINATE",
        "resident": bool(resident),
    }


def _project_master_system_prompt(actor_label: str) -> str:
    return (
        f"You are the {actor_label}. "
        "This is a persistent, command-capable conversation Host connected to the "
        "Universe interface. Work from the repository at the configured cwd and "
        "follow its entry order and installed Runtime contracts. Mode, Role, BOOT, "
        "or a chat message never creates mutation authority. A GOVERNANCE_ONLY Mode "
        "controls the Mode-entry default; it does not veto a later explicitly approved "
        "executable task. Handle read-only inspection, review, explanation, and audit "
        "directly. For implementation or command requests that require Commander "
        "approval, create or reuse the installed durable Task Proposal first and stop "
        "after reporting its proposal_id, proposal_digest, scope, and boundary. Do not "
        "create an Execution Assignment or Binding until a structured Universe Task "
        "Proposal approval packet for that exact id and digest arrives. A plain platform "
        "tool permission response is never that approval. After exact approval, use the "
        "installed Task Assignment and Execution Binding route; the approved Task "
        "Proposal evidence covers internal steps that remain within its unchanged scope. "
        "A Task Frame execution Proposal derived from that approved Task Proposal is an "
        "internal descendant, not a second Commander decision. Build its exact execution "
        "approval by binding the generated Task Frame proposal_id and plan_digest to the "
        "same commander_surface and evidence_ref from the primary approval packet, record "
        "the parent proposal_id and proposal_digest as lineage, and continue without asking "
        "the Commander again. If scope or boundary changes, stop and create a new primary "
        "Task Proposal instead of inheriting approval. "
        "When both Task and Evidence require executable proof, "
        "request or attach the persistent project runtime host with "
        "EXECUTABLE_PROOF_REQUIRED before execution. Invoke subordinate agents only as "
        "declared Task Frame Workers. Route every mutation through Execution Guard and "
        "a receipt-aware write path. Never substitute a raw write, raw subordinate "
        "spawn, direct provider CLI, or inferred authority. If any required evidence "
        "is missing, stop and report the exact blocked state. Each Project Room message "
        "includes Host-observed Project Runtime context; use it for current Mode Anchor "
        "and Commander Surface answers, mark unavailable facts UNKNOWN, reply in the "
        "user's language, and keep ordinary conversation direct."
    )


class GrokProjectMasterRuntime:
    def __init__(
        self,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        *,
        native_runner: NativeRunner = run_native_cli,
        model: str = "",
        effort: str = "AUTO",
        max_turns: int = 8,
        response_timeout_seconds: float = 900.0,
        requested_mode: str = "MASTER",
        actor_label: str | None = None,
        new_session: bool = False,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.store = store
        self.native_runner = native_runner
        self.model = model.strip()
        self.effort = str(effort or "AUTO").strip().upper()
        self.max_turns = max(1, int(max_turns))
        self.response_timeout_seconds = float(response_timeout_seconds)
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self.actor_label = (
            _text(actor_label, "actor_label")
            if actor_label is not None
            else f"Project Master for {self.project_id}"
        )
        self.session_id = None if new_session else store.session_ref_for("GROK")
        self.connection_state = "UNKNOWN"
        self._greeting_pending = False
        self._permission_requester: Callable[[Mapping[str, Any]], str | None] | None = (
            None
        )
        self._gateway: UniverseAcpGateway | None = None
        # Provider startup can publish its session id from a background
        # reader while the host is being replaced.  Advancing this epoch
        # before closing the old gateway makes those late callbacks inert;
        # otherwise a stale provider id can be bound to the fresh NEW slot.
        self._observer_epoch = 0

    @property
    def session_ref(self) -> str:
        return f"grok-acp:{self.session_id}"

    def reply(self, message: Mapping[str, Any]) -> str:
        return self.reply_stream(message, lambda _delta: None)

    def reply_stream(
        self,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> str:
        try:
            gateway = self._acp_gateway()
            prompt = self._prompt(message)
            if self._greeting_pending:
                prompt = f"{self._mode_greeting()}\n\n{prompt}"
                self._greeting_pending = False
            result = gateway.reply_stream(prompt, on_delta)
            return result
        except AgentSessionError as error:
            raise ProjectMasterHostError(str(error)) from error

    def set_permission_requester(
        self,
        requester: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        self._permission_requester = requester
        if self._gateway is not None:
            self._gateway.set_permission_requester(requester)

    def prepare_session(self) -> str:
        try:
            self._acp_gateway()
        except AgentSessionError as error:
            raise ProjectMasterHostError(str(error)) from error
        return self.session_ref

    def rebind_working_directory(self, cwd: Path) -> str:
        try:
            rebound = self._acp_gateway().rebind_working_directory(cwd)
        except AgentSessionError as error:
            raise ProjectMasterHostError(str(error)) from error
        self.project_root = Path(rebound)
        return rebound

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        if self._gateway is None:
            return []
        return self._gateway.drain_work_statuses()

    def runtime_observation(self) -> dict[str, Any]:
        if self._gateway is not None:
            return self._gateway.runtime_observation()
        return {
            "schema": "universe.provider-runtime-observation.v1",
            "provider": "GROK",
            "session_ref": self.session_ref,
            "state": "STOPPED",
            "quota_state": "UNKNOWN",
            "usage": {},
        }

    def close(self) -> None:
        self._observer_epoch += 1
        if self._gateway is not None:
            self._gateway.close()
            self._gateway = None

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        if self._gateway is None:
            raise ProjectMasterHostError("AGENT_PROCESS_IDENTITY_UNAVAILABLE")
        try:
            return self._gateway.supervisor_process_identity(
                endpoint, handshake_token
            )
        except AgentSessionError as error:
            raise ProjectMasterHostError(str(error)) from error

    def _acp_gateway(self) -> UniverseAcpGateway:
        if self._gateway is not None:
            return self._gateway
        if self._permission_requester is None:
            raise ProjectMasterHostError("AGENT_PERMISSION_GATEWAY_UNBOUND")
        executable, environment, default_model = _resolve_grok()
        if executable is None:
            raise ProjectMasterHostError("GROK_CLI_UNAVAILABLE")
        model = self.model or default_model

        observer_epoch = self._observer_epoch

        def observe_session(session_id: str) -> None:
            if observer_epoch != self._observer_epoch:
                return
            self.session_id = session_id
            self.connection_state = self.store.observe_provider_session(
                "GROK", session_id
            )
            self._greeting_pending = self.connection_state != "REUSED"

        self._gateway = UniverseAcpGateway(
            GrokAcpSession(
                executable=executable,
                cwd=self.project_root,
                environment=environment,
                model=model,
                effort=self.effort,
                system_prompt=self._system_prompt(),
                session_id=self.session_id,
                response_timeout_seconds=self.response_timeout_seconds,
                permission_requester=self._permission_requester,
                session_observer=observe_session,
            )
        )
        return self._gateway

    def _system_prompt(self) -> str:
        return _project_master_system_prompt(self.actor_label)

    def _mode_greeting(self) -> str:
        return (
            f"Enter {self.requested_mode} Mode for this connection. "
            "Follow the repository entry order, resolve the requested Mode through "
            "the installed Mode Registry, and perform the Session's own preparation "
            "and currentness checks as setup for the Project Room message below. "
            "The Project Room message is the current work request: do not answer only "
            "with Mode-entry status or treat this setup text as a replacement for it."
        )

    def _prompt(self, message: Mapping[str, Any]) -> str:
        runtime_context = message.get("runtime_context")
        context_text = (
            json.dumps(
                dict(runtime_context),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(runtime_context, Mapping)
            else "{}"
        )
        skill_plan_text = json.dumps(
            message.get("skill_plan_context", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        skill_binding_text = json.dumps(
            message.get("skill_binding_proposals", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        retrieval_text = json.dumps(
            message.get("retrieval_context", {}),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "Universe Project Room message\n"
            f"message_id: {_text(message.get('message_id'), 'message.message_id')}\n"
            f"kind: {_text(message.get('kind'), 'message.kind')}\n"
            f"sender: {_text(message.get('sender'), 'message.sender')}\n"
            f"project_runtime_context: {context_text}\n\n"
            f"project_skill_plan_context: {skill_plan_text}\n\n"
            f"project_skill_binding_proposals: {skill_binding_text}\n\n"
            f"project_retrieval_context: {retrieval_text}\n\n"
            f"{_text(message.get('body'), 'message.body')}"
        )


class CodexProjectMasterRuntime:
    def __init__(
        self,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        *,
        native_runner: NativeRunner = run_native_cli,
        model: str = "",
        effort: str = "AUTO",
        requested_mode: str = "MASTER",
        actor_label: str | None = None,
        new_session: bool = False,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.store = store
        self.native_runner = native_runner
        self.model = model.strip()
        self.effort = str(effort or "AUTO").strip().upper()
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self.actor_label = (
            _text(actor_label, "actor_label")
            if actor_label is not None
            else f"Project Master for {self.project_id}"
        )
        self.session_id = None if new_session else store.session_ref_for("CODEX")
        self.connection_state = "UNKNOWN"
        self._greeting_pending = False
        self._permission_requester: Callable[[Mapping[str, Any]], str | None] | None = (
            None
        )
        self._gateway: UniverseAcpGateway | None = None
        # Provider startup can publish its session id from a background
        # reader while the host is being replaced.  Advancing this epoch
        # before closing the old gateway makes those late callbacks inert;
        # otherwise a stale provider id can be bound to the fresh NEW slot.
        self._observer_epoch = 0

    @property
    def session_ref(self) -> str:
        return (
            f"codex-app-server:{self.session_id}"
            if self.session_id
            else f"codex-app-server:pending:{self.project_id}"
        )

    def reply(self, message: Mapping[str, Any]) -> str:
        return self.reply_stream(message, lambda _delta: None)

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        if self._gateway is None:
            return []
        return self._gateway.drain_work_statuses()

    def runtime_observation(self) -> dict[str, Any]:
        if self._gateway is not None:
            return self._gateway.runtime_observation()
        return {
            "schema": "universe.provider-runtime-observation.v1",
            "provider": "CODEX",
            "session_ref": self.session_ref,
            "state": "STOPPED",
            "quota_state": "UNKNOWN",
            "usage": {},
        }

    def supervisor_process_identity(
        self, endpoint: str, handshake_token: str
    ) -> dict[str, Any]:
        if self._gateway is None:
            raise ProjectMasterHostError("AGENT_PROCESS_IDENTITY_UNAVAILABLE")
        try:
            return self._gateway.supervisor_process_identity(
                endpoint, handshake_token
            )
        except AgentSessionError as error:
            raise ProjectMasterHostError(str(error)) from error

    def reply_stream(
        self,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> str:
        base_prompt = self._prompt(message)
        emitted_delta = False

        def tracked_delta(delta: str) -> None:
            nonlocal emitted_delta
            emitted_delta = True
            on_delta(delta)

        for attempt in range(2):
            try:
                gateway = self._acp_gateway()
                prompt = base_prompt
                if self._greeting_pending:
                    prompt = f"{self._mode_greeting()}\n\n{prompt}"
                    self._greeting_pending = False
                return gateway.reply_stream(prompt, tracked_delta)
            except AgentSessionError as error:
                can_replace_stale_resume = (
                    attempt == 0
                    and str(error) == "CODEX_TURN_FAILED"
                    and self.connection_state == "REUSED"
                    and not emitted_delta
                )
                if not can_replace_stale_resume:
                    raise ProjectMasterHostError(str(error)) from error
                self.close()
                self.session_id = None
                self.connection_state = "UNKNOWN"
                self._greeting_pending = False
        raise ProjectMasterHostError("CODEX_SESSION_RECOVERY_FAILED")

    def set_permission_requester(
        self,
        requester: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        self._permission_requester = requester
        if self._gateway is not None:
            self._gateway.set_permission_requester(requester)

    def prepare_session(self) -> str:
        try:
            self._acp_gateway()
        except AgentSessionError as error:
            raise ProjectMasterHostError(str(error)) from error
        return self.session_ref

    def rebind_working_directory(self, cwd: Path) -> str:
        try:
            rebound = self._acp_gateway().rebind_working_directory(cwd)
        except (AgentSessionError, ClaudeResidentError) as error:
            raise ProjectMasterHostError(str(error)) from error
        self.project_root = Path(rebound)
        return rebound

    def close(self) -> None:
        if self._gateway is not None:
            self._gateway.close()
            self._gateway = None

    def _acp_gateway(self) -> UniverseAcpGateway:
        if self._gateway is not None:
            return self._gateway
        if self._permission_requester is None:
            raise ProjectMasterHostError("AGENT_PERMISSION_GATEWAY_UNBOUND")
        executable, environment, default_model = _resolve_codex()
        if executable is None:
            raise ProjectMasterHostError("CODEX_CLI_UNAVAILABLE")
        model = self.model or default_model

        observer_epoch = self._observer_epoch

        def observe_session(session_id: str) -> None:
            if observer_epoch != self._observer_epoch:
                return
            self.session_id = session_id
            self.connection_state = self.store.observe_provider_session(
                "CODEX", session_id
            )
            self._greeting_pending = self.connection_state != "REUSED"

        self._gateway = UniverseAcpGateway(
            CodexAppServerSession(
                executable=executable,
                cwd=self.project_root,
                environment=environment,
                model=model,
                effort=self.effort,
                system_prompt=self._system_prompt(),
                session_id=self.session_id,
                permission_requester=self._permission_requester,
                session_observer=observe_session,
            )
        )
        return self._gateway

    def _system_prompt(self) -> str:
        return _project_master_system_prompt(self.actor_label)

    def _mode_greeting(self) -> str:
        return (
            f"Enter {self.requested_mode} Mode for this connection. "
            "Follow the repository entry order, resolve the requested Mode through "
            "the installed Mode Registry, and perform the Session's own preparation "
            "and currentness checks as setup for the Project Room message below. "
            "The Project Room message is the current work request: do not answer only "
            "with Mode-entry status or treat this setup text as a replacement for it."
        )

    @staticmethod
    def _prompt(message: Mapping[str, Any]) -> str:
        runtime_context = message.get("runtime_context")
        context_text = (
            json.dumps(
                dict(runtime_context),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(runtime_context, Mapping)
            else "{}"
        )
        skill_plan_text = json.dumps(
            message.get("skill_plan_context", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        skill_binding_text = json.dumps(
            message.get("skill_binding_proposals", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        retrieval_text = json.dumps(
            message.get("retrieval_context", {}),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "Universe Project Room message\n"
            f"message_id: {_text(message.get('message_id'), 'message.message_id')}\n"
            f"kind: {_text(message.get('kind'), 'message.kind')}\n"
            f"sender: {_text(message.get('sender'), 'message.sender')}\n"
            f"project_runtime_context: {context_text}\n\n"
            f"project_skill_plan_context: {skill_plan_text}\n\n"
            f"project_skill_binding_proposals: {skill_binding_text}\n\n"
            f"project_retrieval_context: {retrieval_text}\n\n"
            f"{_text(message.get('body'), 'message.body')}"
        )


class ClaudeProjectMasterRuntime(CodexProjectMasterRuntime):
    def __init__(
        self,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        *,
        native_runner: NativeRunner = run_native_cli,
        model: str = "",
        effort: str = "AUTO",
        max_turns: int = 8,
        requested_mode: str = "MASTER",
        actor_label: str | None = None,
        new_session: bool = False,
    ) -> None:
        super().__init__(
            project_root,
            project_id,
            store,
            native_runner=native_runner,
            model=model,
            effort=effort,
            requested_mode=requested_mode,
            actor_label=actor_label,
            new_session=new_session,
        )
        self.session_id = None if new_session else store.session_ref_for("CLAUDE")
        self.max_turns = max(1, int(max_turns))
        self._permission_broker: ClaudePermissionBroker | None = None
        self._mcp_config_root: Path | None = None

    @property
    def session_ref(self) -> str:
        return (
            f"claude-code:{self.session_id}"
            if self.session_id
            else f"claude-code:pending:{self.project_id}"
        )

    def reply_stream(
        self,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> str:
        base_prompt = self._prompt(message)
        emitted_delta = False

        def tracked_delta(delta: str) -> None:
            nonlocal emitted_delta
            emitted_delta = True
            on_delta(delta)

        for attempt in range(2):
            try:
                gateway = self._acp_gateway()
                prompt = base_prompt
                if self._greeting_pending:
                    prompt = f"{self._mode_greeting()}\n\n{prompt}"
                    self._greeting_pending = False
                return gateway.reply_stream(prompt, tracked_delta)
            except ClaudeResidentError as error:
                can_replace_stale_resume = (
                    attempt == 0
                    and str(error) == "CLAUDE_SESSION_RESUME_NOT_FOUND"
                    and self.session_id is not None
                    and not emitted_delta
                )
                if not can_replace_stale_resume:
                    raise ProjectMasterHostError(str(error)) from error
                self.close()
                self.session_id = None
                self.connection_state = "UNKNOWN"
                self._greeting_pending = False
        raise ProjectMasterHostError("CLAUDE_SESSION_RECOVERY_FAILED")

    def prepare_session(self) -> str:
        for attempt in range(2):
            try:
                gateway = self._acp_gateway()
                start_or_resume = getattr(gateway.session, "start_or_resume", None)
                if not callable(start_or_resume):
                    raise ProjectMasterHostError("CLAUDE_PROCESS_START_UNAVAILABLE")
                start_or_resume()
                # A fresh Claude resident receives its explicit session id before
                # the first turn. Persist that coordinate now so the connection
                # status cannot expose the previous provider's session.
                if self.session_id:
                    self.connection_state = self.store.observe_provider_session(
                        "CLAUDE", self.session_id
                    )
                return self.session_ref
            except ClaudeResidentError as error:
                if (
                    attempt == 0
                    and str(error) == "CLAUDE_SESSION_RESUME_NOT_FOUND"
                    and self.session_id is not None
                ):
                    # Provider-side history may be pruned independently of the
                    # local coordinate. Start a fresh resident and let its
                    # first turn establish the new provider session ref.
                    self.close()
                    self.session_id = None
                    self.connection_state = "UNKNOWN"
                    self._greeting_pending = False
                    continue
                raise ProjectMasterHostError(str(error)) from error
        raise ProjectMasterHostError("CLAUDE_SESSION_RECOVERY_FAILED")

    def _acp_gateway(self) -> UniverseAcpGateway:
        if self._gateway is not None:
            return self._gateway
        if self._permission_requester is None:
            raise ProjectMasterHostError("AGENT_PERMISSION_GATEWAY_UNBOUND")
        executable, environment, default_model = _resolve_claude()
        if executable is None:
            raise ProjectMasterHostError("CLAUDE_CLI_UNAVAILABLE")
        environment = dict(environment)
        environment["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
        model = self.model or default_model

        # A NEW Claude resident has only a local pending coordinate until the
        # provider emits its ``system/init`` session id. Bind the permission
        # bridge and broker to that real id before the first prompt.
        bridge = ClaudePermissionBridge(
            session_ref=self.session_ref,
            permission_requester=self._permission_requester,
        )
        broker: ClaudePermissionBroker | None = None

        observer_epoch = self._observer_epoch

        def observe_session(session_id: str) -> None:
            if observer_epoch != self._observer_epoch:
                return
            self.session_id = session_id
            if broker is not None:
                broker.bind_session_ref(self.session_ref)
            else:
                bridge.bind_session_ref(self.session_ref)
            self.connection_state = self.store.observe_provider_session(
                "CLAUDE", session_id
            )
            self._greeting_pending = self.connection_state != "REUSED"

        # Resident Claude: one long-lived stream-json process for this target,
        # with permission prompts routed to the existing requester through the
        # loopback MCP bridge.
        config_root: Path | None = None
        try:
            broker = ClaudePermissionBroker(
                bridge=bridge,
                target=f"{self.project_id}/{self.requested_mode}",
            ).start()
            config_root = Path(tempfile.mkdtemp(prefix="universe-claude-mcp-"))
            mcp_config = broker.write_mcp_config(config_root / "mcp.json")
            session = ClaudeResidentSession(
                executable=executable,
                cwd=self.project_root,
                # The capability token must never reach Claude's own environment.
                environment=broker.provider_environment(environment),
                model=model,
                effort=self.effort,
                system_prompt=self._system_prompt(),
                session_id=self.session_id,
                session_observer=observe_session,
                permission_mcp_config=mcp_config,
                permission_bridge=bridge,
                permission_ready=broker.wait_for_registration,
                permission_failure=broker.close,
            )
        except Exception:
            if broker is not None:
                broker.close()
            if config_root is not None:
                shutil.rmtree(config_root, ignore_errors=True)
            raise
        self._permission_broker = broker
        self._mcp_config_root = config_root
        self.session_id = session.session_id
        self._gateway = UniverseAcpGateway(session)
        return self._gateway

    def close(self) -> None:
        super().close()
        broker = getattr(self, "_permission_broker", None)
        if broker is not None:
            broker.close()
            self._permission_broker = None
        config_root = getattr(self, "_mcp_config_root", None)
        if config_root is not None:
            shutil.rmtree(config_root, ignore_errors=True)
            self._mcp_config_root = None


class ResidentModeSessionHost:
    """Keep one provider-owned Mode Session connection for one target."""

    def __init__(
        self,
        repository_root: Path,
        target_id: str,
        requested_mode: str,
        database_path: Path,
        *,
        actor_label: str,
        session_node: str | None = None,
        target_kind: str = "PROJECT_MASTER",
        session_supervisor: SessionSupervisorStore | None = None,
        supervisor_endpoint: str = "http://127.0.0.1:1",
        continuity_coordinator: ContinuitySaver | None = None,
        coordinate_resolver: Callable[[], Mapping[str, Any] | None] | None = None,
        permission_requester: Callable[[Mapping[str, Any]], str | None]
        | None = None,
        provider_factory: Callable[
            [str, Path, str, ProjectMasterSessionStore, str, str],
            MasterProvider,
        ]
        | None = None,
    ) -> None:
        self.repository_root = repository_root.expanduser().resolve(strict=True)
        self.target_id = _text(target_id, "target_id")
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self.actor_label = _text(actor_label, "actor_label")
        self.session_node = _text(session_node or self.target_id, "session_node")
        self.target_kind = _text(target_kind, "target_kind").upper()
        self.session_supervisor = session_supervisor
        self._supervisor_endpoint = _normalize_supervisor_endpoint(
            supervisor_endpoint
        )
        self._supervisor_capability = secrets.token_urlsafe(32)
        self.continuity_coordinator = continuity_coordinator
        self.coordinate_resolver = coordinate_resolver
        self.permission_requester = permission_requester or (lambda _request: None)
        self.store = ProjectMasterSessionStore(
            database_path,
            self.target_id,
            session_node=self.session_node,
            session_supervisor=session_supervisor,
            requested_mode=self.requested_mode,
        )
        self._custom_provider_factory = provider_factory
        self.provider_factory = provider_factory or self._default_provider
        self._profile_provider: str | None = None
        self._desired_model = ""
        self._desired_effort = "AUTO"
        self._provider_name: str | None = None
        self._provider: MasterProvider | None = None
        self._provider_session_ref: str | None = None
        self._provider_model = ""
        self._provider_effort = "AUTO"
        self._supervisor_session_id: str | None = None
        self._supervisor_lease_token: str | None = None
        self._supervisor_lease_version: int | None = None
        self._supervisor_process_identity: dict[str, Any] | None = None
        self._last_interaction: dict[str, str] | None = None
        self._last_interaction_at = 0.0
        self._lock = threading.RLock()

    def prepare(
        self,
        provider: str,
        *,
        model: str = "",
        effort: str = "AUTO",
        session_action: str = "RESUME",
    ) -> dict[str, Any]:
        normalized_provider = _provider(provider)
        normalized_action = normalize_session_action(session_action)
        with self._lock:
            active = self._ensure(
                normalized_provider,
                model=model,
                effort=effort,
                session_action=normalized_action,
            )
            return self._connection_status(active=active)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._connection_status(active=self._provider)

    def active_provider_session_ref(self) -> str | None:
        with self._lock:
            active = self._provider
            if active is None:
                return None
            session_id = getattr(active, "session_id", None)
            if isinstance(session_id, str) and session_id.strip():
                return session_id.strip()
            session_ref = getattr(active, "session_ref", None)
            if isinstance(session_ref, str) and session_ref.strip():
                return session_ref.strip()
            return None

    def rebind_working_directory(self, cwd: Path) -> str:
        target = cwd.expanduser().resolve(strict=True)
        with self._lock:
            active = self._provider
            if active is None:
                raise ProjectMasterHostError("MODE_SESSION_NOT_RESIDENT")
            rebind = getattr(active, "rebind_working_directory", None)
            if not callable(rebind):
                raise ProjectMasterHostError("MODE_SESSION_CWD_REBIND_UNAVAILABLE")
            rebound = str(rebind(target))
            self.repository_root = Path(rebound)
            return rebound

    def set_permission_requester(
        self,
        requester: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        with self._lock:
            self.permission_requester = requester
            active = self._provider
            setter = getattr(active, "set_permission_requester", None)
            if callable(setter):
                setter(requester)

    def reply(
        self,
        provider: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.reply_stream(provider, message, lambda _delta: None)

    def reply_stream(
        self,
        provider: str,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> dict[str, Any]:
        normalized_provider = _provider(provider)
        with self._lock:
            active = self._ensure(normalized_provider)
            self.store.observe_session_activity(
                normalized_provider,
                active.session_ref,
                event_type="COMMANDER_MESSAGE_OBSERVED",
                activity_state="ACTIVE",
                evidence_ref=(
                    "universe://session-observer/"
                    f"{self.target_id}/{self.requested_mode}/commander-message"
                ),
            )
            try:
                stream_reply = getattr(active, "reply_stream", None)
                if callable(stream_reply):
                    text = stream_reply(message, on_delta)
                else:
                    text = active.reply(message)
            except Exception as error:
                if self._is_quota_exhaustion(error):
                    if not self._save_quota_continuity(active, normalized_provider):
                        self._mark_dirty_end(
                            f"{normalized_provider}_QUOTA_CONTINUITY_UNAVAILABLE"
                        )
                else:
                    self._mark_dirty_end(
                        f"{normalized_provider}_REPLY_FAILED:{type(error).__name__}"
                    )
                raise
            self.store.observe_session_activity(
                normalized_provider,
                active.session_ref,
                event_type="PROVIDER_REPLY_OBSERVED",
                activity_state="COMPLETED",
                evidence_ref=(
                    "universe://session-observer/"
                    f"{self.target_id}/{self.requested_mode}/provider-reply"
                ),
            )
            self._last_interaction = {
                "target_id": self.target_id,
                "mode": self.requested_mode,
                "provider": normalized_provider,
                "provider_session_ref": active.session_ref,
            }
            self._last_interaction_at = time.monotonic()
            connection_state = str(
                getattr(active, "connection_state", "UNKNOWN")
            )
            result: dict[str, Any] = {
                "provider": normalized_provider,
                "model_ref": self._provider_model or "UNKNOWN",
                "effort": self._provider_effort,
                "session_ref": active.session_ref,
                "connection_state": connection_state,
                "requested_mode": self.requested_mode,
                "session_persistence": "LAST_COORDINATE",
                "text": text,
            }
            result["runtime_observation"] = self._runtime_observation(active)
            return result

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            provider = self._provider
            reader = getattr(provider, "drain_work_statuses", None)
            if not callable(reader):
                return []
            return [dict(item) for item in reader() if isinstance(item, Mapping)]

    def close(self) -> None:
        with self._lock:
            provider = self._provider
            provider_name = self._provider_name
            self._mark_supervisor_process_lease_stale()
            self._provider = None
            self._provider_name = None
            self._provider_session_ref = None
            self._provider_model = ""
            self._provider_effort = "AUTO"
        if provider is not None:
            self._save_continuity("NORMAL_STOP", provider, provider_name)
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def _mark_supervisor_process_lease_stale(self) -> None:
        if (
            self.session_supervisor is None
            or not self._supervisor_session_id
            or not self._supervisor_lease_token
            or self._supervisor_lease_version is None
            or self._supervisor_process_identity is None
        ):
            return
        try:
            self.session_supervisor.mark_lease_stale(
                self._supervisor_session_id,
                self._supervisor_process_identity,
                lease_token=self._supervisor_lease_token,
                expected_lease_version=self._supervisor_lease_version,
                reason="RESIDENT_MODE_SESSION_CLOSED",
            )
        except SessionSupervisorError:
            # A concurrent supervisor transition already owns the lifecycle
            # result. Do not hide the provider close behind stale cleanup.
            pass
        finally:
            self._supervisor_session_id = None
            self._supervisor_lease_token = None
            self._supervisor_lease_version = None
            self._supervisor_process_identity = None

    def _ensure_supervisor_process_lease(self, provider: MasterProvider) -> None:
        if self.session_supervisor is None:
            return
        resolver = getattr(provider, "supervisor_process_identity", None)
        if not callable(resolver):
            # Test/custom providers may not expose a native process identity.
            # They remain usable, but cannot participate in Supervisor leases.
            return
        try:
            self.session_supervisor.sweep_stale_live_sessions()
            selected = self.store.ensure_supervisor_session(
                self._provider_name or self._profile_provider or "UNKNOWN"
            )
            if not isinstance(selected, Mapping):
                raise ProjectMasterHostError(
                    "MODE_SESSION_SUPERVISOR_SESSION_UNAVAILABLE"
                )
            supervisor_session_id = _text(
                selected.get("session_id"), "supervisor_session_id"
            )
            identity = resolver(
                self._supervisor_endpoint,
                self._supervisor_capability,
            )
            if not isinstance(identity, Mapping):
                raise ProjectMasterHostError(
                    "MODE_SESSION_PROCESS_IDENTITY_INVALID"
                )
            observed_identity = dict(identity)
            current = self.session_supervisor.get_session(supervisor_session_id)
            current_lease = current.get("process_lease")
            local_lease_matches = (
                isinstance(current_lease, Mapping)
                and current_lease.get("lease_state") == "OWNED"
                and self._supervisor_session_id == supervisor_session_id
                and bool(self._supervisor_lease_token)
                and self._supervisor_lease_version
                == int(current_lease.get("lease_version", -1))
                and self._supervisor_process_identity is not None
                and _same_process_identity(
                    current_lease.get("process_identity"),
                    self._supervisor_process_identity,
                )
            )
            if (
                isinstance(current_lease, Mapping)
                and current_lease.get("lease_state") == "OWNED"
                and _same_process_identity(
                    current_lease.get("process_identity"), observed_identity
                )
                and local_lease_matches
            ):
                return
            if local_lease_matches:
                # A native provider may replace its underlying process while
                # keeping the same resident session object (for example,
                # Codex recovering a failed turn). The old lease is still
                # ours, so retire it with the exact capability and identity
                # before acquiring a lease for the replacement process.
                try:
                    retired = self.session_supervisor.mark_lease_stale(
                        supervisor_session_id,
                        self._supervisor_process_identity,
                        lease_token=self._supervisor_lease_token,
                        expected_lease_version=self._supervisor_lease_version,
                        reason="RESIDENT_PROVIDER_PROCESS_REPLACED",
                    )
                except SessionSupervisorError as error:
                    raise ProjectMasterHostError(str(error)) from error
                retired_lease = (
                    retired.get("process_lease")
                    if isinstance(retired, Mapping)
                    else None
                )
                expected_version = (
                    int(retired_lease["lease_version"])
                    if isinstance(retired_lease, Mapping)
                    else self._supervisor_lease_version + 1
                )
                self._supervisor_session_id = None
                self._supervisor_lease_token = None
                self._supervisor_lease_version = None
                self._supervisor_process_identity = None
            else:
                expected_version = (
                    int(current_lease.get("lease_version", 0))
                    if isinstance(current_lease, Mapping)
                    else 0
                )
            acquired = self.session_supervisor.acquire_lease(
                supervisor_session_id,
                observed_identity,
                expected_lease_version=expected_version,
                stop_capability=self._supervisor_capability,
            )
        except SessionSupervisorError as error:
            raise ProjectMasterHostError(str(error)) from error
        acquired_lease = acquired.get("lease")
        if not isinstance(acquired_lease, Mapping):
            raise ProjectMasterHostError(
                "MODE_SESSION_PROCESS_LEASE_RESULT_INVALID"
            )
        lease_identity = acquired_lease.get("process_identity")
        if not isinstance(lease_identity, Mapping):
            raise ProjectMasterHostError(
                "MODE_SESSION_PROCESS_LEASE_IDENTITY_MISSING"
            )
        self._supervisor_session_id = supervisor_session_id
        self._supervisor_lease_token = _text(
            acquired.get("lease_token"), "lease_token"
        )
        self._supervisor_lease_version = int(acquired_lease["lease_version"])
        self._supervisor_process_identity = dict(lease_identity)

    def _ensure(
        self,
        provider: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        session_action: str = "RESUME",
    ) -> MasterProvider:
        normalized_action = normalize_session_action(session_action)
        force_new_session = normalized_action == "NEW"
        if model is not None or effort is not None:
            self._profile_provider = provider
            if model is not None:
                self._desired_model = str(model or "").strip()
            if effort is not None:
                self._desired_effort = str(effort or "AUTO").strip().upper()
        elif self._profile_provider != provider:
            self._profile_provider = provider
            self._desired_model = ""
            self._desired_effort = "AUTO"
        selected_model = self._desired_model
        selected_effort = self._desired_effort
        selected_session_ref = (
            None
            if force_new_session
            else self.store.session_ref_for(provider)
        )
        if self._provider is not None and self._provider_name == provider:
            if (
                not force_new_session
                and (
                    selected_session_ref is None
                    or selected_session_ref == self._provider_session_ref
                )
                and selected_model == self._provider_model
                and selected_effort == self._provider_effort
            ):
                self._ensure_supervisor_process_lease(self._provider)
                setattr(self._provider, "connection_state", "REUSED")
                return self._provider
            replacement_trigger = (
                "NEW_SESSION"
                if force_new_session
                else "SESSION_SELECTION_CHANGED"
                if selected_session_ref != self._provider_session_ref
                else "PROVIDER_PROFILE_CHANGED"
            )
        else:
            replacement_trigger = "NEW_SESSION" if force_new_session else "PROVIDER_SWITCH"
        if self._provider is not None:
            previous = self._provider
            previous_name = self._provider_name
            self._save_continuity(replacement_trigger, previous, previous_name)
            self._mark_supervisor_process_lease_stale()
            self._provider = None
            self._provider_name = None
            self._provider_session_ref = None
            self._provider_model = ""
            self._provider_effort = "AUTO"
            close = getattr(previous, "close", None)
            if callable(close):
                close()
        if force_new_session:
            # NEW owns a fresh Supervisor Session Anchor before the provider
            # reports its vendor session id. The provider observation binds to
            # this slot instead of rewriting the previous default session.
            self.store.ensure_supervisor_session(provider, new_session=True)
        if self._custom_provider_factory is None:
            active = self._default_provider(
                provider,
                self.repository_root,
                self.target_id,
                self.store,
                self.requested_mode,
                self.actor_label,
                selected_model,
                selected_effort,
                force_new_session,
            )
        else:
            active = self.provider_factory(
                provider,
                self.repository_root,
                self.target_id,
                self.store,
                self.requested_mode,
                self.actor_label,
            )
        permission_setter = getattr(active, "set_permission_requester", None)
        if callable(permission_setter):
            permission_setter(self.permission_requester)
        prepare = getattr(active, "prepare_session", None)
        if callable(prepare):
            prepare()
        self._provider_name = provider
        self._provider = active
        try:
            self._ensure_supervisor_process_lease(active)
        except Exception:
            self._mark_supervisor_process_lease_stale()
            close = getattr(active, "close", None)
            if callable(close):
                close()
            self._provider = None
            self._provider_name = None
            raise
        self._provider_model = selected_model
        self._provider_effort = selected_effort
        raw_session_id = getattr(active, "session_id", None)
        if isinstance(raw_session_id, str) and raw_session_id.strip():
            # A stale stored coordinate may be replaced during provider
            # preparation.  Reuse the live adapter coordinate for the
            # manager's next activation comparison.
            self._provider_session_ref = raw_session_id.strip()
        else:
            self._provider_session_ref = self.store.session_ref_for(provider)
        if self._provider_session_ref:
            # App-server thread/start exposes the provider id before the
            # first turn. Project it immediately so CLI attach and the
            # browser session card observe the same Mode coordinate.
            patch_mode_current_anchor(
                self.repository_root,
                provider=provider,
                session_ref=_provider_session_identity(
                    provider, self._provider_session_ref
                ),
                mode=self.requested_mode,
            )
        return active

    def save_idle(self, idle_seconds: float) -> Mapping[str, Any] | None:
        with self._lock:
            if (
                self.continuity_coordinator is None
                or self._last_interaction is None
                or time.monotonic() - self._last_interaction_at < idle_seconds
            ):
                return None
            context = dict(self._last_interaction)
        try:
            return self.continuity_coordinator.save(
                project_root=self.repository_root,
                trigger="IDLE",
                compressed_context=json.dumps(
                    context,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                summary=f"{self.target_id} {self.requested_mode} session state",
                runtime_coordinate=self._runtime_coordinate(),
            )
        except Exception:
            return None

    def _save_continuity(
        self,
        trigger: str,
        provider: MasterProvider,
        provider_name: str | None = None,
    ) -> None:
        if self.continuity_coordinator is None:
            return
        context = json.dumps(
            {
                "target_id": self.target_id,
                "mode": self.requested_mode,
                "provider": provider_name or self._provider_name or "UNKNOWN",
                "provider_session_ref": str(
                    getattr(provider, "session_ref", "UNKNOWN")
                ),
                "transition": trigger,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self.continuity_coordinator.save(
                project_root=self.repository_root,
                trigger=trigger,
                compressed_context=context,
                summary=f"{self.target_id} {self.requested_mode} {trigger.lower()}",
                runtime_coordinate=self._runtime_coordinate(),
            )
        except Exception:
            return

    def _save_quota_continuity(
        self,
        provider: MasterProvider,
        provider_name: str,
    ) -> bool:
        if self.continuity_coordinator is None:
            return False
        observation = self._runtime_observation(provider)
        context = {
            "target_id": self.target_id,
            "mode": self.requested_mode,
            "provider": provider_name,
            "provider_session_ref": str(getattr(provider, "session_ref", "UNKNOWN")),
            "transition": "PROVIDER_QUOTA",
            "quota_state": observation.get("quota_state", "UNKNOWN"),
            "usage": observation.get("usage", {}),
        }
        try:
            saved = self.continuity_coordinator.save(
                project_root=self.repository_root,
                trigger="PROVIDER_QUOTA",
                compressed_context=json.dumps(
                    context,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                summary=(
                    f"{self.target_id} {self.requested_mode} provider quota checkpoint"
                ),
                runtime_coordinate=self._runtime_coordinate(),
            )
        except Exception:
            return False
        return str(saved.get("status", "")) in {
            "AUTO_CONTINUITY_SAVED",
            "AUTO_CONTINUITY_ALREADY_SAVED",
        }

    @staticmethod
    def _is_quota_exhaustion(error: Exception) -> bool:
        value = str(error).upper()
        return "QUOTA" in value and any(
            marker in value for marker in ("EXHAUSTED", "RATE_LIMIT", "LIMIT_REACHED")
        )

    @staticmethod
    def _runtime_observation(provider: MasterProvider) -> dict[str, Any]:
        observer = getattr(provider, "runtime_observation", None)
        if callable(observer):
            observed = observer()
            if isinstance(observed, Mapping):
                return dict(observed)
        return {
            "schema": "universe.provider-runtime-observation.v1",
            "provider": "UNKNOWN",
            "session_ref": str(getattr(provider, "session_ref", "UNKNOWN")),
            "state": str(getattr(provider, "connection_state", "UNKNOWN")),
            "quota_state": "UNKNOWN",
            "usage": {},
        }

    def _runtime_coordinate(self) -> Mapping[str, Any] | None:
        if self.coordinate_resolver is None:
            return None
        try:
            return self.coordinate_resolver()
        except Exception:
            return None

    def _mark_dirty_end(self, reason: str) -> None:
        if self.continuity_coordinator is None:
            return
        try:
            self.continuity_coordinator.mark_dirty_end(
                self.repository_root,
                reason,
            )
        except Exception:
            return

    @staticmethod
    def _default_provider(
        provider: str,
        repository_root: Path,
        target_id: str,
        store: ProjectMasterSessionStore,
        requested_mode: str,
        actor_label: str,
        model: str = "",
        effort: str = "AUTO",
        new_session: bool = False,
    ) -> MasterProvider:
        if provider == "GROK":
            return GrokProjectMasterRuntime(
                repository_root,
                target_id,
                store,
                model=model,
                effort=effort,
                requested_mode=requested_mode,
                actor_label=actor_label,
                new_session=new_session,
            )
        if provider == "CODEX":
            return CodexProjectMasterRuntime(
                repository_root,
                target_id,
                store,
                model=model,
                effort=effort,
                requested_mode=requested_mode,
                actor_label=actor_label,
                new_session=new_session,
            )
        if provider == "CLAUDE":
            return ClaudeProjectMasterRuntime(
                repository_root,
                target_id,
                store,
                model=model,
                effort=effort,
                requested_mode=requested_mode,
                actor_label=actor_label,
                new_session=new_session,
            )
        raise ProjectMasterHostError("MODE_SESSION_PROVIDER_UNSUPPORTED")

    def _connection_status(
        self,
        *,
        active: MasterProvider | None,
    ) -> dict[str, Any]:
        state = (
            str(getattr(active, "connection_state", "")).strip().upper()
            if active is not None
            else None
        )
        status = provider_session_connection(
            target_kind=self.target_kind,
            target_id=self.target_id,
            requested_mode=self.requested_mode,
            store=self.store,
            resident=active is not None,
            connection_state=state if state and state != "UNKNOWN" else None,
            model_ref=self._provider_model,
            effort=self._provider_effort,
        )
        status["runtime_observation"] = (
            self._runtime_observation(active) if active is not None else None
        )
        return status


class RoomParticipantConversationWorker:
    """Serialize incremental Room events through one resident provider session."""

    def __init__(
        self,
        *,
        binding_id: str,
        provider: str,
        session_host: ResidentModeSessionHost,
        room_event_observer: NativeRoomObserver,
        permission_observer: RoomPermissionObserver,
    ) -> None:
        self.binding_id = _text(binding_id, "binding_id")
        self.provider = _provider(provider)
        self.session_host = session_host
        self.room_event_observer = room_event_observer
        self.permission_observer = permission_observer
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._permission_lock = threading.RLock()
        self._permission_waiters: dict[str, dict[str, Any]] = {}
        self._active_binding: dict[str, Any] | None = None
        self._active_event: dict[str, Any] | None = None
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"room-participant-{self.binding_id}",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.session_host.set_permission_requester(self._request_permission)
        self._started = True
        self._thread.start()

    def submit(
        self,
        binding: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> bool:
        message = event.get("message")
        if not isinstance(message, Mapping):
            raise ProjectMasterHostError("NATIVE_ROOM_MESSAGE_INVALID")
        room_event_id = _text(event.get("room_event_id"), "event.room_event_id")
        if message.get("room_event_id") != room_event_id:
            raise ProjectMasterHostError("NATIVE_ROOM_EVENT_ID_MISMATCH")
        self._queue.put({"binding": dict(binding), "event": dict(event)})
        return True

    def wait_idle(self, timeout_seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self) -> None:
        self._closed.set()
        with self._permission_lock:
            waiters = list(self._permission_waiters.values())
            self._permission_waiters.clear()
        for waiter in waiters:
            waiter["event"].set()
        if self._started:
            self._queue.put(None)
            self._thread.join(timeout=10)
        self.session_host.close()

    def is_alive(self) -> bool:
        return self._started and self._thread.is_alive()

    def resolve_permission(self, request_id: str, option_id: str) -> bool:
        normalized_request = _text(request_id, "request_id")
        normalized_option = _text(option_id, "option_id")
        with self._permission_lock:
            if self._closed.is_set():
                return False
            waiter = self._permission_waiters.get(normalized_request)
            if waiter is None or normalized_option not in waiter["options"]:
                return False
            waiter["option_id"] = normalized_option
            waiter["event"].set()
            return True

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._process(job["binding"], job["event"])
            finally:
                self._queue.task_done()

    def _process(
        self,
        binding: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> None:
        message = event["message"]
        room_id = _text(event.get("room_id"), "event.room_id")
        room_event_id = _text(event.get("room_event_id"), "event.room_event_id")
        accepted = False
        with self._permission_lock:
            self._active_binding = dict(binding)
            self._active_event = dict(event)

        def observe(event_type: str, **values: Any) -> None:
            self.room_event_observer(
                {
                    "event": event_type,
                    "room_id": room_id,
                    "room_event_id": room_event_id,
                    "binding_id": self.binding_id,
                    "provider_session_ref": binding.get("provider_session_ref"),
                    **values,
                }
            )

        def accept_once() -> None:
            nonlocal accepted
            if accepted:
                return
            accepted = True
            observe("DELIVERY_ACCEPTED")

        def observe_delta(delta: str) -> None:
            accept_once()
            observe("DELTA", delta=str(delta))

        provider_message = {
            "schema": "universe.native-room-input.v1",
            "message_id": room_event_id,
            "kind": "ROOM_MESSAGE",
            "sender": message.get("author_role"),
            "body": message.get("body_text"),
            "runtime_context": {
                "requested_mode": self.session_host.requested_mode,
                "commander_surface": (
                    "UNIVERSE_UI"
                    if message.get("author_role") == "USER"
                    else "ROOM_PARTICIPANT"
                ),
            },
            "room_context": {
                "room_id": room_id,
                "room_sequence": event.get("room_sequence"),
                "correlation_id": event.get("correlation_id"),
            },
        }
        try:
            result = self.session_host.reply_stream(
                self.provider,
                provider_message,
                observe_delta,
            )
            accept_once()
            observe(
                "COMPLETED",
                body=str(result.get("text") or ""),
                provider_session_ref=result.get("session_ref"),
            )
        except Exception as error:
            observe(
                "FAILED",
                reason=f"{type(error).__name__}: {error}",
                delivery_status="UNCERTAIN",
            )
        finally:
            with self._permission_lock:
                self._active_binding = None
                self._active_event = None

    def _request_permission(self, request: Mapping[str, Any]) -> str | None:
        request_id = _text(request.get("request_id"), "request_id")
        options = request.get("options")
        if not isinstance(options, list):
            raise ProjectMasterHostError("ROOM_PERMISSION_OPTIONS_INVALID")
        option_ids = {
            _text(option.get("optionId"), "option.optionId")
            for option in options
            if isinstance(option, Mapping)
        }
        if not option_ids:
            raise ProjectMasterHostError("ROOM_PERMISSION_OPTIONS_INVALID")
        waiter = {
            "event": threading.Event(),
            "options": option_ids,
            "option_id": None,
        }
        with self._permission_lock:
            if self._closed.is_set():
                return None
            if self._active_binding is None or self._active_event is None:
                raise ProjectMasterHostError("ROOM_PERMISSION_CONTEXT_UNAVAILABLE")
            if request_id in self._permission_waiters:
                raise ProjectMasterHostError("ROOM_PERMISSION_REQUEST_DUPLICATE")
            binding = dict(self._active_binding)
            event = dict(self._active_event)
            self._permission_waiters[request_id] = waiter
        try:
            self.permission_observer(binding, event, request)
            if not waiter["event"].wait(600):
                return None
            with self._permission_lock:
                if self._closed.is_set():
                    return None
                selected = waiter["option_id"]
            return str(selected) if selected else None
        finally:
            with self._permission_lock:
                self._permission_waiters.pop(request_id, None)


@dataclass
class ResidentRoomParticipantHandle:
    binding_id: str
    provider: str
    provider_session_ref: str
    worker: RoomParticipantConversationWorker

    def close(self) -> None:
        self.worker.close()


class ResidentRoomParticipantHostManager:
    """Own explicit native controls for imported Room participant sessions."""

    def __init__(
        self,
        *,
        room_event_observer: NativeRoomObserver,
        permission_observer: RoomPermissionObserver | None = None,
        provider_factory: Callable[
            [str, Path, str, ProjectMasterSessionStore, str, str],
            MasterProvider,
        ]
        | None = None,
    ) -> None:
        self.room_event_observer = room_event_observer
        self.permission_observer = permission_observer or self._permission_unavailable
        self.provider_factory = provider_factory
        self._handles: dict[str, ResidentRoomParticipantHandle] = {}
        self._lock = threading.RLock()

    def ensure(
        self,
        *,
        binding: Mapping[str, Any],
        repository_root: Path,
        node: str,
        mode: str,
    ) -> dict[str, Any]:
        binding_id = _text(binding.get("binding_id"), "binding.binding_id")
        provider = _provider(binding.get("provider"))
        session_ref = _text(
            binding.get("provider_session_ref"),
            "binding.provider_session_ref",
        )
        normalized_mode = _text(mode, "mode").upper()
        normalized_node = _text(node, "node")
        root = repository_root.expanduser().resolve(strict=True)
        with self._lock:
            current = self._handles.get(binding_id)
            if (
                current is not None
                and current.worker.is_alive()
                and current.provider == provider
                and _same_provider_session_ref(
                    provider,
                    current.provider_session_ref,
                    session_ref,
                )
            ):
                return {
                    "status": "RESIDENT",
                    "binding_id": binding_id,
                    "provider": provider,
                    "provider_session_ref": session_ref,
                }
            if current is not None:
                current.close()
                self._handles.pop(binding_id, None)
            host = ResidentModeSessionHost(
                root,
                normalized_node,
                normalized_mode,
                _default_room_participant_db(binding_id),
                actor_label=str(binding.get("display_name") or binding.get("slot_role") or "Room Participant"),
                session_node=normalized_node,
                target_kind="ROOM_PARTICIPANT",
                permission_requester=self._permission_unavailable,
                provider_factory=self.provider_factory,
            )
            host.store.observe_provider_session(provider, session_ref)
            try:
                connection = host.prepare(provider)
                observed_ref = host.active_provider_session_ref()
                if not _same_provider_session_ref(
                    provider,
                    observed_ref,
                    session_ref,
                ):
                    raise ProjectMasterHostError(
                        "ROOM_PARTICIPANT_SESSION_RESUME_MISMATCH"
                    )
                worker = RoomParticipantConversationWorker(
                    binding_id=binding_id,
                    provider=provider,
                    session_host=host,
                    room_event_observer=self.room_event_observer,
                    permission_observer=self.permission_observer,
                )
                worker.start()
            except Exception:
                host.close()
                raise
            handle = ResidentRoomParticipantHandle(
                binding_id=binding_id,
                provider=provider,
                provider_session_ref=session_ref,
                worker=worker,
            )
            self._handles[binding_id] = handle
            return {
                "status": "STARTED",
                "binding_id": binding_id,
                "provider": provider,
                "provider_session_ref": session_ref,
                "session_connection": connection,
            }

    def submit(
        self,
        binding: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> bool:
        binding_id = _text(binding.get("binding_id"), "binding.binding_id")
        with self._lock:
            handle = self._handles.get(binding_id)
        if handle is None or not handle.worker.is_alive():
            raise ProjectMasterHostError("ROOM_PARTICIPANT_NATIVE_CONTROL_UNAVAILABLE")
        if str(binding.get("provider") or "").upper() != handle.provider:
            raise ProjectMasterHostError("ROOM_PARTICIPANT_NATIVE_PROVIDER_MISMATCH")
        if not _same_provider_session_ref(
            handle.provider,
            binding.get("provider_session_ref"),
            handle.provider_session_ref,
        ):
            raise ProjectMasterHostError("ROOM_PARTICIPANT_NATIVE_SESSION_MISMATCH")
        return handle.worker.submit(binding, event)

    def stop(self, binding_id: str) -> bool:
        normalized = _text(binding_id, "binding_id")
        with self._lock:
            handle = self._handles.pop(normalized, None)
        if handle is None:
            return False
        handle.close()
        return True

    def resolve_permission(
        self,
        binding_id: str,
        request_id: str,
        option_id: str,
    ) -> bool:
        normalized = _text(binding_id, "binding_id")
        with self._lock:
            handle = self._handles.get(normalized)
        if handle is None or not handle.worker.is_alive():
            return False
        return handle.worker.resolve_permission(request_id, option_id)

    def close(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            handle.close()

    @staticmethod
    def _permission_unavailable(
        _binding: Mapping[str, Any],
        _event: Mapping[str, Any],
        _request: Mapping[str, Any],
    ) -> None:
        raise ProjectMasterHostError("ROOM_PARTICIPANT_PERMISSION_GATEWAY_UNAVAILABLE")


class ProjectMasterConversationWorker:
    def __init__(
        self,
        *,
        provider: MasterProvider,
        store: ProjectMasterSessionStore,
        universe_endpoint: str,
        project_id: str,
        bridge_token: str,
        surface_observer: CommanderSurfaceObserver,
        reply_poster: ReplyPoster = post_master_reply,
        stream_poster: StreamPoster = post_master_stream_event,
        permission_poster: PermissionPoster = post_agent_permission_request,
        completion_observer: Callable[[Mapping[str, Any]], None] | None = None,
        governance_context_resolver: GovernanceContextResolver | None = None,
        governance_context: Mapping[str, Any] | None = None,
        retrieval_context_resolver: RetrievalContextResolver | None = None,
        room_event_observer: NativeRoomObserver | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.universe_endpoint = universe_endpoint
        self.project_id = _text(project_id, "project_id")
        self.bridge_token = _text(bridge_token, "bridge_token")
        self.surface_observer = surface_observer
        self.reply_poster = reply_poster
        self.stream_poster = stream_poster
        self.permission_poster = permission_poster
        self.completion_observer = completion_observer
        self.governance_context_resolver = governance_context_resolver
        self.governance_context = (
            dict(governance_context) if governance_context is not None else None
        )
        self.retrieval_context_resolver = retrieval_context_resolver
        self.room_event_observer = room_event_observer
        self._last_completion: dict[str, Any] | None = None
        self._last_completion_at = 0.0
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._permission_lock = threading.RLock()
        self._permission_waiters: dict[str, dict[str, Any]] = {}
        self._active_bridge_id = ""
        self._active_message_id = ""
        self._thread = threading.Thread(
            target=self._run,
            name=f"project-master-{self.project_id}",
            daemon=True,
        )
        self._started = False
        bind_permission = getattr(self.provider, "set_permission_requester", None)
        if callable(bind_permission):
            bind_permission(self._request_permission)

    def start(
        self,
        *,
        recovery_bridge_id: str | None = None,
        recovery_session_ref: str | None = None,
    ) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        for envelope in self.store.recover(
            bridge_id=recovery_bridge_id,
            master_session_ref=recovery_session_ref,
        ):
            self._queue.put(envelope)

    def submit(self, envelope: Mapping[str, Any]) -> bool:
        normalized = normalize_bridge_envelope(envelope)
        if not self.store.register(normalized):
            return False
        self._queue.put(normalized)
        return True

    def submit_room_event(
        self,
        *,
        binding: Mapping[str, Any],
        event: Mapping[str, Any],
        bridge_id: str,
    ) -> bool:
        message = event.get("message")
        if not isinstance(message, Mapping):
            raise ProjectMasterHostError("NATIVE_ROOM_MESSAGE_INVALID")
        room_event_id = _text(event.get("room_event_id"), "event.room_event_id")
        if message.get("room_event_id") != room_event_id:
            raise ProjectMasterHostError("NATIVE_ROOM_EVENT_ID_MISMATCH")
        self._queue.put(
            {
                "_job_type": "ROOM_EVENT",
                "binding": dict(binding),
                "event": dict(event),
                "bridge_id": _text(bridge_id, "bridge_id"),
            }
        )
        return True

    def wait_idle(self, timeout_seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self) -> None:
        if not self._started:
            close_provider = getattr(self.provider, "close", None)
            if callable(close_provider):
                close_provider()
            return
        with self._permission_lock:
            waiters = list(self._permission_waiters.values())
            self._permission_waiters.clear()
        for waiter in waiters:
            waiter["event"].set()
        self._queue.put(None)
        self._thread.join(timeout=10)
        close_provider = getattr(self.provider, "close", None)
        if callable(close_provider):
            close_provider()

    def resolve_permission(self, request_id: str, option_id: str) -> bool:
        normalized_request = _text(request_id, "request_id")
        normalized_option = _text(option_id, "option_id")
        with self._permission_lock:
            waiter = self._permission_waiters.get(normalized_request)
            if waiter is None or normalized_option not in waiter["options"]:
                return False
            waiter["option_id"] = normalized_option
            waiter["event"].set()
            return True

    def _run(self) -> None:
        while True:
            envelope = self._queue.get()
            try:
                if envelope is None:
                    return
                if envelope.get("_job_type") == "ROOM_EVENT":
                    self._process_room_event(envelope)
                else:
                    self._process(envelope)
            finally:
                self._queue.task_done()

    def _process(self, envelope: Mapping[str, Any]) -> None:
        message = envelope["message"]
        message_id = _text(message["message_id"], "message_id")
        if not self.store.claim(message_id):
            return
        bridge_id = _text(envelope["bridge_id"], "bridge_id")
        self._active_bridge_id = bridge_id
        self._active_message_id = message_id
        sequence = 0

        def emit(event: str, *, delta: str = "", detail: str = "") -> None:
            nonlocal sequence
            sequence += 1
            try:
                self.stream_poster(
                    universe_endpoint=self.universe_endpoint,
                    project_id=self.project_id,
                    bridge_id=bridge_id,
                    in_reply_to=message_id,
                    event=event,
                    sequence=sequence,
                    delta=delta,
                    detail=detail,
                    bridge_token=self.bridge_token,
                    timeout_seconds=5.0,
                )
            except Exception:
                # Streaming is process-local UX evidence. Durable final delivery
                # remains authoritative when a client disconnects.
                pass

        try:
            emit("STARTED")
            surface_observation = self.surface_observer.observe(message)
            provider_message = dict(message)
            provider_message["runtime_context"] = _runtime_context(surface_observation)
            provider_message["skill_plan_context"] = self.store.skill_plan_contexts()
            provider_message["skill_binding_proposals"] = (
                self.store.skill_binding_proposals()
            )
            if self.retrieval_context_resolver is not None:
                retrieval = self.retrieval_context_resolver(
                    self.project_id, provider_message
                )
                if not isinstance(retrieval, Mapping):
                    raise ProjectMasterHostError(
                        "PROJECT_RETRIEVAL_CONTEXT_UNAVAILABLE"
                    )
                provider_message["retrieval_context"] = dict(retrieval)
            governance_context = self._governance_context_for_message()
            if governance_context is not None:
                provider_message["governance_context"] = governance_context
            stream_reply = getattr(self.provider, "reply_stream", None)
            if callable(stream_reply):
                body = stream_reply(
                    provider_message,
                    lambda delta: emit("DELTA", delta=delta),
                )
            else:
                body = self.provider.reply(provider_message)
            self.reply_poster(
                universe_endpoint=self.universe_endpoint,
                project_id=self.project_id,
                bridge_id=bridge_id,
                in_reply_to=message_id,
                kind="RESULT",
                body=body,
                idempotency_key=f"project-master-live-{message_id}",
                bridge_token=self.bridge_token,
                timeout_seconds=10.0,
            )
        except Exception as error:
            emit("FAILED", detail=f"{type(error).__name__}: {error}")
            self.store.fail(message_id, f"{type(error).__name__}: {error}")
            if self.completion_observer is not None:
                try:
                    self.completion_observer(
                        {
                            "status": "FAILED",
                            "project_id": self.project_id,
                            "message_id": message_id,
                            "provider_session_ref": self.provider.session_ref,
                            "reason": f"PROVIDER_REPLY_FAILED:{type(error).__name__}",
                        }
                    )
                except Exception:
                    pass
            self._active_bridge_id = ""
            self._active_message_id = ""
            return
        emit("COMPLETED")
        self.store.complete(message_id)
        if self.completion_observer is not None:
            try:
                completion = {
                    "status": "COMPLETED",
                    "project_id": self.project_id,
                    "message_id": message_id,
                    "provider_session_ref": self.provider.session_ref,
                    "runtime_context": provider_message.get("runtime_context", {}),
                    "work_statuses": self._drain_work_statuses(),
                }
                self._last_completion = completion
                self._last_completion_at = time.monotonic()
                self.completion_observer(completion)
            except Exception:
                pass
        self._active_bridge_id = ""
        self._active_message_id = ""

    def _drain_work_statuses(self) -> list[dict[str, Any]]:
        """Return only redacted Git Trace2 milestones from the provider host."""

        reader = getattr(self.provider, "drain_work_statuses", None)
        if not callable(reader):
            return []
        try:
            raw_statuses = reader()
        except Exception:
            return []
        if not isinstance(raw_statuses, list):
            return []
        statuses: list[dict[str, Any]] = []
        for raw in raw_statuses:
            if not isinstance(raw, Mapping):
                continue
            if str(raw.get("schema") or "") != "universe.git-trace2-work-status.v1":
                continue
            operation = str(raw.get("operation") or "").upper()
            state = str(raw.get("state") or "").upper()
            exit_code = raw.get("exit_code")
            if operation not in {"COMMIT", "PUSH"} or state not in {"COMPLETED", "FAILED"}:
                continue
            if not isinstance(exit_code, int):
                continue
            status = {
                "schema": "universe.git-trace2-work-status.v1",
                "source": "GIT_TRACE2",
                "operation": operation,
                "state": state,
                "exit_code": exit_code,
            }
            for key in ("commit_sha", "short_sha", "branch", "remote"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    status[key] = value.strip()
            statuses.append(status)
        return statuses

    def _process_room_event(self, job: Mapping[str, Any]) -> None:
        binding = job["binding"]
        event = job["event"]
        message = event["message"]
        room_id = _text(event.get("room_id"), "event.room_id")
        room_event_id = _text(event.get("room_event_id"), "event.room_event_id")
        binding_id = _text(binding.get("binding_id"), "binding.binding_id")
        bridge_id = _text(job.get("bridge_id"), "bridge_id")
        self._active_bridge_id = bridge_id
        self._active_message_id = room_event_id
        accepted = False

        def observe(event_type: str, **values: Any) -> None:
            if self.room_event_observer is None:
                return
            self.room_event_observer(
                {
                    "event": event_type,
                    "project_id": self.project_id,
                    "room_id": room_id,
                    "room_event_id": room_event_id,
                    "binding_id": binding_id,
                    "provider_session_ref": self.provider.session_ref,
                    **values,
                }
            )

        def accept_once() -> None:
            nonlocal accepted
            if accepted:
                return
            accepted = True
            observe("DELIVERY_ACCEPTED")

        try:
            runtime_context: Mapping[str, Any] = {}
            if message.get("author_role") == "USER":
                observe_room_event = getattr(
                    self.surface_observer,
                    "observe_room_event",
                    None,
                )
                if not callable(observe_room_event):
                    raise ProjectMasterHostError(
                        "PROJECT_COMMANDER_ROOM_OBSERVER_UNAVAILABLE"
                    )
                runtime_context = _runtime_context(observe_room_event(event))
            provider_message = {
                "schema": "universe.native-room-input.v1",
                "message_id": room_event_id,
                "kind": "ROOM_MESSAGE",
                "sender": message.get("author_role"),
                "body": message.get("body_text"),
                "runtime_context": dict(runtime_context),
                "room_context": {
                    "room_id": room_id,
                    "room_sequence": event.get("room_sequence"),
                    "correlation_id": event.get("correlation_id"),
                },
            }
            if self.retrieval_context_resolver is not None:
                retrieval = self.retrieval_context_resolver(
                    self.project_id, provider_message
                )
                if not isinstance(retrieval, Mapping):
                    raise ProjectMasterHostError(
                        "PROJECT_RETRIEVAL_CONTEXT_UNAVAILABLE"
                    )
                provider_message["retrieval_context"] = dict(retrieval)
            governance_context = self._governance_context_for_message()
            if governance_context is not None:
                provider_message["governance_context"] = governance_context
            stream_reply = getattr(self.provider, "reply_stream", None)

            def on_delta(delta: str) -> None:
                accept_once()
                observe("DELTA", delta=str(delta))

            if callable(stream_reply):
                body = stream_reply(provider_message, on_delta)
            else:
                body = self.provider.reply(provider_message)
            accept_once()
            observe("COMPLETED", body=str(body))
        except Exception as error:
            observe(
                "FAILED",
                reason=f"{type(error).__name__}: {error}",
                delivery_status="UNCERTAIN",
            )
        finally:
            self._active_bridge_id = ""
            self._active_message_id = ""

    def _governance_context_for_message(self) -> dict[str, Any] | None:
        """Return the startup-selected context without reopening Release DB."""
        if self.governance_context is not None:
            return dict(self.governance_context)
        if self.governance_context_resolver is None:
            return None
        resolved = self.governance_context_resolver(self.project_id)
        if not isinstance(resolved, Mapping):
            raise ProjectMasterHostError("PROJECT_GOVERNANCE_CONTEXT_UNAVAILABLE")
        status = str(resolved.get("status") or "").strip().upper()
        if status == "SELECTED":
            return dict(resolved)
        if status == "ABSENT":
            return None
        raise ProjectMasterHostError("PROJECT_GOVERNANCE_CONTEXT_UNAVAILABLE")

    @staticmethod
    def _runtime_observation(provider: MasterProvider) -> dict[str, Any]:
        observer = getattr(provider, "runtime_observation", None)
        if callable(observer):
            observed = observer()
            if isinstance(observed, Mapping):
                return dict(observed)
        return {
            "schema": "universe.provider-runtime-observation.v1",
            "provider": "UNKNOWN",
            "session_ref": str(getattr(provider, "session_ref", "UNKNOWN")),
            "state": str(getattr(provider, "connection_state", "UNKNOWN")),
            "quota_state": "UNKNOWN",
            "usage": {},
        }

    def idle_completion(self, idle_seconds: float) -> Mapping[str, Any] | None:
        if (
            self._last_completion is None
            or time.monotonic() - self._last_completion_at < idle_seconds
        ):
            return None
        return dict(self._last_completion)

    def _request_permission(self, request: Mapping[str, Any]) -> str | None:
        request_id = _text(request.get("request_id"), "request_id")
        options = request.get("options")
        if (
            not self._active_bridge_id
            or not self._active_message_id
            or not isinstance(options, list)
        ):
            raise ProjectMasterHostError("AGENT_PERMISSION_CONTEXT_UNAVAILABLE")
        option_ids = {
            _text(option.get("optionId"), "option.optionId")
            for option in options
            if isinstance(option, Mapping)
        }
        waiter = {
            "event": threading.Event(),
            "options": option_ids,
            "option_id": None,
        }
        with self._permission_lock:
            if request_id in self._permission_waiters:
                raise ProjectMasterHostError("AGENT_PERMISSION_REQUEST_DUPLICATE")
            self._permission_waiters[request_id] = waiter
        try:
            self.permission_poster(
                universe_endpoint=self.universe_endpoint,
                project_id=self.project_id,
                bridge_id=self._active_bridge_id,
                in_reply_to=self._active_message_id,
                permission=request,
                bridge_token=self.bridge_token,
                timeout_seconds=5.0,
            )
            if not waiter["event"].wait(600):
                return None
            selected = waiter["option_id"]
            return str(selected) if selected else None
        finally:
            with self._permission_lock:
                self._permission_waiters.pop(request_id, None)


class LiveProjectMasterBridgeHost(ProjectMasterBridgeHost):
    _worker: ProjectMasterConversationWorker
    _coordinator: CommanderSurfaceObserver

    def __init__(
        self,
        project_root: Path,
        token: str,
        inbox_ref: str,
        worker: ProjectMasterConversationWorker,
        coordinator: CommanderSurfaceObserver,
    ) -> None:
        super().__init__(project_root, token, inbox_ref, require_inbox=False)
        object.__setattr__(self, "_worker", worker)
        object.__setattr__(self, "_coordinator", coordinator)

    def record(self, envelope: Any) -> dict[str, Any]:
        normalized = normalize_bridge_envelope(envelope)
        accepted = self._worker.submit(normalized)
        return {
            "schema": PROJECT_MASTER_HOST_SCHEMA,
            "status": "ACCEPTED" if accepted else "ALREADY_ACCEPTED",
            "bridge_id": normalized["bridge_id"],
            "project_id": normalized["project_id"],
            "message_id": normalized["message"]["message_id"],
            "master_session_ref": normalized["master_session_ref"],
            "accepted_at": utc_now(),
            "repository_write": False,
        }

    def apply_seed_assets(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "project_id",
            "proposal",
            "approval",
        }:
            raise ProjectMasterBridgeError("PROJECT_SEED_ASSET_APPLY_REQUEST_INVALID")
        gateway = self._coordinator
        if not callable(getattr(gateway, "apply_file", None)):
            raise ProjectMasterBridgeError(
                "PROJECT_SEED_ASSET_MUTATION_GATEWAY_UNAVAILABLE"
            )
        try:
            return apply_project_seed_asset_proposal(
                project_root=self.project_root,
                project_id=_text(request.get("project_id"), "project_id"),
                proposal=request.get("proposal"),
                approval=request.get("approval"),
                mutation_gateway=gateway,
            )
        except (ProjectSeedAssetError, ProjectMasterHostError) as error:
            raise ProjectMasterBridgeError(str(error)) from error

    def apply_integration_assets(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "project_id",
            "proposal",
            "approval",
        }:
            raise ProjectMasterBridgeError(
                "PROJECT_INTEGRATION_APPLY_REQUEST_INVALID"
            )
        gateway = self._coordinator
        if not callable(getattr(gateway, "apply_file", None)):
            raise ProjectMasterBridgeError(
                "PROJECT_INTEGRATION_MUTATION_GATEWAY_UNAVAILABLE"
            )
        try:
            return apply_project_integration_proposal(
                project_root=self.project_root,
                project_id=_text(request.get("project_id"), "project_id"),
                proposal=request.get("proposal"),
                approval=request.get("approval"),
                mutation_gateway=gateway,
            )
        except (ProjectIntegrationApplyError, ProjectMasterHostError) as error:
            raise ProjectMasterBridgeError(str(error)) from error

    def apply_skill_plan(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "project_id",
            "handoff",
            "approval",
        }:
            raise ProjectMasterBridgeError("PROJECT_SKILL_PLAN_APPLY_REQUEST_INVALID")
        try:
            context = build_project_skill_plan_context(
                project_id=_text(request.get("project_id"), "project_id"),
                handoff=request.get("handoff"),
                approval=request.get("approval"),
            )
            binding_proposal = build_project_skill_binding_proposal(
                project_root=self.project_root,
                context=context,
            )
            stored, stored_proposal, created = (
                self._worker.store.apply_skill_plan_context(
                    context,
                    binding_proposal,
                )
            )
            receipt = project_skill_plan_receipt(stored)
            receipt["binding_proposal"] = stored_proposal
            receipt["idempotent_replay"] = not created
            return receipt
        except (
            ProjectSkillBindingError,
            ProjectSkillPlanApplyError,
            ProjectMasterHostError,
        ) as error:
            raise ProjectMasterBridgeError(str(error)) from error

    def create_approved_descendant_task_frame(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "primary_proposal",
            "governance_approval",
            "source_work",
            "task_frame",
        }:
            raise ProjectMasterBridgeError(
                "APPROVED_DESCENDANT_TASK_FRAME_REQUEST_INVALID"
            )
        gateway = self._coordinator
        create = getattr(gateway, "create_approved_descendant_task_frame", None)
        if not callable(create):
            raise ProjectMasterBridgeError(
                "APPROVED_DESCENDANT_TASK_FRAME_GATEWAY_UNAVAILABLE"
            )
        try:
            return dict(
                create(
                    primary_proposal=request["primary_proposal"],
                    governance_approval=request["governance_approval"],
                    source_work=request["source_work"],
                    task_frame=request["task_frame"],
                )
            )
        except ProjectMasterHostError as error:
            raise ProjectMasterBridgeError(str(error)) from error

    def create_instruction_authorized_task_frame(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "proposal_reference",
            "task_frame",
        }:
            raise ProjectMasterBridgeError(
                "INSTRUCTION_TASK_FRAME_REQUEST_INVALID"
            )
        create = getattr(self._coordinator, "create_instruction_authorized_task_frame", None)
        if not callable(create):
            raise ProjectMasterBridgeError(
                "INSTRUCTION_TASK_FRAME_GATEWAY_UNAVAILABLE"
            )
        try:
            return dict(
                create(
                    proposal_reference=request["proposal_reference"],
                    task_frame=request["task_frame"],
                )
            )
        except ProjectMasterHostError as error:
            raise ProjectMasterBridgeError(str(error)) from error

    def run_instruction_authorized_task_frame(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "task_frame_id",
            "primary_proposal_id",
            "primary_proposal_digest",
        }:
            raise ProjectMasterBridgeError(
                "INSTRUCTION_TASK_FRAME_RUN_REQUEST_INVALID"
            )
        run = getattr(self._coordinator, "run_approved_descendant_task_frame", None)
        if not callable(run):
            raise ProjectMasterBridgeError(
                "INSTRUCTION_TASK_FRAME_RUN_GATEWAY_UNAVAILABLE"
            )
        try:
            return dict(
                run(
                    task_frame_id=request["task_frame_id"],
                    primary_proposal_id=request["primary_proposal_id"],
                    primary_proposal_digest=request["primary_proposal_digest"],
                    approval_evidence_ref=None,
                )
            )
        except ProjectMasterHostError as error:
            raise ProjectMasterBridgeError(str(error)) from error

    def run_approved_descendant_task_frame(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "task_frame_id",
            "primary_proposal_id",
            "primary_proposal_digest",
            "approval_evidence_ref",
        }:
            raise ProjectMasterBridgeError(
                "APPROVED_DESCENDANT_TASK_FRAME_RUN_REQUEST_INVALID"
            )
        run = getattr(self._coordinator, "run_approved_descendant_task_frame", None)
        if not callable(run):
            raise ProjectMasterBridgeError(
                "APPROVED_DESCENDANT_TASK_FRAME_RUN_GATEWAY_UNAVAILABLE"
            )
        try:
            return dict(
                run(
                    task_frame_id=request["task_frame_id"],
                    primary_proposal_id=request["primary_proposal_id"],
                    primary_proposal_digest=request["primary_proposal_digest"],
                    approval_evidence_ref=request["approval_evidence_ref"],
                )
            )
        except ProjectMasterHostError as error:
            raise ProjectMasterBridgeError(str(error)) from error


@dataclass
class ResidentProjectMasterHandle:
    project_id: str
    project_root: Path
    provider: str
    model: str
    effort: str
    session_ref: str
    endpoint: str
    credential_env: str
    bridge_server: ProjectMasterBridgeHttpServer
    worker: ProjectMasterConversationWorker
    coordinator: CommanderSurfaceObserver
    thread: threading.Thread
    bridge_id: str = ""
    governance_context_key: str = "ABSENT"
    session_supervisor: SessionSupervisorStore | None = None
    supervisor_session_id: str | None = None
    supervisor_lease_token: str | None = None
    supervisor_lease_version: int | None = None
    supervisor_process_identity: dict[str, Any] | None = None

    def close(self) -> None:
        try:
            self.bridge_server.shutdown()
            self.bridge_server.server_close()
            self.worker.close()
            close_coordinator = getattr(self.coordinator, "close", None)
            if callable(close_coordinator):
                close_coordinator()
            self.thread.join(timeout=5)
            os.environ.pop(self.credential_env, None)
        finally:
            if (
                self.session_supervisor is not None
                and self.supervisor_session_id
                and self.supervisor_lease_token
                and self.supervisor_lease_version is not None
                and self.supervisor_process_identity is not None
            ):
                try:
                    self.session_supervisor.mark_lease_stale(
                        self.supervisor_session_id,
                        self.supervisor_process_identity,
                        lease_token=self.supervisor_lease_token,
                        expected_lease_version=self.supervisor_lease_version,
                        reason="RESIDENT_HANDLE_CLOSED",
                    )
                except SessionSupervisorError:
                    # A concurrent close or replacement may have advanced the
                    # lease. The provider is already being shut down; do not
                    # mask that lifecycle result with a stale cleanup attempt.
                    pass


class ResidentProjectMasterHostManager:
    def __init__(
        self,
        *,
        universe_endpoint: str,
        bridge_registrar: BridgeRegistrar,
        session_supervisor: SessionSupervisorStore | None = None,
        continuity_coordinator: ContinuitySaver | None = None,
        provider_factory: Callable[
            [Path, str, ProjectMasterSessionStore], MasterProvider
        ]
        | None = None,
        provider_resolver: Callable[[str], str] | None = None,
        model_resolver: Callable[[str, str], str] | None = None,
        effort_resolver: Callable[[str, str], str] | None = None,
        coordinator_factory: Callable[[Path, str, str], CommanderSurfaceObserver]
        | None = None,
        governance_context_resolver: GovernanceContextResolver | None = None,
        retrieval_context_resolver: RetrievalContextResolver | None = None,
        release_source_binding_resolver: GovernanceContextResolver | None = None,
        completion_observer: Callable[[Mapping[str, Any]], None] | None = None,
        room_event_observer: NativeRoomObserver | None = None,
    ) -> None:
        self.universe_endpoint = universe_endpoint.rstrip("/")
        self.bridge_registrar = bridge_registrar
        self.session_supervisor = session_supervisor
        self.continuity_coordinator = continuity_coordinator
        self.provider_factory = provider_factory
        self.provider_resolver = provider_resolver or (lambda _project_id: "GROK")
        self.model_resolver = model_resolver or (lambda _project_id, _provider: "")
        self.effort_resolver = effort_resolver or (
            lambda _project_id, _provider: "AUTO"
        )
        self.coordinator_factory = coordinator_factory or self._default_coordinator
        self.governance_context_resolver = governance_context_resolver
        self.retrieval_context_resolver = retrieval_context_resolver
        self.release_source_binding_resolver = release_source_binding_resolver
        self.completion_observer = completion_observer
        self.room_event_observer = room_event_observer
        self._handles: dict[str, ResidentProjectMasterHandle] = {}
        self._lock = threading.RLock()

    def ensure(
        self,
        project: Mapping[str, Any],
        *,
        session_action: str = "RESUME",
    ) -> dict[str, Any]:
        project_id = _text(project.get("project_id"), "project.project_id")
        normalized_action = normalize_session_action(session_action)
        force_new_session = normalized_action == "NEW"
        project_root = (
            Path(_text(project.get("project_root"), "project.project_root"))
            .expanduser()
            .resolve(strict=True)
        )
        if self.release_source_binding_resolver is not None:
            release_binding = self.release_source_binding_resolver(project_id)
            if (
                not isinstance(release_binding, Mapping)
                or str(release_binding.get("status") or "").upper() != "SELECTED"
            ):
                raise ProjectMasterHostError("PROJECT_RELEASE_SELECTION_REQUIRED")
        selected_provider = _provider(self.provider_resolver(project_id))
        selected_model = str(
            self.model_resolver(project_id, selected_provider) or ""
        ).strip()
        selected_effort = str(
            self.effort_resolver(project_id, selected_provider) or "AUTO"
        ).strip().upper()
        store = ProjectMasterSessionStore(
            _default_state_db(project_id),
            project_id,
            session_supervisor=self.session_supervisor,
            requested_mode="MASTER",
        )
        selected_session_ref = (
            None
            if force_new_session
            else store.session_ref_for(selected_provider)
        )
        governance_context: dict[str, Any] | None = None
        governance_context_key = "ABSENT"
        if self.governance_context_resolver is not None:
            resolved = self.governance_context_resolver(project_id)
            if not isinstance(resolved, Mapping):
                raise ProjectMasterHostError(
                    "PROJECT_GOVERNANCE_CONTEXT_UNAVAILABLE"
                )
            status = str(resolved.get("status") or "").strip().upper()
            if status == "SELECTED":
                governance_context = dict(resolved)
                governance_context_key = ":".join(
                    [
                        "SELECTED",
                        str(resolved.get("release_id") or "UNKNOWN"),
                        str(resolved.get("source_commit") or "UNKNOWN"),
                        str(resolved.get("selector_digest") or "UNKNOWN"),
                    ]
                )
            elif status != "ABSENT":
                raise ProjectMasterHostError(
                    "PROJECT_GOVERNANCE_CONTEXT_UNAVAILABLE"
                )
        with self._lock:
            handle = self._handles.get(project_id)
            if (
                handle is not None
                and handle.thread.is_alive()
                and not force_new_session
                and handle.provider == selected_provider
                and handle.model == selected_model
                and handle.effort == selected_effort
                and (
                    not selected_session_ref
                    or _same_provider_session_ref(
                        selected_provider,
                        handle.session_ref,
                        selected_session_ref,
                    )
                )
                and handle.governance_context_key == governance_context_key
            ):
                try:
                    provider_available = self._refresh_provider_process_lease(handle)
                except ProjectMasterHostError as error:
                    if not _is_provider_process_unavailable(error):
                        raise
                    provider_available = False
                except BaseException as error:
                    if not _is_provider_process_unavailable(error):
                        raise
                    if self.session_supervisor is not None:
                        self.session_supervisor.sweep_stale_live_sessions()
                    provider_available = False
                if provider_available:
                    setattr(handle.worker.provider, "connection_state", "REUSED")
                    return {
                        "status": "RESIDENT",
                        "project_id": project_id,
                        "provider": selected_provider,
                        "endpoint": handle.endpoint,
                        "session_connection": self._handle_connection(handle),
                    }
                self._save_handle_continuity(handle, "PROVIDER_PROCESS_RESTART")
                if self.session_supervisor is not None:
                    self.session_supervisor.sweep_stale_live_sessions()
                handle.close()
                self._handles.pop(project_id, None)
                handle = None
            if handle is not None:
                self._save_handle_continuity(
                    handle,
                    "NEW_SESSION" if force_new_session else "PROVIDER_SWITCH",
                )
                handle.close()
                self._handles.pop(project_id, None)

            credential_env = _managed_credential_env(project_id)
            os.environ[credential_env] = secrets.token_urlsafe(32)
            provider = (
                self.provider_factory(project_root, project_id, store)
                if self.provider_factory is not None
                else self._default_provider(
                    selected_provider,
                    project_root,
                    project_id,
                    store,
                    model=selected_model,
                    effort=selected_effort,
                    new_session=force_new_session,
                )
            )
            try:
                # A fresh Project has no provider coordinate yet. Reserve only
                # the persistent node/mode slot; the provider hook replaces
                # the UNKNOWN binding with its exact vendor session later.
                store.ensure_supervisor_session(
                    selected_provider, new_session=force_new_session
                )
                bind_permission = getattr(provider, "set_permission_requester", None)
                if callable(bind_permission):
                    bind_permission(self._permission_before_worker)
                prepare_provider = getattr(provider, "prepare_session", None)
                if callable(prepare_provider):
                    prepare_provider()
                # A provider adapter may create or resume its vendor session
                # during preparation.  Persist that exact coordinate before
                # the lease and Room connection are reported, so Room binding
                # never has to infer an anchor from title, workspace, or mode.
                store.observe_provider_session(selected_provider, provider.session_ref)
                coordinator = self.coordinator_factory(
                    project_root,
                    project_id,
                    provider.session_ref,
                )
                preparation = coordinator.prepare()
                if self.session_supervisor is not None:
                    store.observe_current_anchor(
                        _mode_current_anchor_ref(preparation)
                    )
            except Exception:
                close_provider = getattr(provider, "close", None)
                if callable(close_provider):
                    close_provider()
                os.environ.pop(credential_env, None)
                raise
            worker = ProjectMasterConversationWorker(
                provider=provider,
                store=store,
                universe_endpoint=self.universe_endpoint,
                project_id=project_id,
                bridge_token=os.environ[credential_env],
                surface_observer=coordinator,
                completion_observer=(
                    lambda event, root=project_root, owner=coordinator: self._observe_completion(
                        root,
                        event,
                        runtime_coordinate=self._continuity_coordinate(owner),
                    )
                ),
                # Resolve once in ensure(); the worker must not reopen Release DB
                # for every user or room message.
                governance_context_resolver=None,
                governance_context=governance_context,
                retrieval_context_resolver=self.retrieval_context_resolver,
                room_event_observer=self.room_event_observer,
            )
            host = LiveProjectMasterBridgeHost(
                project_root,
                os.environ[credential_env],
                ".ai/inbox/MASTER",
                worker,
                coordinator,
            )
            server = ProjectMasterBridgeHttpServer(("127.0.0.1", 0), host)
            endpoint = f"http://127.0.0.1:{server.server_port}"
            thread = threading.Thread(
                target=server.serve_forever,
                name=f"resident-project-master-{project_id}",
                daemon=True,
            )
            thread.start()
            handle = ResidentProjectMasterHandle(
                project_id=project_id,
                project_root=project_root,
                provider=selected_provider,
                model=selected_model,
                effort=selected_effort,
                session_ref=provider.session_ref,
                endpoint=endpoint,
                credential_env=credential_env,
                bridge_server=server,
                worker=worker,
                coordinator=coordinator,
                thread=thread,
                governance_context_key=governance_context_key,
                session_supervisor=self.session_supervisor,
            )
            try:
                lease = self._register_provider_process_lease(
                    project_id=project_id,
                    provider=provider,
                    endpoint=endpoint,
                    handshake_token=os.environ[credential_env],
                )
                if lease is not None:
                    handle.supervisor_session_id = lease["supervisor_session_id"]
                    handle.supervisor_lease_token = lease["lease_token"]
                    handle.supervisor_lease_version = lease["lease_version"]
                    handle.supervisor_process_identity = lease["process_identity"]
            except Exception:
                handle.close()
                raise
            self._handles[project_id] = handle
            try:
                bridge, _ = self.bridge_registrar(
                    project_id,
                    {
                        "endpoint": endpoint,
                        "credential_env": credential_env,
                        "master_session_ref": provider.session_ref,
                        "binding_evidence_ref": (
                            f"universe://resident-project-master/{project_id}/"
                            f"{provider.session_ref}"
                        ),
                    },
                )
                registered_bridge_id = bridge.get("bridge_id")
                if isinstance(registered_bridge_id, str) and registered_bridge_id:
                    handle.bridge_id = registered_bridge_id
                worker.start(
                    recovery_bridge_id=registered_bridge_id
                    if isinstance(registered_bridge_id, str)
                    and registered_bridge_id
                    else None,
                    recovery_session_ref=provider.session_ref
                    if isinstance(registered_bridge_id, str)
                    and registered_bridge_id
                    else None,
                )
            except Exception:
                self._handles.pop(project_id, None)
                handle.close()
                raise
            return {
                "status": "STARTED",
                "project_id": project_id,
                "provider": selected_provider,
                "endpoint": endpoint,
                "bridge": bridge,
                "session_preparation": preparation,
                "session_connection": self._handle_connection(handle),
            }

    def _refresh_provider_process_lease(
        self, handle: ResidentProjectMasterHandle
    ) -> bool:
        """Reconcile a resident handle after maintenance marked its lease stale."""

        if self.session_supervisor is None:
            return True
        if not _provider_runtime_process_available(handle.worker.provider):
            return False
        resolver = getattr(handle.worker.provider, "supervisor_process_identity", None)
        if not callable(resolver):
            if self.provider_factory is not None:
                return True
            raise ProjectMasterHostError(
                "PROJECT_MASTER_PROCESS_IDENTITY_UNAVAILABLE"
            )
        handshake_token = os.environ.get(handle.credential_env)
        if not handshake_token:
            raise ProjectMasterHostError(
                "PROJECT_MASTER_PROCESS_HANDSHAKE_UNAVAILABLE"
            )
        try:
            identity = resolver(handle.endpoint, handshake_token)
            current = (
                self.session_supervisor.get_session(handle.supervisor_session_id)
                if handle.supervisor_session_id
                else None
            )
        except ProjectMasterHostError as error:
            if _is_provider_process_unavailable(error):
                return False
            raise
        except ClaudeResidentError as error:
            if _is_provider_process_unavailable(error):
                return False
            raise ProjectMasterHostError(str(error)) from error
        except SessionSupervisorError as error:
            raise ProjectMasterHostError(str(error)) from error
        except BaseException as error:  # provider adapters may wrap liveness errors
            if _is_provider_process_unavailable(error):
                return False
            raise
        if not isinstance(identity, Mapping):
            raise ProjectMasterHostError(
                "PROJECT_MASTER_PROCESS_IDENTITY_INVALID"
            )
        current_lease = current.get("process_lease") if isinstance(current, Mapping) else None
        if (
            isinstance(current_lease, Mapping)
            and current_lease.get("lease_state") == "OWNED"
            and handle.supervisor_lease_token
            and handle.supervisor_lease_version
            == int(current_lease.get("lease_version", -1))
            and _same_process_identity(
                handle.supervisor_process_identity, identity
            )
        ):
            return True
        lease = self._register_provider_process_lease(
            project_id=handle.project_id,
            provider=handle.worker.provider,
            endpoint=handle.endpoint,
            handshake_token=handshake_token,
        )
        if lease is None:
            return True
        handle.supervisor_session_id = lease["supervisor_session_id"]
        handle.supervisor_lease_token = lease["lease_token"]
        handle.supervisor_lease_version = lease["lease_version"]
        handle.supervisor_process_identity = lease["process_identity"]
        return True

    def submit_room_event(
        self,
        project_id: str,
        binding: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> bool:
        normalized = _text(project_id, "project_id")
        with self._lock:
            handle = self._handles.get(normalized)
        if handle is None or not handle.thread.is_alive():
            raise ProjectMasterHostError("PROJECT_MASTER_NATIVE_CONTROL_UNAVAILABLE")
        if str(binding.get("provider") or "").upper() != handle.provider:
            raise ProjectMasterHostError("PROJECT_MASTER_NATIVE_PROVIDER_MISMATCH")
        if not _same_provider_session_ref(
            handle.provider,
            binding.get("provider_session_ref"),
            handle.worker.provider.session_ref,
        ):
            raise ProjectMasterHostError("PROJECT_MASTER_NATIVE_SESSION_MISMATCH")
        if not handle.bridge_id:
            raise ProjectMasterHostError("PROJECT_MASTER_BRIDGE_ID_UNAVAILABLE")
        return handle.worker.submit_room_event(
            binding=binding,
            event=event,
            bridge_id=handle.bridge_id,
        )

    def _register_provider_process_lease(
        self,
        *,
        project_id: str,
        provider: MasterProvider,
        endpoint: str,
        handshake_token: str,
    ) -> dict[str, Any] | None:
        if self.session_supervisor is None:
            return None
        if not _provider_runtime_process_available(provider):
            raise ProjectMasterHostError("PROJECT_MASTER_PROCESS_UNAVAILABLE")
        resolver = getattr(provider, "supervisor_process_identity", None)
        if not callable(resolver):
            if self.provider_factory is not None:
                return None
            raise ProjectMasterHostError(
                "PROJECT_MASTER_PROCESS_IDENTITY_UNAVAILABLE"
            )
        try:
            self.session_supervisor.sweep_stale_live_sessions()
            sessions = self.session_supervisor.list_sessions(
                node=project_id, mode="MASTER"
            )
            selected = next(
                (item for item in sessions if item.get("is_default")),
                None,
            )
            if not isinstance(selected, Mapping):
                raise ProjectMasterHostError(
                    "PROJECT_MASTER_SUPERVISOR_SESSION_UNAVAILABLE"
                )
            supervisor_session_id = _text(
                selected.get("session_id"),
                "supervisor_session_id",
            )
            identity = resolver(endpoint, handshake_token)
            if not isinstance(identity, Mapping):
                raise ProjectMasterHostError(
                    "PROJECT_MASTER_PROCESS_IDENTITY_INVALID"
                )
            current = self.session_supervisor.get_session(supervisor_session_id)
            lease = current.get("process_lease")
            expected_version = (
                int(lease.get("lease_version", 0))
                if isinstance(lease, Mapping)
                else 0
            )
            acquired = self.session_supervisor.acquire_lease(
                supervisor_session_id,
                dict(identity),
                expected_lease_version=expected_version,
                stop_capability=handshake_token,
            )
        except SessionSupervisorError as error:
            raise ProjectMasterHostError(str(error)) from error
        acquired_lease = acquired.get("lease")
        if not isinstance(acquired_lease, Mapping):
            raise ProjectMasterHostError(
                "PROJECT_MASTER_PROCESS_LEASE_RESULT_INVALID"
            )
        lease_identity = acquired_lease.get("process_identity")
        if not isinstance(lease_identity, Mapping):
            raise ProjectMasterHostError(
                "PROJECT_MASTER_PROCESS_LEASE_IDENTITY_MISSING"
            )
        return {
            "supervisor_session_id": supervisor_session_id,
            "lease_token": _text(acquired.get("lease_token"), "lease_token"),
            "lease_version": int(acquired_lease["lease_version"]),
            "process_identity": dict(identity),
        }

    def is_resident(self, project_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(_text(project_id, "project_id"))
            return handle is not None and handle.thread.is_alive()

    def connection_status(self, project_id: str) -> dict[str, Any]:
        normalized = _text(project_id, "project_id")
        with self._lock:
            handle = self._handles.get(normalized)
            if handle is not None and handle.thread.is_alive():
                return self._handle_connection(handle)
        database_path = _default_state_db(normalized)
        store = None
        if database_path.is_file():
            store = ProjectMasterSessionStore(
                database_path,
                normalized,
                session_supervisor=self.session_supervisor,
                requested_mode="MASTER",
            )
        provider = ""
        if store is not None:
            coordinate = store.last_provider_session()
            provider = str(coordinate.get("provider") or "") if coordinate else ""
        if not provider:
            try:
                provider = _provider(self.provider_resolver(normalized))
            except Exception:
                provider = ""
        model = self._model_for(normalized, provider)
        effort = self._effort_for(normalized, provider)
        return provider_session_connection(
            target_kind="PROJECT_MASTER",
            target_id=normalized,
            requested_mode="MASTER",
            store=store,
            resident=False,
            model_ref=model,
            effort=effort,
        )

    def rebind_working_directory(
        self,
        session_id: str,
        target_project: Mapping[str, Any],
        *,
        expected_version: Any,
    ) -> dict[str, Any]:
        if self.session_supervisor is None:
            raise ProjectMasterHostError("SESSION_SUPERVISOR_UNAVAILABLE")
        target_id = _text(target_project.get("project_id"), "project.project_id")
        target_root = (
            Path(_text(target_project.get("project_root"), "project.project_root"))
            .expanduser()
            .resolve(strict=True)
        )
        session = self.session_supervisor.get_session(session_id)
        try:
            requested_version = int(expected_version)
        except (TypeError, ValueError) as error:
            raise ProjectMasterHostError("SESSION_VERSION_INVALID") from error
        if requested_version < 0 or int(session.get("row_version", -1)) != requested_version:
            raise ProjectMasterHostError("SESSION_VERSION_CONFLICT")
        if str(session.get("mode") or "").upper() != "MASTER":
            raise ProjectMasterHostError("SESSION_CWD_REBIND_MASTER_REQUIRED")
        provider_ref = str(session.get("provider_session_ref") or "").strip()
        provider = str(session.get("provider") or "").strip().upper()
        if not provider_ref or not provider:
            raise ProjectMasterHostError("SESSION_CWD_REBIND_PROVIDER_UNAVAILABLE")
        source_id = str(session.get("current_project_id") or session.get("node") or "")
        if not source_id:
            raise ProjectMasterHostError("SESSION_CWD_REBIND_SOURCE_UNAVAILABLE")

        with self._lock:
            source_handle = self._handles.get(source_id)
            if source_handle is None or not source_handle.thread.is_alive():
                raise ProjectMasterHostError("SESSION_CWD_REBIND_RESIDENT_REQUIRED")
            if not _same_provider_session_ref(
                provider,
                source_handle.worker.provider.session_ref,
                provider_ref,
            ):
                raise ProjectMasterHostError("SESSION_CWD_REBIND_SESSION_MISMATCH")
            target_handle = self._handles.get(target_id)
            if target_id != source_id and target_handle is not None:
                raise ProjectMasterHostError("SESSION_CWD_TARGET_MASTER_BUSY")

        old_root = source_handle.project_root
        old_anchor = session.get("anchor_ref")
        if target_id == source_id:
            rebind = getattr(
                source_handle.worker.provider,
                "rebind_working_directory",
                None,
            )
            if not callable(rebind):
                raise ProjectMasterHostError("SESSION_CWD_REBIND_UNAVAILABLE")
            rebound = str(rebind(target_root))
            source_handle.project_root = Path(rebound)
            moved = self.session_supervisor.bind_current_location(
                session_id,
                project_id=target_id,
                node=target_id,
                mode="MASTER",
                anchor_ref=old_anchor,
                evidence_ref=f"universe://provider-cwd-rebind/{target_id}",
                expected_version=requested_version,
            )
            return {
                "status": "PROVIDER_WORKING_DIRECTORY_REBOUND",
                "project_id": target_id,
                "working_directory": rebound,
                "session": moved,
                "session_connection": self._handle_connection(source_handle),
            }

        prior_target_default = next(
            (
                item
                for item in self.session_supervisor.list_sessions(
                    node=target_id,
                    mode="MASTER",
                )
                if item["is_default"] and item["session_id"] != session_id
            ),
            None,
        )
        moved: dict[str, Any] | None = None
        try:
            moved = self.session_supervisor.bind_current_location(
                session_id,
                project_id=target_id,
                node=target_id,
                mode="MASTER",
                anchor_ref=old_anchor,
                evidence_ref=f"universe://provider-cwd-rebind/{target_id}",
                expected_version=requested_version,
            )
            if not self.stop(source_id):
                raise ProjectMasterHostError("SESSION_CWD_REBIND_RESIDENT_LOST")
            started = self.ensure(target_project)
        except Exception as error:
            rollback_version = (
                moved["row_version"]
                if isinstance(moved, Mapping)
                else requested_version
            )
            try:
                if isinstance(moved, Mapping):
                    self.session_supervisor.bind_current_location(
                        session_id,
                        project_id=source_id,
                        node=source_id,
                        mode="MASTER",
                        anchor_ref=old_anchor,
                        evidence_ref=(
                            f"universe://provider-cwd-rebind-rollback/{source_id}"
                        ),
                        expected_version=rollback_version,
                    )
                    if prior_target_default is not None:
                        self.session_supervisor.set_default(
                            prior_target_default["session_id"],
                            expected_pointer_version=0,
                        )
                self.ensure(
                    {"project_id": source_id, "project_root": str(old_root)}
                )
            except Exception as rollback_error:
                raise ProjectMasterHostError(
                    "SESSION_CWD_REBIND_ROLLBACK_FAILED"
                ) from rollback_error
            raise ProjectMasterHostError("SESSION_CWD_REBIND_FAILED") from error
        return {
            "status": "PROVIDER_WORKING_DIRECTORY_REBOUND",
            "project_id": target_id,
            "working_directory": str(target_root),
            "session": self.session_supervisor.get_session(session_id),
            "session_connection": started.get("session_connection"),
        }

    def _model_for(self, project_id: str, provider: str) -> str:
        if not provider:
            return ""
        try:
            return str(self.model_resolver(project_id, provider) or "").strip()
        except Exception:
            return ""

    def _effort_for(self, project_id: str, provider: str) -> str:
        if not provider:
            return "AUTO"
        try:
            return str(
                self.effort_resolver(project_id, provider) or "AUTO"
            ).strip().upper()
        except Exception:
            return "AUTO"

    def invalidate(self, project_id: str) -> None:
        normalized = _text(project_id, "project_id")
        with self._lock:
            handle = self._handles.pop(normalized, None)
        if handle is not None:
            self._save_handle_continuity(handle, "NORMAL_STOP")
            handle.close()

    def requeue_provider_start_timeouts(self, project_id: str) -> int:
        normalized = _text(project_id, "project_id")
        store = ProjectMasterSessionStore(
            _default_state_db(normalized),
            normalized,
            session_supervisor=self.session_supervisor,
            requested_mode="MASTER",
        )
        return store.requeue_provider_start_timeouts()

    def stop(self, project_id: str) -> bool:
        normalized = _text(project_id, "project_id")
        with self._lock:
            handle = self._handles.pop(normalized, None)
        if handle is None:
            return False
        self._save_handle_continuity(handle, "NORMAL_STOP")
        handle.close()
        return True

    def resolve_permission(
        self,
        project_id: str,
        request_id: str,
        option_id: str,
    ) -> bool:
        normalized = _text(project_id, "project_id")
        with self._lock:
            handle = self._handles.get(normalized)
        if handle is None or not handle.thread.is_alive():
            return False
        return handle.worker.resolve_permission(request_id, option_id)

    def close(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            self._save_handle_continuity(handle, "NORMAL_STOP")
            handle.close()

    def _save_project_completion(
        self,
        project_root: Path,
        event: Mapping[str, Any],
        trigger: str = "TASK_COMPLETED",
        runtime_coordinate: Mapping[str, Any] | None = None,
    ) -> None:
        if self.continuity_coordinator is None:
            return
        if event.get("status") == "FAILED":
            try:
                self.continuity_coordinator.mark_dirty_end(
                    project_root,
                    str(event.get("reason") or "PROJECT_MASTER_FAILED"),
                )
            except Exception:
                pass
            return
        bounded = {
            "project_id": event.get("project_id"),
            "message_id": event.get("message_id"),
            "provider_session_ref": event.get("provider_session_ref"),
            "runtime_context": event.get("runtime_context", {}),
        }
        self.continuity_coordinator.save(
            project_root=project_root,
            trigger=trigger,
            compressed_context=json.dumps(
                bounded,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            summary=f"Project Master state {event.get('message_id', 'UNKNOWN')}",
            runtime_coordinate=runtime_coordinate,
        )

    def _observe_completion(
        self,
        project_root: Path,
        event: Mapping[str, Any],
        *,
        runtime_coordinate: Mapping[str, Any] | None,
    ) -> None:
        self._save_project_completion(
            project_root,
            event,
            runtime_coordinate=runtime_coordinate,
        )
        if self.completion_observer is not None:
            self.completion_observer(event)

    def save_idle_sessions(self, idle_seconds: float) -> list[dict[str, Any]]:
        with self._lock:
            handles = list(self._handles.values())
        results: list[dict[str, Any]] = []
        for handle in handles:
            completion = handle.worker.idle_completion(idle_seconds)
            if completion is None:
                continue
            try:
                self._save_project_completion(
                    handle.project_root,
                    completion,
                    trigger="IDLE",
                    runtime_coordinate=self._continuity_coordinate(
                        handle.coordinator
                    ),
                )
            except Exception as error:
                results.append(
                    {
                        "project_id": handle.project_id,
                        "status": "FAILED",
                        "reason": type(error).__name__,
                    }
                )
            else:
                results.append(
                    {"project_id": handle.project_id, "status": "OBSERVED"}
                )
        return results

    def reconcile_residents(self) -> list[dict[str, Any]]:
        with self._lock:
            handles = list(self._handles.values())
        results: list[dict[str, Any]] = []
        for handle in handles:
            reason = ""
            if not handle.thread.is_alive():
                reason = "PROJECT_MASTER_BRIDGE_THREAD_EXITED"
            else:
                reconcile = getattr(handle.coordinator, "reconcile", None)
                if callable(reconcile) and reconcile() == "EXITED":
                    reason = "PROJECT_SESSION_RUNTIME_EXITED"
            if not reason:
                continue
            if self.continuity_coordinator is not None:
                try:
                    self.continuity_coordinator.mark_dirty_end(
                        handle.project_root,
                        reason,
                    )
                except Exception:
                    pass
            results.append(
                {
                    "project_id": handle.project_id,
                    "status": "DIRTY_END",
                    "reason": reason,
                }
            )
        return results

    def _save_handle_continuity(
        self, handle: ResidentProjectMasterHandle, trigger: str
    ) -> None:
        if self.continuity_coordinator is None:
            return
        provider = handle.worker.provider
        context = json.dumps(
            {
                "project_id": handle.project_id,
                "mode": str(getattr(provider, "requested_mode", "MASTER")),
                "provider": handle.provider,
                "provider_session_ref": provider.session_ref,
                "transition": trigger,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self.continuity_coordinator.save(
                project_root=handle.project_root,
                trigger=trigger,
                compressed_context=context,
                summary=f"{handle.project_id} MASTER {trigger.lower()}",
                runtime_coordinate=self._continuity_coordinate(handle.coordinator),
            )
        except Exception:
            return

    @staticmethod
    def _continuity_coordinate(
        owner: CommanderSurfaceObserver,
    ) -> Mapping[str, Any] | None:
        resolver = getattr(owner, "continuity_coordinate", None)
        if not callable(resolver):
            return None
        try:
            return resolver()
        except Exception:
            return None

    def _handle_connection(
        self,
        handle: ResidentProjectMasterHandle,
    ) -> dict[str, Any]:
        active = handle.worker.provider
        state = str(getattr(active, "connection_state", "")).strip().upper()
        connection = provider_session_connection(
            target_kind="PROJECT_MASTER",
            target_id=handle.project_id,
            requested_mode=str(getattr(active, "requested_mode", "MASTER")),
            store=handle.worker.store,
            resident=True,
            connection_state=state if state and state != "UNKNOWN" else None,
            model_ref=str(getattr(active, "model", "") or "").strip(),
            effort=str(getattr(active, "effort", "AUTO") or "AUTO").strip(),
        )
        connection["runtime_observation"] = (
            ProjectMasterConversationWorker._runtime_observation(active)
        )
        connection["session_anchor_ref"] = self._session_anchor_ref_for_connection(
            project_id=handle.project_id,
            provider=str(connection.get("last_provider") or ""),
            provider_session_ref=str(connection.get("last_session_ref") or ""),
        )
        context = handle.worker.governance_context
        if context is None:
            connection["governance_context"] = {"status": "ABSENT"}
        else:
            connection["governance_context"] = {
                "status": "SELECTED",
                "release_id": context.get("release_id"),
                "source_commit": context.get("source_commit"),
                "catalog_digest": context.get("catalog_digest"),
                "selector_digest": context.get("selector_digest"),
            }
        return connection

    def _session_anchor_ref_for_connection(
        self,
        *,
        project_id: str,
        provider: str,
        provider_session_ref: str,
    ) -> str:
        """Resolve only an exact Supervisor session identity to its anchor."""

        if self.session_supervisor is None or not provider or not provider_session_ref:
            return "UNKNOWN"
        candidates = [
            item
            for item in self.session_supervisor.list_sessions(
                node=project_id,
                mode="MASTER",
                include_hidden=True,
            )
            if str(item.get("provider") or "").upper() == provider.upper()
            and _same_provider_session_ref(
                provider,
                item.get("provider_session_ref"),
                provider_session_ref,
            )
        ]
        if len(candidates) != 1:
            return "UNKNOWN"
        return str(candidates[0].get("session_anchor_ref") or "UNKNOWN")

    @staticmethod
    def _default_provider(
        provider: str,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        model: str = "",
        effort: str = "AUTO",
        new_session: bool = False,
    ) -> MasterProvider:
        if provider == "GROK":
            return GrokProjectMasterRuntime(
                project_root,
                project_id,
                store,
                model=model,
                effort=effort,
                new_session=new_session,
            )
        if provider == "CODEX":
            return CodexProjectMasterRuntime(
                project_root,
                project_id,
                store,
                model=model,
                effort=effort,
                new_session=new_session,
            )
        if provider == "CLAUDE":
            return ClaudeProjectMasterRuntime(
                project_root,
                project_id,
                store,
                model=model,
                effort=effort,
                new_session=new_session,
            )
        raise ProjectMasterHostError("PROJECT_MASTER_PROVIDER_UNSUPPORTED")

    def _default_coordinator(
        self,
        project_root: Path,
        project_id: str,
        host_session_ref: str,
    ) -> CommanderSurfaceObserver:
        return ProjectModeCoordinator(
            project_root,
            project_id,
            host_session_ref,
            source_binding_resolver=(
                (lambda _root: self.release_source_binding_resolver(project_id))
                if self.release_source_binding_resolver is not None
                else None
            ),
            session_supervisor=self.session_supervisor,
        )

    @staticmethod
    def _permission_before_worker(_request: Mapping[str, Any]) -> str | None:
        raise ProjectMasterHostError("AGENT_PERMISSION_GATEWAY_NOT_READY")


def _resolve_grok() -> tuple[Path | None, dict[str, str], str]:
    resolved = resolve_host_tool("grok")
    if resolved is None:
        return None, {}, "UNKNOWN"
    return resolved.executable, dict(resolved.environment), resolved.model


def _resolve_codex() -> tuple[Path | None, dict[str, str], str]:
    resolved = resolve_host_tool("codex")
    if resolved is None:
        return None, {}, "UNKNOWN"
    return resolved.executable, dict(resolved.environment), resolved.model


def _resolve_claude() -> tuple[Path | None, dict[str, str], str]:
    resolved = resolve_host_tool("claude")
    if resolved is None:
        return None, {}, "UNKNOWN"
    return resolved.executable, dict(resolved.environment), resolved.model


def _required_host_executable(tool: str) -> Path:
    resolved = resolve_host_tool(tool)
    if resolved is None:
        raise ProjectMasterHostError(f"{tool.upper()}_HOST_TOOL_UNAVAILABLE")
    return resolved.executable


def _runtime_tmp() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    root = Path(base) / "Universe" / "runtime-tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _default_state_db(project_id: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "Universe" / "project-master-host" / f"{project_id}.sqlite"


def _default_room_participant_db(binding_id: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    digest = hashlib.sha256(binding_id.encode("utf-8")).hexdigest()[:24]
    return Path(base) / "Universe" / "room-participant-host" / f"{digest}.sqlite"


def _managed_credential_env(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16].upper()
    return f"UNIVERSE_MANAGED_MASTER_{digest}_TOKEN"


def _read_token(environment_name: str) -> str:
    token = os.environ.get(_text(environment_name, "token_env"))
    if not token:
        raise ProjectMasterHostError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
    return token


def _runtime_context(observation: Mapping[str, Any]) -> dict[str, str]:
    stored = observation.get("snapshot")
    snapshot = (
        stored.get("snapshot")
        if isinstance(stored, Mapping) and isinstance(stored.get("snapshot"), Mapping)
        else {}
    )
    coordinates = (
        snapshot.get("coordinates")
        if isinstance(snapshot.get("coordinates"), Mapping)
        else {}
    )
    return {
        "surface_observation_status": str(observation.get("status", "UNKNOWN")),
        "mode": str(observation.get("anchor_mode", coordinates.get("mode", "UNKNOWN"))),
        "mode_current_anchor": str(
            stored.get("anchor_id", "UNKNOWN")
            if isinstance(stored, Mapping)
            else "UNKNOWN"
        ),
        "commander_surface": str(coordinates.get("commander_surface", "UNKNOWN")),
        "observed_at": str(
            stored.get("observed_at", "UNKNOWN")
            if isinstance(stored, Mapping)
            else "UNKNOWN"
        ),
    }


def _mode_current_anchor_ref(preparation: Mapping[str, Any]) -> str:
    anchor = preparation.get("mode_current_anchor")
    stored = anchor.get("snapshot") if isinstance(anchor, Mapping) else None
    snapshot = (
        stored.get("snapshot")
        if isinstance(stored, Mapping) and isinstance(stored.get("snapshot"), Mapping)
        else None
    )
    anchor_ref = snapshot.get("anchor_id") if isinstance(snapshot, Mapping) else None
    if not isinstance(anchor_ref, str) or not anchor_ref.strip():
        raise ProjectMasterHostError("PROJECT_MASTER_ANCHOR_UNAVAILABLE")
    return anchor_ref.strip()


def _path_is_within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _approved_source_path(value: Any, field: str) -> Path:
    text = _text(value, field)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ProjectMasterHostError("APPROVED_SOURCE_PATH_NOT_ABSOLUTE")
    return candidate.resolve(strict=False)


def _absolute_paths_in_value(value: Any) -> set[Path]:
    """Extract declared absolute paths without assigning semantics to unknown scope keys."""

    values: list[Any] = [value]
    paths: set[Path] = set()
    while values:
        current = values.pop()
        if isinstance(current, Mapping):
            values.extend(current.values())
        elif isinstance(current, list):
            values.extend(current)
        elif isinstance(current, str):
            candidate = Path(current).expanduser()
            if candidate.is_absolute():
                paths.add(candidate.resolve(strict=False))
    return paths


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectMasterHostError(f"{field} must be non-empty text")
    return value.strip()


def _provider(value: Any) -> str:
    normalized = _text(value, "provider").upper()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ProjectMasterHostError("PROJECT_MASTER_PROVIDER_UNSUPPORTED")
    return normalized


def normalize_session_action(value: Any) -> str:
    """Normalize the explicit resume/new boundary used by session preparation."""

    normalized = str(value or "RESUME").strip().upper()
    if normalized not in {"RESUME", "NEW"}:
        raise ProjectMasterHostError("MODE_SESSION_ACTION_INVALID")
    return normalized


def _normalize_supervisor_endpoint(value: Any) -> str:
    raw = _text(value, "supervisor_endpoint")
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as error:
        raise ProjectMasterHostError("MODE_SESSION_SUPERVISOR_ENDPOINT_INVALID") from error
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ProjectMasterHostError("MODE_SESSION_SUPERVISOR_ENDPOINT_INVALID")
    return f"http://127.0.0.1:{port}"


def _same_process_identity(stored: Any, observed: Any) -> bool:
    if not isinstance(stored, Mapping) or not isinstance(observed, Mapping):
        return False
    return all(stored.get(field) == observed.get(field) for field in PROCESS_IDENTITY_FIELDS)


def _is_provider_process_unavailable(error: BaseException) -> bool:
    message = str(error).upper()
    return any(
        marker in message
        for marker in (
            "PROCESS_NOT_ALIVE",
            "PROCESS_IDENTITY_UNAVAILABLE",
        )
    )


def _provider_runtime_process_available(provider: Any) -> bool:
    observer = getattr(provider, "runtime_observation", None)
    if not callable(observer):
        return True
    try:
        observation = observer()
    except Exception:
        return True
    if not isinstance(observation, Mapping):
        return True
    state = str(observation.get("state") or "").strip().upper()
    return state not in {"STOPPED", "FAILED", "PROCESS_NOT_ALIVE"}


def _same_provider_session_ref(provider: str, left: Any, right: Any) -> bool:
    """Compare native session coordinates without changing their stored form."""

    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return _provider_session_identity(provider, left) == _provider_session_identity(
        provider,
        right,
    )


def _provider_session_identity(provider: str, value: str) -> str:
    """Remove only the known transport label for a Provider session reference."""

    normalized_provider = _provider(provider)
    normalized_value = value.strip()
    prefixes = {
        "CODEX": ("codex-app-server:",),
        "CLAUDE": ("claude-code:",),
        "GROK": ("grok-acp:", "grok-cli:"),
    }[normalized_provider]
    lowered = normalized_value.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            suffix = normalized_value[len(prefix) :].strip()
            if suffix:
                return suffix
    return normalized_value


def main() -> int:
    parser = argparse.ArgumentParser(description="Live local Project Master Host")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--universe-endpoint", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--inbox-ref", default=".ai/inbox/MASTER")
    parser.add_argument(
        "--provider", default="GROK", choices=sorted(SUPPORTED_PROVIDERS)
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--max-turns", default=8, type=int)
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--port", default=0, type=int)
    args = parser.parse_args()

    token = _read_token(args.token_env)
    state_db = args.state_db or _default_state_db(args.project_id)
    store = ProjectMasterSessionStore(state_db, args.project_id)
    if args.provider == "GROK":
        provider = GrokProjectMasterRuntime(
            args.project_root,
            args.project_id,
            store,
            model=args.model,
            max_turns=args.max_turns,
        )
    elif args.provider == "CODEX":
        provider = CodexProjectMasterRuntime(
            args.project_root,
            args.project_id,
            store,
        )
    else:
        provider = ClaudeProjectMasterRuntime(
            args.project_root,
            args.project_id,
            store,
            max_turns=args.max_turns,
        )
    coordinator = ProjectModeCoordinator(
        args.project_root,
        args.project_id,
        provider.session_ref,
    )
    preparation = coordinator.prepare()
    worker = ProjectMasterConversationWorker(
        provider=provider,
        store=store,
        universe_endpoint=args.universe_endpoint,
        project_id=args.project_id,
        bridge_token=token,
        surface_observer=coordinator,
    )
    host = LiveProjectMasterBridgeHost(
        args.project_root,
        token,
        args.inbox_ref,
        worker,
        coordinator,
    )
    server = ProjectMasterBridgeHttpServer(("127.0.0.1", args.port), host)
    worker.start()
    print(
        json.dumps(
            {
                "schema": PROJECT_MASTER_HOST_SCHEMA,
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "master_session_ref": provider.session_ref,
                "project_id": args.project_id,
                "provider": args.provider,
                "state_db": str(state_db),
                "session_preparation": preparation["status"],
                "status": "LISTENING",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        worker.close()
        coordinator.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
