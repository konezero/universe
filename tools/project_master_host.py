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
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from session_supervisor import SessionSupervisorError, SessionSupervisorStore
from uuid import uuid4

from agent_session_gateway import (
    AgentSessionError,
    CodexAppServerSession,
    GrokAcpSession,
    UniverseAcpGateway,
)
from claude_permission_bridge import ClaudePermissionBridge
from claude_permission_broker import ClaudePermissionBroker
from claude_resident_session import ClaudeResidentSession
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


class ProjectMasterHostError(RuntimeError):
    pass


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
SourceCommitResolver = Callable[[Path], str]
GovernanceContextResolver = Callable[[str], Mapping[str, Any]]
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
        native_runner: NativeRunner = run_native_cli,
        source_commit_resolver: SourceCommitResolver | None = None,
        session_supervisor: SessionSupervisorStore | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.host_session_ref = _text(host_session_ref, "host_session_ref")
        self.native_runner = native_runner
        self.source_commit_resolver = source_commit_resolver or self._git_head
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

    def prepare(self) -> Mapping[str, Any]:
        definition = self._master_definition()
        source_commit = self.source_commit_resolver(self.project_root)
        request = {
            "command": "BOOT",
            "source_state": "SOURCE_READY",
            "source_ref": (f"git-object-database://{self.project_id}@{source_commit}"),
            "source_commit": source_commit,
            "source_repository": str(self.project_root),
            "mode": "MASTER",
            "role": definition["role"],
            "scope": definition["scope"],
            "host_session_ref": self.host_session_ref,
            "anchor_snapshot_ref": "UNKNOWN",
            "host_executable_capability": "AVAILABLE",
            "mode_profile": definition["mode_profile"],
            "task_requirement": "NONE",
            "evidence_profile": "NONE",
        }
        result = self._invoke(
            ("prepare-session", "--repo-root", str(self.project_root)),
            request,
        )
        anchor = result.get("mode_current_anchor")
        anchor_status = anchor.get("status") if isinstance(anchor, Mapping) else None
        if result.get("status") != "SESSION_PREPARED" or anchor_status not in {
            "MODE_CURRENT_ANCHOR_CREATED",
            "MODE_CURRENT_ANCHOR_OBSERVED",
        }:
            raise ProjectMasterHostError("PROJECT_MASTER_SESSION_PREPARATION_FAILED")
        _mode_boot_binding(result, expected_mode="MASTER", expected_role="MASTER")
        self._prepared = dict(result)
        return result

    def observe(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        message_id = _text(message.get("message_id"), "message.message_id")
        result = self._invoke(
            (
                "mode-anchor",
                "observe-commander-input",
                "--repo-root",
                str(self.project_root),
            ),
            {
                "mode": "MASTER",
                "commander_surface": "UNIVERSE_UI",
                "evidence_ref": (f"universe://project-room/messages/{message_id}"),
            },
        )
        if result.get("status") != "COMMANDER_INPUT_OBSERVED":
            raise ProjectMasterHostError("PROJECT_COMMANDER_SURFACE_OBSERVATION_FAILED")
        return result

    def observe_room_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        room_id = _text(event.get("room_id"), "event.room_id")
        room_event_id = _text(event.get("room_event_id"), "event.room_event_id")
        message = event.get("message")
        if not isinstance(message, Mapping) or message.get("author_role") != "USER":
            raise ProjectMasterHostError("PROJECT_COMMANDER_ROOM_EVENT_INVALID")
        result = self._invoke(
            (
                "mode-anchor",
                "observe-commander-input",
                "--repo-root",
                str(self.project_root),
            ),
            {
                "mode": "MASTER",
                "commander_surface": "UNIVERSE_UI",
                "evidence_ref": (
                    f"universe://rooms/{room_id}/events/{room_event_id}"
                ),
            },
        )
        if result.get("status") != "COMMANDER_INPUT_OBSERVED":
            raise ProjectMasterHostError("PROJECT_COMMANDER_SURFACE_OBSERVATION_FAILED")
        return result

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

        source_ref = _text(
            primary_proposal.get("source_ref") or primary_proposal.get("request_ref"),
            "primary.source_ref",
        )
        task_summary_ref = _text(
            primary_proposal.get("request_ref"), "primary.request_ref"
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
            "candidate_source_ref": "NONE",
            "source_review_result": None,
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
                    "origin_governance_session_ref": "UNKNOWN",
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
            "turns": [
                {"turn_id": turn["turn_id"], "role": turn["role"]}
                for turn in normalized_frame["turns"]
            ],
            "repository_write": False,
        }

    @staticmethod
    def _sequential_declared_turns(turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Project the approved Boss/Worker order into Runtime dependencies.

        The approved execution plan carries Worker metadata, while the Runtime
        declaration needs explicit input edges.  Without those edges every
        turn becomes a root and the Runtime correctly rejects the frame.
        """

        declared: list[dict[str, Any]] = []
        previous_turn_id = ""
        root_seen = False
        for index, turn in enumerate(turns):
            turn_id = _text(turn.get("turn_id"), f"task_frame.turns[{index}].turn_id")
            role = _text(turn.get("role"), f"task_frame.turns[{index}].role").upper()
            if role == "BOSS":
                if root_seen or index != 0:
                    raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID")
                root_seen = True
                inputs: list[str] = []
            else:
                if not root_seen:
                    raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID")
                inputs = [previous_turn_id]
            declared.append(
                {
                    "turn_id": turn_id,
                    "role": role,
                    "input_turn_ids": inputs,
                }
            )
            previous_turn_id = turn_id
        if not root_seen:
            raise ProjectMasterHostError("DESCENDANT_TASK_FRAME_BOSS_TOPOLOGY_INVALID")
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
        if set(task_frame) != required:
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
            not operations
            or len(set(operations)) != len(operations)
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
        if not normalized_targets:
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
        }

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
            "node": self.project_id,
            "mode": "MASTER",
            "session_id": binding["session_id"],
            "frame_id": binding["frame_id"],
            "anchor_id": binding["anchor_id"],
            "currentness": binding["runtime_currentness_observation"],
            "source_ref": (
                f"git-object-database://{self.project_id}@"
                + self.source_commit_resolver(self.project_root)
            ),
        }

    def _ensure_runtime(self) -> dict[str, str]:
        with self._runtime_lock:
            if (
                self._runtime_binding is not None
                and self._runtime_process is not None
                and self._runtime_process.poll() is None
            ):
                return dict(self._runtime_binding)
            prepared = self._prepared or dict(self.prepare())
            anchor = prepared.get("mode_current_anchor")
            snapshot = anchor.get("snapshot") if isinstance(anchor, Mapping) else None
            payload = (
                snapshot.get("snapshot") if isinstance(snapshot, Mapping) else None
            )
            anchor_id = (
                _text(payload.get("anchor_id"), "mode_current_anchor.anchor_id")
                if isinstance(payload, Mapping)
                else ""
            )
            if not anchor_id:
                raise ProjectMasterHostError("PROJECT_MASTER_ANCHOR_UNAVAILABLE")
            mode_boot_binding = _mode_boot_binding(
                prepared,
                expected_mode="MASTER",
                expected_role="MASTER",
            )
            if mode_boot_binding["anchor_id"] != anchor_id:
                raise ProjectMasterHostError("PROJECT_MASTER_MODE_BOOT_MISMATCH")
            session_id = f"project-master-{uuid4().hex}"
            frame_id = mode_boot_binding["frame_id"]
            token = secrets.token_urlsafe(32)
            python = _required_host_executable("python")
            command = [
                str(python),
                str(self.runtime_cli),
                "session-boot",
                "serve",
                "--repo-root",
                str(self.project_root),
                "--session-id",
                session_id,
                "--frame-id",
                frame_id,
                "--anchor-id",
                anchor_id,
                "--boot-binding-id",
                mode_boot_binding["binding_id"],
                "--host-action",
                "PROJECT_MASTER_SEED_APPLY",
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
                    startup.get("status") != "SESSION_BOOT_IMAGE_CREATED"
                    or not isinstance(host_adapter, Mapping)
                    or not isinstance(runtime_state, Mapping)
                    or runtime_state.get("anchor_id") != anchor_id
                    or runtime_state.get("mode") != "MASTER"
                    or runtime_state.get("role") != "MASTER"
                    or runtime_state.get("executable_runtime_currentness") != "CURRENT"
                    or not isinstance(startup.get("mode_boot_binding"), Mapping)
                    or startup["mode_boot_binding"].get("binding_id")
                    != mode_boot_binding["binding_id"]
                    or startup["mode_boot_binding"].get("status") != "ACTIVE"
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
                "mode_boot_binding_id": mode_boot_binding["binding_id"],
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

    def _register_process_lease(
        self,
        *,
        process: subprocess.Popen[str],
        command: list[str],
        endpoint: str,
        token: str,
    ) -> None:
        if self.session_supervisor is None:
            return
        sessions = self.session_supervisor.list_sessions(
            node=self.project_id, mode="MASTER"
        )
        session = next((item for item in sessions if item["is_default"]), None)
        if session is None:
            raise ProjectMasterHostError("SUPERVISOR_PROJECT_SESSION_UNAVAILABLE")
        identity = launched_process_identity(
            process,
            executable=Path(command[0]),
            command=command,
            endpoint=endpoint,
            handshake_token=token,
        )
        existing = session.get("process_lease")
        expected_version = (
            0 if existing is None else int(existing.get("lease_version", 0))
        )
        acquired = self.session_supervisor.acquire_lease(
            session["session_id"],
            identity,
            expected_lease_version=expected_version,
            stop_capability=token,
        )
        self._supervisor_session_id = str(session["session_id"])
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

    def _master_definition(self) -> Mapping[str, str]:
        registry_path = (
            self.project_root
            / ".ai"
            / "runtime"
            / "project_instance"
            / "mode_registry.json"
        )
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            definition = registry["modes"]["MASTER"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ProjectMasterHostError("PROJECT_MASTER_MODE_UNAVAILABLE") from error
        if not isinstance(definition, Mapping):
            raise ProjectMasterHostError("PROJECT_MASTER_MODE_UNAVAILABLE")
        return {
            "role": _text(definition.get("role"), "MASTER.role"),
            "scope": _text(definition.get("scope"), "MASTER.scope"),
            "mode_profile": _text(
                definition.get("mode_profile"), "MASTER.mode_profile"
            ),
        }

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

    def _git_head(self, project_root: Path) -> str:
        result = self.native_runner(
            NativeCliRequest(
                executable=_required_host_executable("git"),
                arguments=("rev-parse", "HEAD"),
                cwd=project_root,
                timeout_seconds=15,
            )
        )
        source_commit = result.stdout.strip()
        if (
            result.status != "COMPLETED"
            or result.return_code != 0
            or len(source_commit) != 40
            or any(
                character not in "0123456789abcdefABCDEF" for character in source_commit
            )
        ):
            raise ProjectMasterHostError("PROJECT_SOURCE_COMMIT_UNAVAILABLE")
        return source_commit.lower()


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
        session_supervisor: SessionSupervisorStore | None = None,
        requested_mode: str = "MASTER",
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.project_id = _text(project_id, "project_id")
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
                node=self.project_id,
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
            supervisor_session_id = self._supervisor_session_id(
                normalized_provider, normalized_session
            )
            candidate, _ = self.session_supervisor.register_session(
                {
                    "session_id": supervisor_session_id,
                    "node": self.project_id,
                    "mode": self.requested_mode,
                    "provider": normalized_provider,
                    "provider_session_ref": normalized_session,
                    "state": "LIVE",
                    "currentness": "UNKNOWN",
                }
            )
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
                    f"{self.project_id}/{self.requested_mode}/provider-attach"
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
        supervisor_session_id = self._supervisor_session_id(provider, session_ref)
        try:
            return self.session_supervisor.observe_session_activity(
                supervisor_session_id,
                event_type=event_type,
                activity_state=activity_state,
                evidence_ref=evidence_ref,
            )
        except SessionSupervisorError as error:
            if error.code != "SESSION_NOT_FOUND":
                raise
            self.observe_provider_session(provider, session_ref)
            return self.session_supervisor.observe_session_activity(
                supervisor_session_id,
                event_type=event_type,
                activity_state=activity_state,
                evidence_ref=evidence_ref,
            )

    def observe_current_anchor(self, anchor_ref: str) -> dict[str, Any] | None:
        if self.session_supervisor is None:
            return None
        selected = next(
            (
                session
                for session in self.session_supervisor.list_sessions(
                    node=self.project_id,
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
                node=self.project_id,
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
                "node": self.project_id,
                "mode": self.requested_mode,
                "provider": legacy["provider"],
                "provider_session_ref": legacy["session_ref"],
                "state": "DISCONNECTED",
                "currentness": "UNKNOWN",
            }
        )
        if not session["is_default"]:
            self.session_supervisor.set_default(
                supervisor_session_id,
                expected_pointer_version=session["default_pointer_version"],
            )

    def _supervisor_session_id(self, provider: str, session_ref: str) -> str:
        del provider, session_ref
        material = json.dumps(
            {
                "node": self.project_id,
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

    def recover(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE inbox_message
                SET state = 'PENDING', updated_at = ?
                WHERE state = 'PROCESSING'
                """,
                (utc_now(),),
            )
            rows = connection.execute(
                """
                SELECT envelope_json
                FROM inbox_message
                WHERE state = 'PENDING'
                ORDER BY updated_at, message_id
                """
            ).fetchall()
        return [json.loads(str(row["envelope_json"])) for row in rows]

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
        "request or attach the Session Boot executor with "
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
        requested_mode: str = "MASTER",
        actor_label: str | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.store = store
        self.native_runner = native_runner
        self.model = model.strip()
        self.effort = str(effort or "AUTO").strip().upper()
        self.max_turns = max(1, int(max_turns))
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self.actor_label = (
            _text(actor_label, "actor_label")
            if actor_label is not None
            else f"Project Master for {self.project_id}"
        )
        self.session_id = store.session_ref_for("GROK")
        self.connection_state = "UNKNOWN"
        self._greeting_pending = False
        self._permission_requester: Callable[[Mapping[str, Any]], str | None] | None = (
            None
        )
        self._gateway: UniverseAcpGateway | None = None

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
        if self._gateway is not None:
            self._gateway.close()
            self._gateway = None

    def _acp_gateway(self) -> UniverseAcpGateway:
        if self._gateway is not None:
            return self._gateway
        if self._permission_requester is None:
            raise ProjectMasterHostError("AGENT_PERMISSION_GATEWAY_UNBOUND")
        executable, environment, default_model = _resolve_grok()
        if executable is None:
            raise ProjectMasterHostError("GROK_CLI_UNAVAILABLE")
        model = self.model or default_model

        def observe_session(session_id: str) -> None:
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
            "and currentness checks. Treat this as Mode intent only."
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
        return (
            "Universe Project Room message\n"
            f"message_id: {_text(message.get('message_id'), 'message.message_id')}\n"
            f"kind: {_text(message.get('kind'), 'message.kind')}\n"
            f"sender: {_text(message.get('sender'), 'message.sender')}\n"
            f"project_runtime_context: {context_text}\n\n"
            f"project_skill_plan_context: {skill_plan_text}\n\n"
            f"project_skill_binding_proposals: {skill_binding_text}\n\n"
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
        self.session_id = store.session_ref_for("CODEX")
        self.connection_state = "UNKNOWN"
        self._greeting_pending = False
        self._permission_requester: Callable[[Mapping[str, Any]], str | None] | None = (
            None
        )
        self._gateway: UniverseAcpGateway | None = None

    @property
    def session_ref(self) -> str:
        return (
            f"codex-app-server:{self.session_id}"
            if self.session_id
            else f"codex-app-server:pending:{self.project_id}"
        )

    def reply(self, message: Mapping[str, Any]) -> str:
        return self.reply_stream(message, lambda _delta: None)

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

        def observe_session(session_id: str) -> None:
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
            "and currentness checks. Treat this as Mode intent only."
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
        return (
            "Universe Project Room message\n"
            f"message_id: {_text(message.get('message_id'), 'message.message_id')}\n"
            f"kind: {_text(message.get('kind'), 'message.kind')}\n"
            f"sender: {_text(message.get('sender'), 'message.sender')}\n"
            f"project_runtime_context: {context_text}\n\n"
            f"project_skill_plan_context: {skill_plan_text}\n\n"
            f"project_skill_binding_proposals: {skill_binding_text}\n\n"
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
        )
        self.session_id = store.session_ref_for("CLAUDE")
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

    def _acp_gateway(self) -> UniverseAcpGateway:
        if self._gateway is not None:
            return self._gateway
        if self._permission_requester is None:
            raise ProjectMasterHostError("AGENT_PERMISSION_GATEWAY_UNBOUND")
        executable, environment, default_model = _resolve_claude()
        if executable is None:
            raise ProjectMasterHostError("CLAUDE_CLI_UNAVAILABLE")
        model = self.model or default_model

        def observe_session(session_id: str) -> None:
            self.session_id = session_id
            self.connection_state = self.store.observe_provider_session(
                "CLAUDE", session_id
            )
            self._greeting_pending = self.connection_state != "REUSED"

        # Resident Claude: one long-lived stream-json process for this target,
        # with permission prompts routed to the existing requester through the
        # loopback MCP bridge.
        bridge = ClaudePermissionBridge(
            session_ref=self.session_ref,
            permission_requester=self._permission_requester,
        )
        broker: ClaudePermissionBroker | None = None
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
        session_supervisor: SessionSupervisorStore | None = None,
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
        self.continuity_coordinator = continuity_coordinator
        self.coordinate_resolver = coordinate_resolver
        self.permission_requester = permission_requester or (lambda _request: None)
        self.store = ProjectMasterSessionStore(
            database_path,
            self.target_id,
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
        self._last_interaction: dict[str, str] | None = None
        self._last_interaction_at = 0.0
        self._lock = threading.RLock()

    def prepare(
        self,
        provider: str,
        *,
        model: str = "",
        effort: str = "AUTO",
    ) -> dict[str, Any]:
        normalized_provider = _provider(provider)
        with self._lock:
            active = self._ensure(
                normalized_provider,
                model=model,
                effort=effort,
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

    def close(self) -> None:
        with self._lock:
            provider = self._provider
            provider_name = self._provider_name
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

    def _ensure(
        self,
        provider: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> MasterProvider:
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
        selected_session_ref = self.store.session_ref_for(provider)
        if self._provider is not None and self._provider_name == provider:
            if (
                (
                    selected_session_ref is None
                    or selected_session_ref == self._provider_session_ref
                )
                and selected_model == self._provider_model
                and selected_effort == self._provider_effort
            ):
                setattr(self._provider, "connection_state", "REUSED")
                return self._provider
            replacement_trigger = (
                "SESSION_SELECTION_CHANGED"
                if selected_session_ref != self._provider_session_ref
                else "PROVIDER_PROFILE_CHANGED"
            )
        else:
            replacement_trigger = "PROVIDER_SWITCH"
        if self._provider is not None:
            previous = self._provider
            previous_name = self._provider_name
            self._save_continuity(replacement_trigger, previous, previous_name)
            self._provider = None
            self._provider_name = None
            self._provider_session_ref = None
            self._provider_model = ""
            self._provider_effort = "AUTO"
            close = getattr(previous, "close", None)
            if callable(close):
                close()
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
        self._provider = active
        self._provider_name = provider
        self._provider_model = selected_model
        self._provider_effort = selected_effort
        self._provider_session_ref = self.store.session_ref_for(provider)
        if self._provider_session_ref is None:
            raw_session_id = getattr(active, "session_id", None)
            if isinstance(raw_session_id, str) and raw_session_id.strip():
                self._provider_session_ref = raw_session_id.strip()
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
            target_kind="UNIVERSE_CONDUCTOR",
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
                and current.provider_session_ref == session_ref
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
                permission_requester=self._permission_unavailable,
                provider_factory=self.provider_factory,
            )
            host.store.observe_provider_session(provider, session_ref)
            try:
                connection = host.prepare(provider)
                observed_ref = host.active_provider_session_ref()
                if observed_ref != session_ref:
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
        if binding.get("provider_session_ref") != handle.provider_session_ref:
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

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        for envelope in self.store.recover():
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
                }
                self._last_completion = completion
                self._last_completion_at = time.monotonic()
                self.completion_observer(completion)
            except Exception:
                pass
        self._active_bridge_id = ""
        self._active_message_id = ""

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

    def close(self) -> None:
        self.bridge_server.shutdown()
        self.bridge_server.server_close()
        self.worker.close()
        close_coordinator = getattr(self.coordinator, "close", None)
        if callable(close_coordinator):
            close_coordinator()
        self.thread.join(timeout=5)
        os.environ.pop(self.credential_env, None)


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
        self.completion_observer = completion_observer
        self.room_event_observer = room_event_observer
        self._handles: dict[str, ResidentProjectMasterHandle] = {}
        self._lock = threading.RLock()

    def ensure(self, project: Mapping[str, Any]) -> dict[str, Any]:
        project_id = _text(project.get("project_id"), "project.project_id")
        project_root = (
            Path(_text(project.get("project_root"), "project.project_root"))
            .expanduser()
            .resolve(strict=True)
        )
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
        selected_session_ref = store.session_ref_for(selected_provider)
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
                and handle.provider == selected_provider
                and handle.model == selected_model
                and handle.effort == selected_effort
                and (
                    not selected_session_ref
                    or handle.session_ref == selected_session_ref
                )
                and handle.governance_context_key == governance_context_key
            ):
                setattr(handle.worker.provider, "connection_state", "REUSED")
                return {
                    "status": "RESIDENT",
                    "project_id": project_id,
                    "provider": selected_provider,
                    "endpoint": handle.endpoint,
                    "session_connection": self._handle_connection(handle),
                }
            if handle is not None:
                self._save_handle_continuity(handle, "PROVIDER_SWITCH")
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
                )
            )
            try:
                bind_permission = getattr(provider, "set_permission_requester", None)
                if callable(bind_permission):
                    bind_permission(self._permission_before_worker)
                prepare_provider = getattr(provider, "prepare_session", None)
                if callable(prepare_provider):
                    prepare_provider()
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
            worker.start()
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
            )
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
        if binding.get("provider_session_ref") != handle.worker.provider.session_ref:
            raise ProjectMasterHostError("PROJECT_MASTER_NATIVE_SESSION_MISMATCH")
        if not handle.bridge_id:
            raise ProjectMasterHostError("PROJECT_MASTER_BRIDGE_ID_UNAVAILABLE")
        return handle.worker.submit_room_event(
            binding=binding,
            event=event,
            bridge_id=handle.bridge_id,
        )

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

    @staticmethod
    def _handle_connection(
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

    @staticmethod
    def _default_provider(
        provider: str,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        model: str = "",
        effort: str = "AUTO",
    ) -> MasterProvider:
        if provider == "GROK":
            return GrokProjectMasterRuntime(
                project_root, project_id, store, model=model, effort=effort
            )
        if provider == "CODEX":
            return CodexProjectMasterRuntime(
                project_root, project_id, store, model=model, effort=effort
            )
        if provider == "CLAUDE":
            return ClaudeProjectMasterRuntime(
                project_root, project_id, store, model=model, effort=effort
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


def _mode_boot_binding(
    preparation: Mapping[str, Any],
    *,
    expected_mode: str,
    expected_role: str,
) -> dict[str, str]:
    binding = preparation.get("mode_boot_binding")
    if not isinstance(binding, Mapping) or binding.get("status") != "PREPARED":
        raise ProjectMasterHostError("PROJECT_MASTER_MODE_BOOT_BINDING_UNAVAILABLE")
    normalized = {
        field: _text(binding.get(field), f"mode_boot_binding.{field}")
        for field in ("binding_id", "mode", "role", "frame_id", "anchor_id")
    }
    if (
        normalized["mode"] != expected_mode
        or normalized["role"] != expected_role
    ):
        raise ProjectMasterHostError("PROJECT_MASTER_MODE_BOOT_BINDING_MISMATCH")
    if normalized["anchor_id"] != _mode_current_anchor_ref(preparation):
        raise ProjectMasterHostError("PROJECT_MASTER_MODE_BOOT_BINDING_MISMATCH")
    return normalized


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
