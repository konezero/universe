"""Canonical ai-career reference runtime package."""

from .anchor_session_memory_adapter import (
    AnchorSessionMemoryHostAdapter,
    AnchorSessionMemoryHostServer,
    call_host_adapter,
)
from .anchor_session_memory_runtime import AnchorSessionMemoryRuntime
from .continuity_runtime import (
    ContinuityCommandError,
    ContinuityCommandProfile,
    load_continuity_profile,
    run_continuity_command,
)
from .continuity_store_runtime import ContinuityStore, ContinuityStoreError
from .execution_binding_runtime import (
    ExecutionBindingError,
    apply_approved_git_proposal,
    apply_execution_binding,
    build_assignment_proposal,
)
from .execution_guard_adapter import invoke_execution_guard
from .execution_guard_runtime import ExecutionGuardError, ExecutionGuardRuntime
from .file_mutation_gateway import FileMutationGateway
from .git_command_gateway import GitCommandGateway
from .git_action_registry import GitAction, resolve_git_action
from .git_proposal_runtime import GitProposalError, GitProposalJournal
from .mode_registry_runtime import (
    ModeDefinition,
    ModeRegistry,
    ModeRegistryError,
    load_mode_registry,
    plan_mode_registry_mutation,
    verify_mode_request,
)
from .os_status_runtime import (
    OsStatusError,
    evaluate_source_only_os_status,
)
from .receipt_verifying_write_gateway import ReceiptVerifyingWriteGateway
from .session_boot_adapter import (
    PreparedSessionBoot,
    prepare_session_boot_server,
    read_installation_manifest,
    read_project_runtime_status,
)
from .session_boot_runtime import (
    SessionBootCoordinates,
    SessionBootError,
    build_session_boot_artifacts,
)
from .task_frame_adapter import invoke_task_frame_runtime
from .task_frame_runtime import (
    ParentObservation,
    TaskFrameProfile,
    TaskFrameProfileError,
    TaskFrameRuntime,
    TaskTurn,
    load_profile,
)

__version__ = "1.5.0"

__all__ = [
    "AnchorSessionMemoryHostAdapter",
    "AnchorSessionMemoryHostServer",
    "AnchorSessionMemoryRuntime",
    "ContinuityCommandError",
    "ContinuityCommandProfile",
    "ContinuityStore",
    "ContinuityStoreError",
    "ExecutionBindingError",
    "ExecutionGuardError",
    "ExecutionGuardRuntime",
    "FileMutationGateway",
    "GitCommandGateway",
    "GitAction",
    "GitProposalError",
    "GitProposalJournal",
    "ModeDefinition",
    "ModeRegistry",
    "ModeRegistryError",
    "OsStatusError",
    "PreparedSessionBoot",
    "ReceiptVerifyingWriteGateway",
    "ParentObservation",
    "TaskFrameProfile",
    "TaskFrameProfileError",
    "TaskFrameRuntime",
    "TaskTurn",
    "SessionBootCoordinates",
    "SessionBootError",
    "build_session_boot_artifacts",
    "build_assignment_proposal",
    "call_host_adapter",
    "invoke_task_frame_runtime",
    "invoke_execution_guard",
    "evaluate_source_only_os_status",
    "apply_execution_binding",
    "apply_approved_git_proposal",
    "load_profile",
    "load_mode_registry",
    "load_continuity_profile",
    "prepare_session_boot_server",
    "plan_mode_registry_mutation",
    "read_installation_manifest",
    "read_project_runtime_status",
    "run_continuity_command",
    "resolve_git_action",
    "verify_mode_request",
]
