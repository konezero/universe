"""Receipt-aware, bounded Git mutation gateway for one repository root."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .execution_guard_runtime import ExecutionGuardError, ExecutionGuardRuntime
from .git_action_registry import (
    LOCAL_GIT_TIMEOUT_SECONDS,
    GitAction,
    command_payload_sha256,
    resolve_git_action,
)


REMOTE_GIT_TIMEOUT_SECONDS = 120
GIT_MUTATION_TIMEOUT_SECONDS = 300


class GitCommandGateway:
    """Run a small Git mutation allow-list after consuming a matching receipt."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve(strict=True)

    @staticmethod
    def command_payload_sha256(command_argv: Sequence[str]) -> str:
        """Return the canonical command payload digest required by the Guard."""

        return command_payload_sha256(command_argv)

    @classmethod
    def validate_command(cls, command_argv: Any, *, repository_root: Path) -> list[str] | None:
        """Return argv only when it resolves through the shared registry."""

        action = resolve_git_action(command_argv, repository_root=repository_root)
        return list(action.command_argv) if action is not None else None

    @classmethod
    def resolve_action(
        cls, command_argv: Any, *, repository_root: Path
    ) -> GitAction | None:
        return resolve_git_action(command_argv, repository_root=repository_root)

    def apply(
        self,
        *,
        guard: ExecutionGuardRuntime,
        snapshot: Mapping[str, Any],
        payload: Mapping[str, Any],
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = payload.get("request")
        if not isinstance(request, Mapping) or str(request.get("operation", "")).upper() != "COMMAND":
            return _blocked("GIT_COMMAND_OPERATION_REQUIRED")
        if not self._matches_repository_root(request.get("target")):
            return _blocked("GIT_COMMAND_TARGET_MISMATCH")
        action = self.resolve_action(
            request.get("command_argv"), repository_root=self.repository_root
        )
        if action is None:
            return _blocked("GIT_COMMAND_NOT_ALLOWLISTED")
        argv = list(action.command_argv)
        if str(request.get("payload_sha256", "")) != self.command_payload_sha256(argv):
            return _blocked("GIT_COMMAND_PAYLOAD_MISMATCH")
        if not self._is_git_work_tree():
            return _blocked("GIT_REPOSITORY_REQUIRED")

        try:
            consumed = guard.consume(
                receipt_id=str(payload.get("receipt_id", "")),
                snapshot=snapshot,
                request=request,
                observed_at=str(payload.get("observed_at", "")),
                task_frame_lineage_verification=task_frame_lineage_verification,
            )
        except ExecutionGuardError as error:
            return _blocked(error.error_code, detail=error.detail)
        if consumed.get("status") != "PERMIT_RECEIPT_CONSUMED":
            return _blocked("PERMIT_RECEIPT_REJECTED", receipt_result=consumed)

        environment = dict(os.environ)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        execution_argv = argv
        hardening: dict[str, Any] = {
            "shell": False,
            "terminal_prompt": False,
        }
        try:
            if action.name == "REBASE_CURRENT_BRANCH":
                result, execution_argv, hardening = self._run_rebase_action(
                    action, environment=environment, hardening=hardening
                )
            elif argv[1] == "commit":
                with tempfile.TemporaryDirectory(prefix="ai-career-git-hooks-") as hooks:
                    execution_argv = [
                        "git",
                        "-c",
                        f"core.hooksPath={hooks}",
                        "-c",
                        "commit.gpgSign=false",
                        *argv[1:],
                    ]
                    hardening.update(
                        {
                            "repository_hooks": "DISABLED",
                            "commit_signing": (
                                "DISABLED" if argv[1] == "commit" else "NOT_APPLICABLE"
                            ),
                        }
                    )
                    result = self._run(
                        execution_argv,
                        environment=environment,
                    )
            else:
                result = self._run(execution_argv, environment=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "status": "GIT_COMMAND_FAILED",
                "decision": "BLOCKED",
                "repository_write": False,
                "reasons": ["HOST_GIT_COMMAND_FAILED"],
                "detail": str(error),
                "receipt_id": consumed["receipt_id"],
            }

        return {
            "status": "GIT_COMMAND_APPLIED" if result.returncode == 0 else "GIT_COMMAND_FAILED",
            "decision": "APPLIED" if result.returncode == 0 else "BLOCKED",
            "repository_write": result.returncode == 0,
            "command_argv": argv,
            "git_action": action.name,
            "execution_argv": execution_argv,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "receipt_id": consumed["receipt_id"],
            "execution_hardening": hardening,
        }

    def _run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.repository_root,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_MUTATION_TIMEOUT_SECONDS,
            env=dict(environment),
        )

    def _run_rebase_action(
        self,
        action: GitAction,
        *,
        environment: Mapping[str, str],
        hardening: dict[str, Any],
    ) -> tuple[subprocess.CompletedProcess[str], list[str], dict[str, Any]]:
        fetch_argv, rebase_argv = action.execution_argvs
        fetch = self._run(fetch_argv, environment=environment)
        if fetch.returncode != 0:
            return fetch, list(fetch_argv), hardening
        with tempfile.TemporaryDirectory(prefix="ai-career-git-hooks-") as hooks:
            execution_argv = [
                "git",
                "-c",
                f"core.hooksPath={hooks}",
                *rebase_argv[1:],
            ]
            hardening.update(
                {
                    "repository_hooks": "DISABLED",
                    "commit_signing": "NOT_APPLICABLE",
                }
            )
            result = self._run(execution_argv, environment=environment)
        return result, execution_argv, hardening

    def _matches_repository_root(self, target: Any) -> bool:
        if not isinstance(target, str) or not target.strip():
            return False
        try:
            return Path(target).resolve(strict=True) == self.repository_root
        except OSError:
            return False

    def _is_git_work_tree(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repository_root,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=LOCAL_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "GIT_COMMAND_BLOCKED",
        "decision": "BLOCKED",
        "repository_write": False,
        "reasons": [reason],
        **extra,
    }
