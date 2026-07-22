"""Aggregate the receipt-aware Host mutation paths for one repository root."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .execution_guard_runtime import ExecutionGuardRuntime
from .file_mutation_gateway import FileMutationGateway
from .git_command_gateway import GitCommandGateway


class ReceiptVerifyingWriteGateway:
    """Expose only mutation paths that consume a matching Guard receipt."""

    pre_write_hook = "AVAILABLE"
    mutation_gateway = "AVAILABLE"

    def __init__(self, repository_root: Path) -> None:
        self._files = FileMutationGateway(repository_root)
        self._git = GitCommandGateway(repository_root)

    def apply_file(
        self,
        *,
        guard: ExecutionGuardRuntime,
        snapshot: Mapping[str, Any],
        payload: Mapping[str, Any],
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._files.apply(
            guard=guard,
            snapshot=snapshot,
            payload=payload,
            task_frame_lineage_verification=task_frame_lineage_verification,
        )

    def apply_git(
        self,
        *,
        guard: ExecutionGuardRuntime,
        snapshot: Mapping[str, Any],
        payload: Mapping[str, Any],
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._git.apply(
            guard=guard,
            snapshot=snapshot,
            payload=payload,
            task_frame_lineage_verification=task_frame_lineage_verification,
        )
